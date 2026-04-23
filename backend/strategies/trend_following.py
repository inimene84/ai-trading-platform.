"""
Trend Following Strategy — EMA crossover + MACD + ADX filter.

Logic:
  BUY  when: EMA20 > EMA50 AND MACD hist > 0 AND ADX > 20 (trending)
  SELL when: EMA20 < EMA50 AND MACD hist < 0 AND ADX > 20
  NEUTRAL: no clear trend (ADX < 20) or mixed signals

Confidence scoring:
  +0.3 EMA cross confirmed
  +0.2 MACD direction matches
  +0.2 ADX > 25 (strong trend)
  +0.15 price above/below 200 EMA
  +0.15 volume confirmation
"""

import logging
import pandas as pd
from backend.strategies.base import BaseStrategy, StrategySignal

logger = logging.getLogger(__name__)


def ema(series: list[float], period: int) -> list[float]:
    s = pd.Series(series)
    return s.ewm(span=period, adjust=False).mean().tolist()


def adx(highs, lows, closes, period=14):
    """Average Directional Index."""
    h = pd.Series(highs)
    l = pd.Series(lows)
    c = pd.Series(closes)
    up_move   = h.diff()
    down_move = -l.diff()
    plus_dm   = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm  = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_s     = tr.ewm(span=period, adjust=False).mean()
    plus_di   = 100 * plus_dm.ewm(span=period, adjust=False).mean() / (atr_s + 1e-9)
    minus_di  = 100 * minus_dm.ewm(span=period, adjust=False).mean() / (atr_s + 1e-9)
    dx        = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9))
    adx_val   = dx.ewm(span=period, adjust=False).mean()
    return float(adx_val.iloc[-1]), float(plus_di.iloc[-1]), float(minus_di.iloc[-1])


class TrendFollowingStrategy(BaseStrategy):
    name = "trend_following"

    def __init__(self, fast_ema=50, slow_ema=100, long_ema=200, adx_threshold=25):
        self.fast_ema    = fast_ema
        self.slow_ema    = slow_ema
        self.long_ema    = long_ema
        self.adx_thresh  = adx_threshold

    def generate_signal(self, symbol: str, bars: list[dict], **kwargs) -> StrategySignal:
        if len(bars) < self.slow_ema + 10:
            return StrategySignal(symbol=symbol, signal="NEUTRAL", confidence=0.0,
                                  strategy=self.name, reasoning="Insufficient bars")

        closes  = self._closes(bars)
        highs   = self._highs(bars)
        lows    = self._lows(bars)
        volumes = self._volumes(bars)

        # ATR for SL/TP
        h_s = pd.Series(highs); l_s = pd.Series(lows); c_s = pd.Series(closes)
        tr = pd.concat([h_s - l_s, (h_s - c_s.shift()).abs(), (l_s - c_s.shift()).abs()], axis=1).max(axis=1)
        atr = float(tr.ewm(span=14, adjust=False).mean().iloc[-1])

        fast    = ema(closes, self.fast_ema)
        slow    = ema(closes, self.slow_ema)
        long200 = ema(closes, min(self.long_ema, len(closes) - 1))

        # MACD
        macd_line   = pd.Series(fast) - pd.Series(slow)
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist   = float((macd_line - signal_line).iloc[-1])

        # ADX
        adx_val, plus_di, minus_di = adx(highs, lows, closes)

        # Volume confirmation
        avg_vol = float(pd.Series(volumes[-20:]).mean())
        vol_ok  = volumes[-1] > avg_vol * 1.1 if avg_vol > 0 else False

        price      = closes[-1]
        fast_cur   = fast[-1]
        slow_cur   = slow[-1]
        long_cur   = long200[-1]
        fast_prev  = fast[-2]
        slow_prev  = slow[-2]

        # Determine direction
        ema_bullish = fast_cur > slow_cur
        ema_bearish = fast_cur < slow_cur
        ema_cross   = (fast_cur > slow_cur) != (fast_prev > slow_prev)  # fresh cross
        above_200   = price > long_cur

        # Higher-timeframe trend filter
        trend = self._dominant_trend(closes)
        if trend == "DOWN" and ema_bullish:
            return StrategySignal(symbol=symbol, signal="NEUTRAL", confidence=0.1,
                                  strategy=self.name, reasoning="Against dominant downtrend")
        if trend == "UP" and ema_bearish:
            return StrategySignal(symbol=symbol, signal="NEUTRAL", confidence=0.1,
                                  strategy=self.name, reasoning="Against dominant uptrend")

        trending    = adx_val > self.adx_thresh

        confidence = 0.0
        reasons    = []

        if ema_bullish:
            confidence += 0.3
            reasons.append(f"EMA{self.fast_ema}>{self.slow_ema}")
        if macd_hist > 0:
            confidence += 0.2
            reasons.append("MACD+")
        if adx_val > 25:
            confidence += 0.2
            reasons.append(f"ADX={adx_val:.1f}")
        elif adx_val > self.adx_thresh:
            confidence += 0.1
        if above_200:
            confidence += 0.15
            reasons.append("above200EMA")
        if vol_ok:
            confidence += 0.15
            reasons.append("vol+")
        if ema_cross:
            confidence *= 1.1  # bonus for fresh cross

        if ema_bullish and trending and confidence >= 0.45:
            signal = "BUY"
        elif ema_bearish:
            # Reverse scoring for SELL
            sell_conf = 0.0
            sell_reasons = []
            if fast_cur < slow_cur:
                sell_conf += 0.3; sell_reasons.append(f"EMA{self.fast_ema}<{self.slow_ema}")
            if macd_hist < 0:
                sell_conf += 0.2; sell_reasons.append("MACD-")
            if adx_val > 25:
                sell_conf += 0.2; sell_reasons.append(f"ADX={adx_val:.1f}")
            elif adx_val > self.adx_thresh:
                sell_conf += 0.1
            if not above_200:
                sell_conf += 0.15; sell_reasons.append("below200EMA")
            if vol_ok:
                sell_conf += 0.15; sell_reasons.append("vol+")
            if trending and sell_conf >= 0.45:
                return StrategySignal(
                    symbol=symbol, signal="SELL", confidence=min(sell_conf, 1.0),
                    entry_price=price, stop_loss=price + atr * 2.5, take_profit=price - atr * 5.0,
                    strategy=self.name, reasoning=" | ".join(sell_reasons),
                )
            signal = "NEUTRAL"
            confidence = max(sell_conf, 0.05)
        else:
            signal = "NEUTRAL"
            confidence = max(confidence, 0.05)

        return StrategySignal(
            symbol=symbol, signal=signal, confidence=min(confidence, 1.0),
            entry_price=price, stop_loss=price - atr * 2.5 if signal == "BUY" else None,
            take_profit=price + atr * 5.0 if signal == "BUY" else None,
            strategy=self.name, reasoning=" | ".join(reasons),
        )
