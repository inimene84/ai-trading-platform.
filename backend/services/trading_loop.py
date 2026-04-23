"""
Automated Trading Loop Service
Runs as a background asyncio task, scanning markets and generating signals/trades.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

from backend.database.connection import SessionLocal
from backend.database.models import TradingSignal, Trade, PortfolioSnapshot
from backend.services.ctrader_service import ctrader_broker
from backend.services.binance_futures_service import binance_futures_broker
from backend.services.influxdb_writer import influx
from backend.services.influxdb_sentiment_reader import sentiment_reader
from backend.services.binance_market_data import binance_market_data
from backend.services import kronos_service
from backend.strategies.market_regime import MarketRegimeDetector
from backend.services.unified_trading import UnifiedTrading, UnifiedOrder, OrderSide, OrderType

load_dotenv()

# ── Broker selector ──────────────────────────────────────────────────────────
_ACTIVE_BROKER_NAME = os.getenv("ACTIVE_BROKER", "ctrader")
if _ACTIVE_BROKER_NAME == "binance_futures":
    _active_broker = binance_futures_broker
else:
    _active_broker = ctrader_broker

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
# Use root logger propagation so uvicorn/nohup captures all cycle logs
logger.propagate = True  # propagate to root logger (captured by nohup)


class TradingLoopService:
    """Background trading loop that scans markets and generates paper trades."""

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._state = "stopped"  # stopped, running, error
        self._error: Optional[str] = None
        self._interval_minutes = 5
        env_syms = os.getenv('TRADING_SYMBOLS', '')
        self._symbols = [s.strip() for s in env_syms.split(',') if s.strip()] if env_syms else [
            'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT',
            'ADAUSDT', 'DOGEUSDT', 'AVAXUSDT', 'DOTUSDT', 'LINKUSDT',
            'MATICUSDT', 'LTCUSDT', 'UNIUSDT', 'ATOMUSDT', 'NEARUSDT',
            'OPUSDT', 'ARBUSDT', 'APTUSDT', 'INJUSDT', 'SUIUSDT'
        ]
        self._strategy_name = "combined"
        self._last_cycle: Optional[str] = None
        self._next_cycle: Optional[str] = None
        self._cycle_count = 0
        # Market regime detector — one instance shared across all cycles/symbols
        self._regime_detector = MarketRegimeDetector()

    @property
    def status(self) -> dict:
        # Fetch real balance from active broker
        balance_info = _active_broker.get_balance() if hasattr(_active_broker, 'get_balance') else {"balance": 0.0}
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
            "cash": balance_info.get("balance", 0.0),
            "equity": balance_info.get("equity", 0.0),
            "margin_used": balance_info.get("margin", 0.0),
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

        self._task = asyncio.create_task(self._loop())
        logger.info(
            f"Trading loop started: interval={interval_minutes}m, "
            f"symbols={self._symbols}, strategy={strategy}"
        )
        return {"message": "Trading loop started", "status": self.status}

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
        """Execute one trading cycle across all symbols."""
        self._cycle_count += 1
        self._last_cycle = datetime.now(timezone.utc).isoformat()
        _cycle_start = datetime.now(timezone.utc).timestamp()
        _signals_generated = 0
        _trades_executed = 0
        _errors = 0
        self._state = "running"
        self._error = None

        logger.info(
            f"=== Trading Cycle #{self._cycle_count} at {self._last_cycle} ==="
        )

        min_confidence   = float(os.getenv("MIN_SIGNAL_STRENGTH", "0.60"))
        ai_threshold     = float(os.getenv("AI_ANALYSIS_THRESHOLD", "0.60"))  # was 0.65 — lowered so borderline signals get AI review too
        max_positions    = int(os.getenv("MAX_POSITIONS", "4"))
        lot_size         = 0.0  # default; actual qty from broker_result

        db = SessionLocal()
        try:
            # Count current open positions
            open_count = db.query(Trade).filter(Trade.status == "open").count()

            for symbol in self._symbols:
                try:
                    # Skip forex/crypto outside market hours
                    if not self._is_market_open(symbol):
                        logger.info(f"  {symbol}: market closed - skipping")
                        continue

                    # Fetch data - try Binance first, fallback to yfinance
                    bars = await self._fetch_bars(symbol)
                    if not bars or len(bars) < 50:
                        logger.warning(
                            f"Insufficient data for {symbol}: {len(bars) if bars else 0} bars"
                        )
                        continue

                    # Fetch Binance-native market data (funding rate, OI, 24h ticker)
                    binance_extra = await self._fetch_binance_extra(symbol)

                    # ── Detect market regime ──────────────────────────────
                    try:
                        regime_result = await asyncio.to_thread(
                            self._regime_detector.detect, bars
                        )
                        _regime_str = regime_result.regime
                        _regime_weights = regime_result.weights()
                    except Exception as _re:
                        logger.warning(f"  RegimeDetector [{symbol}] error: {_re}")
                        _regime_str = "UNKNOWN"
                        _regime_weights = None

                    # ── Run strategy with regime-aware weights ────────────────
                    signal = await asyncio.to_thread(
                        self._run_strategy, symbol, bars,
                        _regime_str, _regime_weights
                    )
                    if not signal:
                        continue


                    # === Kronos AI Model Prediction ===
                    try:
                        import pandas as _pd
                        _bars_df = _pd.DataFrame(bars)
                        kronos_result = await kronos_service.predict(_bars_df, symbol)
                        logger.info(
                            f"  Kronos [{symbol}]: {kronos_result['signal']} "
                            f"{kronos_result['predicted_change_pct']:+.2f}%"
                        )
                    except Exception as _ke:
                        logger.warning(f"  Kronos [{symbol}] error: {_ke}")
                        kronos_result = {"signal": "NEUTRAL", "confidence": 0.0,
                                        "predicted_close": None, "predicted_change_pct": 0.0,
                                        "error": str(_ke)}

                    # ── Kronos Signal Gate (veto/boost layer) ────────────────
                    # Kronos is an ML model that predicts price direction.
                    # Previously its output was purely decorative (logged only).
                    # Now it actively gates signals BEFORE AI analysis.
                    _k_signal = kronos_result.get("signal", "NEUTRAL")
                    _k_conf   = float(kronos_result.get("confidence", 0.0))
                    _k_change = float(kronos_result.get("predicted_change_pct", 0.0))

                    if signal.signal in ("BUY", "SELL") and _k_signal not in ("NEUTRAL", ""):
                        _kronos_agrees = (
                            (signal.signal == "BUY"  and _k_signal == "UP")   or
                            (signal.signal == "BUY"  and _k_signal == "BUY")  or
                            (signal.signal == "SELL" and _k_signal == "DOWN") or
                            (signal.signal == "SELL" and _k_signal == "SELL")
                        )
                        _kronos_contradicts = not _kronos_agrees

                        if _kronos_contradicts and _k_conf >= 0.75 and signal.confidence < 0.72:
                            # Strong Kronos contradiction with moderate strategy signal → VETO
                            logger.warning(
                                f"  ⛔ KRONOS VETO [{symbol}]: strategy={signal.signal}({signal.confidence:.2f}) "
                                f"but Kronos={_k_signal}(conf={_k_conf:.2f}, {_k_change:+.2f}%) → NEUTRAL"
                            )
                            signal.signal = "NEUTRAL"
                            signal.confidence = 0.0
                        elif _kronos_contradicts and _k_conf >= 0.60:
                            # Moderate Kronos contradiction → reduce confidence
                            _old_conf = signal.confidence
                            signal.confidence = max(signal.confidence * 0.75, 0.0)
                            logger.info(
                                f"  ⚠️  KRONOS DOWNGRADE [{symbol}]: strategy={signal.signal} "
                                f"conf {_old_conf:.2f}→{signal.confidence:.2f} "
                                f"(Kronos={_k_signal} conf={_k_conf:.2f})"
                            )
                        elif _kronos_agrees and _k_conf >= 0.60:
                            # Kronos confirms → small confidence boost
                            _old_conf = signal.confidence
                            signal.confidence = min(signal.confidence * 1.08, 1.0)
                            logger.info(
                                f"  ✅ KRONOS BOOST [{symbol}]: {signal.signal} "
                                f"conf {_old_conf:.2f}→{signal.confidence:.2f} "
                                f"(Kronos={_k_signal} conf={_k_conf:.2f})"
                            )

                    logger.info(
                        f"  {symbol}: {signal.signal} "
                        f"confidence={signal.confidence:.2f} "
                        f"price={signal.entry_price}"
                    )

                    # Store signal in DB
                    db_signal = TradingSignal(
                        symbol=symbol,
                        direction=signal.signal,
                        confidence=signal.confidence,
                        entry_price=signal.entry_price,
                        stop_loss=signal.stop_loss,
                        take_profit=signal.take_profit,
                        strategy=signal.strategy or self._strategy_name,
                    )
                    db.add(db_signal)
                    db.commit()  # commit so signal exists before potential order
                    _signals_generated += 1

                    # ── InfluxDB: write OHLCV (last bar) + signal ──
                    last_bar = bars[-1]
                    await influx.write_ohlcv(
                        symbol=symbol,
                        open_=last_bar["open"],
                        high=last_bar["high"],
                        low=last_bar["low"],
                        close=last_bar["close"],
                        volume=float(last_bar["volume"]),
                        timeframe="1h",
                    )
                    await influx.write_signal(
                        symbol=symbol,
                        direction=signal.signal,
                        confidence=signal.confidence,
                        entry_price=signal.entry_price or 0.0,
                        stop_loss=signal.stop_loss,
                        take_profit=signal.take_profit,
                        strategy=signal.strategy or self._strategy_name,
                        ai_used=False,
                        signal_id=db_signal.id,
                    )

                    # === Kronos Gate (foundation model veto/boost layer) ===
                    kronos_gate_applied = False
                    if signal.signal in ("BUY", "SELL"):
                        from backend.services.kronos_gate import apply_kronos_gate
                        # Ensure kronos_result is available (it may already be computed earlier)
                        kronos_res = kronos_result if 'kronos_result' in dir() else {}
                        if not kronos_res and bars:
                            try:
                                kronos_res = await kronos_service.predict(pd.DataFrame(bars), symbol)
                            except Exception:
                                kronos_res = {}
                        gate = apply_kronos_gate(
                            strategy_signal=signal.signal,
                            strategy_confidence=signal.confidence,
                            kronos_result=kronos_res,
                            symbol=symbol,
                        )
                        if gate.action in ("veto", "flip", "boost", "dampen"):
                            kronos_gate_applied = True
                            signal.signal = gate.final_signal
                            signal.confidence = gate.confidence
                            logger.info(
                                f"  KronosGate [{symbol}]: {gate.action.upper()} "
                                f"{gate.original_signal} → {gate.final_signal} "
                                f"conf={gate.confidence:.2f}"
                            )
                            db_signal.reasoning = f"[KronosGate] {gate.reasoning}\n\n"
                            db_signal.direction = signal.signal
                            db_signal.confidence = signal.confidence
                            db.commit()

                    # === Opinion Layer AI Analysis ===
                    if signal.signal in ("BUY", "SELL") and signal.confidence >= ai_threshold:
                        _opinion_start = time.time()
                        logger.info(f"  {symbol}: conf={signal.confidence:.2f} >= {ai_threshold} - running Opinion Layer...")
                        from backend.services.opinion_layer import analyze_symbol as analyze_opinion
                        opinion = await analyze_opinion(
                            symbol=symbol,
                            bars=bars,
                            include_kronos=True,
                            include_social=True,
                            include_alerts=True,
                        )
                        logger.info(
                            f"  Opinion Layer [{symbol}]: {opinion.direction} "
                            f"conf={opinion.confidence:.2f} "
                            f"agents={len(opinion.agent_opinions)}"
                        )
                        # Veto or boost the strategy signal
                        if opinion.direction == "HOLD":
                            signal.signal = "NEUTRAL"
                            signal.confidence = 0.0
                            logger.info(f"  Opinion Layer VETO: signal neutralized")
                        elif opinion.direction in ("BUY", "SELL"):
                            # If direction aligns with strategy signal -> boost confidence
                            # If opposing -> use lower confidence and consider veto
                            if opinion.direction == signal.signal:
                                boost = min(opinion.confidence * 0.2, 0.15)
                                signal.confidence = min(signal.confidence + boost, 1.0)
                                logger.info(f"  Opinion Layer BOOST: conf boosted to {signal.confidence:.2f}")
                            else:
                                # Opposing signal: if opinion confidence > strategy confidence, let it override
                                if opinion.confidence > signal.confidence * 0.8:
                                    old_signal = signal.signal
                                    signal.signal = opinion.direction
                                    signal.confidence = opinion.confidence
                                    logger.info(
                                        f"  Opinion Layer OVERRIDES: {old_signal}->{signal.signal} "
                                        f"conf={signal.confidence:.2f}"
                                    )
                                else:
                                    # Reduce confidence but keep original signal
                                    signal.confidence *= max(0.6, 1.0 - opinion.confidence * 0.4)
                                    logger.info(
                                        f"  Opinion Layer DAMPEN: conf reduced to {signal.confidence:.2f}"
                                    )

                        # Store reasoning back to DB
                        db_signal.reasoning = opinion.reasoning
                        db_signal.ai_analysis = json.dumps({
                            "direction": opinion.direction,
                            "confidence": opinion.confidence,
                            "agent_opinions": [
                                {"agent": op.agent, "signal": op.signal, "confidence": op.confidence}
                                for op in opinion.agent_opinions
                            ],
                            "kronos": opinion.kronos,
                            "social": opinion.social,
                            "alert_count": len(opinion.alerts),
                            "total_duration_s": time.time() - _opinion_start,
                        })
                        db_signal.direction = signal.signal
                        db_signal.confidence = signal.confidence
                        db.commit()
                        # ── InfluxDB: write agent state for each opinion ──
                        for op in opinion.agent_opinions:
                            await influx.write_agent_state(
                                agent=f"opinion_{op.agent}",
                                symbol=symbol,
                                direction=op.signal.upper() if op.signal != "bullish" and op.signal != "bearish" and op.signal != "neutral" else op.signal.upper(),
                                confidence=op.confidence,
                                reasoning=op.reasoning[:500],
                            )
                        await influx.write_signal(
                            symbol=symbol,
                            direction=signal.signal,
                            confidence=signal.confidence,
                            entry_price=signal.entry_price or 0.0,
                            stop_loss=signal.stop_loss,
                            take_profit=signal.take_profit,
                            strategy=signal.strategy or self._strategy_name,
                            ai_used=True,
                            signal_id=db_signal.id,
                        )
                    elif signal.signal in ("BUY", "SELL"):
                        logger.info(f"  {symbol}: conf={signal.confidence:.2f} < {ai_threshold} - skipping AI (using strategy signal)")

                    # === EXECUTION ===
                    if signal.signal in ("BUY", "SELL") and signal.confidence >= min_confidence:
                        existing = (
                            db.query(Trade)
                            .filter(Trade.symbol == symbol, Trade.status == "open")
                            .first()
                        )

                        if signal.signal == "BUY":
                            if existing:
                                db_signal.status = "rejected"
                                logger.info(f"  {symbol}: already have open BUY - skipping")
                            elif open_count >= max_positions:
                                db_signal.status = "rejected"
                                logger.info(f"  {symbol}: max positions ({max_positions}) reached - skipping")
                            else:
                                # === OPEN LONG via UnifiedTrading router ===
                                ut = UnifiedTrading()
                                order = UnifiedOrder(
                                    symbol=symbol,
                                    side=OrderSide.BUY,
                                    order_type=OrderType.MARKET,
                                    quantity=0.01,  # default lot; broker will normalize
                                    price=signal.entry_price,
                                    stop_loss=signal.stop_loss,
                                    take_profit=signal.take_profit,
                                )
                                broker_result = await asyncio.get_event_loop().run_in_executor(
                                    None, lambda o=order: ut.place_order(o)
                                )
                                ok = broker_result.success
                                broker_note = f"UT:{broker_result.mode}" if ok else f"ut_error:{broker_result.message[:60]}"

                                trade = Trade(
                                    symbol=symbol,
                                    direction="BUY",
                                    quantity=broker_result.get('quantity') or lot_size,
                                    entry_price=broker_result.get('filled_price') or signal.entry_price,
                                    stop_loss=signal.stop_loss,
                                    take_profit=signal.take_profit,
                                    status="open" if ok else "failed",
                                    strategy=signal.strategy or self._strategy_name,
                                    notes=f"conf={signal.confidence:.2f} | {broker_note}",
                                    signal_id=db_signal.id,
                                    binance_order_id=broker_result.get('order_id', '') if ok else None,
                                    exchange='binance_futures' if ok else None,
                                    filled_price=broker_result.get('filled_price') if ok else None,
                                )
                                db.add(trade)
                                if ok:
                                    open_count += 1
                                db_signal.status = "executed" if ok else "failed"
                                logger.info(f"  -> BUY 1 lot {symbol} @ {signal.entry_price} [{broker_note}]")
                                _trades_executed += 1
                                db.flush()  # flush to get trade.id before writing to InfluxDB
                                await influx.write_trade(
                                    symbol=symbol,
                                    direction="BUY",
                                    quantity=lot_size,
                                    entry_price=signal.entry_price or 0.0,
                                    status="open" if ok else "failed",
                                    strategy=signal.strategy or self._strategy_name,
                                    stop_loss=signal.stop_loss,
                                    take_profit=signal.take_profit,
                                )

                        elif signal.signal == "SELL":
                            if existing and existing.direction == "BUY":
                                # === CLOSE LONG via UnifiedTrading router ===
                                ut = UnifiedTrading()
                                close_order = UnifiedOrder(
                                    symbol=symbol,
                                    side=OrderSide.SELL,
                                    order_type=OrderType.MARKET,
                                    quantity=float(existing.quantity),
                                    price=signal.entry_price,
                                )
                                broker_result = await asyncio.get_event_loop().run_in_executor(
                                    None, lambda o=close_order: ut.place_order(o)
                                )
                                ok = broker_result.success
                                broker_note = f"UT:{broker_result.mode}:closed" if ok else f"ut_error:{broker_result.message[:60]}"

                                cur_price = signal.entry_price or existing.entry_price
                                pnl = (cur_price - existing.entry_price) * existing.quantity
                                existing.exit_price = cur_price
                                existing.pnl = pnl
                                existing.status = "closed"
                                existing.closed_at = datetime.now(timezone.utc)
                                existing.notes = (existing.notes or "") + f" | Closed conf={signal.confidence:.2f} [{broker_note}]"
                                if ok:
                                    open_count -= 1
                                db_signal.status = "executed" if ok else "failed"
                                logger.info(f"  -> SELL (close) {symbol} @ {cur_price} PnL={pnl:+.4f} [{broker_note}]")
                                _trades_executed += 1
                                await influx.write_trade(
                                    symbol=symbol,
                                    direction="SELL",
                                    quantity=existing.quantity,
                                    entry_price=cur_price,
                                    status="closed",
                                    strategy=existing.strategy or self._strategy_name,
                                    pnl=pnl,
                                )

                            elif not existing and open_count < max_positions:
                                # === OPEN SHORT via active broker ===
                                broker_result = await asyncio.get_event_loop().run_in_executor(
                                    None,
                    lambda s=symbol: _active_broker.place_order(
                            s,
                            direction="SELL",
                            quantity=lot_size,
                            price=signal.entry_price,
                            stop_loss=signal.stop_loss,
                            take_profit=signal.take_profit,
                        )
                                )
                                ok = broker_result.get('status') in ('simulated', 'sent')
                                broker_note = f"Broker:{broker_result.get('broker','?')}" if ok else f"broker_error:{str(broker_result.get('error','?'))[:60]}"

                                trade = Trade(
                                    symbol=symbol,
                                    direction="SELL",
                                    quantity=broker_result.get('quantity') or lot_size,
                                    entry_price=broker_result.get('filled_price') or signal.entry_price,
                                    stop_loss=signal.stop_loss,
                                    take_profit=signal.take_profit,
                                    status="open" if ok else "failed",
                                    strategy=signal.strategy or self._strategy_name,
                                    notes=f"conf={signal.confidence:.2f} | {broker_note} (short)",
                                    signal_id=db_signal.id,
                                    binance_order_id=broker_result.get('order_id', '') if ok else None,
                                    exchange='binance_futures' if ok else None,
                                    filled_price=broker_result.get('filled_price') if ok else None,
                                )
                                db.add(trade)
                                if ok:
                                    open_count += 1
                                db_signal.status = "executed" if ok else "failed"
                                logger.info(f"  -> SELL (short) 1 lot {symbol} @ {signal.entry_price} [{broker_note}]")
                                _trades_executed += 1
                                db.flush()  # flush to get trade.id before writing to InfluxDB
                                await influx.write_trade(
                                    symbol=symbol,
                                    direction="SELL",
                                    quantity=lot_size,
                                    entry_price=signal.entry_price or 0.0,
                                    status="open" if ok else "failed",
                                    strategy=signal.strategy or self._strategy_name,
                                    stop_loss=signal.stop_loss,
                                    take_profit=signal.take_profit,
                                )
                            else:
                                db_signal.status = "rejected"
                                logger.info(f"  {symbol}: SELL rejected (existing={existing is not None}, positions={open_count}/{max_positions})")

                    else:
                        db_signal.status = "expired" if signal.signal == "NEUTRAL" else "rejected"

                    # SL/TP check for open positions
                    self._check_sl_tp(db, symbol, bars)

                except Exception as e:
                    logger.warning(f"Symbol processing error: {e}")
                    continue

            # Save portfolio snapshot (simple version)
            # Save portfolio snapshot (simple version)
            self._save_portfolio_snapshot(db)

            # ── InfluxDB: write system health + portfolio ──
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
            bal = _active_broker.get_balance() if hasattr(_active_broker, 'get_balance') else {}
            await influx.write_portfolio_snapshot(
                cash=bal.get("balance", 0.0),
                equity=bal.get("equity", 0.0),
                margin_used=bal.get("margin", 0.0),
                open_positions=open_count,
                cycle=self._cycle_count,
            )

            # ── InfluxDB: write Binance Futures wallet + positions ────────────
            if _ACTIVE_BROKER_NAME == 'binance_futures':
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

        except Exception as e:
            db.rollback()
            raise
        finally:
            db.close()

    def _save_portfolio_snapshot(self, db):
        """Save portfolio snapshot with real broker balance."""
        try:
            # Get open trades
            open_trades = db.query(Trade).filter(Trade.status == "open").all()
            
            # Get real balance from active broker
            balance_info = _active_broker.get_balance() if hasattr(_active_broker, 'get_balance') else {"balance": 0.0}
            real_cash = balance_info.get("balance", 0.0)
            real_equity = balance_info.get("equity", 0.0)

            # Save snapshot
            snapshot = PortfolioSnapshot(
                total_value=real_cash,
                cash=real_cash,
                positions_value=0.0,
                total_pnl=0.0,
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

    def _check_sl_tp(self, db, symbol: str, bars: list[dict]):
        """Check stop-loss and take-profit for open positions."""
        if not bars:
            return
        current_price = bars[-1]["close"]
        trades = (
            db.query(Trade)
            .filter(Trade.symbol == symbol, Trade.status == "open")
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
                trade.exit_price = current_price
                trade.pnl = pnl
                trade.status = "closed"
                trade.closed_at = datetime.now(timezone.utc)
                trade.notes = (trade.notes or "") + " | Closed via SL/TP"
                logger.info(
                    f"  -> {symbol} position closed (SL/TP), PnL={pnl:+.2f}"
                )

    def _is_market_open(self, symbol: str) -> bool:
        """Check if the market is open for the given symbol.
        Crypto: always open (24/7)
        Forex: Mon 21:00 UTC - Fri 21:00 UTC (approximate)
        """
        from datetime import datetime, timezone
        # Crypto is always open - detect USDT pairs and common crypto
        s = symbol.upper()
        if s.endswith('USDT') or s.endswith('BUSD') or s.endswith('BTC'):
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
        """Convert Binance-native BTCUSDT → yfinance BTC-USD format."""
        s = symbol.upper().strip()
        if s.endswith('USDT'):
            return s[:-4] + '-USD'
        if s.endswith('BUSD'):
            return s[:-4] + '-USD'
        return s  # already yfinance format or unknown

    async def _fetch_bars(self, symbol: str) -> list[dict]:
        """Fetch OHLCV bars. Try Binance Futures API first, fallback to yfinance."""
        # Try Binance klines first (native format, no conversion needed)
        try:
            bars = await binance_market_data.get_klines(
                symbol, interval='1h', limit=1500
            )
            if bars and len(bars) >= 50:
                logger.info(f"  [{symbol}] Binance klines: {len(bars)} bars")
                return bars
            else:
                logger.warning(f"  [{symbol}] Binance klines insufficient ({len(bars) if bars else 0}), trying yfinance")
        except Exception as e:
            logger.warning(f"  [{symbol}] Binance klines failed: {e}, trying yfinance")

        # Fallback to yfinance
        return await asyncio.to_thread(self._fetch_bars_yfinance, symbol)

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
