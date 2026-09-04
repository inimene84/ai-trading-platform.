"""Unit tests for UnifiedFeedService (mocked providers, no network)."""

import pytest

from backend.services.unified_feed import Quote, UnifiedFeedService


def _quote(symbol: str, asset_class: str, price: float, source: str = "binance") -> Quote:
    return Quote(
        symbol=symbol, asset_class=asset_class, price=price,
        change_abs=1.0, change_pct=0.5, high=price + 1, low=price - 1,
        volume=100.0, source=source, as_of="2026-01-01T00:00:00+00:00",
        stale=False, error=None,
    )


@pytest.mark.asyncio
async def test_crypto_quotes_never_raise(monkeypatch):
    feed = UnifiedFeedService()

    async def boom(_symbols):
        raise RuntimeError("binance down")

    monkeypatch.setattr(
        "backend.services.unified_feed.binance_market_data.get_all_tickers_24h",
        boom,
    )
    quotes = await feed.get_quotes(["BTCUSDC"], asset_class="crypto")
    assert len(quotes) == 1
    assert quotes[0]["stale"] is True
    assert quotes[0]["price"] is None
    assert "binance" in (quotes[0]["error"] or "").lower() or quotes[0]["error"]


@pytest.mark.asyncio
async def test_crypto_quotes_map_tickers(monkeypatch):
    feed = UnifiedFeedService()

    async def fake_tickers(symbols):
        return [{
            "symbol": "BTCUSDC",
            "lastPrice": "100000.5",
            "priceChange": "120.0",
            "priceChangePercent": "0.12",
            "highPrice": "101000",
            "lowPrice": "99000",
            "quoteVolume": "5000000",
        }]

    monkeypatch.setattr(
        "backend.services.unified_feed.binance_market_data.get_all_tickers_24h",
        fake_tickers,
    )
    quotes = await feed.get_quotes(["BTCUSDC"], asset_class="crypto")
    assert quotes[0]["price"] == 100000.5
    assert quotes[0]["stale"] is False
    assert quotes[0]["source"] == "binance"
    assert quotes[0]["asset_class"] == "crypto"


@pytest.mark.asyncio
async def test_default_universe_env(monkeypatch):
    monkeypatch.setenv("FEED_SYMBOLS_CRYPTO", "AAAUSDC")
    monkeypatch.setenv("FEED_SYMBOLS_EQUITIES", "AAPL")
    monkeypatch.setenv("FEED_SYMBOLS_METALS", "XAUUSD")
    feed = UnifiedFeedService()
    universe = feed.default_universe()
    assert universe["crypto"] == ["AAAUSDC"]
    assert universe["equities"] == ["AAPL"]
    assert universe["metals"] == ["XAUUSD"]


@pytest.mark.asyncio
async def test_error_quote_shape():
    feed = UnifiedFeedService()
    q = feed._error_quote("EURUSD", "forex", "yfinance", "timeout")
    assert q["stale"] is True
    assert q["price"] is None
    assert q["error"] == "timeout"
    assert q["symbol"] == "EURUSD"


@pytest.mark.asyncio
async def test_get_bars_caches_via_datahub(monkeypatch):
    feed = UnifiedFeedService()
    calls = {"n": 0}

    async def fake_fetch_bars(**kwargs):
        calls["n"] += 1
        return {"symbol": kwargs["symbol"], "data": [{"close": 1.0}], "source": "binance"}

    monkeypatch.setattr("backend.services.unified_feed.fetch_bars", fake_fetch_bars)
    first = await feed.get_bars("BTCUSDC", "1h", 10)
    second = await feed.get_bars("BTCUSDC", "1h", 10)
    assert first["symbol"] == "BTCUSDC"
    assert "as_of" in first
    assert second["data"] == first["data"]
    assert calls["n"] == 1
