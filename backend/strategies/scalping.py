"""
Scalping Strategy — fast EMA ribbon + VWAP + order flow imbalance.

Designed for 1m–5m bars on liquid pairs (BTC/USDT, EURUSD, etc.)
Very short holding time — targets 3–8 pips/ticks profit.

Logic:
  BUY  when: price > VWAP AND EMA5 > EMA8 > EMA13 (ribbon aligned bullish)
             AND momentum candle (body > 60% of range)
  SELL when: opposite
  NEUTRAL: choppy or EMA ribbon not aligned

Risk: tight ATR-based stop (0.8× ATR), take profit 1.5× ATR.
"""

import logging
import pandas as pd
from backend.strategies.base import BaseStrategy, StrategySignal

logger = logging.getLogger(__name__)


def _vwap(bars: list[dict]) -> float:
    """Volume Weighted Average Price for the session."""
    typical = [(b["high"] + b["low"] + b["close"]) / 3 for b in bars]
    vols    = [b.get("volume", 1) for b in bars]
    total_vol = sum(vols)
    if total_vol == 0:
        return bars[-1]["close"]
    return sum(t * v for t, v in zip(typical, vols)) / total_vol


class ScalpingStrategy(BaseStrategy):
    name = "scalping"

    def __init__(
        self,
        ema_fast: int = 5,
        ema_mid: int = 8,
        ema_slow: int = 13,
        atr_stop_mult: float = 1.0,
        atr_tp_mult: float = 2.0,
        min_body_pct: float = 0.35,  # candle body must be > 35% of total range
    ):
        self.ema_fast      = ema_fast
        self.ema_mid       = ema_mid
        self.ema_slow      = ema_slow
        self.atr_stop_mult = atr_stop_mult
        self.atr_tp_mult   = atr_tp_mult
        self.min_body_pct  = min_body_pct

    def generate_signal(self, symbol: str, bars: list[dict], **kwargs) -> StrategySignal:
        if len(bars) < 30:
            return StrategySignal(symbol=symbol, signal="NEUTRAL", confidence=0.0,
                                  strategy=self.name, reasoning="Insufficient bars")

        closes = self._closes(bars)
        highs  = self._highs(bars)
        lows   = self._lows(bars)

        # EMA ribbon
        s  = pd.Series(closes)
        e5 = float(s.ewm(span=self.ema_fast, adjust=False).mean().iloc[-1])
        e8 = float(s.ewm(span=self.ema_mid,  adjust=False).mean().iloc[-1])
        e13= float(s.ewm(span=self.ema_slow, adjust=False).mean().iloc[-1])

        # ATR
        h = pd.Series(highs)
        lows_s = pd.Series(lows)
        c = pd.Series(closes)
        tr  = pd.concat([h - lows_s, (h - c.shift()).abs(), (lows_s - c.shift()).abs()], axis=1).max(axis=1)
        atr = float(tr.ewm(span=14, adjust=False).mean().iloc[-1])

        # VWAP
        vwap = _vwap(bars[-50:])  # intraday VWAP using last 50 bars

        # Last candle body analysis
        last = bars[-1]
        candle_range  = last["high"] - last["low"]
        candle_body   = abs(last["close"] - last["open"])
        body_pct      = (candle_body / candle_range) if candle_range > 0 else 0
        bullish_candle = last["close"] > last["open"]
        bearish_candle = last["close"] < last["open"]
        strong_candle  = body_pct >= self.min_body_pct

        price = closes[-1]

        # EMA ribbon alignment check
        bull_ribbon = e5 > e8 > e13
        bear_ribbon = e5 < e8 < e13

        confidence = 0.0
        signal     = "NEUTRAL"
        reasons    = []

        if bull_ribbon and price > vwap and bullish_candle:
            confidence = 0.35
            reasons.append(f"EMA{self.ema_fast}>{self.ema_mid}>{self.ema_slow} ribbon")
            reasons.append(f"price>{vwap:.5f} VWAP")
            if strong_candle:
                confidence += 0.3
                reasons.append(f"strong bull candle ({body_pct:.0%})")
            # Momentum: last 3 closes trending up
            if len(closes) >= 3 and closes[-1] > closes[-2] > closes[-3]:
                confidence += 0.2
                reasons.append("3-bar momentum")
            # Tight spread (scalping needs low spread)
            signal = "BUY"
            stop_loss   = price - atr * self.atr_stop_mult
            take_profit = price + atr * self.atr_tp_mult

        elif bear_ribbon and price < vwap and bearish_candle:
            confidence = 0.35
            reasons.append(f"EMA{self.ema_fast}<{self.ema_mid}<{self.ema_slow} ribbon")
            reasons.append(f"price<{vwap:.5f} VWAP")
            if strong_candle:
                confidence += 0.3
                reasons.append(f"strong bear candle ({body_pct:.0%})")
            if len(closes) >= 3 and closes[-1] < closes[-2] < closes[-3]:
                confidence += 0.2
                reasons.append("3-bar momentum")
            signal = "SELL"
            stop_loss   = price + atr * self.atr_stop_mult
            take_profit = price - atr * self.atr_tp_mult

        else:
            return StrategySignal(symbol=symbol, signal="NEUTRAL", confidence=0.1,
                                  strategy=self.name,
                                  reasoning=f"Ribbon not aligned (EMA5={e5:.5f} EMA8={e8:.5f} EMA13={e13:.5f})")

        return StrategySignal(
            symbol=symbol, signal=signal, confidence=min(confidence, 1.0),
            entry_price=price, stop_loss=stop_loss, take_profit=take_profit,
            strategy=self.name, reasoning=" | ".join(reasons),
        )
