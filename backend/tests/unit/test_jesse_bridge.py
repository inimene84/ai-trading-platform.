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
