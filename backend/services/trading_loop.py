"""
Automated Trading Loop Service
Runs as a background asyncio task, scanning markets and generating signals/trades.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

from backend.database.connection import SessionLocal
from backend.database.models import TradingSignal, Trade, PortfolioSnapshot
from backend.services.ctrader_service import ctrader_broker
from backend.services.binance_futures_service import binance_futures_broker
from backend.services.influxdb_writer import influx
from backend.services.binance_market_data import binance_market_data
from backend.strategies.market_regime import MarketRegimeDetector
from backend.services.unified_trading import UnifiedTrading, UnifiedOrder, OrderSide, OrderType
from backend.services.position_manager import get_position_manager, ExitOpinion

load_dotenv()

# ── Broker selector ──────────────────────────────────────────────────────────
def get_active_broker():
    """Dynamically resolve the active broker based on environment."""
    broker_name = os.getenv("ACTIVE_BROKER", "ctrader")
    if broker_name == "binance_futures":
        return binance_futures_broker
    return ctrader_broker

import structlog
logger = structlog.get_logger(__name__)


class TradingLoopService:
    """Background trading loop that scans markets and generates paper trades."""

    def __init__(self):
        from backend.services.risk_config import get_risk_config
        self.risk_config = get_risk_config()
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._state = "stopped"  # stopped, running, error
        self._error: Optional[str] = None
        self._interval_minutes = 15
        env_syms = os.getenv('TRADING_SYMBOLS', '')
        self._symbols = [s.strip() for s in env_syms.split(',') if s.strip()] if env_syms else [
            'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT',
            'ADAUSDT', 'DOGEUSDT', 'AVAXUSDT', 'DOTUSDT', 'LINKUSDT',
            'POLUSDT', 'LTCUSDT', 'UNIUSDT', 'ATOMUSDT', 'NEARUSDT',
            'OPUSDT', 'ARBUSDT', 'APTUSDT', 'INJUSDT', 'SUIUSDT'
        ]
        self._strategy_name = "combined"
        self._last_cycle: Optional[str] = None
        self._next_cycle: Optional[str] = None
        self._cycle_count = 0
        # Market regime detector — one instance shared across all cycles/symbols
        self._regime_detector = MarketRegimeDetector()
        self._unified_trading = None  # Will be set to singleton in start()
        self._pyramid_layers = {}
        self._execution_lock = asyncio.Lock()
        # Cooldown tracker: symbol → datetime when SL/emergency exit fired
        # Prevents immediate re-entry after a losing close (30 min cooldown)
        self._sl_cooldown: dict = {}
        # P0 margin gate state (set each cycle from broker balance)
        self._entries_blocked = False
        self._entries_blocked_reason = ""
        self._cycle_available = 0.0
        self._cycle_margin_used = 0.0
        # Trailing-stop high/low-water marks, keyed by trade id → extreme price
        # reached since entry (max for longs, min for shorts). In-memory only;
        # reconstructed lazily from entry/current price on first sight.
        self._high_water: dict = {}
        self._last_digest_date = None

    @property
    def status(self) -> dict:
        # Fetch real balance from active broker
        broker = get_active_broker()
        balance_info = broker.get_balance() if hasattr(broker, 'get_balance') else {"balance": 0.0}
        return {
            "state": self._state,
            "running": self._running,
            "interval_minutes": self._interval_minutes,
            "symbols": self._symbols,
            "strategy": self._strategy_name,
            "last_cycle": self._last_cycle,
            "next_cycle": self._next_cycle,
            "cycle_count": self._cycle_count,
            "error": self._error,
            "cash": 0.0,  # Paper trading
            "equity": 0.0,  # Paper trading
            "margin_used": 0.0,  # Paper trading
        }

    async def start(
        self,
        interval_minutes: int = 15,
        symbols: list[str] | None = None,
        strategy: str = "combined",
    ):
        """Start the trading loop."""
        if self._running:
            return {"message": "Trading loop is already running"}

        self._interval_minutes = interval_minutes
        if symbols:
            self._symbols = symbols
        self._strategy_name = strategy
        self._running = True
        self._state = "running"
        self._error = None
        self._unified_trading = UnifiedTrading()  # Set singleton instance
        
        from backend.services.trading_mode import get_trading_mode
        mode = get_trading_mode()
        logger.info(f"TradingLoopService starting in mode={mode.value.upper()}")
        
        # Reconstruct pyramid layers and distinct open-position count from DB
        self._open_count = 0
        self._reconstruct_pyramid_layers()
        self._reconstruct_cooldowns()
        db = SessionLocal()
        try:
            open_trades = db.query(Trade).filter(
                Trade.status.in_(["open", "filled"])
            ).all()
            distinct = {(t.symbol, t.direction) for t in open_trades}
            self._open_count = len(distinct)
            logger.info(
                f"Open position state reconstructed: "
                f"{self._open_count} distinct positions across {len(open_trades)} trade rows"
            )
        except Exception as e:
            logger.warning(f"Failed to reconstruct open-position count: {e}")
        finally:
            db.close()

        self._task = asyncio.create_task(self._loop())
        logger.info(
            f"Trading loop started: interval={interval_minutes}m, "
            f"symbols={self._symbols}, strategy={strategy}"
        )
        return {"message": "Trading loop started", "status": self.status}

    def _reconstruct_cooldowns(self):
        """Repopulate SL cooldowns from recently-closed trades so a restart
        doesn't immediately re-enter a symbol that just stopped out."""
        db = SessionLocal()
        try:
            window_min = max(
                getattr(self.risk_config, "sl_cooldown_minutes", 30),
                getattr(self.risk_config, "opinion_close_cooldown_min", 30),
            )
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_min)
            recent = db.query(Trade).filter(
                Trade.status == "closed", Trade.closed_at.isnot(None)
            ).order_by(Trade.closed_at.desc()).limit(50).all()
            restored = 0
            for t in recent:
                closed = t.closed_at
                if closed and closed.tzinfo is None:
                    closed = closed.replace(tzinfo=timezone.utc)
                if closed and closed >= cutoff and t.symbol not in self._sl_cooldown:
                    self._sl_cooldown[t.symbol] = closed
                    restored += 1
            if restored:
                logger.info(f"Restored {restored} SL cooldown(s) from recent closed trades")
        except Exception as e:
            logger.warning(f"Failed to reconstruct cooldowns: {e}")
        finally:
            db.close()

    def _reconstruct_pyramid_layers(self):
        """Populate self._pyramid_layers from open trades in DB."""
        db = SessionLocal()
        try:
            open_trades = db.query(Trade).filter(Trade.status.in_(["open", "filled"])).all()
            self._pyramid_layers = {}
            for t in open_trades:
                self._pyramid_layers.setdefault(t.symbol, []).append(t.entry_price)
            if self._pyramid_layers:
                logger.info(f"Reconstructed pyramid layers for symbols: {list(self._pyramid_layers.keys())}")
        except Exception as e:
            logger.warning(f"Failed to reconstruct pyramid layers: {e}")
        finally:
            db.close()

    async def _sync_positions_with_broker(self, db):
        """Sync DB trades with actual broker positions.
        Uses BinanceFuturesService directly — UnifiedTrading.get_positions() returns empty.
        """
        try:
            from backend.services.binance_futures_service import BinanceFuturesService as _BFS_SYNC
            _bfs_sync = _BFS_SYNC()
            broker_raw = await asyncio.get_event_loop().run_in_executor(None, _bfs_sync.get_positions)
            # Only symbols with non-zero position amount
            broker_symbols = {
                bp['symbol'] for bp in broker_raw
                if float(bp.get('quantity') or bp.get('positionAmt') or 0) != 0
            }
            
            db_trades = db.query(Trade).filter(Trade.status.in_(["open", "filled"])).all()
            
            updated = 0
            exit_price_cache: dict = {}
            cancelled_orphans: set = set()
            for t in db_trades:
                if t.symbol not in broker_symbols:
                    # Position was closed externally (e.g. SL/TP hit on Binance)
                    logger.info(f"  [ {t.symbol} ] Position not found in broker, marking as closed in DB.")

                    # Record exit price + realized P&L (one mark/fill lookup per symbol)
                    if t.symbol not in exit_price_cache:
                        exit_price_cache[t.symbol] = await asyncio.get_event_loop().run_in_executor(
                            None, _bfs_sync.get_exit_price, t.symbol
                        )
                    exit_px = exit_price_cache[t.symbol]

                    t.status = "closed"
                    t.closed_at = datetime.now(timezone.utc)
                    if exit_px and t.entry_price and t.quantity:
                        t.exit_price = exit_px
                        if str(t.direction).upper() == "BUY":
                            t.pnl = round((exit_px - t.entry_price) * t.quantity, 4)
                        else:
                            t.pnl = round((t.entry_price - exit_px) * t.quantity, 4)
                    t.notes = (t.notes or "") + " | Closed externally (sync)"

                    # Cancel any orphaned reduce-only orders (SL/TP/trailing) left
                    # on the exchange after a native stop fired, once per symbol.
                    if t.symbol not in cancelled_orphans:
                        try:
                            # Cancels regular AND conditional/algo SL/TP/trailing orders
                            _bfs_sync.cancel_all_orders(t.symbol)
                            cancelled_orphans.add(t.symbol)
                        except Exception as _ce:
                            logger.warning(f"  [ {t.symbol} ] orphan order cleanup failed: {_ce}")
                    
                    # Also cleanup pyramid layers
                    if t.symbol in self._pyramid_layers:
                        del self._pyramid_layers[t.symbol]
                    # Set cooldown so we don't immediately re-enter after SL hit
                    self._sl_cooldown[t.symbol] = datetime.now(timezone.utc)
                    updated += 1
            
            if updated > 0:
                db.commit()
                logger.info(f"Synced {updated} closed position(s) from broker.")
                
        except Exception as e:
            db.rollback()
            logger.warning(f"Failed to sync positions with broker: {e}")

    async def stop(self):
        """Stop the trading loop."""
        self._running = False
        self._state = "stopped"
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._next_cycle = None
        logger.info("Trading loop stopped")
        return {"message": "Trading loop stopped", "status": self.status}

    async def _loop(self):
        """Main loop: run cycles at the configured interval."""
        try:
            while self._running:
                try:
                    await self._run_cycle()
                except Exception as e:
                    logger.error(f"Trading cycle error: {e}")
                    self._error = str(e)
                    self._state = "error"

                if not self._running:
                    break

                next_time = datetime.now(timezone.utc).timestamp() + (
                    self._interval_minutes * 60
                )
                self._next_cycle = datetime.fromtimestamp(
                    next_time, tz=timezone.utc
                ).isoformat()

                # Sleep in small increments so we can cancel quickly
                for _ in range(self._interval_minutes * 60):
                    if not self._running:
                        break
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False
            self._state = "stopped"

    async def _run_cycle(self):
        """Execute one trading cycle across all symbols in parallel."""
        self._cycle_count += 1
        
        # UTC rollover daily digest trigger
        current_date_utc = datetime.now(timezone.utc).date()
        if self._last_digest_date is None:
            self._last_digest_date = current_date_utc
        elif current_date_utc > self._last_digest_date:
            self._last_digest_date = current_date_utc
            try:
                from backend.services.daily_digest import run_daily_digest
                asyncio.create_task(run_daily_digest())
                logger.info("Daily performance digest task queued successfully.")
            except Exception as e:
                logger.error(f"Failed to trigger automated daily digest: {e}")

        # Bind the cycle number to all structured logs emitted in this cycle
        structlog.contextvars.bind_contextvars(cycle=self._cycle_count)
        
        self._last_cycle = datetime.now(timezone.utc).isoformat()
        _cycle_start = datetime.now(timezone.utc).timestamp()
        self._state = "running"

        # ── RISK GUARD GATEKEEPER ──────────────────────────────────────────
        from backend.services.risk_guard import enforce_risk_limits, RiskBreach
        db_risk = SessionLocal()
        try:
            open_trades = db_risk.query(Trade).filter(Trade.status.in_(["open", "filled"])).all()
            latest_snapshot = (
                db_risk.query(PortfolioSnapshot)
                .filter(PortfolioSnapshot.total_value > 0)
                .order_by(PortfolioSnapshot.timestamp.desc())
                .first()
            )
            enforce_risk_limits(db_risk, self.risk_config, open_trades, latest_snapshot)
        except RiskBreach as rb:
            logger.critical(
                f"[RISK BREACH] {rb.reason}. LOOP STOPPED. "
                f"\u26a0\ufe0f Manual restart required after resolving the breach."
            )
            self._state = "error"
            self._running = False
            self._error = f"RISK_BREACH: {rb.reason}"
            return {"signals": 0, "trades": 0, "risk_breach": True}
        except Exception as e:
            # FAIL CLOSED: a transient DB error / bad snapshot in the guard must
            # NOT let an unguarded trading cycle proceed. Previously this branch
            # only logged and fell through to order placement — meaning any
            # hiccup in the risk check silently disabled all risk enforcement
            # for that cycle. Skip the entire cycle instead.
            logger.error(f"[RISK GUARD] Failed to check limits — skipping cycle (fail-closed): {e}")
            db_risk.rollback()
            self._state = "idle"
            return {"signals": 0, "trades": 0, "risk_guard_error": True}
        finally:
            db_risk.close()
        # ─────────────────────────────────────────────────────────────────────

        # ── C8: ACCOUNT-LEVEL KILL SWITCH ──────────────────────────────────
        try:
            _acct = binance_futures_broker._get_client().futures_account()
            _kill_floor = self.risk_config.kill_floor_usdt
            # Only act on a VALID reading. A missing field means malformed/partial
            # response — treat as "unknown" and skip rather than reading 0 and
            # either false-triggering or (worse) silently bypassing.
            if _acct is None or 'totalMarginBalance' not in _acct:
                logger.warning(
                    "[KILL SWITCH] Skipped: futures_account() returned no totalMarginBalance field"
                )
            else:
                _equity = float(_acct.get('totalMarginBalance') or 0.0)
                # Trigger on ANY valid equity at/below floor — including $0.00.
                # A drained/empty account ($0) is exactly when the loop MUST stop.
                if _equity <= _kill_floor:
                    logger.critical(
                        f"[KILL SWITCH] Equity ${_equity:.2f} <= floor ${_kill_floor:.2f} — LOOP STOPPED. "
                        f"\u26a0\ufe0f Manual restart required after depositing funds above ${_kill_floor:.0f}."
                    )
                    # Write kill event to InfluxDB for Grafana alerting
                    try:
                        await influx._write(
                            influx.BUCKET_SYSTEM, 'system_event',
                            {'event': 'kill_switch', 'reason': 'equity_below_floor'},
                            {'equity': float(_equity), 'kill_floor': float(_kill_floor)}
                        )
                    except Exception:
                        pass  # best-effort
                    self._state = "stopped"
                    self._running = False
                    self._error = "KILL_SWITCH_TRIGGERED"
                    return {"signals": 0, "trades": 0, "kill_switch": True}
        except Exception as _ks_e:
            logger.warning(f"[KILL SWITCH] Check skipped: {_ks_e}")
        # ─────────────────────────────────────────────────────────────────────

        # ── STEP 0: Emergency Position Manager BEFORE broker sync ──────────
        # We check DB open trades against LIVE Binance prices BEFORE the sync
        # so positions that are still open on Binance get the drawdown check.
        _exits_triggered = 0
        try:
            pm = get_position_manager()
            db_pre = SessionLocal()
            try:
                from sqlalchemy import func as _func
                pre_open = db_pre.query(Trade).filter(Trade.status.in_(["open", "filled"])).all()
                if pre_open:
                    logger.info(f"  [POSITION MGR] Pre-sync review: {len(pre_open)} open positions")
                    # Use BinanceFuturesService directly — UnifiedTrading returns 0 positions
                    from backend.services.binance_futures_service import BinanceFuturesService as _BFS
                    live_prices = {}
                    try:
                        _bfs = _BFS()
                        for bp in _bfs.get_positions():
                            live_prices[bp['symbol']] = float(bp.get('mark_price') or bp.get('entry_price') or 0)
                    except Exception as _bfs_err:
                        logger.error(f"  [STEP 0] Price fetch error: {_bfs_err}")

                    ut_pre = UnifiedTrading()  # FIX: was undefined - NameError on emergency exit
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
                                    db_pre.add(trade)
                                    if trade.symbol in self._pyramid_layers:
                                        del self._pyramid_layers[trade.symbol]
                                    # Mark cooldown so we don't immediately re-enter
                                    self._sl_cooldown[trade.symbol] = datetime.now(timezone.utc)
                                    _exits_triggered += 1
                                    logger.warning(f"  [EMERGENCY EXIT] {trade.symbol} CLOSED. PnL={pnl_pct:.1f}%")
                                else:
                                    # already_flat = success (exchange SL already fired)
                                    if hasattr(res, 'message') and ('-2022' in str(res.message) or 'already' in str(res.message).lower()):
                                        logger.info(f"  [EMERGENCY EXIT] {trade.symbol} already flat on exchange — marking DB closed")
                                        trade.status = 'closed'
                                        trade.closed_at = datetime.now(timezone.utc)
                                        trade.notes = (trade.notes or '') + ' | EMERGENCY-EXIT: already flat on exchange'
                                        db_pre.add(trade)
                                        if trade.symbol in self._pyramid_layers:
                                            del self._pyramid_layers[trade.symbol]
                                        self._sl_cooldown[trade.symbol] = datetime.now(timezone.utc)
                                        _exits_triggered += 1
                                    else:
                                        logger.error(f"  [EMERGENCY EXIT] {trade.symbol} FAILED: {res.message}")
                        except Exception as e:
                            logger.error(f"  [EMERGENCY EXIT] {trade.symbol} error: {e}")
                    if _exits_triggered:
                        db_pre.commit()
            except Exception as e:
                logger.error(f"Pre-sync PM error: {e}")
                db_pre.rollback()
            finally:
                db_pre.close()
        except Exception as e:
            logger.error(f"Pre-sync PM outer error: {e}")
        self._error = None

        logger.info(
            f"=== Trading Cycle #{self._cycle_count} at {self._last_cycle} (Parallel) ==="
        )

        min_confidence = self.risk_config.min_signal_strength
        ai_threshold = self.risk_config.ai_analysis_threshold
        max_positions = self.risk_config.max_positions
        # Snapshot account equity once per cycle for risk-based position sizing.
        try:
            _bal = get_active_broker().get_balance()
            self._cycle_equity = float(_bal.get("equity", _bal.get("balance", 0.0)) or 0.0)
            self._cycle_available = float(_bal.get("available", 0.0) or 0.0)
            self._cycle_margin_used = float(_bal.get("margin_used", 0.0) or 0.0)
        except Exception as _be:
            logger.warning(f"Could not fetch equity for sizing: {_be}")
            self._cycle_equity = 0.0
            self._cycle_available = 0.0
            self._cycle_margin_used = 0.0

        # ── P0 margin gates: decide ONCE per cycle whether new entries are
        # affordable, instead of letting every symbol fail at the broker.
        self._entries_blocked = False
        self._entries_blocked_reason = ""
        _min_avail = self.risk_config.min_available_margin_usdt
        _wallet_cap = self.risk_config.pyramid_max_wallet_pct
        if self._cycle_available < _min_avail:
            self._entries_blocked = True
            self._entries_blocked_reason = (
                f"available margin ${self._cycle_available:.2f} < floor ${_min_avail:.2f}"
            )
        elif (self._cycle_equity > 0
              and self._cycle_margin_used >= self._cycle_equity * _wallet_cap):
            self._entries_blocked = True
            self._entries_blocked_reason = (
                f"margin used ${self._cycle_margin_used:.2f} >= "
                f"{_wallet_cap:.0%} of equity ${self._cycle_equity:.2f}"
            )
        if self._entries_blocked:
            logger.warning(
                f"[MARGIN GATE] New entries + pyramid adds BLOCKED this cycle: "
                f"{self._entries_blocked_reason}. Exits/SL/TP still active."
            )
        # Pyramid DCA config
        self._pyramid_mode = self.risk_config.pyramid_mode
        self._pyramid_max_layers = self.risk_config.pyramid_max_layers
        # CRITICAL: directional exposure cap (correlation risk)
        self._max_directional_exposure_usdt = self.risk_config.max_directional_exposure_usdt
        # Pyramid layer separation
        self._pyramid_atr_multiplier = self.risk_config.pyramid_atr_multiplier
        self._pyramid_min_conf_increase = self.risk_config.pyramid_min_conf_increase
        self._pyramid_usdt_per_layer = self.risk_config.pyramid_usdt_per_layer
        self._pyramid_max_wallet_pct = self.risk_config.pyramid_max_wallet_pct
        # Tighter pyramid: only add if price improves by at least 0.5% vs last layer
        self._pyramid_min_improvement = self.risk_config.pyramid_min_improvement
        self._sl_atr_mult = self.risk_config.sl_atr_mult
        self._tp_atr_mult = self.risk_config.tp_atr_mult
        
        db = SessionLocal()
        try:
            # Sync DB trades with actual broker positions
            await self._sync_positions_with_broker(db)
            
            # Count distinct positions (symbol/direction) — layers on the same
            # symbol/direction are one position, not independent positions.
            from sqlalchemy import func
            distinct_pos = (
                db.query(Trade.symbol, Trade.direction)
                .filter(Trade.status.in_(["open", "filled"]))
                .distinct()
                .subquery()
            )
            self._open_count = db.query(func.count()).select_from(distinct_pos).scalar() or 0
        finally:
            db.close()

        # ── STEP 1A: Position Manager — Review ALL open positions for exits ──
        _exits_triggered = 0
        try:
            pm = get_position_manager()
            db_check = SessionLocal()
            try:
                open_trades = db_check.query(Trade).filter(Trade.status.in_(["open", "filled"])).all()
                logger.info(f"  [POSITION MGR] Reviewing {len(open_trades)} open positions for exits...")
                for trade in open_trades:
                    try:
                        trade_bars = await self._fetch_bars(trade.symbol)
                        if not trade_bars or len(trade_bars) < 10:
                            continue
                        curr_price = trade_bars[-1]["close"]
                        # Fetch funding rate for position exit review
                        try:
                            fr_data = await binance_market_data.get_funding_rate(trade.symbol)
                            current_funding_rate = float(fr_data.get('fundingRate', 0)) if fr_data else 0.0
                        except Exception:
                            current_funding_rate = 0.0

                        from backend.services.opinion_layer import analyze_symbol as analyze_opinion
                        exit_opinion = await pm.analyze_open_position(
                            symbol=trade.symbol,
                            trade={
                                "entry_price": trade.entry_price,
                                "direction": trade.direction,
                                "quantity": trade.quantity,
                                "opened_at": trade.timestamp.isoformat() if trade.timestamp else None,
                                "stop_loss": trade.stop_loss,
                                "take_profit": trade.take_profit,
                            },
                            bars=trade_bars,
                            current_price=curr_price,
                            opinion_layer_fn=analyze_opinion,
                            current_funding_rate=current_funding_rate,
                        )
                        if exit_opinion.exit:
                            logger.warning(
                                f"  [POSITION MGR] EXIT {trade.symbol}: {exit_opinion.reasoning}"
                            )
                            # Close position immediately
                            ut = UnifiedTrading()
                            close_side = OrderSide.SELL if trade.direction == "BUY" else OrderSide.BUY
                            res = ut.place_order(UnifiedOrder(
                                symbol=trade.symbol,
                                side=close_side,
                                order_type=OrderType.MARKET,
                                quantity=trade.quantity,
                                reduce_only=True,
                            ))
                            if res.success:
                                trade.exit_price = res.filled_price or curr_price
                                if trade.direction == "BUY":
                                    trade.pnl = (curr_price - trade.entry_price) * trade.quantity
                                else:
                                    trade.pnl = (trade.entry_price - curr_price) * trade.quantity
                                trade.status = "closed"
                                trade.closed_at = datetime.now(timezone.utc)
                                trade.notes = (trade.notes or "") + f" | AI EXIT: {exit_opinion.reasoning[:120]}"
                                db_check.add(trade)
                                if self._pyramid_mode and trade.symbol in self._pyramid_layers:
                                    del self._pyramid_layers[trade.symbol]
                                _exits_triggered += 1
                                logger.info(f"  -> {trade.symbol} CLOSED via Position Manager. PnL={trade.pnl:+.2f}")
                            else:
                                # already_flat = success (exchange SL already fired)
                                if hasattr(res, 'message') and ('-2022' in str(res.message) or 'already' in str(res.message).lower()):
                                    logger.info(f"  -> {trade.symbol} already flat on exchange — marking DB closed")
                                    trade.status = 'closed'
                                    trade.closed_at = datetime.now(timezone.utc)
                                    trade.notes = (trade.notes or '') + ' | AI-EXIT: already flat on exchange'
                                    db_check.add(trade)
                                    if self._pyramid_mode and trade.symbol in self._pyramid_layers:
                                        del self._pyramid_layers[trade.symbol]
                                    _exits_triggered += 1
                                else:
                                    logger.error(f"  -> FAILED to close {trade.symbol}: {res.message}")
                                trade.notes = (trade.notes or "") + f" | AI EXIT FAILED: {res.message}"
                                db_check.add(trade)
                    except Exception as e:
                        logger.error(f"  Position review error for {trade.symbol}: {e}")
                if _exits_triggered > 0:
                    db_check.commit()
                    logger.info(f"  Position Manager: {_exits_triggered} positions closed this cycle.")
                    # Refresh open count after exits
                    distinct_pos = (
                        db_check.query(Trade.symbol, Trade.direction)
                        .filter(Trade.status.in_(["open", "filled"]))
                        .distinct()
                        .subquery()
                    )
                    self._open_count = db_check.query(func.count()).select_from(distinct_pos).scalar() or 0
            except Exception as e:
                logger.error(f"Position Manager cycle error: {e}")
                db_check.rollback()
            finally:
                db_check.close()
        except Exception as e:
            logger.error(f"Position Manager init error: {e}")

        # ── MARGIN-AWARE GATEKEEPER ───────────────────────────────────────
        # Skip symbol evaluation if available balance is below minimum layer cost ($5)
        # to avoid API cost, LLM/Kronos latency, and log/DB spam.
        margin_sufficient = True
        try:
            _bal = get_active_broker().get_balance()
            self._cycle_available = float(_bal.get("available", 0.0) or 0.0)
            self._cycle_equity = float(_bal.get("equity", _bal.get("balance", 0.0)) or 0.0)
        except Exception as _be:
            logger.warning(f"Could not re-fetch balance for margin check: {_be}")
            self._cycle_available = getattr(self, "_cycle_available", 0.0)

        if self._cycle_available < 5.0:
            logger.warning(f"[MARGIN GATE] Insufficient available balance (${self._cycle_available:.2f} < $5.00). Skipping symbol evaluation.")
            margin_sufficient = False
            
            # Write a single ACCOUNT-level signal to DB
            db_sig = SessionLocal()
            try:
                db_sig.add(TradingSignal(
                    symbol="ACCOUNT",
                    strategy=self._strategy_name,
                    direction="HOLD",
                    confidence=0.0,
                    status="evaluated",
                    reasoning=f"Insufficient margin: available={self._cycle_available:.4f} < 5.0"
                ))
                db_sig.commit()
            except Exception as _se:
                logger.warning(f"Failed to write margin gate signal: {_se}")
                db_sig.rollback()
            finally:
                db_sig.close()

        results = []
        if margin_sufficient:
            # ── SYMBOL-QUALITY GATE: drop blacklisted + illiquid symbols ──
            # Kills the realized-PnL loss tail caused by low-liquidity / new-listing
            # symbols (SIREN, AIGENSYN, MAGMA, SPK ...) before any analysis runs.
            tradeable = await self._filter_tradeable_symbols(self._symbols)

            # Run all symbols in parallel
            tasks = [
                self._process_symbol(
                    symbol, min_confidence, ai_threshold, max_positions
                )
                for symbol in tradeable
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        _signals_generated = 0
        _trades_executed = 0
        _errors = 0

        for res in results:
            if isinstance(res, Exception):
                logger.error(f"Symbol task error: {res}")
                _errors += 1
            elif isinstance(res, dict):
                _signals_generated += res.get("signals", 0)
                _trades_executed += res.get("trades", 0)

        # Save portfolio snapshot and system health
        db = SessionLocal()
        try:
            self._save_portfolio_snapshot(db)
            
            _cycle_ms = (datetime.now(timezone.utc).timestamp() - _cycle_start) * 1000
            await influx.write_system_health(
                cycle=self._cycle_count,
                symbols_scanned=len(self._symbols),
                signals_generated=_signals_generated,
                trades_executed=_trades_executed,
                cycle_duration_ms=_cycle_ms,
                errors=_errors,
                state=self._state,
            )

            # Rolling performance metrics (win rate / drawdown / equity)
            try:
                await self._write_performance_metrics(db)
            except Exception as _pe:
                logger.warning(f"performance metrics write failed: {_pe}")
            
            _active_broker = get_active_broker()
            bal = _active_broker.get_balance() if hasattr(_active_broker, 'get_balance') else {}
            await influx.write_portfolio_snapshot(
                cash=bal.get("available", 0.0),
                equity=bal.get("equity", 0.0),
                margin_used=bal.get("margin_used", 0.0),
                open_positions=self._open_count,
                cycle=self._cycle_count,
            )
            
            if os.getenv("ACTIVE_BROKER") == 'binance_futures':
                try:
                    await influx.write_binance_wallet(
                        balance=bal.get('balance', 0.0),
                        available=bal.get('available', 0.0),
                        equity=bal.get('equity', 0.0),
                        unrealized_pnl=bal.get('unrealized_pnl', 0.0),
                        margin_used=bal.get('margin_used', 0.0),
                    )
                    positions = binance_futures_broker.get_positions()
                    for pos in positions:
                        await influx.write_binance_position(
                            symbol=pos['symbol'],
                            side=pos['side'],
                            quantity=pos['quantity'],
                            entry_price=pos['entry_price'],
                            unrealized_pnl=pos['unrealized_pnl'],
                            leverage=pos.get('leverage', 10),
                            mark_price=pos.get('mark_price', 0.0),
                            liquidation_price=pos.get('liquidation_price', 0.0),
                        )
                except Exception as _bf_e:
                    logger.warning(f'Binance InfluxDB write error: {_bf_e}')
        finally:
            db.close()

    async def _process_symbol(self, symbol: str, min_confidence: float, ai_threshold: float, max_positions: int):
        """Analyze a single symbol and execute a trade if conditions are met."""
        import structlog
        structlog.contextvars.bind_contextvars(symbol=symbol)
        from backend.services.decision_engine import DecisionEngine
        from backend.database.connection import SessionLocal
        from backend.database.models import Trade, TradingSignal
        from datetime import datetime, timezone
        from backend.services.unified_trading import UnifiedTrading, UnifiedOrder, OrderSide, OrderType
        import asyncio

        db = SessionLocal()
        _signals = 0
        _trades = 0

        try:
            # 1. Fetch existing position (DB + live exchange — hedge mode can drift)
            existing = db.query(Trade).filter(
                Trade.symbol == symbol,
                Trade.status.in_(["open", "filled"])
            ).first()

            exchange_legs = []
            try:
                from backend.services.binance_futures_service import BinanceFuturesService
                exchange_legs = [
                    p for p in BinanceFuturesService().get_positions()
                    if p.get("symbol") == symbol and float(p.get("quantity") or 0) > 0
                ]
            except Exception as ex_e:
                logger.warning(f"  [ {symbol} ] Exchange position check failed: {ex_e}")

            if exchange_legs and not existing:
                sides = [p.get("side") for p in exchange_legs]
                logger.warning(
                    f"  [ {symbol} ] Exchange has open leg(s) {sides} but no DB trade — "
                    "blocking new entry until reconciled"
                )
                bars = await self._fetch_bars(symbol)
                if bars:
                    self._check_sl_tp(db, symbol, bars)
                    db.commit()
                return {"signals": 0, "trades": 0}

            # Block opposite-leg hedges: exchange already has a position on this symbol.
            if exchange_legs and existing and len(exchange_legs) >= 1:
                live_sides = {p.get("side") for p in exchange_legs}
                if len(live_sides) > 1:
                    logger.warning(
                        f"  [ {symbol} ] Hedge legs detected on exchange {sorted(live_sides)} — "
                        "skipping new entries"
                    )
                    bars = await self._fetch_bars(symbol)
                    if bars:
                        self._check_sl_tp(db, symbol, bars)
                        db.commit()
                    return {"signals": 0, "trades": 0}

            # 2. Fetch bars
            bars = await self._fetch_bars(symbol)
            if not bars or len(bars) < 50:
                return {"signals": 0, "trades": 0}

            if not self._is_market_open(symbol):
                return {"signals": 0, "trades": 0}

            # 3. Check cooldown
            cooldown_active = False
            if symbol in self._sl_cooldown:
                elapsed = (datetime.now(timezone.utc) - self._sl_cooldown[symbol]).total_seconds() / 60
                if elapsed < getattr(self.risk_config, "opinion_close_cooldown_min", 30):
                    cooldown_active = True
                else:
                    del self._sl_cooldown[symbol]

            # 3b. Margin gate — when the cycle pre-check found no affordable
            # margin, skip the whole entry pipeline (strategy + Kronos + LLM)
            # for this symbol. Exits and SL/TP management still run below.
            if getattr(self, "_entries_blocked", False):
                self._check_sl_tp(db, symbol, bars)
                db.commit()
                return {"signals": 0, "trades": 0}

            # Fetch funding rate before evaluation to allow gating
            try:
                from backend.services.binance_market_data import get_binance_market_data
                bmd = get_binance_market_data()
                fr_data = await bmd.get_funding_rate(symbol)
                current_funding_rate = float(fr_data.get('fundingRate', 0)) if fr_data else 0.0
            except Exception:
                current_funding_rate = 0.0

            # 4. Evaluate using Decision Engine
            decision_engine = DecisionEngine(self.risk_config)
            decision_engine.account_equity = getattr(self, "_cycle_equity", 0.0)
            decision = await decision_engine.evaluate_symbol(
                symbol=symbol,
                bars=bars,
                existing_position=existing,
                open_count=self._open_count,
                pyramid_layers=self._pyramid_layers.get(symbol, []),
                cooldown_active=cooldown_active,
                current_funding_rate=current_funding_rate
            )

            ev = getattr(decision_engine, "last_evaluation", None) or {}
            signal_status = "evaluated"
            signal_reason = ev.get("reason", "")
            order_result = None
            # Keep decision metadata for signal persist even if the order fails.
            decision_snapshot = decision

            if decision:
                _signals = 1
                signal_reason = "pyramid entry" if decision.is_pyramid else "entry decision"
                # Write signal to InfluxDB for Grafana dashboards
                try:
                    await influx.write_signal(
                        symbol=symbol,
                        direction=decision.action,
                        confidence=getattr(decision, "confidence", 0.0),
                        entry_price=decision.entry_price,
                        stop_loss=decision.stop_loss,
                        take_profit=decision.take_profit,
                        strategy=self._strategy_name,
                        ai_used=True,
                    )
                except Exception as _ie:
                    logger.warning(f"  [ {symbol} ] InfluxDB write_signal failed: {_ie}")

                # Check directional exposure + correlation limits (if not pyramid)
                _new_notional = decision.quantity * decision.entry_price
                if not decision.is_pyramid:
                    all_open = db.query(Trade).filter(Trade.status.in_(["open", "filled"])).all()
                    _long_notional = sum(t.quantity * (t.entry_price or 0.0) for t in all_open if t.direction == "BUY")
                    _short_notional = sum(t.quantity * (t.entry_price or 0.0) for t in all_open if t.direction == "SELL")
                    # Correlation cap: distinct symbols already open in this direction
                    _same_dir_syms = {t.symbol for t in all_open if t.direction == decision.action}
                    _dir_cap = self.risk_config.max_same_direction_positions

                    if len(_same_dir_syms) >= _dir_cap:
                        logger.warning(
                            f"  [ {symbol} ] {decision.action} blocked: already "
                            f"{len(_same_dir_syms)} {decision.action} positions (cap {_dir_cap}) "
                            f"— correlated-exposure guard"
                        )
                        signal_status = "rejected"
                        signal_reason = (
                            f"{signal_reason} | same-direction cap "
                            f"({len(_same_dir_syms)}/{_dir_cap} {decision.action})"
                        )
                        decision = None
                    elif decision.action == "BUY" and (_long_notional + _new_notional > self.risk_config.max_directional_exposure_usdt):
                        logger.warning(f"  [ {symbol} ] BUY blocked: LONG exposure cap reached")
                        signal_status = "rejected"
                        signal_reason = f"{signal_reason} | LONG exposure cap reached"
                        decision = None
                    elif decision.action == "BUY" and getattr(self, "_cycle_equity", 0.0) > 0 and (_long_notional + _new_notional > self._cycle_equity * 0.8):
                        logger.warning(f"  [ {symbol} ] BUY blocked: 80% Portfolio LONG Correlation limit reached")
                        decision = None
                    elif decision.action == "SELL" and (_short_notional + _new_notional > self.risk_config.max_directional_exposure_usdt):
                        logger.warning(f"  [ {symbol} ] SELL blocked: SHORT exposure cap reached")
                        signal_status = "rejected"
                        signal_reason = f"{signal_reason} | SHORT exposure cap reached"
                        decision = None
                    else:
                        signal_status = "approved"

            if decision:
                # 5. Execute Order — serialized across the parallel symbol tasks
                #    so concurrent BUYs can't both pass the max-positions check
                #    and overshoot the cap (race on shared self._open_count).
                async with self._execution_lock:
                    # Re-check the position cap inside the lock with the freshest count
                    if not existing and self._open_count >= self.risk_config.max_positions:
                        logger.warning(
                            f"  [ {symbol} ] entry skipped: max positions "
                            f"({self.risk_config.max_positions}) reached at execution time"
                        )
                        signal_status = "rejected"
                        signal_reason = (
                            f"{signal_reason} | max positions "
                            f"({self.risk_config.max_positions}) at execution time"
                        )
                        decision = None

                    if decision:
                        ut = UnifiedTrading()
                        order_side = OrderSide.BUY if decision.action == "BUY" else OrderSide.SELL

                        logger.info(
                            f"  [ {symbol} ] Attempting {decision.action} order: "
                            f"qty={decision.quantity:.6f} @ {decision.entry_price} | "
                            f"SL={decision.stop_loss} TP={decision.take_profit}"
                        )

                        order = UnifiedOrder(
                            symbol=symbol, side=order_side, quantity=decision.quantity,
                            price=decision.entry_price,
                            stop_loss=decision.stop_loss, take_profit=decision.take_profit,
                            is_pyramid=decision.is_pyramid,
                        )

                        order_result = await asyncio.get_event_loop().run_in_executor(
                            None, lambda: ut.place_order(order)
                        )
                        if order_result.success and not existing:
                            self._open_count += 1
                if decision and order_result and order_result.success:
                    _trades = 1
                    filled_px = float(order_result.filled_price or decision.entry_price)
                    filled_qty = float(order_result.filled_qty or decision.quantity)
                    signal_status = "executed"
                    signal_reason = f"{signal_reason} | filled {order_result.order_id}"
                    logger.info(f"  [ {symbol} ] SUCCESS: {order_result.order_id} filled @ {filled_px}")

                    if decision.is_pyramid:
                        self._pyramid_layers.setdefault(symbol, []).append(filled_px)

                    trade = Trade(
                        symbol=symbol, direction=decision.action, quantity=filled_qty,
                        entry_price=filled_px, status="open",
                        strategy=self._strategy_name,
                        binance_order_id=order_result.order_id,
                        stop_loss=decision.stop_loss, take_profit=decision.take_profit,
                        notes=f"pyramid_layer_{len(self._pyramid_layers.get(symbol, []))}" if decision.is_pyramid else None
                    )
                    db.add(trade)
                    db.commit()

                    # Write to InfluxDB for Grafana dashboards
                    try:
                        await influx.write_trade(
                            symbol=symbol, direction=decision.action,
                            quantity=filled_qty, entry_price=filled_px,
                            status="open", strategy=self._strategy_name, pnl=0.0,
                        )
                    except Exception as _ie:
                        logger.warning(f"  [ {symbol} ] InfluxDB write_trade failed: {_ie}")
                elif decision and order_result:
                    signal_status = "skipped"
                    fail_msg = order_result.message or "unknown"
                    if "position_already_open" in fail_msg:
                        signal_reason = (
                            f"{signal_reason} | duplicate blocked: "
                            "position already open on Binance (no second entry)"
                        )
                    else:
                        signal_reason = f"{signal_reason} | order failed: {fail_msg}"
                    logger.warning(f"  [ {symbol} ] FAILED: {fail_msg}")

            # Persist evaluation AFTER order attempt so status reflects Binance reality.
            # Prefer the Decision object over last_evaluation — pyramid re-entries used
            # to leave last_evaluation stuck at "evaluating" (HOLD 0% in the UI).
            try:
                if ev or decision_snapshot:
                    dec = decision_snapshot
                    sig_direction = (
                        dec.action if dec else ev.get("direction", "HOLD")
                    )
                    sig_confidence = (
                        float(getattr(dec, "confidence", 0.0))
                        if dec else float(ev.get("confidence", 0.0))
                    )
                    db.add(TradingSignal(
                        symbol=symbol,
                        strategy=self._strategy_name,
                        direction=sig_direction,
                        confidence=sig_confidence,
                        entry_price=(
                            dec.entry_price if dec else ev.get("entry_price")
                        ),
                        stop_loss=(
                            dec.stop_loss if dec else ev.get("stop_loss")
                        ),
                        take_profit=(
                            dec.take_profit if dec else ev.get("take_profit")
                        ),
                        status=signal_status,
                        reasoning=signal_reason,
                    ))
                    db.commit()
                    try:
                        await influx.write_agent_state(
                            agent="decision_engine",
                            symbol=symbol,
                            direction=ev.get("direction", "HOLD"),
                            confidence=float(ev.get("confidence", 0.0)),
                            reasoning=signal_reason,
                        )
                    except Exception:
                        pass
            except Exception as _se:
                logger.warning(f"  [ {symbol} ] signal persist failed: {_se}")
                db.rollback()

            # SL/TP Check
            self._check_sl_tp(db, symbol, bars)
            db.commit()

            return {"signals": _signals, "trades": _trades}

        except Exception as e:
            logger.error(f"Error processing {symbol}: {e}")
            db.rollback()
            return {"signals": 0, "trades": 0}
        finally:
            db.close()
            
    async def _write_performance_metrics(self, db):
        """Compute and emit rolling win-rate / drawdown / equity to InfluxDB."""
        from sqlalchemy import func as _func
        closed = db.query(Trade).filter(Trade.status == "closed").all()
        wins = sum(1 for t in closed if (t.pnl or 0) > 0)
        losses = sum(1 for t in closed if (t.pnl or 0) < 0)
        decided = wins + losses
        win_rate = (wins / decided * 100.0) if decided else 0.0
        realized_pnl = round(sum((t.pnl or 0.0) for t in closed), 4)

        equity = float(getattr(self, "_cycle_equity", 0.0) or 0.0)
        # Drawdown vs peak equity seen in portfolio snapshots
        peak = db.query(_func.max(PortfolioSnapshot.total_value)).scalar() or equity
        drawdown_pct = ((equity - peak) / peak * 100.0) if peak and peak > 0 else 0.0

        await influx.write_performance(
            equity=equity,
            realized_pnl=realized_pnl,
            win_rate=round(win_rate, 2),
            wins=wins,
            losses=losses,
            total_trades=len(closed),
            drawdown_pct=round(drawdown_pct, 3),
            open_positions=self._open_count,
            cycle=self._cycle_count,
        )

    def _save_portfolio_snapshot(self, db):
        """Save portfolio snapshot with real broker balance."""
        try:
            # Get open trades from DB
            open_trades = db.query(Trade).filter(Trade.status.in_(["open", "filled"])).all()
            
            # Get balance - use paper portfolio if in paper mode, otherwise live broker
            paper_mode = os.getenv("PAPER_TRADING", "false").lower() == "true"
            
            if paper_mode:
                # Get balance from paper portfolio
                ut = UnifiedTrading()
                paper_pf = ut.get_paper_portfolio()
                if paper_pf:
                    real_cash = paper_pf.get("cash", 0.0)
                    real_equity = real_cash  # Simplified for paper trading
                    logger.info(f"  Paper portfolio balance: ${real_cash:,.2f}")
                else:
                    real_cash = 0.0
                    real_equity = 0.0
                    logger.warning("  No paper portfolio found, balance = $0")
            else:
                # Get real balance from active broker
                broker = get_active_broker()
                balance_info = broker.get_balance() if hasattr(broker, 'get_balance') else {"balance": 0.0}
                real_cash = balance_info.get("balance", 0.0)
                real_equity = balance_info.get("equity", 0.0)
            
            # Compute realized P&L from all closed trades
            from sqlalchemy import func as _func
            realized_pnl = db.query(_func.coalesce(_func.sum(Trade.pnl), 0.0)).filter(
                Trade.status == "closed"
            ).scalar() or 0.0

            # Compute positions value from open trades
            positions_val = sum(
                (t.quantity or 0) * (t.entry_price or 0)
                for t in open_trades
            )

            # Save snapshot
            snapshot = PortfolioSnapshot(
                total_value=real_equity,
                cash=real_cash,
                positions_value=round(positions_val, 4),
                total_pnl=round(float(realized_pnl), 4),
                open_positions=len(open_trades),
                cycle_number=self._cycle_count,
            )
            db.add(snapshot)
            db.commit()
            
            logger.info(
                f"  Portfolio: cash=${real_cash:,.2f}, equity=${real_equity:,.2f}, open_pos={len(open_trades)}"
            )
        except Exception as e:
            logger.warning(f"Failed to save portfolio snapshot: {e}")
            db.rollback()

    def _apply_trailing_stop(self, db, symbol: str, bars: list[dict]):
        """Ratchet-only ATR trailing stop.

        Once a position has moved `trail_activation_atr` x ATR into profit, the
        stop trails the high-water mark by `trail_atr_mult` x ATR and only ever
        tightens (never loosens). Writes the new level into Trade.stop_loss so
        the existing _check_sl_tp close path enforces it. FAILS SAFE: any error
        leaves the existing stop untouched (never widens or removes a stop).
        """
        cfg = self.risk_config
        if not getattr(cfg, "trailing_stop_enabled", False):
            return
        # When the exchange-native trailing stop is active it trails the market
        # continuously; skip the per-cycle software ratchet to avoid two
        # competing trail mechanisms moving the same stop.
        if getattr(cfg, "native_trailing_enabled", False):
            return
        if not bars or len(bars) < 16:
            return
        try:
            current_price = bars[-1]["close"]

            # ATR over the same window the decision engine uses
            highs = [b["high"] for b in bars[-15:]]
            lows = [b["low"] for b in bars[-15:]]
            closes = [b["close"] for b in bars[-16:-1]]
            trs = []
            for h, l, c in zip(highs, lows, closes):
                trs.append(max(h - l, abs(h - c), abs(l - c)))
            atr = sum(trs) / len(trs) if trs else 0.0
            if atr <= 0:
                atr = current_price * 0.02

            activation_dist = cfg.trail_activation_atr * atr
            trail_dist = cfg.trail_atr_mult * atr

            trades = (
                db.query(Trade)
                .filter(Trade.symbol == symbol, Trade.status.in_(["open", "filled"]))
                .all()
            )
            for trade in trades:
                if not trade.entry_price:
                    continue

                if trade.direction == "BUY":
                    # Track the highest price seen since entry
                    hw = self._high_water.get(trade.id, max(trade.entry_price, current_price))
                    hw = max(hw, current_price)
                    self._high_water[trade.id] = hw

                    # Step-Trailing: Move to breakeven at 0.5x activation distance
                    if hw - trade.entry_price < activation_dist:
                        if hw - trade.entry_price >= (activation_dist * 0.5):
                            candidate = trade.entry_price  # Breakeven
                            old_stop = trade.stop_loss if trade.stop_loss is not None else float("-inf")
                            if candidate > old_stop:
                                trade.stop_loss = candidate
                                logger.info(f"  [ {symbol} ] STEP-TRAIL ↑ stop to breakeven (hw={hw:.6f} >= 0.5 activation)")
                                self._sync_exchange_stop(trade, candidate)
                        continue
                    
                    candidate = hw - trail_dist
                    # Never above current price; never loosen an existing stop
                    candidate = min(candidate, current_price)
                    old_stop = trade.stop_loss if trade.stop_loss is not None else float("-inf")
                    if candidate > old_stop:
                        trade.stop_loss = candidate
                        logger.info(
                            f"  [ {symbol} ] TRAIL ↑ stop {old_stop if old_stop != float('-inf') else 'None'} "
                            f"-> {candidate:.6f} (hw={hw:.6f}, atr={atr:.6f})"
                        )
                        self._sync_exchange_stop(trade, candidate)
                else:  # SHORT
                    lw = self._high_water.get(trade.id, min(trade.entry_price, current_price))
                    lw = min(lw, current_price)
                    self._high_water[trade.id] = lw

                    # Step-Trailing: Move to breakeven at 0.5x activation distance
                    if trade.entry_price - lw < activation_dist:
                        if trade.entry_price - lw >= (activation_dist * 0.5):
                            candidate = trade.entry_price  # Breakeven
                            old_stop = trade.stop_loss if trade.stop_loss is not None else float("inf")
                            if candidate < old_stop:
                                trade.stop_loss = candidate
                                logger.info(f"  [ {symbol} ] STEP-TRAIL ↓ stop to breakeven (lw={lw:.6f} <= 0.5 activation)")
                                self._sync_exchange_stop(trade, candidate)
                        continue
                    candidate = lw + trail_dist
                    candidate = max(candidate, current_price)
                    old_stop = trade.stop_loss if trade.stop_loss is not None else float("inf")
                    if candidate < old_stop:
                        trade.stop_loss = candidate
                        logger.info(
                            f"  [ {symbol} ] TRAIL ↓ stop {old_stop if old_stop != float('inf') else 'None'} "
                            f"-> {candidate:.6f} (lw={lw:.6f}, atr={atr:.6f})"
                        )
                        self._sync_exchange_stop(trade, candidate)
        except Exception as e:
            logger.warning(f"  [ {symbol} ] trailing-stop error (stop unchanged): {e}")

    def _sync_exchange_stop(self, trade, new_stop: float):
        """Push a ratcheted trail level to the exchange-native STOP_MARKET.

        Keeps the Binance reduce-only STOP_MARKET (placed at entry) tracking the
        trailed DB stop, so locked profit is protected during the inter-cycle
        sleep and even if this process dies. No-op unless we are live on Binance
        Futures. FAILS SAFE: never raises into the trail logic; on any error the
        existing exchange stop is left in force (the broker method places the new
        stop before cancelling the old, so the position is never left naked).
        """
        try:
            if os.getenv("ACTIVE_BROKER", "ctrader") != "binance_futures":
                return
            from backend.services.trading_mode import get_trading_mode, TradingMode
            if get_trading_mode() != TradingMode.LIVE:
                return
            res = binance_futures_broker.replace_stop_loss(
                symbol=trade.symbol,
                direction=trade.direction,
                new_stop_price=new_stop,
                quantity=trade.quantity,
            )
            status = res.get("status") if isinstance(res, dict) else None
            if status not in ("replaced", "simulated", "skipped"):
                logger.warning(
                    f"  [ {trade.symbol} ] exchange-stop sync returned {res}"
                )
        except Exception as e:
            logger.warning(
                f"  [ {trade.symbol} ] exchange-stop sync error (DB stop set, "
                f"exchange stop unchanged): {e}"
            )

    def _ensure_exchange_protection(self, db, symbol: str):
        """Re-place missing Binance SL/TP from DB levels (fail-safe, never cancels first)."""
        try:
            if os.getenv("ACTIVE_BROKER", "ctrader") != "binance_futures":
                return
            from backend.services.trading_mode import get_trading_mode, TradingMode
            if get_trading_mode() != TradingMode.LIVE:
                return
            trades = (
                db.query(Trade)
                .filter(Trade.symbol == symbol, Trade.status.in_(["open", "filled"]))
                .all()
            )
            for trade in trades:
                if not trade.stop_loss and not trade.take_profit:
                    continue
                res = binance_futures_broker.ensure_protective_orders(
                    trade.symbol,
                    trade.direction,
                    trade.stop_loss,
                    trade.take_profit,
                )
                if res.get("status") == "restored":
                    logger.warning(
                        f"  [ {symbol} ] Exchange protection restored: {res.get('restored')}"
                    )
        except Exception as e:
            logger.warning(f"  [ {symbol} ] exchange protection check failed: {e}")

    def _check_sl_tp(self, db, symbol: str, bars: list[dict]):
        """Check stop-loss and take-profit for open positions."""
        if not bars:
            return
        self._ensure_exchange_protection(db, symbol)
        # Ratchet the trailing stop up/down BEFORE evaluating the crossing,
        # so a profit-locked stop can fire in the same cycle.
        self._apply_trailing_stop(db, symbol, bars)
        current_price = bars[-1]["close"]
        trades = (
            db.query(Trade)
            .filter(Trade.symbol == symbol, Trade.status.in_(["open", "filled"]))
            .all()
        )
        for trade in trades:
            hit = False
            if trade.direction == "BUY":
                if trade.stop_loss and current_price <= trade.stop_loss:
                    hit = True
                    trade.notes = (trade.notes or "") + " | SL hit"
                elif trade.take_profit and current_price >= trade.take_profit:
                    hit = True
                    trade.notes = (trade.notes or "") + " | TP hit"
            else:  # SHORT
                if trade.stop_loss and current_price >= trade.stop_loss:
                    hit = True
                    trade.notes = (trade.notes or "") + " | SL hit (short)"
                elif trade.take_profit and current_price <= trade.take_profit:
                    hit = True
                    trade.notes = (trade.notes or "") + " | TP hit (short)"

            if hit:
                if trade.direction == "BUY":
                    pnl = (current_price - trade.entry_price) * trade.quantity
                else:
                    pnl = (trade.entry_price - current_price) * trade.quantity

                # Send close order via UnifiedTrading BEFORE updating DB
                ut = UnifiedTrading()
                close_side = OrderSide.SELL if trade.direction == "BUY" else OrderSide.BUY
                res = ut.place_order(UnifiedOrder(
                    symbol=trade.symbol,
                    side=close_side,
                    order_type=OrderType.MARKET,
                    quantity=trade.quantity,
                    reduce_only=True
                ))

                if res.success:
                    trade.exit_price = res.filled_price or current_price
                    trade.pnl = pnl
                    trade.status = "closed"
                    trade.closed_at = datetime.now(timezone.utc)
                    trade.notes = (trade.notes or "") + f" | Closed via SL/TP ({res.mode})"
                    self._high_water.pop(trade.id, None)
                    if self._pyramid_mode and trade.symbol in self._pyramid_layers:
                        del self._pyramid_layers[trade.symbol]
                        logger.info(f"  [ {trade.symbol} ] Pyramid layers cleared (SL/TP hit)")
                    logger.info(
                        f"  -> {symbol} position closed (SL/TP), PnL={pnl:+.2f}"
                    )
                else:
                    # already_flat = success (exchange SL already fired)
                    if hasattr(res, 'message') and ('-2022' in str(res.message) or 'already' in str(res.message).lower()):
                        logger.info(f"  -> {symbol} already flat on exchange — marking DB closed")
                        trade.status = 'closed'
                        trade.closed_at = datetime.now(timezone.utc)
                        trade.notes = (trade.notes or '') + ' | SL/TP: already flat on exchange'
                        if self._pyramid_mode and trade.symbol in self._pyramid_layers:
                            del self._pyramid_layers[trade.symbol]
                    else:
                        logger.error(f"  -> Failed to close {symbol} via SL/TP: {res.message}")
                    trade.notes = (trade.notes or "") + f" | SL/TP close FAILED: {res.message}"

    def _is_market_open(self, symbol: str) -> bool:
        """Check if the market is open for the given symbol.
        Crypto: always open (24/7)
        Forex: Mon 21:00 UTC - Fri 21:00 UTC (approximate)
        """
        from datetime import datetime, timezone
        # Crypto is always open - detect USDT pairs and common crypto
        s = symbol.upper()
        if s.endswith('USDT') or s.endswith('USDC') or s.endswith('BUSD') or s.endswith('BTC'):
            return True
        crypto = ['BTC-USD', 'ETH-USD', 'SOL-USD', 'BNB-USD', 'XRP-USD']
        if symbol in crypto or (symbol.endswith('-USD') and '=' not in symbol):
            return True
        
        # Forex hours check
        now = datetime.now(timezone.utc)
        weekday = now.weekday()  # Mon=0, Fri=4, Sat=5, Sun=6
        
        if weekday == 4 and now.hour >= 21:  # Fri after 21:00 UTC
            return False
        if weekday == 5:  # Saturday
            return False
        if weekday == 6 and now.hour < 21:  # Sun before 21:00 UTC
            return False
        
        # Mon-Thu: always open
        return True

    @staticmethod
    def _to_yfinance_symbol(symbol: str) -> str:
        """Convert Binance-native BTCUSDT/BTCUSDC → yfinance BTC-USD format."""
        s = symbol.upper().strip()
        if s.endswith('USDT') or s.endswith('USDC'):
            return s[:-4] + '-USD'
        if s.endswith('BUSD'):
            return s[:-4] + '-USD'
        return s  # already yfinance format or unknown

    async def _filter_tradeable_symbols(self, symbols: list[str]) -> list[str]:
        """Apply the symbol-quality gate: hard blacklist + 24h liquidity floor.

        Low-liquidity / new-listing symbols produced the entire realized-PnL
        loss tail (wide spreads, slippage, forced liquidations). One batch
        ticker call per cycle (cached) decides what is tradeable.
        Fails OPEN: if the volume snapshot is unavailable we keep all symbols
        rather than halting trading.
        """
        blacklist = self.risk_config.symbol_blacklist
        min_vol = float(self.risk_config.min_24h_quote_volume_usdt or 0)

        # 1) Hard blacklist always applies (works even with no network)
        candidates = [s for s in symbols if s.upper() not in blacklist]
        blacklisted = [s for s in symbols if s.upper() in blacklist]
        if blacklisted:
            logger.warning(f"  [SYMBOL GATE] blacklisted (skipped): {blacklisted}")

        if min_vol <= 0 or not candidates:
            return candidates

        # 2) 24h quote-volume liquidity floor (single batch call, cached)
        try:
            tickers = await binance_market_data.get_all_tickers_24h(candidates)
            vol_by_sym = {
                t.get("symbol", "").upper(): float(t.get("quoteVolume", 0) or 0)
                for t in (tickers or [])
            }
        except Exception as e:
            logger.warning(
                f"  [SYMBOL GATE] volume snapshot failed ({e}); "
                f"failing OPEN — keeping {len(candidates)} symbols"
            )
            return candidates

        if not vol_by_sym:
            logger.warning("  [SYMBOL GATE] empty volume snapshot; failing OPEN")
            return candidates

        passed, rejected = [], []
        for s in candidates:
            v = vol_by_sym.get(s.upper())
            # Unknown symbol (delisted / not on futures) → reject as unsafe
            if v is None:
                rejected.append((s, "no-ticker"))
                continue
            if v >= min_vol:
                passed.append(s)
            else:
                rejected.append((s, f"{v/1e6:.1f}M<{min_vol/1e6:.0f}M"))
        if rejected:
            logger.warning(
                f"  [SYMBOL GATE] illiquid (skipped): "
                f"{[f'{s}({why})' for s, why in rejected]}"
            )
        logger.info(
            f"  [SYMBOL GATE] {len(passed)}/{len(symbols)} symbols pass "
            f"(min 24h vol ${min_vol/1e6:.0f}M)"
        )
        return passed

    async def _fetch_bars(self, symbol: str) -> list[dict]:
        """Fetch OHLCV bars. Try Binance Futures API first, fallback to yfinance.
        Also writes the most recent bar to InfluxDB trading-raw for Grafana."""
        bars = None
        # Try Binance klines first (native format, no conversion needed)
        try:
            bars = await binance_market_data.get_klines(
                symbol, interval='1h', limit=1500
            )
            if bars and len(bars) >= 50:
                logger.info(f"  [{symbol}] Binance klines: {len(bars)} bars")
            else:
                logger.warning(f"  [{symbol}] Binance klines insufficient ({len(bars) if bars else 0}), trying yfinance")
                bars = None
        except Exception as e:
            logger.warning(f"  [{symbol}] Binance klines failed: {e}, trying yfinance")

        # Fallback to yfinance
        if bars is None:
            bars = await asyncio.to_thread(self._fetch_bars_yfinance, symbol)

        # ── Write last bar to InfluxDB for Grafana price charts ──────────────
        if bars:
            try:
                last = bars[-1]
                await influx.write_ohlcv(
                    symbol=symbol,
                    open_=float(last.get("open", 0)),
                    high=float(last.get("high", 0)),
                    low=float(last.get("low", 0)),
                    close=float(last.get("close", 0)),
                    volume=float(last.get("volume", 0)),
                    timeframe="1h",
                )
            except Exception as _e:
                logger.debug(f"  [{symbol}] OHLCV write skipped: {_e}")

        return bars

    def _fetch_bars_yfinance(self, symbol: str) -> list[dict]:
        """Fallback: Fetch OHLCV bars via yfinance."""
        yf_symbol = self._to_yfinance_symbol(symbol)
        logger.info(f"  [{symbol}] fetching yfinance data as '{yf_symbol}'")
        ticker = yf.Ticker(yf_symbol)
        hist = ticker.history(period="3mo", interval="1h")
        bars = []
        for i, row in hist.iterrows():
            bars.append({
                "date": i.isoformat(),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": int(row["Volume"]),
            })
        return bars

    async def _fetch_binance_extra(self, symbol: str) -> dict:
        """Fetch additional Binance market data: funding rate, OI, 24h ticker."""
        result = {}
        try:
            funding, oi, ticker = await asyncio.gather(
                binance_market_data.get_funding_rate(symbol),
                binance_market_data.get_open_interest(symbol),
                binance_market_data.get_ticker_24h(symbol),
                return_exceptions=True,
            )
            if isinstance(funding, dict):
                result['funding_rate'] = funding
            if isinstance(oi, dict):
                result['open_interest'] = oi
            if isinstance(ticker, dict):
                result['ticker_24h'] = ticker
        except Exception as e:
            logger.warning(f"  [{symbol}] Binance extra data error: {e}")
        return result

    def _run_strategy(
        self,
        symbol: str,
        bars: list[dict],
        regime: str = "UNKNOWN",
        regime_weights: dict | None = None,
    ):
        """Run the strategy on the given bars with regime-aware weights.

        Args:
            symbol:         Trading symbol
            bars:           OHLCV bar list
            regime:         Market regime string from MarketRegimeDetector
            regime_weights: Weight dict from MarketRegimeDetector
        """
        from backend.strategies.combined import CombinedStrategy
        strategy = CombinedStrategy()
        return strategy.generate_signal(
            symbol, bars,
            regime=regime,
            regime_weights=regime_weights,
        )

    def _get_current_price(self, bars: list[dict]) -> Optional[float]:
        """Get current price from bars."""
        if bars:
            return bars[-1]["close"]
        return None


# Singleton instance
trading_loop = TradingLoopService()
