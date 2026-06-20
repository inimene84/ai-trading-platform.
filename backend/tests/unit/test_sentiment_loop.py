"""Standalone test for sentiment_loop: aggregation + dry-run over all symbols.
Mocks crypto_news_service and influx so no network/DB is touched."""
import asyncio
import sys
import types

# ── Stub the two external deps BEFORE importing the loop ──────────────────────
fake_news = types.ModuleType("backend.services.crypto_news_service")

class _FakeNews:
    async def get_crypto_news(self, symbols=None):
        sym = (symbols or ["BTCUSDT"])[0].upper()
        # Give BTC bullish coverage, ETH bearish, everything else no news.
        if sym.startswith("BTC"):
            return [
                {"title": "Bitcoin rally surge to record high", "sentiment": "positive", "categories": "BTC|Markets"},
                {"title": "BTC gains as adoption rises", "sentiment": "positive", "categories": "BTC"},
            ]
        if sym.startswith("ETH"):
            return [
                {"title": "Ethereum crash plunge after hack", "sentiment": "negative", "categories": "ETH|Tech"},
            ]
        return []  # no news -> must still emit NEUTRAL row

fake_news.crypto_news_service = _FakeNews()
sys.modules["backend.services.crypto_news_service"] = fake_news

fake_influx = types.ModuleType("backend.services.influxdb_writer")
WRITES = []

class _FakeInflux:
    async def write_news_sentiment(self, **kwargs):
        WRITES.append(kwargs)

fake_influx.influx = _FakeInflux()
sys.modules["backend.services.influxdb_writer"] = fake_influx

# ── Now import the loop ───────────────────────────────────────────────────────
from backend.services.sentiment_loop import (
    SentimentLoopService, _aggregate_keyword_sentiment, _load_symbols,
)


def test_aggregate():
    # bullish
    s, i, d, n = _aggregate_keyword_sentiment(
        [{"sentiment": "positive"}, {"sentiment": "positive"}]
    )
    assert d == "BULLISH" and s > 0 and n == 2, (s, i, d, n)
    # bearish
    s, i, d, n = _aggregate_keyword_sentiment([{"sentiment": "negative"}])
    assert d == "BEARISH" and s < 0, (s, i, d, n)
    # empty -> neutral
    s, i, d, n = _aggregate_keyword_sentiment([])
    assert d == "NEUTRAL" and s == 0.0 and n == 0, (s, i, d, n)
    print("  aggregate: OK")


async def test_dry_run_emits_all_symbols():
    svc = SentimentLoopService()
    svc._symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT"]
    summary = await svc.run_once(dry_run=True)
    per = summary["per_symbol"]
    assert len(per) == 4, f"expected 4 symbols, got {len(per)}"
    assert per["BTCUSDT"]["direction"] == "BULLISH", per["BTCUSDT"]
    assert per["ETHUSDT"]["direction"] == "BEARISH", per["ETHUSDT"]
    assert per["SOLUSDT"]["direction"] == "NEUTRAL", per["SOLUSDT"]
    assert WRITES == [], "dry_run must NOT write to influx"
    print(f"  dry_run: OK — all {len(per)} symbols emitted, 0 influx writes")


async def test_live_run_writes_base_tags():
    WRITES.clear()
    svc = SentimentLoopService()
    svc._symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    summary = await svc.run_once(dry_run=False)
    assert summary["written"] == 3, summary
    tags = sorted(w["symbol"] for w in WRITES)
    assert tags == ["BTC", "ETH", "SOL"], tags  # base coin tags, no USDT, no CRYPTO fallback
    btc = next(w for w in WRITES if w["symbol"] == "BTC")
    assert btc["direction"] == "BULLISH" and btc["source"] == "native-sentiment-loop", btc
    print(f"  live_run: OK — wrote base tags {tags}, no CRYPTO fallback")


def main():
    print("test_aggregate")
    test_aggregate()
    print("test_dry_run_emits_all_symbols")
    asyncio.run(test_dry_run_emits_all_symbols())
    print("test_live_run_writes_base_tags")
    asyncio.run(test_live_run_writes_base_tags())
    print("\nALL TESTS PASSED ✓")


if __name__ == "__main__":
    main()
