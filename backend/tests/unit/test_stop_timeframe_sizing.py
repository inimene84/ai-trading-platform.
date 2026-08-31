"""Stops are sized from a slower timeframe than the entry trigger.

M5 ATR on a quiet pair gave 1.7-7.7 pip stops, which the broker discarded
outright. The 10-pip floor is a backstop; the real fix is sizing protection
from a timeframe whose range is meaningful.
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.services.ctrader_service import CTraderService
from backend.services.signal_candidate_engine import signal_candidate_engine


def _bars(n, base, step, rng):
    """n bars trending by `step` per bar with a per-bar high/low range of `rng`."""
    out = []
    for i in range(n):
        close = base + i * step
        out.append(
            {
                "close": close,
                "high": close + rng / 2,
                "low": close - rng / 2,
                "volume": 100,
            }
        )
    return out


# Quiet M5: 0.8 pip bar range. Same market on H1: 12 pip range.
M5_QUIET = _bars(40, 1.16000, 0.00002, 0.00008)
H1_REAL = _bars(40, 1.16000, 0.00040, 0.00120)


def test_stop_atr_prefers_the_slow_timeframe():
    features = {"atr": 0.00008, "stop_atr": 0.00120}
    assert signal_candidate_engine.stop_atr(features) == pytest.approx(0.00120)


def test_stop_atr_falls_back_to_signal_atr():
    assert signal_candidate_engine.stop_atr({"atr": 0.00008}) == pytest.approx(0.00008)
    assert signal_candidate_engine.stop_atr({}) == 0.0


@pytest.mark.asyncio
async def test_slow_timeframe_atr_is_attached_from_the_configured_period():
    features = signal_candidate_engine._compute_features(M5_QUIET)
    signal_atr = features["atr"]

    with patch(
        "backend.services.signal_candidate_engine.ctrader_service.get_trendbars",
        return_value=H1_REAL,
    ) as slow_fetch:
        await signal_candidate_engine._attach_stop_atr("EURUSD", "ctrader", features)

    assert slow_fetch.call_args.args[1] == signal_candidate_engine.STOP_TIMEFRAME
    assert features["atr"] == pytest.approx(signal_atr), "entry ATR must be untouched"
    assert features["stop_atr"] > features["atr"] * 5


@pytest.mark.asyncio
async def test_missing_slow_bars_leave_signal_atr_in_place():
    features = signal_candidate_engine._compute_features(M5_QUIET)
    with patch(
        "backend.services.signal_candidate_engine.ctrader_service.get_trendbars",
        side_effect=RuntimeError("timeout"),
    ):
        await signal_candidate_engine._attach_stop_atr("EURUSD", "ctrader", features)
    assert "stop_atr" not in features
    assert signal_candidate_engine.stop_atr(features) == pytest.approx(features["atr"])


def test_m5_sized_stop_would_be_rejected_but_h1_sized_stop_is_not():
    """The whole point: H1 sizing clears the broker minimum without clamping."""
    m5 = signal_candidate_engine._compute_features(M5_QUIET)
    h1 = signal_candidate_engine._compute_features(H1_REAL)
    entry = m5["last_close"]
    pip = float(CTraderService.pip_size_for("EURUSD"))
    minimum = CTraderService.MIN_FX_STOP_PIPS

    m5_stop_pips = (1.5 * m5["atr"]) / pip
    h1_stop_pips = (1.5 * h1["atr"]) / pip

    assert m5_stop_pips < minimum, "M5 sizing lands inside the broker stop level"
    assert h1_stop_pips > minimum, "H1 sizing clears it on its own"

    # And the clamp leaves the H1-sized stop untouched.
    sl, _ = CTraderService.clamp_protective_prices(
        "EURUSD", entry, entry - 1.5 * h1["atr"], None, direction="BUY"
    )
    assert abs(entry - sl) / pip == pytest.approx(h1_stop_pips, rel=1e-6)


@pytest.mark.asyncio
async def test_momentum_candidate_uses_the_slow_atr_end_to_end():
    trending = _bars(40, 1.16000, 0.00006, 0.00008)
    with patch(
        "backend.services.signal_candidate_engine.ctrader_service.get_trendbars",
    ) as fetch:
        # First call is the signal timeframe, second the stop timeframe.
        fetch.side_effect = [trending, H1_REAL]
        created = await signal_candidate_engine.scan_markets(
            universe=["EURUSD"], timeframe="M5"
        )

    pip = float(CTraderService.pip_size_for("EURUSD"))
    for c in created:
        distance = abs(c["entry_price"] - c["stop_loss"]) / pip
        assert distance >= CTraderService.MIN_FX_STOP_PIPS


@pytest.mark.asyncio
async def test_crypto_stop_atr_uses_binance_interval_mapping():
    features = signal_candidate_engine._compute_features(_bars(40, 60000, 5, 20))
    klines = AsyncMock(return_value=_bars(40, 60000, 50, 400))
    with patch(
        "backend.services.signal_candidate_engine.binance_market_data.get_klines", klines
    ):
        await signal_candidate_engine._attach_stop_atr("BTCUSDT", "binance_futures", features)

    # Binance rejects "H1"; it must be mapped before the request.
    assert klines.call_args.kwargs["interval"] == "1h"
    assert features["stop_atr"] > features["atr"]
