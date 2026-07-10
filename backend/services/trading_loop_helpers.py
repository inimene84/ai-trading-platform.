"""
Trading Loop Helper Services
=============================
Decoupled manager classes for TradingLoopService.
Implements Emergency Exits, Broker Sync, Trailing Stops, Partial TP,
Exchange Protection, and Performance Metrics to clean up the main loop orchestrator.
"""

import logging
import os
import asyncio
from datetime import datetime, timezone

from backend.database.models import Trade, PortfolioSnapshot
from backend.services.influxdb_writer import influx
from backend.services.unified_trading import UnifiedTrading, UnifiedOrder, OrderSide, OrderType
from backend.services.position_manager import get_position_manager

logger = logging.getLogger(__name__)

# An externally-closed perp position's exit fill is always near the entry
# (SL/TP sit within a few ATR; even liquidation at 10x is ~±10%). Anything
# further off is a bad price source, not a real fill — e.g. 35 historical
# ARBUSDT rows were closed at a bogus 0.00075 vs ~$0.08 entries, fabricating
# +$856 of phantom P&L that poisoned every stat and the AI feedback loop.
MAX_EXIT_PRICE_DEVIATION = 0.5


def is_plausible_exit_price(entry_price, exit_price) -> bool:
    """True when exit_price is a believable close for a position opened at entry_price."""
    if not exit_price or exit_price <= 0 or not entry_price or entry_price <= 0:
        return False
    return abs(exit_price - entry_price) / entry_price <= MAX_EXIT_PRICE_DEVIATION


class EmergencyExitManager:
    """Manages Step 0 pre-sync emergency exit checks."""

    @staticmethod
    async def run_emergency_exits(
        db,
        broker,
        pyramid_layers: dict,
        sl_cooldown: dict,
    ) -> int:
        exits_triggered = 0
        try:
            pm = get_position_manager()
            pre_open = db.query(Trade).filter(Trade.status.in_(["open", "filled"])).all()
            if not pre_open:
                return 0

            logger.info(f"  [POSITION MGR] Pre-sync review: {len(pre_open)} open positions")
            
            # Fetch live mark prices from broker
            live_prices = {}
            try:
                for bp in broker.get_positions():
                    live_prices[bp['symbol']] = float(bp.get('mark_price') or bp.get('entry_price') or 0)
            except Exception as _bfs_err:
                logger.error(f"  [STEP 0] Price fetch error: {_bfs_err}")
                return 0

            ut_pre = UnifiedTrading()
            for trade in pre_open:
                try:
                    live_px = live_prices.get(trade.symbol, 0.0)
                    if live_px <= 0:
                        continue  # can't check without a price
                    entry_px = trade.entry_price or 0.0
                    if not entry_px:
                        continue
                    direction = trade.direction or "BUY"
                    if direction == "BUY":
                        pnl_pct = ((live_px - entry_px) / entry_px) * 100
                    else:
                        pnl_pct = ((entry_px - live_px) / entry_px) * 100
                        
                    emergency_threshold = pm.emergency_drawdown_pct
                    if pnl_pct <= emergency_threshold:
                        logger.warning(
                            f"  [EMERGENCY EXIT] {trade.symbol}: PnL={pnl_pct:.1f}% "
                            f"<= threshold {emergency_threshold:.1f}% — FORCE CLOSING"
                        )
                        close_side = OrderSide.SELL if direction == "BUY" else OrderSide.BUY
                        res = ut_pre.place_order(UnifiedOrder(
                            symbol=trade.symbol,
                            side=close_side,
                            order_type=OrderType.MARKET,
                            quantity=trade.quantity,
                            reduce_only=True,
                        ))
                        if res.success:
                            trade.exit_price = res.filled_price or live_px
                            trade.pnl = (pnl_pct / 100) * entry_px * trade.quantity
                            trade.status = "closed"
                            trade.closed_at = datetime.now(timezone.utc)
                            trade.notes = (trade.notes or "") + f" | EMERGENCY EXIT: PnL={pnl_pct:.1f}%"
                            db.add(trade)
                            if trade.symbol in pyramid_layers:
                                del pyramid_layers[trade.symbol]
                            sl_cooldown[trade.symbol] = datetime.now(timezone.utc)
                            exits_triggered += 1
                            logger.warning(f"  [EMERGENCY EXIT] {trade.symbol} CLOSED. PnL={pnl_pct:.1f}%")
                        else:
                            if hasattr(res, 'message') and ('-2022' in str(res.message) or 'already' in str(res.message).lower()):
                                logger.info(f"  [EMERGENCY EXIT] {trade.symbol} already flat on exchange — marking DB closed")
                                trade.status = 'closed'
                                trade.closed_at = datetime.now(timezone.utc)
                                trade.notes = (trade.notes or '') + ' | EMERGENCY-EXIT: already flat on exchange'
                                db.add(trade)
                                if trade.symbol in pyramid_layers:
                                    del pyramid_layers[trade.symbol]
                                sl_cooldown[trade.symbol] = datetime.now(timezone.utc)
                                exits_triggered += 1
                            else:
                                logger.error(f"  [EMERGENCY EXIT] {trade.symbol} FAILED: {res.message}")
                except Exception as e:
                    logger.error(f"  [EMERGENCY EXIT] {trade.symbol} error: {e}")
            if exits_triggered > 0:
                db.commit()
        except Exception as e:
            logger.error(f"Pre-sync PM outer error: {e}")
        return exits_triggered


class BrokerPositionSyncService:
    """Syncs DB open trades with live broker state, reconciling external closures."""

    @staticmethod
    async def sync_positions(
        db,
        broker,
        pyramid_layers: dict,
        sl_cooldown: dict,
    ) -> int:
        updated = 0
        try:
            broker_raw = await asyncio.get_event_loop().run_in_executor(
                None, lambda: broker.get_positions(raise_on_error=True)
            )
            broker_symbols = {
                bp['symbol'] for bp in broker_raw
                if float(bp.get('quantity') or bp.get('positionAmt') or 0) != 0
            }

            db_trades = db.query(Trade).filter(Trade.status.in_(["open", "filled"])).all()
            # An entirely empty exchange snapshot while SQL still has open
            # trades is ambiguous: it may mean every position closed, but it
            # also occurs on permissions/testnet/API degradation. Never flatten
            # the whole DB and cancel every protective order from one empty
            # response. A non-empty snapshot can safely reconcile symbols that
            # are individually absent; an all-empty snapshot requires operator
            # confirmation or a later explicit reconciliation path.
            if db_trades and not broker_raw:
                logger.error(
                    "Broker returned an empty positions snapshot while DB has "
                    f"{len(db_trades)} open trade row(s); refusing bulk close/order cancellation"
                )
                return 0

            exit_price_cache: dict = {}
            cancelled_orphans: set = set()

            for t in db_trades:
                if t.symbol not in broker_symbols:
                    logger.info(f"  [ {t.symbol} ] Position not found in broker, marking as closed in DB.")

                    if t.symbol not in exit_price_cache:
                        exit_price_cache[t.symbol] = await asyncio.get_event_loop().run_in_executor(
                            None, broker.get_exit_price, t.symbol
                        )
                    exit_px = exit_price_cache[t.symbol]

                    t.status = "closed"
                    t.closed_at = datetime.now(timezone.utc)
                    if exit_px and t.entry_price and t.quantity and is_plausible_exit_price(t.entry_price, exit_px):
                        t.exit_price = exit_px
                        if str(t.direction).upper() == "BUY":
                            t.pnl = round((exit_px - t.entry_price) * t.quantity, 4)
                        else:
                            t.pnl = round((t.entry_price - exit_px) * t.quantity, 4)
                        t.notes = (t.notes or "") + " | Closed externally (sync)"
                    else:
                        # Bad or missing exit price: record the close honestly as
                        # unknown P&L rather than fabricating a number from a
                        # corrupt price (which previously produced phantom gains).
                        if exit_px:
                            logger.error(
                                f"  [ {t.symbol} ] implausible exit price {exit_px} "
                                f"vs entry {t.entry_price} — recording close with unknown P&L"
                            )
                        t.exit_price = None
                        t.pnl = None
                        t.notes = (t.notes or "") + (
                            f" | Closed externally (sync; exit price unavailable/implausible raw={exit_px})"
                        )

                    if t.symbol not in cancelled_orphans:
                        try:
                            broker.cancel_all_orders(t.symbol)
                            cancelled_orphans.add(t.symbol)
                        except Exception as _ce:
                            logger.warning(f"  [ {t.symbol} ] orphan order cleanup failed: {_ce}")

                    if t.symbol in pyramid_layers:
                        del pyramid_layers[t.symbol]
                    sl_cooldown[t.symbol] = datetime.now(timezone.utc)
                    updated += 1

            if updated > 0:
                db.commit()
                logger.info(f"Synced {updated} closed position(s) from broker.")
        except Exception as e:
            db.rollback()
            logger.warning(f"Failed to sync positions with broker: {e}")
        return updated


class TrailingStopManager:
    """Manages ATR trailing stops and step trailing, syncing trailed levels to broker."""

    @staticmethod
    def apply_trailing_stop(
        db,
        symbol: str,
        bars: list[dict],
        high_water: dict,
        risk_config,
        broker,
    ) -> None:
        if not getattr(risk_config, "trailing_stop_enabled", False):
            return
        if getattr(risk_config, "native_trailing_enabled", False):
            return
        if not bars or len(bars) < 16:
            return

        try:
            current_price = bars[-1]["close"]
            highs = [b["high"] for b in bars[-15:]]
            lows = [b["low"] for b in bars[-15:]]
            closes = [b["close"] for b in bars[-16:-1]]
            trs = []
            for h, l_val, c in zip(highs, lows, closes):
                trs.append(max(h - l_val, abs(h - c), abs(l_val - c)))
            atr = sum(trs) / len(trs) if trs else 0.0
            if atr <= 0:
                atr = current_price * 0.02

            activation_dist = risk_config.trail_activation_atr * atr
            trail_dist = risk_config.trail_atr_mult * atr

            trades = db.query(Trade).filter(Trade.symbol == symbol, Trade.status.in_(["open", "filled"])).all()
            for trade in trades:
                if not trade.entry_price:
                    continue

                if trade.direction == "BUY":
                    hw = high_water.get(trade.id, max(trade.entry_price, current_price))
                    hw = max(hw, current_price)
                    high_water[trade.id] = hw

                    if hw - trade.entry_price < activation_dist:
                        if hw - trade.entry_price >= (activation_dist * 0.75):
                            fee_offset = trail_dist * 0.1
                            candidate = trade.entry_price + fee_offset
                            old_stop = trade.stop_loss if trade.stop_loss is not None else float("-inf")
                            if candidate > old_stop:
                                trade.stop_loss = candidate
                                logger.info(f"  [ {symbol} ] STEP-TRAIL ↑ stop to BE+fees {candidate:.6f} (hw={hw:.6f} >= 0.75 activation)")
                                TrailingStopManager._sync_exchange_stop(trade, candidate, broker)
                        continue

                    candidate = hw - trail_dist
                    candidate = min(candidate, current_price)
                    old_stop = trade.stop_loss if trade.stop_loss is not None else float("-inf")
                    if candidate > old_stop:
                        trade.stop_loss = candidate
                        logger.info(f"  [ {symbol} ] TRAIL ↑ stop {old_stop if old_stop != float('-inf') else 'None'} -> {candidate:.6f}")
                        TrailingStopManager._sync_exchange_stop(trade, candidate, broker)
                else:  # SHORT
                    lw = high_water.get(trade.id, min(trade.entry_price, current_price))
                    lw = min(lw, current_price)
                    high_water[trade.id] = lw

                    if trade.entry_price - lw < activation_dist:
                        if trade.entry_price - lw >= (activation_dist * 0.75):
                            fee_offset = trail_dist * 0.1
                            candidate = trade.entry_price - fee_offset
                            old_stop = trade.stop_loss if trade.stop_loss is not None else float("inf")
                            if candidate < old_stop:
                                trade.stop_loss = candidate
                                logger.info(f"  [ {symbol} ] STEP-TRAIL ↓ stop to BE+fees {candidate:.6f} (lw={lw:.6f} <= 0.75 activation)")
                                TrailingStopManager._sync_exchange_stop(trade, candidate, broker)
                        continue

                    candidate = lw + trail_dist
                    candidate = max(candidate, current_price)
                    old_stop = trade.stop_loss if trade.stop_loss is not None else float("inf")
                    if candidate < old_stop:
                        trade.stop_loss = candidate
                        logger.info(f"  [ {symbol} ] TRAIL ↓ stop {old_stop if old_stop != float('inf') else 'None'} -> {candidate:.6f}")
                        TrailingStopManager._sync_exchange_stop(trade, candidate, broker)
        except Exception as e:
            logger.warning(f"  [ {symbol} ] trailing-stop error (stop unchanged): {e}")

    @staticmethod
    def _sync_exchange_stop(trade, new_stop: float, broker):
        try:
            if os.getenv("ACTIVE_BROKER", "ctrader") != "binance_futures":
                return
            from backend.services.trading_mode import get_trading_mode, TradingMode
            if get_trading_mode() != TradingMode.LIVE:
                return
            res = broker.replace_stop_loss(
                symbol=trade.symbol,
                direction=trade.direction,
                new_stop_price=new_stop,
                quantity=trade.quantity,
            )
            status = res.get("status") if isinstance(res, dict) else None
            if status not in ("replaced", "simulated", "skipped"):
                logger.warning(f"  [ {trade.symbol} ] exchange-stop sync returned {res}")
        except Exception as e:
            logger.warning(f"  [ {trade.symbol} ] exchange-stop sync error: {e}")


class PartialTPManager:
    """Manages partial profit taking (TP) by closing a portion of the position."""

    @staticmethod
    def apply_partial_tp(
        db,
        symbol: str,
        bars: list[dict],
        risk_config,
        strategy_name: str,
    ) -> None:
        if not getattr(risk_config, "partial_tp_enabled", False):
            return
        if not bars or len(bars) < 16:
            return
        try:
            import numpy as np
            current_price = bars[-1]["close"]

            highs = np.array([b["high"] for b in bars[-15:]])
            lows = np.array([b["low"] for b in bars[-15:]])
            closes = np.array([b["close"] for b in bars[-16:-1]])
            tr = np.maximum(np.maximum(highs - lows, np.abs(highs - closes)), np.abs(lows - closes))
            atr = float(np.mean(tr))
            if atr <= 0:
                atr = current_price * 0.02

            partial_dist = risk_config.partial_tp_atr_mult * atr
            close_pct = risk_config.partial_tp_close_pct

            trades = db.query(Trade).filter(Trade.symbol == symbol, Trade.status.in_(["open", "filled"])).all()
            for trade in trades:
                if not trade.entry_price or not trade.quantity:
                    continue
                notes = trade.notes or ""
                if "PARTIAL_TP_DONE" in notes:
                    continue

                if trade.direction == "BUY":
                    profit_dist = current_price - trade.entry_price
                else:
                    profit_dist = trade.entry_price - current_price

                if profit_dist < partial_dist:
                    continue

                close_qty = trade.quantity * close_pct
                if close_qty <= 0:
                    continue

                ut = UnifiedTrading()
                close_side = OrderSide.SELL if trade.direction == "BUY" else OrderSide.BUY
                res = ut.place_order(UnifiedOrder(
                    symbol=trade.symbol,
                    side=close_side,
                    order_type=OrderType.MARKET,
                    quantity=close_qty,
                    reduce_only=True,
                ))

                if res.success:
                    filled_px = res.filled_price or current_price
                    if trade.direction == "BUY":
                        partial_pnl = (filled_px - trade.entry_price) * close_qty
                    else:
                        partial_pnl = (trade.entry_price - filled_px) * close_qty

                    trade.quantity = trade.quantity - close_qty
                    trade.notes = (notes + f" | PARTIAL_TP_DONE: closed {close_pct*100:.0f}% "
                                   f"({close_qty:.6f}) @ {filled_px:.6f}, partial PnL=${partial_pnl:+.4f}")
                    logger.info(f"  [ {symbol} ] PARTIAL TP: closed {close_pct*100:.0f}% ({close_qty:.6f}) @ {filled_px:.6f}")
                    try:
                        asyncio.get_event_loop().create_task(
                            influx._write(
                                influx.BUCKET_SYSTEM, "partial_tp",
                                {"symbol": symbol, "direction": trade.direction},
                                {"pnl": partial_pnl, "close_qty": close_qty, "filled_price": filled_px},
                            )
                        )
                    except Exception:
                        pass
                else:
                    logger.warning(f"  [ {symbol} ] PARTIAL TP failed: {res.message}")
        except Exception as e:
            logger.warning(f"  [ {symbol} ] partial-TP error: {e}")


class ExchangeProtectionManager:
    """Validates and restores missing SL/TP orders on the exchange."""

    @staticmethod
    def ensure_exchange_protection(
        db,
        symbol: str,
        broker,
    ) -> None:
        try:
            if os.getenv("ACTIVE_BROKER", "ctrader") != "binance_futures":
                return
            from backend.services.trading_mode import get_trading_mode, TradingMode
            if get_trading_mode() != TradingMode.LIVE:
                return

            trades = db.query(Trade).filter(Trade.symbol == symbol, Trade.status.in_(["open", "filled"])).all()
            for trade in trades:
                if not trade.stop_loss and not trade.take_profit:
                    continue
                res = broker.ensure_protective_orders(
                    trade.symbol,
                    trade.direction,
                    trade.stop_loss,
                    trade.take_profit,
                )
                if res.get("status") == "restored":
                    logger.warning(f"  [ {symbol} ] Exchange protection restored: {res.get('restored')}")
        except Exception as e:
            logger.warning(f"  [ {symbol} ] exchange protection check failed: {e}")


class PerformanceMetricsWriter:
    """Computes and writes rolling win rate and drawdown metrics to InfluxDB."""

    @staticmethod
    async def write_metrics(
        db,
        cycle_equity: float,
        open_count: int,
        cycle_count: int,
    ) -> None:
        try:
            from sqlalchemy import func as _func
            closed = db.query(Trade).filter(Trade.status == "closed").all()
            wins = sum(1 for t in closed if (t.pnl or 0) > 0)
            losses = sum(1 for t in closed if (t.pnl or 0) < 0)
            decided = wins + losses
            win_rate = (wins / decided * 100.0) if decided else 0.0
            realized_pnl = round(sum((t.pnl or 0.0) for t in closed), 4)

            # Drawdown vs peak equity seen in portfolio snapshots
            peak = db.query(_func.max(PortfolioSnapshot.total_value)).scalar() or cycle_equity
            drawdown_pct = ((cycle_equity - peak) / peak * 100.0) if peak and peak > 0 else 0.0

            await influx.write_performance(
                equity=cycle_equity,
                realized_pnl=realized_pnl,
                win_rate=round(win_rate, 2),
                wins=wins,
                losses=losses,
                total_trades=len(closed),
                drawdown_pct=round(drawdown_pct, 3),
                open_positions=open_count,
                cycle=cycle_count,
            )
        except Exception as e:
            logger.warning(f"Failed to write performance metrics: {e}")
