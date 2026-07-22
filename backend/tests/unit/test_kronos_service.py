"""
Unit tests for Kronos Service and Sidecar Client (WP3)
"""

import pytest
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
            "amount": (base_price + i * 10) * 100.0,
        })
    return bars


@pytest.mark.asyncio
async def test_kronos_service_fail_closed(monkeypatch):
    """Sidecar down + stub disabled → NEUTRAL (fail-closed)."""
    monkeypatch.setattr(kronos_service, "ALLOW_LOCAL_STUB", False)
    kronos_service._prediction_cache.clear()

    bars = create_sample_bars(30)
    res = await kronos_service.predict(bars, "BTCUSDT")

    assert res is not None
    assert res["signal"] == "NEUTRAL"
    assert res["confidence"] == 0.0
    assert "fail-closed" in (res.get("error") or "").lower() or "unreachable" in (
        res.get("error") or ""
    ).lower()


@pytest.mark.asyncio
async def test_kronos_service_local_stub_opt_in(monkeypatch):
    """Explicit stub opt-in returns directional metrics for offline tests."""
    monkeypatch.setattr(kronos_service, "ALLOW_LOCAL_STUB", True)
    kronos_service._prediction_cache.clear()

    bars = create_sample_bars(30)
    res = await kronos_service.predict(bars, "BTCUSDT")

    assert res is not None
    assert "signal" in res
    assert "cum_change_5_pct" in res
    assert res["signal"] in ["BUY", "SELL", "NEUTRAL"]


@pytest.mark.asyncio
async def test_kronos_service_caching(monkeypatch):
    monkeypatch.setattr(kronos_service, "ALLOW_LOCAL_STUB", True)
    kronos_service._prediction_cache.clear()

    bars = create_sample_bars(30)
    symbol = "ETHUSDT"

    res1 = await kronos_service.predict(bars, symbol)
    res2 = await kronos_service.predict(bars, symbol)

    assert res1 == res2
