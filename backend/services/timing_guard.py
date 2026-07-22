"""
Heuristic Candlestick Timing Guard (WP4)
========================================
Fast, numeric pattern recognition scoring to detect bad trade entry timing
(e.g., buying into blow-off upper wicks, RSI overbought divergence, or volume exhaustion).
Runs in <5ms with zero external network or LLM dependencies.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# Default risk score threshold (env-configurable)
DEFAULT_RISK_THRESHOLD = float(os.getenv("TIMING_GUARD_RISK_THRESHOLD", "0.65"))


@dataclass
class TimingGuardResult:
    approved: bool
    risk_score: float  # 0.0 (safe) to 1.0 (extremely unsafe entry)
    warning_signals: List[str] = field(default_factory=list)
    reasoning: str = ""


def evaluate_heuristic_timing(
    bars: List[Dict[str, Any]],
    proposed_signal: str = "BUY",
    risk_threshold: float = DEFAULT_RISK_THRESHOLD
) -> TimingGuardResult:
    """
    Evaluates micro-structure timing risk using numeric candlestick heuristics.
    """
    if proposed_signal == "NEUTRAL" or not bars or len(bars) < 15:
        return TimingGuardResult(
            approved=True,
            risk_score=0.0,
            warning_signals=[],
            reasoning="NEUTRAL signal or insufficient bars — timing check skipped"
        )

    df = pd.DataFrame(bars)
    recent = df.tail(10)
    last = recent.iloc[-1]

    warnings = []
    risk_score = 0.1

    o, h, l, c = float(last["open"]), float(last["high"]), float(last["low"]), float(last["close"])
    body = abs(c - o)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    # Compute RSI 14
    closes = df["close"].values
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[-14:]) if len(gains) >= 14 else 0.001
    avg_loss = np.mean(losses[-14:]) if len(losses) >= 14 else 0.001
    rs = avg_gain / (avg_loss + 1e-9)
    rsi = float(100 - (100 / (1 + rs)))

    # Compute Bollinger Bands (20, 2)
    sma20 = float(df["close"].tail(20).mean())
    std20 = float(df["close"].tail(20).std())
    upper_bb = sma20 + (std20 * 2.0)
    lower_bb = sma20 - (std20 * 2.0)

    if proposed_signal == "BUY":
        # 1. Blow-off upper wick (rejection at local high)
        if upper_wick > (body * 2.0) and upper_wick > 0:
            risk_score += 0.30
            warnings.append(f"Long upper wick rejection ({upper_wick:.2f})")

        # 2. Extreme RSI Overbought
        if rsi > 72:
            risk_score += 0.25
            warnings.append(f"RSI overbought ({rsi:.1f})")

        # 3. Extended outside Upper Bollinger Band
        if c >= upper_bb * 0.998:
            risk_score += 0.20
            warnings.append("Price extended at Upper Bollinger Band limit")

        # 4. Bearish volume divergence on breakout
        if len(recent) >= 4:
            prev_max_high = recent["high"].iloc[:-1].max()
            avg_vol = recent["volume"].iloc[:-1].mean()
            if h > prev_max_high and float(last["volume"]) < (avg_vol * 0.65):
                risk_score += 0.25
                warnings.append("Bearish volume divergence on high attempt")

    elif proposed_signal == "SELL":
        # 1. Rejection lower wick at low
        if lower_wick > (body * 2.0) and lower_wick > 0:
            risk_score += 0.30
            warnings.append(f"Long lower wick rejection ({lower_wick:.2f})")

        # 2. Extreme RSI Oversold
        if rsi < 28:
            risk_score += 0.25
            warnings.append(f"RSI oversold ({rsi:.1f})")

        # 3. Extended outside Lower Bollinger Band
        if c <= lower_bb * 1.002:
            risk_score += 0.20
            warnings.append("Price extended at Lower Bollinger Band limit")

    risk_score = min(risk_score, 1.0)
    approved = (risk_score < risk_threshold)

    reasoning = (
        f"Heuristic Timing Guard: score={risk_score:.2f} (threshold={risk_threshold:.2f}). "
        f"Warnings: {', '.join(warnings) if warnings else 'None'}"
    )

    return TimingGuardResult(
        approved=approved,
        risk_score=round(risk_score, 3),
        warning_signals=warnings,
        reasoning=reasoning
    )
