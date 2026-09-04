"""
Unit tests for multi-broker endpoints (/brokers, /markets, /order/smart, /ctrader/tokens).
"""
import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_get_brokers_endpoint(client):
    """Verify /api/trading/brokers returns both Binance and cTrader status."""
    response = client.get("/api/trading/brokers")
    assert response.status_code == 200
    data = response.json()
    assert "brokers" in data
    assert "binance_futures" in data["brokers"]
    assert "ctrader" in data["brokers"]
    assert "status" in data["brokers"]["ctrader"]
    assert "circuit_breaker" in data["brokers"]["ctrader"]


def test_get_markets_endpoint(client):
    """Verify /api/trading/markets returns combined forex, metals, and crypto instruments."""
    response = client.get("/api/trading/markets")
    assert response.status_code == 200
    data = response.json()
    assert "markets" in data
    assert data["total"] > 0
    symbols = [m["symbol"] for m in data["markets"]]
    assert "EURUSD" in symbols
    assert "BTCUSDT" in symbols
    assert "XAUUSD" in symbols


@pytest.mark.asyncio
async def test_get_forex_markets_uses_unified_feed(monkeypatch):
    """GET /trading/markets/forex is backed by unified_feed quotes."""
    from backend.services.unified_feed import Quote

    async def fake_quotes(symbols=None, asset_class=None):
        if asset_class == "forex":
            return [Quote(
                symbol="EURUSD", asset_class="forex", price=1.0854,
                change_abs=0.002, change_pct=0.18, high=1.09, low=1.08,
                volume=None, source="yfinance", as_of="2026-01-01T00:00:00+00:00",
                stale=False, error=None,
            )]
        if asset_class == "metal":
            return [Quote(
                symbol="XAUUSD", asset_class="metal", price=2735.6,
                change_abs=22.0, change_pct=0.82, high=2740.0, low=2700.0,
                volume=None, source="yfinance", as_of="2026-01-01T00:00:00+00:00",
                stale=False, error=None,
            )]
        return []

    monkeypatch.setattr("backend.routes.trading.unified_feed.get_quotes", fake_quotes)
    from backend.routes.trading import get_forex_markets
    payload = await get_forex_markets()
    symbols = [row["symbol"] for row in payload["data"]]
    assert "EUR/USD" in symbols
    assert "XAU/USD" in symbols
    eur = next(r for r in payload["data"] if r["symbol"] == "EUR/USD")
    assert eur["price"] == pytest.approx(1.0854)
    assert eur["up"] is True


def test_smart_order_crypto_routing(client, auth_headers):
    """Verify crypto orders are automatically routed to Binance Futures in paper mode."""
    response = client.post(
        "/api/trading/order/smart",
        json={
            "symbol": "BTCUSDT",
            "direction": "BUY",
            "quantity": 0.01,
            "order_type": "MARKET",
            "price": 60000.0,
        },
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["target_broker"] == "binance_futures"


def test_smart_order_forex_routing(client, auth_headers):
    """Verify forex orders are automatically routed to cTrader in paper mode."""
    response = client.post(
        "/api/trading/order/smart",
        json={
            "symbol": "EURUSD",
            "direction": "BUY",
            "quantity": 0.1,
            "order_type": "MARKET",
            "price": 1.0850,
        },
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["target_broker"] == "ctrader"


def test_ctrader_tokens_info(client):
    """Verify /api/trading/ctrader/tokens endpoint status."""
    response = client.get("/api/trading/ctrader/tokens")
    assert response.status_code == 200
    data = response.json()
    assert "configured" in data
