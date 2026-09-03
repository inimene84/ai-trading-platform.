"""
Layered Multi-Asset Signal Candidate & Timing Engine
Coordinates market scanning (cTrader & Binance), news/macro correlation,
4 strategy evaluators (Momentum, Fade, Straddle, Slingshot), precision pip-margin
sizing, and timing execution windows (Pre-event, At-release, Post-reaction, Bar-close).
"""

import asyncio
import time
import logging
import os
import uuid
from typing import Dict, Any, List, Optional, Set
from datetime import datetime, timezone

from backend.services.ctrader_service import CTraderService, ctrader_service
from backend.services.ctrader_trade_sync import persist_ctrader_execution
from backend.services.unified_trading import UnifiedTrading, UnifiedOrder, OrderSide, OrderType
from backend.services.binance_market_data import binance_market_data
from backend.services.multi_asset_bars import classify_symbol, tf_to_binance_interval

logger = logging.getLogger(__name__)


class TimingMode:
    PRE_EVENT = "PRE_EVENT"
    AT_RELEASE = "AT_RELEASE"
    POST_REACTION = "POST_REACTION"
    BAR_CLOSE = "BAR_CLOSE"


class CandidateStatus:
    PENDING = "PENDING"
    READY = "READY"
    EXECUTED = "EXECUTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class SignalCandidateEngine:
    def __init__(self):
        self.candidates: Dict[str, Dict[str, Any]] = {}
        self.timing_config: Dict[str, Any] = {
            "pre_event_window_min": 15,       # Minutes before release to evaluate pre-event
            "at_release_window_sec": 45,      # Max seconds for at-release before moving to post
            "post_reaction_delay_min": 2,     # Wait X mins after release to enter post-reaction
            "post_reaction_window_min": 10,   # Window duration for post-reaction entries
            "max_spread_pips": 2.0,           # Max allowable spread in pips for FX
            "max_slippage_pips": 1.5,         # Max slippage tolerance
            "default_risk_pct": 0.5,          # Default risk % per trade
            "account_equity_override": 10000.0,
            "strategies_enabled": {
                "momentum": True,
                "fade": True,
                "straddle": True,
                "slingshot": True,
            },
        }
        self.execution_config: Dict[str, Any] = {
            "forex_only": os.getenv("CTRADER_FOREX_ONLY", "true").lower() == "true",
            "max_open_ctrader_positions": int(
                os.getenv("CTRADER_MAX_OPEN_POSITIONS")
                or os.getenv("MAX_CTRADER_POSITIONS")
                or "10"
            ),
            "max_ready_per_poll": int(os.getenv("CTRADER_MAX_READY_PER_POLL", "10")),
            "max_ctrader_lots": float(os.getenv("CTRADER_MAX_LOTS", "0.10")),
            "max_metal_lots": float(os.getenv("CTRADER_MAX_METAL_LOTS", "0.01")),
            "one_position_per_symbol": os.getenv("CTRADER_ONE_POSITION_PER_SYMBOL", "true").lower() == "true",
            "include_metals": os.getenv("CTRADER_INCLUDE_METALS", "false").lower() == "true",
            "max_same_base": int(os.getenv("CTRADER_MAX_SAME_BASE", "2")),
            "max_candidates": int(os.getenv("CTRADER_MAX_CANDIDATES", "200")),
        }
        # High-impact calendar currencies map to the liquid FX pair, never gold.
        # NZD OCR / AUD GDP used to become XAUUSD SELL and n8n retried forever.
        self.MACRO_CURRENCY_PAIRS: Dict[str, str] = {
            "USD": "EURUSD",
            "EUR": "EURUSD",
            "GBP": "GBPUSD",
            "JPY": "USDJPY",
            "AUD": "AUDUSD",
            "NZD": "NZDUSD",
            "CAD": "USDCAD",
            "CHF": "USDCHF",
        }

    def _is_ctrader_forex_candidate(self, cand: Dict[str, Any]) -> bool:
        if cand.get("broker") != "ctrader":
            return False
        asset = classify_symbol(str(cand.get("symbol", "")))
        if asset == "forex":
            return True
        if asset == "metal" and self.execution_config.get("include_metals"):
            return True
        return False

    def _open_ctrader_position_count(self) -> int:
        try:
            st = ctrader_service.status()
            if st.get("connected"):
                return int(st.get("open_positions") or 0)
            return len(ctrader_service.get_positions())
        except Exception as err:
            logger.warning(f"Could not read cTrader open positions: {err}")
            return 0

    def _open_ctrader_symbols(self) -> Set[str]:
        try:
            return {
                str(p.get("symbol", "")).upper()
                for p in (ctrader_service.get_positions() or [])
                if p.get("symbol")
            }
        except Exception as err:
            logger.warning(f"Could not read cTrader open symbols: {err}")
            return set()

    @staticmethod
    def _symbol_base(symbol: str) -> str:
        sym = str(symbol or "").upper()
        return sym[:3] if len(sym) >= 6 else sym

    def _open_ctrader_base_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for sym in self._open_ctrader_symbols():
            base = self._symbol_base(sym)
            if base:
                counts[base] = counts.get(base, 0) + 1
        return counts

    def _same_base_slots_available(self, symbol: str) -> bool:
        cap = int(self.execution_config.get("max_same_base") or 0)
        if cap <= 0:
            return True
        base = self._symbol_base(symbol)
        return self._open_ctrader_base_counts().get(base, 0) < cap

    def _account_equity(self) -> float:
        """Live cTrader equity when connected; otherwise a conservative fallback.

        A live account with no snapshot must not inherit the $10k paper override —
        that sized 0.10 lots on a ~$159 IC Markets book.
        """
        try:
            equity = float(getattr(ctrader_service, "equity", 0) or 0)
            if equity > 0:
                return equity
        except Exception:
            pass
        if ctrader_service.has_credentials():
            return float(os.getenv("CTRADER_FALLBACK_EQUITY", "150"))
        return float(self.timing_config.get("account_equity_override") or 10_000.0)

    def prune_candidates(self, now_ts: Optional[int] = None) -> int:
        """Drop expired/cancelled rows so the in-memory queue cannot grow without bound."""
        now_ts = now_ts or int(time.time())
        kept: Dict[str, Dict[str, Any]] = {}
        removed = 0
        for cid, cand in self.candidates.items():
            status = cand.get("status")
            latest = int(cand.get("latest_exec_at") or 0)
            if status in (CandidateStatus.EXPIRED, CandidateStatus.CANCELLED):
                removed += 1
                continue
            if latest and now_ts > latest and status != CandidateStatus.EXECUTED:
                removed += 1
                continue
            kept[cid] = cand
        max_keep = int(self.execution_config.get("max_candidates") or 200)
        if len(kept) > max_keep:
            ordered = sorted(
                kept.values(),
                key=lambda c: str(c.get("created_at") or ""),
                reverse=True,
            )
            kept = {c["id"]: c for c in ordered[:max_keep]}
            removed += len(self.candidates) - len(kept) - removed
        self.candidates = kept
        return removed

    def _ctrader_execution_slots_remaining(self) -> int:
        cap = int(self.execution_config.get("max_open_ctrader_positions", 10))
        return max(0, cap - self._open_ctrader_position_count())

    def _ctrader_execution_slot_available(self) -> bool:
        return self._ctrader_execution_slots_remaining() > 0

    def clear_candidates(self, statuses: Optional[List[str]] = None) -> int:
        """Drop in-memory candidates (default: everything except EXECUTED)."""
        if statuses is None:
            statuses = [
                CandidateStatus.PENDING,
                CandidateStatus.READY,
                CandidateStatus.EXPIRED,
                CandidateStatus.CANCELLED,
            ]
        removed = 0
        for cid, cand in list(self.candidates.items()):
            if cand.get("status") in statuses:
                del self.candidates[cid]
                removed += 1
        return removed
    MIN_FEATURE_BARS = 20
    # Entries trigger on the signal timeframe, but stops are sized from a
    # slower one. M5 ATR on a quiet pair produced 2 pip stops that the broker
    # discarded outright, leaving positions with no protection at all.
    STOP_TIMEFRAME = os.getenv("SIGNAL_STOP_TIMEFRAME", "H1")

    @staticmethod
    def stop_atr(features: Dict[str, Any]) -> float:
        """ATR to size protective levels from, falling back to the signal ATR."""
        return float(features.get("stop_atr") or features.get("atr") or 0.0)

    async def _attach_stop_atr(self, symbol: str, broker: str, features: Dict[str, Any]) -> None:
        """Add a slower-timeframe ATR for stop sizing.

        Leaves `atr` untouched so entry triggers keep their signal-timeframe
        sensitivity. On any failure the signal ATR is used and the broker
        minimum-distance clamp remains the backstop.
        """
        tf = self.STOP_TIMEFRAME
        if not tf or tf == "":
            return
        try:
            if broker == "ctrader":
                bars = await asyncio.to_thread(
                    ctrader_service.get_trendbars, symbol, tf, count=40
                )
            else:
                bars = await binance_market_data.get_klines(
                    symbol, interval=tf_to_binance_interval(tf), limit=40
                )
            slow = self._compute_features(bars)
            if slow and slow.get("atr"):
                features["stop_atr"] = slow["atr"]
                features["stop_timeframe"] = tf
        except Exception as err:
            logger.warning(f"{symbol}: {tf} stop ATR unavailable ({err}); using signal ATR")

    @staticmethod
    def _compute_features(bars: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Compute standard technical indicators from candlestick bars.

        Returns None when there is not enough history. The previous placeholder
        (price 1.0, RSI 50, volatility 0.1%) looked exactly like a compressed
        range, so an empty API response would fire a straddle candidate on
        fabricated data. Callers must skip the symbol instead.
        """
        if not bars or len(bars) < SignalCandidateEngine.MIN_FEATURE_BARS:
            return None

        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]

        last_close = closes[-1]

        # ATR calculation (simple true range over last min(14, len))
        n = min(14, len(bars) - 1)
        trs = []
        for i in range(len(bars) - n, len(bars)):
            h_l = highs[i] - lows[i]
            h_cp = abs(highs[i] - closes[i - 1])
            l_cp = abs(lows[i] - closes[i - 1])
            trs.append(max(h_l, h_cp, l_cp))
        atr = sum(trs) / len(trs) if trs else (highs[-1] - lows[-1])
        if last_close > 0 and atr > last_close * 0.05:
            logger.warning(
                "ATR %s exceeds 5%% of price %s — clamping (possible JPY scale mismatch)",
                atr,
                last_close,
            )
            atr = last_close * 0.012

        # Simple RSI (14 period)
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            if diff >= 0:
                gains.append(diff)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(abs(diff))

        avg_gain = sum(gains[-14:]) / 14 if len(gains) >= 14 else 0.0001
        avg_loss = sum(losses[-14:]) / 14 if len(losses) >= 14 else 0.0001
        rs = avg_gain / (avg_loss if avg_loss != 0 else 0.00001)
        rsi = 100 - (100 / (1 + rs))

        # EMAs
        def _ema(series, period):
            k = 2 / (period + 1)
            ema = series[0]
            for price in series[1:]:
                ema = (price * k) + (ema * (1 - k))
            return ema

        ema_fast = _ema(closes, min(9, len(closes)))
        ema_slow = _ema(closes, min(21, len(closes)))

        trend = "BULLISH" if ema_fast > ema_slow else "BEARISH" if ema_fast < ema_slow else "NEUTRAL"
        volatility_pct = (atr / last_close) * 100 if last_close > 0 else 0.1

        return {
            "last_close": last_close,
            "atr": round(atr, 5),
            "rsi": round(rsi, 2),
            "ema_fast": round(ema_fast, 5),
            "ema_slow": round(ema_slow, 5),
            "trend": trend,
            "volatility_pct": round(volatility_pct, 3),
            "recent_high": max(highs[-10:]),
            "recent_low": min(lows[-10:]),
        }

    # ── Strategy 1: Momentum (Trend + Breakout Confirmation) ─────────────────
    def _evaluate_momentum(self, symbol: str, features: Dict[str, Any], broker: str) -> Optional[Dict[str, Any]]:
        if not self.timing_config["strategies_enabled"].get("momentum", True):
            return None

        # Bullish momentum: EMA trend is BULLISH + RSI between 55 and 70 (healthy impulse)
        # Bearish momentum: EMA trend is BEARISH + RSI between 30 and 45
        direction = None
        if features["trend"] == "BULLISH" and 52 <= features["rsi"] <= 72:
            direction = "BUY"
        elif features["trend"] == "BEARISH" and 28 <= features["rsi"] <= 48:
            direction = "SELL"

        if not direction:
            return None

        price = features["last_close"]
        atr = self.stop_atr(features)

        if direction == "BUY":
            sl = round(price - (1.5 * atr), 5)
            tp = round(price + (2.5 * atr), 5)
        else:
            sl = round(price + (1.5 * atr), 5)
            tp = round(price - (2.5 * atr), 5)

        return {
            "strategy": "MOMENTUM_TREND_PULSE",
            "direction": direction,
            "entry_price": price,
            "stop_loss": sl,
            "take_profit": tp,
            "timing_mode": TimingMode.POST_REACTION,
            "confidence": 0.82,
            "reason": f"M5 {direction} momentum confirmed. RSI {features['rsi']} aligned with {features['trend']} trend.",
        }

    # ── Strategy 2: Fade (Contrarian Mean-Reversion) ─────────────────────────
    def _evaluate_fade(self, symbol: str, features: Dict[str, Any], broker: str) -> Optional[Dict[str, Any]]:
        if not self.timing_config["strategies_enabled"].get("fade", True):
            return None

        # Overextended spike: RSI > 75 (overbought -> Fade Short) or RSI < 25 (oversold -> Fade Long)
        direction = None
        if features["rsi"] >= 75:
            direction = "SELL"
        elif features["rsi"] <= 25:
            direction = "BUY"

        if not direction:
            return None

        price = features["last_close"]
        atr = self.stop_atr(features)

        if direction == "BUY":
            sl = round(features["recent_low"] - (0.5 * atr), 5)
            tp = round(features["ema_slow"], 5)
        else:
            sl = round(features["recent_high"] + (0.5 * atr), 5)
            tp = round(features["ema_slow"], 5)

        return {
            "strategy": "FADE_OVEREXTENSION",
            "direction": direction,
            "entry_price": price,
            "stop_loss": sl,
            "take_profit": tp,
            "timing_mode": TimingMode.POST_REACTION,
            "confidence": 0.78,
            "reason": f"Extreme RSI {features['rsi']} reached. Fading overbought/oversold surge back to mean {features['ema_slow']}.",
        }

    # ── Strategy 3: Straddle (Pre-Event Compression Bracket) ─────────────────
    def _evaluate_straddle(self, symbol: str, features: Dict[str, Any], broker: str) -> Optional[Dict[str, Any]]:
        if not self.timing_config["strategies_enabled"].get("straddle", True):
            return None

        # Low volatility compression before catalyst (volatility_pct < 0.15% or RSI near 50)
        if 46 <= features["rsi"] <= 54 and features["volatility_pct"] < 0.25:
            price = features["last_close"]
            atr = self.stop_atr(features)
            upper_bracket = round(features["recent_high"] + (0.4 * atr), 5)
            lower_bracket = round(features["recent_low"] - (0.4 * atr), 5)

            return {
                "strategy": "STRADDLE_VOLATILITY_BRACKET",
                "direction": "BUY",  # Primary breakout bias, with lower bracket hedge
                "entry_price": upper_bracket,
                "stop_loss": round(price - (0.8 * atr), 5),
                "take_profit": round(upper_bracket + (2.0 * atr), 5),
                "timing_mode": TimingMode.PRE_EVENT,
                "confidence": 0.75,
                "reason": f"Tight range compression. Volatility {features['volatility_pct']}%. Pre-event bracket around {lower_bracket} - {upper_bracket}.",
            }
        return None

    # ── Strategy 4: Slingshot (Rejection Reversal) ───────────────────────────
    def _evaluate_slingshot(self, symbol: str, features: Dict[str, Any], broker: str) -> Optional[Dict[str, Any]]:
        if not self.timing_config["strategies_enabled"].get("slingshot", True):
            return None

        # Rejection of extreme wick against prevailing higher timeframe trend
        price = features["last_close"]
        atr = self.stop_atr(features)
        if features["trend"] == "BULLISH" and features["rsi"] < 42:
            # Bullish trend pulling back deeply, potential spring / slingshot long
            return {
                "strategy": "SLINGSHOT_PULLBACK_REENTRY",
                "direction": "BUY",
                "entry_price": price,
                "stop_loss": round(features["recent_low"] - (0.3 * atr), 5),
                "take_profit": round(features["recent_high"] + (1.0 * atr), 5),
                "timing_mode": TimingMode.BAR_CLOSE,
                "confidence": 0.80,
                "reason": f"Slingshot pullback in bullish trend. Discounted entry near {price}.",
            }
        elif features["trend"] == "BEARISH" and features["rsi"] > 58:
            # Bearish trend rallying into resistance, slingshot short
            return {
                "strategy": "SLINGSHOT_PULLBACK_REENTRY",
                "direction": "SELL",
                "entry_price": price,
                "stop_loss": round(features["recent_high"] + (0.3 * atr), 5),
                "take_profit": round(features["recent_low"] - (1.0 * atr), 5),
                "timing_mode": TimingMode.BAR_CLOSE,
                "confidence": 0.80,
                "reason": f"Slingshot bounce into resistance in bearish trend. Entry near {price}.",
            }
        return None

    # ── Sizing Calculator ───────────────────────────────────────────────────
    def _calculate_size(self, symbol: str, entry_price: float, stop_loss: float, broker: str) -> Dict[str, Any]:
        """Compute exact lot / quantity sizing using pip-margin models."""
        equity = self._account_equity()
        risk_pct = self.timing_config.get("default_risk_pct", 0.5)
        risk_amount = (equity * risk_pct) / 100.0

        if broker == "ctrader":
            spec = ctrader_service.get_symbol_specification(symbol)
            pip_size = spec.get("pip_size", 0.0001)
            stop_pips = max(abs(entry_price - stop_loss) / pip_size, 5.0)

            # pip_value for 1.0 standard lot
            calc_1lot = ctrader_service.calculate_pip_margin(symbol, 1.0, entry_price, 100.0, "USD")
            pip_val_1lot = calc_1lot.get("pip_value", 10.0)

            # lots = risk_amount / (stop_pips * pip_val_1lot)
            lots = round(risk_amount / max(stop_pips * pip_val_1lot, 0.1), 2)
            max_lots = float(self.execution_config.get("max_ctrader_lots") or 0.10)
            if classify_symbol(symbol) == "metal":
                max_lots = min(max_lots, float(self.execution_config.get("max_metal_lots") or 0.01))
            lots = max(0.01, min(lots, max_lots if max_lots > 0 else lots, 10.0))

            calc_final = ctrader_service.calculate_pip_margin(symbol, lots, entry_price, 100.0, "USD")
            return {
                "lots": lots,
                "quantity": lots,
                "stop_pips": round(stop_pips, 1),
                "risk_usd": round(risk_amount, 2),
                "margin_required": calc_final.get("required_margin", 0.0),
                "pip_value": calc_final.get("pip_value", 0.0),
            }
        else:
            # Crypto sizing
            price_dist = max(abs(entry_price - stop_loss), entry_price * 0.005)
            qty = round(risk_amount / price_dist, 4) if price_dist > 0 else 0.001
            qty = max(0.001, min(qty, 100.0))
            return {
                "lots": qty,
                "quantity": qty,
                "stop_pips": 0,
                "risk_usd": round(risk_amount, 2),
                "margin_required": round((qty * entry_price) / 20.0, 2),  # assume 20x leverage
                "pip_value": 0.0,
            }

    # ── Market Scanning Execution ───────────────────────────────────────────
    async def scan_markets(self, universe: Optional[List[str]] = None, timeframe: str = "M5") -> List[Dict[str, Any]]:
        """Scan specified market universe and produce trade candidate signals."""
        if not universe:
            universe = [
                # Forex Majors
                "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
                # Forex Crosses
                "EURGBP", "EURJPY", "GBPJPY", "EURAUD", "GBPAUD", "NZDJPY", "CHFJPY", "AUDJPY", "CADJPY",
                # Metals
                "XAUUSD", "XAGUSD",
                # Crypto
                "BTCUSDT", "ETHUSDT", "SOLUSDT",
            ]

        candidates_generated = []
        now_ts = int(time.time())
        self.prune_candidates(now_ts)

        for sym in universe:
            try:
                asset_class = classify_symbol(sym)
                if asset_class == "metal" and not self.execution_config.get("include_metals"):
                    continue
                if self.execution_config.get("forex_only") and asset_class == "crypto":
                    continue
                broker = "binance_futures" if asset_class == "crypto" else "ctrader"

                # Ingest bars
                if broker == "ctrader":
                    bars = ctrader_service.get_trendbars(sym, timeframe, count=40)
                else:
                    bars = await binance_market_data.get_klines(
                        sym, interval=tf_to_binance_interval(timeframe), limit=40
                    )

                features = self._compute_features(bars)
                if not features:
                    logger.warning(
                        f"Skipping {sym}: only {len(bars or [])} bars returned, "
                        f"need {self.MIN_FEATURE_BARS}"
                    )
                    continue
                await self._attach_stop_atr(sym, broker, features)

                # Evaluate strategies in order of priority
                evaluators = [
                    self._evaluate_momentum,
                    self._evaluate_fade,
                    self._evaluate_straddle,
                    self._evaluate_slingshot,
                ]

                for ev in evaluators:
                    raw_signal = ev(sym, features, broker)
                    if raw_signal:
                        sl, tp = CTraderService.clamp_protective_prices(
                            sym,
                            raw_signal["entry_price"],
                            raw_signal.get("stop_loss"),
                            raw_signal.get("take_profit"),
                            direction=raw_signal.get("direction"),
                        )
                        raw_signal["stop_loss"] = sl
                        raw_signal["take_profit"] = tp
                        size_data = self._calculate_size(sym, raw_signal["entry_price"], raw_signal["stop_loss"], broker)

                        # Timing window parameters
                        timing_mode = raw_signal["timing_mode"]
                        if timing_mode == TimingMode.PRE_EVENT:
                            earliest = now_ts
                            latest = now_ts + (self.timing_config["pre_event_window_min"] * 60)
                        elif timing_mode == TimingMode.POST_REACTION:
                            earliest = now_ts + (self.timing_config["post_reaction_delay_min"] * 60)
                            latest = earliest + (self.timing_config["post_reaction_window_min"] * 60)
                        else:
                            earliest = now_ts
                            latest = now_ts + 900  # 15 minutes default validity

                        candidate = {
                            "id": f"sig-{uuid.uuid4().hex[:8]}",
                            "symbol": sym,
                            "broker": broker,
                            "strategy": raw_signal["strategy"],
                            "direction": raw_signal["direction"],
                            "entry_price": raw_signal["entry_price"],
                            "stop_loss": raw_signal["stop_loss"],
                            "take_profit": raw_signal["take_profit"],
                            "timing_mode": timing_mode,
                            "status": CandidateStatus.PENDING if earliest > now_ts else CandidateStatus.READY,
                            "confidence": raw_signal["confidence"],
                            "reason": raw_signal["reason"],
                            "features": features,
                            "sizing": size_data,
                            "earliest_exec_at": earliest,
                            "latest_exec_at": latest,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        }

                        self.candidates[candidate["id"]] = candidate
                        candidates_generated.append(candidate)
                        break  # 1 candidate per symbol per scan run

            except Exception as err:
                logger.warning(f"Error scanning {sym}: {err}")

        return candidates_generated

    # ── News & Macro Scanning Execution ─────────────────────────────────────
    async def scan_news_and_events(self, lookahead_minutes: int = 60) -> List[Dict[str, Any]]:
        """Correlate economic events and news sentiment to propose news-triggered trade setups."""
        from backend.routes.news import get_economic_calendar, get_news_feed, get_market_sentiment

        news_candidates = []
        try:
            cal_res = await get_economic_calendar()
            await get_news_feed()
            await get_market_sentiment()

            events = cal_res.get("events", [])
            high_impact = [e for e in events if e.get("impact") == "high"]

            # Check for major-currency catalysts. Unmapped currencies are skipped
            # instead of becoming a gold trade.
            self.prune_candidates()
            for ev in high_impact[:3]:
                curr = str(ev.get("currency") or "USD").upper()
                matched_sym = self.MACRO_CURRENCY_PAIRS.get(curr)
                if not matched_sym:
                    logger.info(
                        "Skipping macro event %s (%s): no FX pair mapping",
                        ev.get("event"),
                        curr,
                    )
                    continue
                event_key = f"{curr}:{(ev.get('event') or '').strip().lower()}"
                already = any(
                    c.get("strategy") == "MACRO_EVENT_POST_REACTION"
                    and c.get("symbol") == matched_sym
                    and (c.get("reason") or "").find(str(ev.get("event") or "")) >= 0
                    and c.get("status") in (CandidateStatus.PENDING, CandidateStatus.READY)
                    for c in self.candidates.values()
                )
                if already:
                    continue

                bars = ctrader_service.get_trendbars(matched_sym, "M5", count=30)
                features = self._compute_features(bars)
                if not features:
                    logger.warning(
                        f"Skipping macro candidate for {matched_sym}: insufficient bars"
                    )
                    continue
                await self._attach_stop_atr(matched_sym, "ctrader", features)
                direction = "BUY" if features["trend"] == "BULLISH" else "SELL"
                entry = features["last_close"]
                atr = self.stop_atr(features)
                # Protection must sit on the correct side of entry. A short with
                # a stop below and a target above is an inverted bracket that
                # never caps the loss.
                if direction == "BUY":
                    sl = round(entry - (1.2 * atr), 5)
                    tp = round(entry + (2.4 * atr), 5)
                else:
                    sl = round(entry + (1.2 * atr), 5)
                    tp = round(entry - (2.4 * atr), 5)
                sl, tp = CTraderService.clamp_protective_prices(
                    matched_sym, entry, sl, tp, direction=direction
                )
                size_data = self._calculate_size(matched_sym, entry, sl, "ctrader")

                now_ts = int(time.time())
                candidate = {
                    "id": f"sig-news-{uuid.uuid4().hex[:8]}",
                    "symbol": matched_sym,
                    "broker": "ctrader",
                    "strategy": "MACRO_EVENT_POST_REACTION",
                    "direction": direction,
                    "entry_price": entry,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "timing_mode": TimingMode.POST_REACTION,
                    "status": CandidateStatus.PENDING,
                    "confidence": 0.85,
                    "reason": f"High Impact Macro Event: '{ev.get('event')}' ({ev.get('currency')}). Post-reaction momentum window armed.",
                    "features": features,
                    "sizing": size_data,
                    "earliest_exec_at": now_ts + (self.timing_config["post_reaction_delay_min"] * 60),
                    "latest_exec_at": now_ts + (self.timing_config["post_reaction_window_min"] * 60) + 300,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                self.candidates[candidate["id"]] = candidate
                news_candidates.append(candidate)

        except Exception as e:
            logger.error(f"Error during scan_news_and_events: {e}")

        return news_candidates

    # ── Ready for Execution Queue ───────────────────────────────────────────
    def get_ready_signals(
        self,
        current_ts: Optional[int] = None,
        *,
        broker: Optional[str] = None,
        forex_only: bool = False,
        limit: Optional[int] = None,
        enforce_ctrader_position_cap: bool = True,
    ) -> List[Dict[str, Any]]:
        """Return signals whose timing window is currently open and valid."""
        if not current_ts:
            current_ts = int(time.time())

        ready = []
        for cid, cand in list(self.candidates.items()):
            # Check expiration
            if current_ts > cand["latest_exec_at"]:
                if cand["status"] in [CandidateStatus.PENDING, CandidateStatus.READY]:
                    cand["status"] = CandidateStatus.EXPIRED
                continue

            # Check timing window
            if cand["earliest_exec_at"] <= current_ts <= cand["latest_exec_at"]:
                if cand["status"] == CandidateStatus.PENDING:
                    cand["status"] = CandidateStatus.READY
                if cand["status"] == CandidateStatus.READY:
                    ready.append(cand)

        self.prune_candidates(current_ts)
        if broker:
            ready = [c for c in ready if c.get("broker") == broker.lower()]
        if forex_only or self.execution_config.get("forex_only"):
            ready = [c for c in ready if self._is_ctrader_forex_candidate(c)]
        cap = int(self.execution_config.get("max_same_base") or 0)
        if cap > 0:
            base_counts = self._open_ctrader_base_counts()
            limited: List[Dict[str, Any]] = []
            for cand in ready:
                base = self._symbol_base(str(cand.get("symbol") or ""))
                used = base_counts.get(base, 0)
                if used >= cap:
                    continue
                limited.append(cand)
                base_counts[base] = used + 1
            ready = limited

        ready.sort(key=lambda c: float(c.get("confidence") or 0), reverse=True)

        if self.execution_config.get("one_position_per_symbol"):
            open_syms = self._open_ctrader_symbols()
            ready = [c for c in ready if str(c.get("symbol", "")).upper() not in open_syms]
            seen: Set[str] = set()
            unique: List[Dict[str, Any]] = []
            for cand in ready:
                sym = str(cand.get("symbol", "")).upper()
                if not sym or sym in seen:
                    continue
                seen.add(sym)
                unique.append(cand)
            ready = unique

        if enforce_ctrader_position_cap:
            slots = self._ctrader_execution_slots_remaining()
            if slots <= 0:
                ready = []
            else:
                ready = ready[:slots]

        poll_limit = limit
        if poll_limit is None:
            poll_limit = self.execution_config.get("max_ready_per_poll")
        if poll_limit is not None:
            ready = ready[: max(0, int(poll_limit))]

        return ready

    async def _resolve_mark_price(self, symbol: str, broker: str, fallback: float = 0.0) -> float:
        """Fetch a live quote for paper market fills when no explicit price is set."""
        if fallback and fallback > 0:
            return float(fallback)
        sym = symbol.upper()
        try:
            if broker == "binance_futures":
                from backend.services.binance_futures_service import BinanceFuturesService
                bfs = BinanceFuturesService()
                ticker = await asyncio.to_thread(
                    bfs._get_client().futures_symbol_ticker, symbol=sym
                )
                return float(ticker.get("price", 0) or 0)
            tick = await binance_market_data.get_ticker_24h(sym)
            if tick and tick.get("lastPrice"):
                return float(tick["lastPrice"])
        except Exception as err:
            logger.warning(f"Could not resolve mark price for {sym}: {err}")
        return 0.0

    # ── Execution Dispatcher ────────────────────────────────────────────────
    async def execute_candidate(self, candidate_id: str, force: bool = False) -> Dict[str, Any]:
        """Dispatch candidate trade signal through smart unified broker router."""
        cand = self.candidates.get(candidate_id)
        if not cand:
            return {"success": False, "error": f"Candidate {candidate_id} not found."}

        now_ts = int(time.time())
        if not force:
            if now_ts < cand["earliest_exec_at"]:
                return {
                    "success": False,
                    "error": f"Timing window not yet open. Opens in {cand['earliest_exec_at'] - now_ts}s.",
                }
            if now_ts > cand["latest_exec_at"]:
                cand["status"] = CandidateStatus.EXPIRED
                return {"success": False, "error": "Timing window expired."}

        if self.execution_config.get("forex_only") and cand.get("broker") == "binance_futures":
            return {
                "success": False,
                "error": "Forex-only execution mode: crypto candidates are blocked.",
            }
        if cand.get("broker") == "ctrader":
            if self.execution_config.get("forex_only") and not self._is_ctrader_forex_candidate(cand):
                return {
                    "success": False,
                    "skipped": True,
                    "error": "Forex-only execution mode: candidate is not a cTrader FX pair.",
                }
            if not self._ctrader_execution_slot_available():
                return {
                    "success": False,
                    "error": (
                        f"Max open cTrader positions reached "
                        f"({self.execution_config.get('max_open_ctrader_positions', 10)})."
                    ),
                }
            if self.execution_config.get("one_position_per_symbol"):
                sym = str(cand.get("symbol", "")).upper()
                if sym in self._open_ctrader_symbols():
                    return {
                        "success": False,
                        "skipped": True,
                        "error": f"Already have an open cTrader position in {sym}.",
                    }
            if not self._same_base_slots_available(str(cand.get("symbol") or "")):
                base = self._symbol_base(str(cand.get("symbol") or ""))
                return {
                    "success": False,
                    "skipped": True,
                    "error": (
                        f"Same-base cap reached for {base} "
                        f"({self.execution_config.get('max_same_base', 2)} open)."
                    ),
                }

        # Route through appropriate broker engine
        try:
            qty = cand["sizing"]["lots"] if cand["broker"] == "ctrader" else cand["sizing"]["quantity"]
            if cand["broker"] == "ctrader":
                live_size = self._calculate_size(
                    str(cand.get("symbol") or ""),
                    float(cand.get("entry_price") or 0),
                    float(cand.get("stop_loss") or 0),
                    "ctrader",
                )
                cand["sizing"] = live_size
                qty = live_size["lots"]
                max_lots = float(self.execution_config.get("max_ctrader_lots") or 0)
                if classify_symbol(str(cand.get("symbol") or "")) == "metal":
                    metal_cap = float(self.execution_config.get("max_metal_lots") or 0.01)
                    max_lots = min(max_lots or metal_cap, metal_cap)
                if max_lots > 0:
                    qty = min(float(qty), max_lots)
            side = cand["direction"].upper()

            if cand["broker"] == "ctrader":
                connected = await asyncio.to_thread(ctrader_service.ensure_connected)
                if not connected and ctrader_service.has_credentials():
                    return {
                        "success": False,
                        "error": "cTrader is not connected; refused to simulate a live forex fill.",
                        "candidate_id": candidate_id,
                        "symbol": cand["symbol"],
                    }
                order_res = ctrader_service.place_order(
                    symbol=cand["symbol"],
                    direction=side,
                    quantity=qty,
                    price=cand.get("entry_price"),
                    stop_loss=cand.get("stop_loss"),
                    take_profit=cand.get("take_profit"),
                )
                status = (order_res or {}).get("status", "")
                err_text = str((order_res or {}).get("error") or "")
                success = status in ("ok", "filled", "sent")
                if status == "simulated" and not ctrader_service.has_credentials():
                    success = True
                order_id = order_res.get("order_id") if order_res else None
                msg = f"cTrader order {order_id or 'pending'} placed ({status or 'unknown'})"
                if not success and "MARKET_CLOSED" in err_text.upper():
                    cand["status"] = CandidateStatus.CANCELLED
                    cand["execution_result"] = {
                        "success": False,
                        "order_id": None,
                        "message": err_text,
                        "broker": "ctrader",
                    }
                    return {
                        "success": False,
                        "skipped": True,
                        "candidate_id": candidate_id,
                        "symbol": cand["symbol"],
                        "error": err_text,
                    }
            else:
                order_side = OrderSide.BUY if side == "BUY" else OrderSide.SELL
                fill_price = await self._resolve_mark_price(
                    cand["symbol"], cand["broker"], float(cand.get("entry_price") or 0)
                )
                ut = UnifiedTrading()
                order_req = UnifiedOrder(
                    symbol=cand["symbol"],
                    side=order_side,
                    order_type=OrderType.MARKET,
                    quantity=qty,
                    price=fill_price,
                    stop_loss=cand.get("stop_loss"),
                    take_profit=cand.get("take_profit"),
                )
                res = ut.place_order(order_req)
                success = res.success
                order_id = res.order_id
                msg = res.message

            cand["execution_result"] = {
                "success": success,
                "order_id": order_id,
                "message": msg,
                "broker": cand["broker"],
            }
            if success:
                cand["status"] = CandidateStatus.EXECUTED
                cand["executed_at"] = datetime.now(timezone.utc).isoformat()
                if cand["broker"] == "ctrader":
                    persist_ctrader_execution(
                        symbol=cand["symbol"],
                        direction=side,
                        quantity=qty,
                        entry_price=float(
                            (order_res or {}).get("price")
                            or (order_res or {}).get("entry_price")
                            or cand.get("entry_price")
                            or 0
                        ),
                        stop_loss=cand.get("stop_loss"),
                        take_profit=cand.get("take_profit"),
                        strategy=cand.get("strategy"),
                        order_id=order_id,
                        position_id=(order_res or {}).get("position_id"),
                        notes=msg,
                    )
            else:
                logger.warning(
                    f"Candidate {candidate_id} execution failed ({cand['broker']}): {msg}"
                )

            return {
                "success": success,
                "candidate_id": candidate_id,
                "symbol": cand["symbol"],
                "broker": cand["broker"],
                "order_result": {
                    "success": success,
                    "order_id": order_id,
                    "message": msg,
                },
            }

        except Exception as err:
            logger.error(f"Failed to execute candidate {candidate_id}: {err}")
            return {"success": False, "error": str(err)}


signal_candidate_engine = SignalCandidateEngine()
