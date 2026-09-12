"""Costed period returns for promotion metrics (fees + slippage + funding).

Zero-cost series are retained for diagnostics only. Promotion gates read the
costed series; promoting on zero-cost DSR/Sharpe alone is ZERO_COST_ONLY.
"""

from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np

from backend.ml.geometry import (
    DEFAULT_FUNDING_RATE_PER_8H,
    DEFAULT_SLIPPAGE_RATE,
    DEFAULT_TAKER_FEE_RATE,
    HOUSE_BAR_TIMEFRAME,
)


def _as_float_array(values: Iterable[float]) -> np.ndarray:
    return np.asarray(list(values), dtype=float)


def bars_per_funding_interval(timeframe: str = HOUSE_BAR_TIMEFRAME) -> float:
    """How many primary bars fit in one 8h funding interval."""
    mapping = {
        "1m": 480.0,
        "5m": 96.0,
        "15m": 32.0,
        "30m": 16.0,
        "1h": 8.0,
        "2h": 4.0,
        "4h": 2.0,
        "8h": 1.0,
        "1d": 1.0 / 3.0,
    }
    return mapping.get(timeframe, 8.0)


def per_bar_roundtrip_cost(
    *,
    taker_fee_rate: float = DEFAULT_TAKER_FEE_RATE,
    slippage_rate: float = DEFAULT_SLIPPAGE_RATE,
    funding_rate_per_8h: float = DEFAULT_FUNDING_RATE_PER_8H,
    timeframe: str = HOUSE_BAR_TIMEFRAME,
    apply_roundtrip_fees: bool = True,
) -> float:
    """Per-bar cost drag as a fraction of notional.

    Fees and slippage are charged once per completed round-trip when
    ``apply_roundtrip_fees`` is True (caller should pass per-trade returns,
    not per-bar mark-to-market). Funding is pro-rated onto every bar.
    """
    fee_slip = 0.0
    if apply_roundtrip_fees:
        fee_slip = 2.0 * (float(taker_fee_rate) + float(slippage_rate))
    funding_per_bar = float(funding_rate_per_8h) / bars_per_funding_interval(timeframe)
    return fee_slip + funding_per_bar


def apply_costs(
    returns: Sequence[float] | np.ndarray,
    *,
    taker_fee_rate: float = DEFAULT_TAKER_FEE_RATE,
    slippage_rate: float = DEFAULT_SLIPPAGE_RATE,
    funding_rate_per_8h: float = DEFAULT_FUNDING_RATE_PER_8H,
    timeframe: str = HOUSE_BAR_TIMEFRAME,
    apply_roundtrip_fees: bool = True,
) -> np.ndarray:
    """Subtract fees, slippage, and pro-rated funding from period returns."""
    raw = _as_float_array(returns)
    drag = per_bar_roundtrip_cost(
        taker_fee_rate=taker_fee_rate,
        slippage_rate=slippage_rate,
        funding_rate_per_8h=funding_rate_per_8h,
        timeframe=timeframe,
        apply_roundtrip_fees=apply_roundtrip_fees,
    )
    return raw - drag


def costed_edge_bps(
    costed_returns: Sequence[float] | np.ndarray,
    *,
    bars_per_year: float = 24.0 * 365.0,
) -> float:
    """Annualized mean costed edge in basis points."""
    series = _as_float_array(costed_returns)
    if series.size == 0:
        return 0.0
    mean_bar = float(np.nanmean(series))
    return mean_bar * float(bars_per_year) * 10_000.0


def net_sharpe_after_costs(
    costed_returns: Sequence[float] | np.ndarray,
    *,
    bars_per_year: float = 24.0 * 365.0,
) -> float:
    """Annualized Sharpe of the costed return series (population std)."""
    series = _as_float_array(costed_returns)
    if series.size < 2:
        return 0.0
    std = float(np.nanstd(series, ddof=1))
    if std <= 0.0:
        return 0.0
    return float(np.nanmean(series) / std * np.sqrt(bars_per_year))
