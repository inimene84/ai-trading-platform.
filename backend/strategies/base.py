"""
Base strategy interface and signal types.
All strategies implement generate_signal() → StrategySignal.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class StrategySignal:
    symbol: str
    signal: str           # BUY, SELL, NEUTRAL
    confidence: float     # 0.0 – 1.0
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    strategy: str = ""
    reasoning: str = ""


class BaseStrategy(ABC):
    name: str = "base"

    @abstractmethod
    def generate_signal(self, symbol: str, bars: list[dict], **kwargs) -> StrategySignal:
        """
        Compute a trading signal from OHLCV bar dicts.
        bars: [{"timestamp": int, "open": f, "high": f, "low": f, "close": f, "volume": f}, ...]
        """
        ...

    @staticmethod
    def _closes(bars: list[dict]) -> list[float]:
        return [b["close"] for b in bars]

    @staticmethod
    def _highs(bars: list[dict]) -> list[float]:
        return [b["high"] for b in bars]

    @staticmethod
    def _lows(bars: list[dict]) -> list[float]:
        return [b["low"] for b in bars]

    @staticmethod
    def _volumes(bars: list[dict]) -> list[float]:
        return [b.get("volume", 0) for b in bars]

    @staticmethod
    def _dominant_trend(closes: list[float], period: int = 50) -> str:
        """Determine dominant trend from SMA slope. Returns 'UP', 'DOWN', or 'FLAT'."""
        if len(closes) < period + 5:
            return "FLAT"
        import pandas as pd
        sma = pd.Series(closes).rolling(period).mean()
        slope = (float(sma.iloc[-1]) - float(sma.iloc[-5])) / (float(sma.iloc[-5]) + 1e-9)
        if slope > 0.001:
            return "UP"
        elif slope < -0.001:
            return "DOWN"
        return "FLAT"
