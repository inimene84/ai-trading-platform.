"""
Mean Reversion Strategy — Bollinger Band + RSI extremes.

Logic:
  BUY  when: price touches lower BB AND RSI < 35 (oversold) AND price inside channel
  SELL when: price touches upper BB AND RSI > 65 (overbought)
  NEUTRAL: price in middle zone, no extreme

Best for: ranging/sideways markets, stocks, crypto altcoins.
Combine with ADX < 20 filter (avoid trending markets).
"""

import logging
import pandas as pd
from backend.strategies.base import BaseStrategy, StrategySignal
from backend.strategies.trend_following import adx

logger = logging.getLogger(__name__)


class MeanReversionStrategy(BaseStrategy):
    name = "mean_reversion"

    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 2.5,
        rsi_period: int = 14,
        rsi_oversold: float = 25.0,
        rsi_overbought: float = 75.0,
    ):
        self.bb_period      = bb_period
        self.bb_std         = bb_std
        self.rsi_period     = rsi_period
        self.rsi_oversold   = rsi_oversold
        self.rsi_overbought = rsi_overbought

    def _rsi(self, closes: list[float]) -> float:
        s = pd.Series(closes)
        d = s.diff()
        gain = d.clip(lower=0).rolling(self.rsi_period).mean()
        loss = (-d.clip(upper=0)).rolling(self.rsi_period).mean()
        rs   = gain / (loss + 1e-9)
        rsi  = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1])

    def _bollinger(self, closes: list[float]):
        s      = pd.Series(closes)
        mid    = s.rolling(self.bb_period).mean()
        std    = s.rolling(self.bb_period).std()
        upper  = mid + self.bb_std * std
        lower  = mid - self.bb_std * std
        pct_b  = (closes[-1] - float(lower.iloc[-1])) / (float(upper.iloc[-1]) - float(lower.iloc[-1]) + 1e-9)
        width  = float(upper.iloc[-1]) - float(lower.iloc[-1])
        return float(upper.iloc[-1]), float(mid.iloc[-1]), float(lower.iloc[-1]), pct_b, width

    def generate_signal(self, symbol: str, bars: list[dict], **kwargs) -> StrategySignal:
        if len(bars) < self.bb_period + self.rsi_period:
            return StrategySignal(symbol=symbol, signal="NEUTRAL", confidence=0.0,
                                  strategy=self.name, reasoning="Insufficient bars")

        closes = self._closes(bars)
        rsi    = self._rsi(closes)
        upper, mid, lower, pct_b, width = self._bollinger(closes)
        price  = closes[-1]

        # Squeeze filter: very tight bands = avoid (no momentum)
        avg_close = float(pd.Series(closes[-50:]).mean())
        band_pct  = width / (avg_close + 1e-9)
        if band_pct < 0.002:  # bands < 0.2% of price
            return StrategySignal(symbol=symbol, signal="NEUTRAL", confidence=0.1,
                                  strategy=self.name, reasoning="BB squeeze — waiting")

        # ADX filter: skip mean reversion in trending markets
        highs = self._highs(bars)
        lows  = self._lows(bars)
        adx_val, _, _ = adx(highs, lows, closes)
        if adx_val > 25:
            return StrategySignal(symbol=symbol, signal="NEUTRAL", confidence=0.1,
                                  strategy=self.name, reasoning=f"Trending market (ADX={adx_val:.1f}) — skip mean reversion")

        confidence = 0.0
        signal     = "NEUTRAL"
        reasons    = []

        if pct_b < 0.1 and rsi < self.rsi_oversold:
            # Oversold — mean reversion BUY
            confidence = 0.4
            if pct_b < 0.0:    # price outside lower band
                confidence += 0.3; reasons.append(f"below LBand ({price:.5f}<{lower:.5f})")
            if rsi < 30:       # extremely oversold
                confidence += 0.2; reasons.append(f"RSI={rsi:.1f} extreme")
            elif rsi < self.rsi_oversold:
                confidence += 0.1; reasons.append(f"RSI={rsi:.1f} oversold")
            # Volume surge (capitulation candle)
            vols = self._volumes(bars)
            if vols[-1] > pd.Series(vols[-20:]).mean() * 1.5:
                confidence += 0.1; reasons.append("vol surge")
            signal = "BUY"

        elif pct_b > 0.9 and rsi > self.rsi_overbought:
            # Overbought — mean reversion SELL
            confidence = 0.4
            if pct_b > 1.0:   # price outside upper band
                confidence += 0.3; reasons.append(f"above UBand ({price:.5f}>{upper:.5f})")
            if rsi > 70:
                confidence += 0.2; reasons.append(f"RSI={rsi:.1f} extreme")
            elif rsi > self.rsi_overbought:
                confidence += 0.1; reasons.append(f"RSI={rsi:.1f} overbought")
            vols = self._volumes(bars)
            if vols[-1] > pd.Series(vols[-20:]).mean() * 1.5:
                confidence += 0.1; reasons.append("vol surge")
            signal = "SELL"

        # ATR-based stops for proper risk:reward
        atr = self._atr(bars)
        if signal == "BUY":
            stop_loss   = price - atr * 2.0
            take_profit = price + atr * 4.0  # 2:1 RR minimum
        else:
            stop_loss   = price + atr * 2.0
            take_profit = price - atr * 4.0

        return StrategySignal(
            symbol=symbol, signal=signal, confidence=min(confidence, 1.0),
            entry_price=price, stop_loss=stop_loss, take_profit=take_profit,
            strategy=self.name, reasoning=" | ".join(reasons) or f"RSI={rsi:.1f} pct_b={pct_b:.2f}",
        )

    def _atr(self, bars, period=14):
        h = pd.Series(self._highs(bars))
        l = pd.Series(self._lows(bars))
        c = pd.Series(self._closes(bars))
        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        return float(tr.ewm(span=period, adjust=False).mean().iloc[-1])
