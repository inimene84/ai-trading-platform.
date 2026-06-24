"""
Market Regime Detector

Classifies the current market into one of four regimes:
  TRENDING   - Strong directional move (ADX high, price riding EMA)
  RANGING    - Sideways / mean-reverting market (ADX low, BB squeeze)
  VOLATILE   - High volatility, large ATR spikes, erratic price action
  BREAKOUT   - Price breaking out of consolidation with volume expansion

Each regime maps to optimal strategy weights for the CombinedStrategy.

Usage:
    detector = MarketRegimeDetector()
    regime   = detector.detect(bars)
    weights  = detector.get_weights(regime)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import pandas as pd

logger = logging.getLogger(__name__)

Regime = Literal["TRENDING", "RANGING", "VOLATILE", "BREAKOUT"]

# ── Regime → strategy weights ─────────────────────────────────────────────────
# Weights must sum to 1.0
REGIME_WEIGHTS: dict[Regime, dict[str, float]] = {
    "TRENDING": {
        "trend_following": 0.60,
        "mean_reversion":  0.10,
        "breakout":        0.30,
    },
    "RANGING": {
        "trend_following": 0.15,
        "mean_reversion":  0.60,
        "breakout":        0.25,
    },
    "VOLATILE": {
        "trend_following": 0.20,
        "mean_reversion":  0.15,
        "breakout":        0.65,
    },
    "BREAKOUT": {
        "trend_following": 0.25,
        "mean_reversion":  0.10,
        "breakout":        0.65,
    },
}

# In RANGING regime, block trend signals unless they're very strong
REGIME_TREND_BLOCK: dict[Regime, float] = {
    "TRENDING":  0.40,   # allow trend signals above this threshold
    "RANGING":   0.80,   # require very strong trend signal to override in ranging market
    "VOLATILE":  0.60,   # moderate block
    "BREAKOUT":  0.50,   # moderate block
}


@dataclass
class RegimeResult:
    regime: Regime
    confidence: float          # 0.0 - 1.0
    adx: float
    bb_width_ratio: float      # BB width / price (normalized)
    atr_ratio: float           # ATR / price (normalized volatility)
    price_vs_ema: float        # price vs EMA200 distance (normalised)
    reasoning: str

    def weights(self) -> dict[str, float]:
        return REGIME_WEIGHTS[self.regime]

    def trend_block_threshold(self) -> float:
        return REGIME_TREND_BLOCK[self.regime]


class MarketRegimeDetector:
    """
    Detects market regime using multiple indicators:
      - ADX  (trend strength)
      - Bollinger Band width ratio  (consolidation vs expansion)
      - ATR ratio  (volatility relative to price)
      - Price position vs EMA200  (trend context)
      - Rate of change of ATR  (volatility acceleration = breakout candidate)
    """

    def __init__(
        self,
        adx_period: int = 14,
        bb_period: int = 20,
        atr_period: int = 14,
        ema_long: int = 200,
        lookback: int = 5,           # bars to smooth regime transitions
    ):
        self.adx_period = adx_period
        self.bb_period  = bb_period
        self.atr_period = atr_period
        self.ema_long   = ema_long
        self.lookback   = lookback
        self._history: list[Regime] = []

    # ─────────────────────────────────────────────────────────────────────────
    def detect(self, bars: list[dict]) -> RegimeResult:
        """Run regime detection on bar data. Returns RegimeResult."""
        if len(bars) < max(self.ema_long, self.bb_period, 50):
            # Not enough data — assume ranging (safest default)
            return RegimeResult(
                regime="RANGING", confidence=0.4,
                adx=0.0, bb_width_ratio=0.0, atr_ratio=0.0, price_vs_ema=0.0,
                reasoning="Insufficient bars for regime detection, defaulting to RANGING",
            )

        closes  = pd.Series([b["close"]  for b in bars], dtype=float)
        highs   = pd.Series([b["high"]   for b in bars], dtype=float)
        lows    = pd.Series([b["low"]    for b in bars], dtype=float)
        volumes = pd.Series([b.get("volume", 0) for b in bars], dtype=float)

        price = float(closes.iloc[-1])

        # ── ADX ───────────────────────────────────────────────────────────────
        adx_val, plus_di, minus_di = self._calc_adx(highs, lows, closes)

        # ── ATR ───────────────────────────────────────────────────────────────
        atr_series = self._calc_atr(highs, lows, closes)
        atr_current = float(atr_series.iloc[-1])
        atr_ratio   = atr_current / price if price > 0 else 0.0

        # ATR acceleration: is volatility expanding recently?
        atr_mean_recent = float(atr_series.iloc[-5:].mean())
        atr_mean_prior  = float(atr_series.iloc[-20:-5].mean()) if len(atr_series) >= 20 else atr_mean_recent
        atr_acceleration = (atr_mean_recent / atr_mean_prior) - 1.0 if atr_mean_prior > 0 else 0.0

        # ── Bollinger Bands ───────────────────────────────────────────────────
        bb_mid   = closes.rolling(self.bb_period).mean()
        bb_std   = closes.rolling(self.bb_period).std()
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std
        bb_width = float((bb_upper.iloc[-1] - bb_lower.iloc[-1]) / (bb_mid.iloc[-1] + 1e-9))

        # BB width percentile vs last 50 bars (is it wide or narrow?)
        bb_widths_history = ((bb_upper - bb_lower) / (bb_mid + 1e-9)).iloc[-50:]
        bb_pct = float((bb_widths_history < bb_width).mean())  # 0=very narrow, 1=very wide

        # ── EMA200 distance ───────────────────────────────────────────────────
        ema200     = float(closes.ewm(span=self.ema_long, adjust=False).mean().iloc[-1])
        price_vs_ema = (price - ema200) / ema200 if ema200 > 0 else 0.0

        # ── Price in BB extremes (mean reversion trigger) ─────────────────────
        bb_position = (price - float(bb_lower.iloc[-1])) / (
            float(bb_upper.iloc[-1]) - float(bb_lower.iloc[-1]) + 1e-9
        )  # 0=at lower band, 1=at upper band

        # ── Volume confirmation ───────────────────────────────────────────────
        vol_sma20  = float(volumes.rolling(20).mean().iloc[-1])
        vol_recent = float(volumes.iloc[-3:].mean())
        vol_surge  = (vol_recent / vol_sma20) if vol_sma20 > 0 else 1.0

        # ── REGIME CLASSIFICATION ─────────────────────────────────────────────
        regime, confidence, reasoning = self._classify(
            adx_val, atr_ratio, atr_acceleration, bb_width, bb_pct,
            bb_position, price_vs_ema, vol_surge,
        )

        # ── Smoothing: prevent rapid flip-flop ────────────────────────────────
        self._history.append(regime)
        if len(self._history) > self.lookback:
            self._history = self._history[-self.lookback:]

        if len(self._history) >= 3:
            # If last 2 bars were different regime, reduce confidence of new regime
            prev_two = self._history[-3:-1]
            if prev_two[0] != regime or prev_two[1] != regime:
                confidence = max(confidence * 0.75, 0.30)
                reasoning += " [smoothed: recent regime transition]"

        result = RegimeResult(
            regime=regime,
            confidence=confidence,
            adx=adx_val,
            bb_width_ratio=bb_width,
            atr_ratio=atr_ratio,
            price_vs_ema=price_vs_ema,
            reasoning=reasoning,
        )

        logger.info(
            f"  RegimeDetector: {regime} (conf={confidence:.2f}) "
            f"ADX={adx_val:.1f} BB={bb_width:.3f} ATR%={atr_ratio*100:.2f}% "
            f"vol_surge={vol_surge:.2f}"
        )
        return result

    # ─────────────────────────────────────────────────────────────────────────
    def _classify(
        self,
        adx: float,
        atr_ratio: float,
        atr_accel: float,
        bb_width: float,
        bb_pct: float,
        bb_pos: float,
        price_vs_ema: float,
        vol_surge: float,
    ) -> tuple[Regime, float, str]:
        """
        Decision tree for regime classification.
        Returns (regime, confidence, reasoning).
        """

        # ── 1. VOLATILE: extreme ATR, wide BB, erratic ─────────────────────
        if atr_ratio > 0.035 and bb_pct > 0.80:
            conf = min(0.50 + (atr_ratio - 0.035) * 10 + (bb_pct - 0.80) * 0.5, 0.95)
            return "VOLATILE", conf, (
                f"VOLATILE: ATR%={atr_ratio*100:.2f}% (>3.5%), BB_pct={bb_pct:.2f} (>0.80)"
            )

        # ── 2. BREAKOUT: BB narrow then expanding + volume surge ───────────
        if atr_accel > 0.25 and vol_surge > 1.5 and bb_pct > 0.60:
            conf = min(0.50 + atr_accel * 0.5 + (vol_surge - 1.5) * 0.1, 0.90)
            return "BREAKOUT", conf, (
                f"BB_pct={bb_pct:.2f}"
            )

        # ── 3. TRENDING: ADX high, price far from EMA ──────────────────────
        if adx >= 25:
            conf = min(0.45 + (adx - 25) * 0.015, 0.95)
            direction = "bullish" if price_vs_ema > 0 else "bearish"
            return "TRENDING", conf, (
                f"TRENDING ({direction}): ADX={adx:.1f} (>=25), "
                f"price_vs_EMA200={price_vs_ema*100:.1f}%"
            )

        # ── 4. RANGING: ADX low, narrow BB, price oscillating ─────────────
        if adx < 20:
            conf = min(0.40 + (20 - adx) * 0.02 + (0.5 - bb_pct) * 0.3, 0.85)
            conf = max(conf, 0.35)
            return "RANGING", conf, (
                f"RANGING: ADX={adx:.1f} (<20), BB_pct={bb_pct:.2f}, "
                f"BB_pos={bb_pos:.2f}"
            )

        # ── 5. Transitional (ADX 20-25): lean based on secondary signals ──
        if bb_pct < 0.35:
            return "RANGING", 0.45, (
                f"TRANSITIONAL→RANGING: ADX={adx:.1f}, narrow BB ({bb_pct:.2f})"
            )
        if atr_accel > 0.10:
            return "BREAKOUT", 0.45, (
                f"TRANSITIONAL→BREAKOUT: ADX={adx:.1f}, ATR_accel={atr_accel:.2f}"
            )
        return "TRENDING", 0.40, (
            f"TRANSITIONAL: ADX={adx:.1f}, defaulting to TRENDING"
        )

    # ─────────────────────────────────────────────────────────────────────────
    def _calc_adx(
        self,
        highs: pd.Series,
        lows: pd.Series,
        closes: pd.Series,
    ) -> tuple[float, float, float]:
        """Returns (ADX, +DI, -DI)."""
        period = self.adx_period
        up_move   = highs.diff()
        down_move = -lows.diff()
        plus_dm   = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
        minus_dm  = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
        tr = pd.concat(
            [highs - lows,
             (highs - closes.shift()).abs(),
             (lows  - closes.shift()).abs()],
            axis=1,
        ).max(axis=1)
        atr_s    = tr.ewm(span=period, adjust=False).mean()
        plus_di  = 100 * plus_dm.ewm(span=period, adjust=False).mean() / (atr_s + 1e-9)
        minus_di = 100 * minus_dm.ewm(span=period, adjust=False).mean() / (atr_s + 1e-9)
        dx       = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9))
        adx_val  = dx.ewm(span=period, adjust=False).mean()
        return (
            float(adx_val.iloc[-1]),
            float(plus_di.iloc[-1]),
            float(minus_di.iloc[-1]),
        )

    def _calc_atr(self, highs: pd.Series, lows: pd.Series, closes: pd.Series) -> pd.Series:
        """Returns ATR series."""
        tr = pd.concat(
            [highs - lows,
             (highs - closes.shift()).abs(),
             (lows  - closes.shift()).abs()],
            axis=1,
        ).max(axis=1)
        return tr.ewm(span=self.atr_period, adjust=False).mean()
