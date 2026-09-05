"""Unit tests for forecast batch store and Kronos include_path passthrough."""

import pytest

from backend.routes import forecast as forecast_mod
from backend.services import kronos_service


def test_set_and_get_batch_latest_fallback():
    forecast_mod.set_batch_latest(
        [{"symbol": "BTCUSDC", "signal": "BUY", "cum_change_5_pct": 0.3}],
        as_of="2026-01-01T00:00:00+00:00",
    )
    snap = forecast_mod.get_batch_latest()
    assert snap["as_of"] == "2026-01-01T00:00:00+00:00" or snap.get("results")
    assert any(r.get("symbol") == "BTCUSDC" for r in snap["results"])


def test_neutral_forecast_includes_path_none():
    payload = forecast_mod._neutral_forecast("btcusdc", "1h", "sidecar down")
    assert payload["signal"] == "NEUTRAL"
    assert payload["forecast_path"] is None
    assert payload["reversal_risk"] is False
    assert payload["symbol"] == "BTCUSDC"
    assert payload["error"] == "sidecar down"


@pytest.mark.asyncio
async def test_predict_include_path_in_cache_key(monkeypatch):
    monkeypatch.setattr(kronos_service, "ALLOW_LOCAL_STUB", False)
    monkeypatch.setattr(kronos_service, "KRONOS_SIDECAR_URL", "http://127.0.0.1:59999")
    monkeypatch.setattr(kronos_service, "FALLBACK_LOCAL_URL", "http://127.0.0.1:59999")
    kronos_service._prediction_cache.clear()
    bars = [
        {
            "date": f"2026-01-01T{i:02d}:00:00+00:00",
            "open": 100 + i, "high": 101 + i, "low": 99 + i,
            "close": 100.5 + i, "volume": 10.0,
        }
        for i in range(10)
    ]
    res = await kronos_service.predict(bars, "BTCUSDT", include_path=True, pred_len=10)
    assert res["forecast_path"] is None
    assert res["signal"] == "NEUTRAL"


@pytest.mark.asyncio
async def test_kronos_make_neutral_has_forecast_path():
    n = kronos_service._make_neutral("x")
    assert n["forecast_path"] is None
    assert n["reversal_risk"] is False
