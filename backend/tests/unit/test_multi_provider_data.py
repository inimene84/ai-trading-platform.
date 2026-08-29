"""
Unit tests for Multi-Provider OHLCV Data Service (WP2)
"""

import pytest
from backend.services.multi_provider_data import MultiProviderDataFetcher, multi_provider_data
from backend.services.data_hub import DataHub


@pytest.mark.asyncio
async def test_multi_provider_datahub_cache():
    fetcher = MultiProviderDataFetcher()
    symbol = "BTCUSDT"

    # Pre-populate cache in DataHub
    topic = f"ohlcv:{symbol}:1h"
    dummy_bars = [
        {"date": "2026-01-01T00:00:00+00:00", "open": 60000.0, "high": 60500.0, "low": 59900.0, "close": 60400.0, "volume": 100.0}
    ] * 25

    fetcher.data_hub.publish(topic, dummy_bars, ttl_ms=60_000)

    # Calling get_ohlcv should hit DataHub cache immediately
    bars = await fetcher.get_ohlcv(symbol, interval="1h", limit=20)
    assert bars is not None
    assert len(bars) == 20
    assert bars[0]["close"] == 60400.0


@pytest.mark.asyncio
async def test_bybit_kline_format_parser(monkeypatch):
    fetcher = MultiProviderDataFetcher()

    # Mock _fetch_bybit to return sample bar structure
    async def mock_bybit(symbol, interval, limit):
        return [
            {"date": "2026-01-01T00:00:00+00:00", "open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0, "volume": 1000.0}
        ] * 30

    monkeypatch.setattr(fetcher, "_fetch_bybit", mock_bybit)

    bars = await fetcher.get_ohlcv("ETHUSDT", interval="1h", limit=25)
    assert len(bars) == 25
    assert bars[0]["open"] == 100.0
