"""
Unit tests for Kronos Service and Sidecar Client (WP3)
"""

import pytest
import pandas as pd
from backend.services import kronos_service


def create_sample_bars(n=30):
    bars = []
    base_price = 50000.0
    for i in range(n):
        bars.append({
            "date": f"2026-01-01T{i:02d}:00:00+00:00",
            "open": base_price + i * 10,
            "high": base_price + i * 10 + 5,
            "low": base_price + i * 10 - 5,
            "close": base_price + i * 10 + 2,
            "volume": 100.0,
            "amount": (base_price + i * 10) * 100.0
        })
    return bars


@pytest.mark.asyncio
async def test_kronos_service_fallback():
    bars = create_sample_bars(30)

    # Calling predict without running sidecar should trigger fallback safely
    res = await kronos_service.predict(bars, "BTCUSDT")

    assert res is not None
    assert "signal" in res
    assert "confidence" in res
    assert "cum_change_5_pct" in res
    assert "cum_change_10_pct" in res
    assert res["signal"] in ["BUY", "SELL", "NEUTRAL"]


@pytest.mark.asyncio
async def test_kronos_service_caching():
    bars = create_sample_bars(30)
    symbol = "ETHUSDT"

    # First call
    res1 = await kronos_service.predict(bars, symbol)
    # Second call with identical last bar date
    res2 = await kronos_service.predict(bars, symbol)

    assert res1 == res2
