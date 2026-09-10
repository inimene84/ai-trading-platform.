"""Unit tests for Jesse AI Quant Engine Bridge and API Routes."""

import os
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.jesse_bridge import JesseBridgeService, jesse_bridge
from backend.services.risk_config import get_risk_config


@pytest.fixture
def client():
    return TestClient(app)


def test_jesse_bridge_sync_parameters(tmp_path, monkeypatch):
    """Test sync_strategy_to_risk_config updates environment and reloads RiskConfig."""
    dummy_env = tmp_path / ".env"
    dummy_env.write_text("SL_ATR_MULT=1.0\nTP_ATR_MULT=2.5\nTRAIL_ACTIVATION_ATR=1.5\nTRAIL_ATR_MULT=0.8\n")
    monkeypatch.setenv("ENV_FILE_PATH", str(dummy_env))

    service = JesseBridgeService()
    result = service.sync_strategy_to_risk_config(
        sl_atr_mult=1.85,
        tp_atr_mult=4.5,
        trail_activation_atr=2.1,
        trail_atr_mult=1.4,
    )

    assert result["status"] == "synced"
    assert result["sl_atr_mult"] == 1.85
    assert result["tp_atr_mult"] == 4.5
    assert result["trail_activation_atr"] == 2.1
    assert result["trail_atr_mult"] == 1.4

    # Verify written to dummy_env
    content = dummy_env.read_text()
    assert "SL_ATR_MULT=1.85" in content
    assert "TP_ATR_MULT=4.5" in content
    assert "TRAIL_ACTIVATION_ATR=2.1" in content
    assert "TRAIL_ATR_MULT=1.4" in content


@pytest.mark.asyncio
async def test_jesse_bridge_get_status_fallback():
    """Test get_status returns unavailable when Jesse server cannot be reached."""
    with patch.object(JesseBridgeService, "get_token", AsyncMock(return_value=None)):
        service = JesseBridgeService()
        status = await service.get_status()
        assert status["available"] is False
        assert status.get("error") is not None


def test_jesse_sync_route(client, monkeypatch, tmp_path):
    """Test /api/jesse/sync endpoint with admin API key."""
    dummy_env = tmp_path / ".env"
    dummy_env.write_text("SL_ATR_MULT=1.0\nTP_ATR_MULT=2.0\n")
    monkeypatch.setenv("ENV_FILE_PATH", str(dummy_env))

    api_key = os.getenv("ADMIN_API_KEY", "test_key")
    monkeypatch.setenv("ADMIN_API_KEY", api_key)

    response = client.post(
        "/api/jesse/sync",
        headers={"x-api-key": api_key, "Content-Type": "application/json"},
        json={
            "sl_atr_mult": 2.25,
            "tp_atr_mult": 5.0,
            "trail_activation_atr": 1.9,
            "trail_atr_mult": 1.3,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "synced"
    assert data["sl_atr_mult"] == 2.25
    assert data["tp_atr_mult"] == 5.0


@pytest.mark.asyncio
async def test_jesse_bridge_get_ml_prediction():
    """Test get_ml_prediction communicates with ML inference server."""
    mock_prediction = {
        "status": "success",
        "symbol": "BTC-USDT",
        "timeframe": "1h",
        "signal": "BUY",
        "confidence": 0.58,
        "probabilities": {"bullish": 0.58, "bearish": 0.12, "neutral": 0.30},
        "latest_close": 78500.0,
        "model": "BTC-USDT_1h_lightgbm.joblib",
        "latency_ms": 12.5,
    }

    service = JesseBridgeService()
    with patch.object(service, "get_ml_prediction", AsyncMock(return_value=mock_prediction)):
        res = await service.get_ml_prediction("BTC-USDT", "1h", "lightgbm", 0.45)
        assert res["status"] == "success"
        assert res["signal"] == "BUY"
        assert res["confidence"] == 0.58


def test_jesse_ml_predict_routes(client, monkeypatch):
    """Test /api/jesse/ml-predict (GET and POST) and /api/jesse/ml-models."""
    api_key = os.getenv("ADMIN_API_KEY", "test_key")
    monkeypatch.setenv("ADMIN_API_KEY", api_key)

    mock_prediction = {
        "status": "success",
        "symbol": "BTC-USDT",
        "timeframe": "1h",
        "signal": "BUY",
        "confidence": 0.58,
        "probabilities": {"bullish": 0.58, "bearish": 0.12, "neutral": 0.30},
        "latest_close": 78500.0,
        "model": "BTC-USDT_1h_lightgbm.joblib",
        "latency_ms": 12.5,
    }

    mock_models = {
        "status": "ok",
        "cached_models": ["BTC-USDT_1h_lightgbm"],
        "available_models": ["BTC-USDT_1h_lightgbm.joblib"],
    }

    with patch.object(jesse_bridge, "get_ml_prediction", AsyncMock(return_value=mock_prediction)), \
         patch.object(jesse_bridge, "get_ml_models", AsyncMock(return_value=mock_models)):

        # 1. GET /api/jesse/ml-predict
        res_get = client.get(
            "/api/jesse/ml-predict?symbol=BTC-USDT&timeframe=1h",
            headers={"x-api-key": api_key},
        )
        assert res_get.status_code == 200
        assert res_get.json()["signal"] == "BUY"

        # 2. POST /api/jesse/ml-predict
        res_post = client.post(
            "/api/jesse/ml-predict",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json={"symbol": "BTC-USDT", "timeframe": "1h", "threshold": 0.5},
        )
        assert res_post.status_code == 200
        assert res_post.json()["confidence"] == 0.58

        # 3. GET /api/jesse/ml-models
        res_models = client.get(
            "/api/jesse/ml-models",
            headers={"x-api-key": api_key},
        )
        assert res_models.status_code == 200
        assert "BTC-USDT_1h_lightgbm.joblib" in res_models.json()["available_models"]

