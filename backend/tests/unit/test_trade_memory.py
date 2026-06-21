"""Standalone tests for trade_memory (Track C).
Mocks the Qdrant async client so no network/DB is touched. Verifies:
  • feature-vector determinism + normalisation
  • record_trade upserts a normalised vector with the right payload
  • recall_similar summarises neighbours into a directional bias
  • a bullish neighbour cluster yields a bullish signal; bearish -> bearish
  • below-min-samples yields a neutral, zero-confidence result
Run with:  PYTHONPATH=<repo root> python backend/tests/unit/test_trade_memory.py
"""
import asyncio
import math
import os
import sys
import types

# Force deterministic feature embedding (no LLM path) and small thresholds.
os.environ["TRADE_MEMORY_ENABLED"] = "true"
os.environ["TRADE_MEMORY_USE_LLM"] = "false"
os.environ["TRADE_MEMORY_MIN_SAMPLES"] = "3"
os.environ["TRADE_MEMORY_RECALL_K"] = "8"

from backend.services.trade_memory import (
    TradeMemoryService, feature_vector, extract_features, _FEATURE_ORDER,
    RecallResult,
)


# ── Fake Qdrant ──────────────────────────────────────────────────────────────
class _Hit:
    def __init__(self, score, payload):
        self.score = score
        self.payload = payload


class _FakeQdrant:
    def __init__(self, search_hits=None):
        self.upserts = []
        self._search_hits = search_hits or []

    async def get_collections(self):
        return types.SimpleNamespace(collections=[])

    async def create_collection(self, **kw):
        return True

    async def get_collection(self, **kw):
        return types.SimpleNamespace(points_count=len(self.upserts))

    async def upsert(self, collection_name, points):
        self.upserts.extend(points)

    async def search(self, **kw):
        return self._search_hits

    async def query_points(self, **kw):
        return types.SimpleNamespace(points=self._search_hits)


def _make_service(search_hits=None):
    svc = TradeMemoryService()
    svc._client = _FakeQdrant(search_hits=search_hits)
    svc._collection_ready = True  # skip create
    return svc


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_feature_vector_deterministic_and_normalised():
    print("test_feature_vector_deterministic_and_normalised")
    ctx = {"direction": "BUY", "regime": "TRENDING", "momentum_signal": "bullish",
           "rsi": 70, "funding_rate": 0.0002, "sentiment_score": 0.5}
    v1 = feature_vector(ctx, 64)
    v2 = feature_vector(ctx, 64)
    assert v1 == v2, "must be deterministic"
    assert len(v1) == 64, len(v1)
    norm = math.sqrt(sum(x * x for x in v1))
    assert abs(norm - 1.0) < 1e-9, f"must be L2-normalised, got {norm}"
    # Different context -> different vector
    v3 = feature_vector({**ctx, "direction": "SELL"}, 64)
    assert v3 != v1
    print("  OK — deterministic, normalised, discriminating")


def test_extract_features_ranges():
    print("test_extract_features_ranges")
    f = extract_features({"direction": "SELL", "rsi": 0, "funding_rate": 1.0})
    assert f["direction"] == -1.0
    assert f["rsi"] == -1.0          # rsi 0 -> -1
    assert f["funding"] == 1.0       # clipped
    assert set(_FEATURE_ORDER).issubset(f.keys())
    print("  OK — feature mapping in range")


def test_record_trade_upserts_normalised_vector():
    print("test_record_trade_upserts_normalised_vector")
    svc = _make_service()
    pid = asyncio.run(svc.record_trade(
        symbol="BTCUSDT", direction="BUY", pnl=12.5,
        context={"regime": "TRENDING", "momentum_signal": "bullish"},
        entry_price=100.0, exit_price=110.0, trade_id=42,
    ))
    assert pid == 42, pid
    assert len(svc._client.upserts) == 1
    pt = svc._client.upserts[0]
    assert pt.payload["symbol"] == "BTCUSDT"
    assert pt.payload["base"] == "BTC"
    assert pt.payload["win"] == 1
    assert pt.payload["pnl"] == 12.5
    norm = math.sqrt(sum(x * x for x in pt.vector))
    assert abs(norm - 1.0) < 1e-9, norm
    print("  OK — upserted normalised vector with correct payload")


def test_recall_bullish_cluster():
    print("test_recall_bullish_cluster")
    hits = [
        _Hit(0.95, {"symbol": "BTCUSDT", "direction": "BUY", "pnl": 8.0, "win": 1}),
        _Hit(0.92, {"symbol": "BTCUSDT", "direction": "BUY", "pnl": 5.0, "win": 1}),
        _Hit(0.90, {"symbol": "BTCUSDT", "direction": "BUY", "pnl": 6.0, "win": 1}),
        _Hit(0.88, {"symbol": "BTCUSDT", "direction": "BUY", "pnl": -1.0, "win": 0}),
    ]
    svc = _make_service(search_hits=hits)
    res = asyncio.run(svc.recall_similar({"regime": "TRENDING"}, symbol="BTCUSDT"))
    assert res.signal == "bullish", res.signal
    assert res.samples == 4
    assert res.win_rate == 0.75
    assert res.avg_pnl > 0
    assert 0 < res.confidence <= 0.6
    print(f"  OK — {res.reasoning} (conf {res.confidence})")


def test_recall_bearish_cluster():
    print("test_recall_bearish_cluster")
    hits = [
        _Hit(0.95, {"symbol": "ETHUSDT", "direction": "BUY", "pnl": -8.0, "win": 0}),
        _Hit(0.93, {"symbol": "ETHUSDT", "direction": "BUY", "pnl": -5.0, "win": 0}),
        _Hit(0.91, {"symbol": "ETHUSDT", "direction": "BUY", "pnl": -3.0, "win": 0}),
        _Hit(0.89, {"symbol": "ETHUSDT", "direction": "BUY", "pnl": 1.0, "win": 1}),
    ]
    svc = _make_service(search_hits=hits)
    res = asyncio.run(svc.recall_similar({"regime": "RANGING"}, symbol="ETHUSDT"))
    assert res.signal == "bearish", res.signal
    assert res.avg_pnl < 0
    print(f"  OK — {res.reasoning} (conf {res.confidence})")


def test_recall_below_min_samples_neutral():
    print("test_recall_below_min_samples_neutral")
    hits = [
        _Hit(0.95, {"symbol": "SOLUSDT", "direction": "BUY", "pnl": 8.0, "win": 1}),
        _Hit(0.92, {"symbol": "SOLUSDT", "direction": "BUY", "pnl": 5.0, "win": 1}),
    ]
    svc = _make_service(search_hits=hits)
    res = asyncio.run(svc.recall_similar({}, symbol="SOLUSDT"))
    assert res.signal == "neutral", res.signal
    assert res.confidence == 0.0
    assert res.samples == 2
    print(f"  OK — {res.reasoning}")


def test_to_dict_trims_neighbours():
    print("test_to_dict_trims_neighbours")
    r = RecallResult(signal="bullish", confidence=0.3, samples=1,
                     neighbours=[{"symbol": "BTCUSDT", "direction": "BUY",
                                  "pnl": 1.0, "win": 1, "score": 0.9,
                                  "closed_at": "x", "extra": "drop me"}])
    d = r.to_dict()
    assert "extra" not in d["neighbours"][0]
    assert set(d["neighbours"][0].keys()) == {"symbol", "direction", "pnl", "score", "closed_at"}
    print("  OK — neighbour payloads trimmed for metadata")


if __name__ == "__main__":
    test_feature_vector_deterministic_and_normalised()
    test_extract_features_ranges()
    test_record_trade_upserts_normalised_vector()
    test_recall_bullish_cluster()
    test_recall_bearish_cluster()
    test_recall_below_min_samples_neutral()
    test_to_dict_trims_neighbours()
    print("\nALL TESTS PASSED \u2713")
