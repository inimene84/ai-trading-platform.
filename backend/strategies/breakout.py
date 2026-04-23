"""
Breakout Strategy — Donchian Channel + ATR + Volume confirmation.

Logic:
  BUY  when: price breaks above N-period high AND volume spikes AND ATR expands
  SELL when: price breaks below N-period low AND volume spikes AND ATR expands
  NEUTRAL: price inside channel, no breakout

Best for: crypto altcoins, volatile forex pairs, after periods of consolidation.

Entry: on confirmed close above/below channel
Stop:  ATR × 1.5 below entry
Target: channel width projected from breakout point (1:2 RR minimum)
"""

import logging
import pandas as pd
from backend.strategies.base import BaseStrategy, StrategySignal

logger = logging.getLogger(__name__)


class BreakoutStrategy(BaseStrategy):
    name = "breakout"

    def __init__(
        self,
        channel_period: int = 50,    # Donchian channel lookback
        atr_period: int = 14,
        atr_multiplier: float = 2.5, # stop = entry ± ATR × multiplier
        volume_factor: float = 1.8,  # breakout needs volume > avg × this
        min_channel_width_pct: float = 0.01,  # filter tiny ranges
    ):
        self.channel_period        = channel_period
        self.atr_period            = atr_period
        self.atr_multiplier        = atr_multiplier
        self.volume_factor         = volume_factor
        self.min_channel_width_pct = min_channel_width_pct

    def _atr(self, highs, lows, closes) -> float:
        h = pd.Series(highs); l = pd.Series(lows); c = pd.Series(closes)
        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        return float(tr.ewm(span=self.atr_period, adjust=False).mean().iloc[-1])

    def generate_signal(self, symbol: str, bars: list[dict], **kwargs) -> StrategySignal:
        if len(bars) < self.channel_period + 5:
            return StrategySignal(symbol=symbol, signal="NEUTRAL", confidence=0.0,
                                  strategy=self.name, reasoning="Insufficient bars")

        closes  = self._closes(bars)
        highs   = self._highs(bars)
        lows    = self._lows(bars)
        volumes = self._volumes(bars)

        # Donchian channel: high/low of previous N bars (excluding last bar)
        lookback_highs = highs[-(self.channel_period + 1):-1]
        lookback_lows  = lows[-(self.channel_period + 1):-1]
        channel_high   = max(lookback_highs)
        channel_low    = min(lookback_lows)
        channel_width  = channel_high - channel_low

        price   = closes[-1]
        avg_vol = float(pd.Series(volumes[-20:]).mean()) if volumes else 0
        vol_ok  = volumes[-1] > avg_vol * self.volume_factor if avg_vol > 0 else False

        atr = self._atr(highs, lows, closes)

        # Higher-timeframe trend filter
        trend = self._dominant_trend(closes)
        # Only take breakouts in the direction of the dominant trend
        if price > channel_high and trend == "DOWN":
            return StrategySignal(symbol=symbol, signal="NEUTRAL", confidence=0.1,
                                  strategy=self.name, reasoning="Bullish breakout against downtrend")
        if price < channel_low and trend == "UP":
            return StrategySignal(symbol=symbol, signal="NEUTRAL", confidence=0.1,
                                  strategy=self.name, reasoning="Bearish breakdown against uptrend")


        # Channel width filter — skip tiny ranges (noise)
        if channel_width / (price + 1e-9) < self.min_channel_width_pct:
            return StrategySignal(symbol=symbol, signal="NEUTRAL", confidence=0.1,
                                  strategy=self.name, reasoning="Consolidating — channel too narrow")

        signal     = "NEUTRAL"
        confidence = 0.0
        reasons    = []
        stop_loss  = None
        take_profit = None

        # ATR expansion check: current ATR > average ATR
        s_h = pd.Series(highs); s_l = pd.Series(lows); s_c = pd.Series(closes)
        tr  = pd.concat([s_h - s_l, (s_h - s_c.shift()).abs(), (s_l - s_c.shift()).abs()], axis=1).max(axis=1)
        atr_avg = float(tr.rolling(20).mean().iloc[-1])
        atr_expanding = atr > atr_avg * 1.05

        if price > channel_high:
            # Bullish breakout
            confidence = 0.4
            reasons.append(f"Broke {channel_high:.5f} high ({self.channel_period}p)")
            if vol_ok:
                confidence += 0.25; reasons.append(f"vol×{volumes[-1]/avg_vol:.1f}")
            if atr_expanding:
                confidence += 0.2;  reasons.append("ATR expanding")
            # Two consecutive closes above? Stronger confirmation
            if len(closes) >= 2 and closes[-2] > channel_high:
                confidence += 0.15; reasons.append("2nd close above")
            stop_loss   = price - atr * self.atr_multiplier
            take_profit = price + channel_width * 2.0  # project channel width
            signal = "BUY"

        elif price < channel_low:
            # Bearish breakdown
            confidence = 0.4
            reasons.append(f"Broke {channel_low:.5f} low ({self.channel_period}p)")
            if vol_ok:
                confidence += 0.25; reasons.append(f"vol×{volumes[-1]/avg_vol:.1f}")
            if atr_expanding:
                confidence += 0.2;  reasons.append("ATR expanding")
            if len(closes) >= 2 and closes[-2] < channel_low:
                confidence += 0.15; reasons.append("2nd close below")
            stop_loss   = price + atr * self.atr_multiplier
            take_profit = price - channel_width * 2.0
            signal = "SELL"

        return StrategySignal(
            symbol=symbol, signal=signal, confidence=min(confidence, 1.0),
            entry_price=price, stop_loss=stop_loss, take_profit=take_profit,
            strategy=self.name,
            reasoning=" | ".join(reasons) or f"inside channel [{channel_low:.5f}-{channel_high:.5f}]",
        )
