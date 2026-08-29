"""
Unit tests for cTrader Open API Charting and Sample Tools endpoints and services.
"""

import pytest
from starlette.testclient import TestClient
from backend.main import app
from backend.services.ctrader_service import ctrader_service


@pytest.fixture
def client():
    return TestClient(app)


def test_get_trendbars_service():
    """Verify get_trendbars returns structured OHLCV bars with proper timestamps."""
    bars = ctrader_service.get_trendbars("EURUSD", "M5", count=25)
    assert len(bars) == 25
    first = bars[0]
    assert "open" in first
    assert "high" in first
    assert "low" in first
    assert "close" in first
    assert "volume" in first
    assert "timestamp" in first
    assert "time" in first
    assert first["high"] >= first["low"]
    assert first["open"] > 0
    assert first["close"] > 0


def test_get_tick_data_service():
    """Verify get_tick_data returns valid tick stream structure."""
    ticks = ctrader_service.get_tick_data("EURUSD", quote_type="BID", hours=2)
    assert len(ticks) > 0
    t = ticks[0]
    assert t["symbol"] == "EURUSD"
    assert t["type"] == "BID"
    assert t["price"] > 0
    assert "timestamp" in t
    assert "volume" in t


def test_symbol_specifications():
    """Verify symbol specification correctly returns digits, pip size, lot size."""
    eurusd_spec = ctrader_service.get_symbol_specification("EURUSD")
    assert eurusd_spec["digits"] == 5
    assert eurusd_spec["pip_position"] == 4
    assert eurusd_spec["pip_size"] == 0.0001
    assert eurusd_spec["lot_size"] == 100_000

    usdjpy_spec = ctrader_service.get_symbol_specification("USDJPY")
    assert usdjpy_spec["digits"] == 3
    assert usdjpy_spec["pip_position"] == 2
    assert usdjpy_spec["pip_size"] == 0.01

    btcusd_spec = ctrader_service.get_symbol_specification("BTCUSD")
    assert btcusd_spec["digits"] == 2
    assert btcusd_spec["pip_position"] == 0


def test_calculate_pip_margin():
    """Verify financial math matches OpenAPI.Net SymbolExtensions specifications."""
    # 1.0 standard lot of EURUSD (100,000 units) at price 1.085 with 1:100 leverage
    calc = ctrader_service.calculate_pip_margin(
        symbol="EURUSD",
        lots=1.0,
        price=1.0850,
        leverage=100.0,
        deposit_asset="USD"
    )
    assert calc["volume_units"] == 100_000
    assert calc["pip_value"] == 10.0  # $10 per pip for 1 lot EURUSD in USD deposit
    assert calc["required_margin"] == 1085.0  # (100,000 * 1.0850) / 100 = $1,085.0


def test_trendbars_endpoint(client):
    """Test GET /api/trading/ctrader/trendbars endpoint."""
    response = client.get("/api/trading/ctrader/trendbars?symbol=EURUSD&period=M5&count=20")
    assert response.status_code == 200
    data = response.json()
    assert data["symbol"] == "EURUSD"
    assert data["period"] == "M5"
    assert len(data["bars"]) == 20


def test_ticks_endpoint(client):
    """Test GET /api/trading/ctrader/ticks endpoint."""
    response = client.get("/api/trading/ctrader/ticks?symbol=GBPUSD&type=BID&hours=1")
    assert response.status_code == 200
    data = response.json()
    assert data["symbol"] == "GBPUSD"
    assert data["type"] == "BID"
    assert len(data["ticks"]) > 0


def test_symbol_spec_endpoint(client):
    """Test GET /api/trading/ctrader/symbol-spec endpoint."""
    response = client.get("/api/trading/ctrader/symbol-spec?symbol=EURUSD")
    assert response.status_code == 200
    data = response.json()
    assert data["symbol"] == "EURUSD"
    assert data["digits"] == 5
    assert data["lot_size"] == 100_000


def test_pip_margin_calc_endpoint(client):
    """Test POST /api/trading/ctrader/calc/pip-margin endpoint."""
    payload = {
        "symbol": "EURUSD",
        "lots": 0.5,
        "price": 1.1000,
        "leverage": 200.0,
        "deposit_asset": "USD"
    }
    response = client.post("/api/trading/ctrader/calc/pip-margin", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["volume_units"] == 50_000
    assert data["pip_value"] == 5.0
    assert data["required_margin"] == 275.0  # (50,000 * 1.10) / 200 = 275.0
