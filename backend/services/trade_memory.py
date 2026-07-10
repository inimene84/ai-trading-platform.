"""
Trade Memory — Semantic Recall of Past Trades (Track C)
=======================================================
Closes the learning loop: every closed trade is stored in a dedicated Qdrant
collection as a *market-context vector* plus its realised outcome (PnL, win/loss).
At decision time the opinion layer asks "what happened the last time the market
looked like this?" and gets back the win-rate / avg-PnL of the most *similar*
historical setups — a directional bias grounded in the system's own track record,
not just the most recent N trades.

Design goals (mirrors the native sentiment loop philosophy):
  • No hard dependency on an external embeddings API. The default embedding is a
    deterministic numeric feature vector derived from the trade's own market
    context (regime, momentum, RSI, volatility, funding, sentiment, direction).
    Similarity over these features means "similar market regime", which is the
    meaningful notion of similarity for trades.
  • Optional richer semantics via the LiteLLM proxy (set TRADE_MEMORY_USE_LLM=true)
    — falls back silently to the feature vector if the embedding call fails.
  • Never raises into the trading path. Every public method degrades to a safe
    neutral result on any error.

Env:
  TRADE_MEMORY_ENABLED         (default true)
  QDRANT_COLLECTION_TRADE_MEMORY (default "trade-memory")
  TRADE_MEMORY_VECTOR_SIZE     (default 64)   # feature-vector dim, padded
  TRADE_MEMORY_RECALL_K        (default 8)    # neighbours to consider
  TRADE_MEMORY_MIN_SAMPLES     (default 3)    # min neighbours before emitting bias
  TRADE_MEMORY_USE_LLM         (default false)
"""

from __future__ import annotations

import os
import math
import time
import logging
import hashlib
import asyncio
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.utils.embeddings import generate_text_embedding

logger = logging.getLogger(__name__)

# Reuse the existing async Qdrant client primitives. We talk to a *separate*
# collection so we never collide with the crypto-news archive.
try:
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.models import (
        VectorParams, Distance, PointStruct, Filter, FieldCondition, MatchValue,
    )
    _QDRANT_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when lib is absent
    _QDRANT_AVAILABLE = False
    AsyncQdrantClient = None  # type: ignore

    # Lightweight stand-ins so the module imports and unit tests run even when
    # qdrant-client is not installed (mirrors qdrant_client.py's own fallback).
    class VectorParams:  # type: ignore
        def __init__(self, size=0, distance=None):
            self.size = size
            self.distance = distance

    class Distance:  # type: ignore
        COSINE = "Cosine"

    class PointStruct:  # type: ignore
        def __init__(self, id=None, vector=None, payload=None):
            self.id = id
            self.vector = vector
            self.payload = payload

    class Filter:  # type: ignore
        def __init__(self, must=None, should=None, must_not=None):
            self.must = must
            self.should = should
            self.must_not = must_not

    class FieldCondition:  # type: ignore
        def __init__(self, key=None, match=None):
            self.key = key
            self.match = match

    class MatchValue:  # type: ignore
        def __init__(self, value=None):
            self.value = value


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# ─────────────────────────────────────────────────────────────────────────────
# Feature extraction → deterministic embedding
# ─────────────────────────────────────────────────────────────────────────────

VECTOR_SIZE = int(os.getenv("TRADE_MEMORY_VECTOR_SIZE", "64"))


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def extract_features(context: Dict[str, Any]) -> Dict[str, float]:
    """Reduce a heterogeneous market-context dict to a small set of normalised
    features in roughly [-1, 1]. Missing inputs default to 0.0 (neutral).

    Accepted context keys (all optional):
      direction        "BUY"/"LONG" -> +1, "SELL"/"SHORT" -> -1
      regime           "TRENDING"/"TREND_UP" -> +1, "RANGING"/"CHOP" -> 0, "TREND_DOWN" -> -1
      trend_signal     -1..1 (or "bullish"/"bearish"/"neutral")
      momentum_signal  -1..1 (or string)
      mean_reversion_signal  -1..1 (or string)
      volatility       0..1   (normalised; e.g. ATR/price)
      rsi              0..100
      funding_rate     fraction, e.g. 0.0001
      sentiment_score  -1..1
      kronos_change_pct  signed % expected move
      confidence       0..1
    """
    f: Dict[str, float] = {}

    def _signal_to_num(v: Any) -> float:
        if isinstance(v, (int, float)):
            return _clip(float(v))
        s = str(v or "").lower()
        if s in ("bullish", "buy", "long", "up"):
            return 1.0
        if s in ("bearish", "sell", "short", "down"):
            return -1.0
        return 0.0

    f["direction"] = _signal_to_num(context.get("direction"))

    regime = str(context.get("regime", "")).upper()
    if "DOWN" in regime:
        f["regime"] = -1.0
    elif "TREND" in regime or "UP" in regime:
        f["regime"] = 1.0
    else:  # RANGING / CHOP / unknown
        f["regime"] = 0.0

    f["trend"] = _signal_to_num(context.get("trend_signal"))
    f["momentum"] = _signal_to_num(context.get("momentum_signal"))
    f["mean_reversion"] = _signal_to_num(context.get("mean_reversion_signal"))

    vol = context.get("volatility")
    f["volatility"] = _clip(float(vol) if vol is not None else 0.0, 0.0, 1.0)

    rsi = context.get("rsi")
    # Map RSI 0..100 -> -1..1 centred on 50.
    f["rsi"] = _clip(((float(rsi) - 50.0) / 50.0) if rsi is not None else 0.0)

    fr = context.get("funding_rate")
    # Funding is tiny (~0.0001). Scale ×1000 then clip.
    f["funding"] = _clip((float(fr) * 1000.0) if fr is not None else 0.0)

    f["sentiment"] = _clip(float(context.get("sentiment_score", 0.0) or 0.0))

    kc = context.get("kronos_change_pct")
    # Expected % move; ±5% saturates.
    f["kronos"] = _clip((float(kc) / 5.0) if kc is not None else 0.0)

    f["confidence"] = _clip(float(context.get("confidence", 0.0) or 0.0), 0.0, 1.0)

    return f


# Fixed feature order so the vector is stable across calls/processes.
_FEATURE_ORDER = [
    "direction", "regime", "trend", "momentum", "mean_reversion",
    "volatility", "rsi", "funding", "sentiment", "kronos", "confidence",
]


def feature_vector(context: Dict[str, Any], size: int = VECTOR_SIZE) -> List[float]:
    """Deterministic L2-normalised embedding for a market context.

    The first len(_FEATURE_ORDER) dims carry the real features. Remaining dims
    are filled with a deterministic low-amplitude hash expansion of the features
    so that cosine distance still discriminates while keeping vectors a fixed
    width that Qdrant collections require.
    """
    feats = extract_features(context)
    base = [feats[k] for k in _FEATURE_ORDER]

    vec = list(base)
    if size > len(base):
        # Deterministic pseudo-random expansion seeded by the feature values,
        # low amplitude so it never dominates the real features.
        seed = ",".join(f"{x:.4f}" for x in base)
        h = hashlib.sha256(seed.encode()).digest()
        i = 0
        while len(vec) < size:
            byte = h[i % len(h)]
            # map 0..255 -> roughly [-0.15, 0.15]
            vec.append(((byte / 255.0) - 0.5) * 0.3)
            i += 1
    else:
        vec = vec[:size]

    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


# ─────────────────────────────────────────────────────────────────────────────
# Recall result type
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RecallResult:
    """Outcome of a semantic recall over past trades."""
    signal: str = "neutral"          # bullish | bearish | neutral
    confidence: float = 0.0          # 0..1
    samples: int = 0                 # neighbours considered
    win_rate: float = 0.0            # 0..1 among neighbours
    avg_pnl: float = 0.0             # average realised PnL among neighbours
    avg_similarity: float = 0.0      # mean cosine score of neighbours
    reasoning: str = "No comparable historical trades"
    neighbours: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        # Trim neighbour payloads for compactness in API/opinion metadata.
        d["neighbours"] = [
            {k: n.get(k) for k in ("symbol", "direction", "pnl", "score", "closed_at")}
            for n in self.neighbours
        ]
        return d


# ─────────────────────────────────────────────────────────────────────────────
# Service
# ─────────────────────────────────────────────────────────────────────────────

class TradeMemoryService:
    """Stores closed trades as context vectors and recalls similar setups."""

    def __init__(self):
        self.enabled = _env_bool("TRADE_MEMORY_ENABLED", True)
        self.collection = os.getenv("QDRANT_COLLECTION_TRADE_MEMORY", "trade-memory")
        self.vector_size = VECTOR_SIZE
        self.recall_k = int(os.getenv("TRADE_MEMORY_RECALL_K", "8"))
        self.min_samples = int(os.getenv("TRADE_MEMORY_MIN_SAMPLES", "3"))
        self.use_llm = _env_bool("TRADE_MEMORY_USE_LLM", False)

        self.url = os.getenv("QDRANT_URL", "http://qdrant:6333")
        self.api_key = os.getenv("QDRANT_API_KEY", "")

        self._client = None
        self._collection_ready = False
        self._task = None
        if _QDRANT_AVAILABLE and self.enabled:
            try:
                self._client = AsyncQdrantClient(
                    url=self.url,
                    api_key=self.api_key or None,
                    timeout=5.0,
                )
            except Exception as e:  # pragma: no cover
                logger.warning(f"TradeMemory: Qdrant client init failed: {e}")
                self._client = None
        elif not _QDRANT_AVAILABLE:
            logger.warning("TradeMemory: qdrant-client not installed — recall disabled")

    def start(self) -> None:
        """Start the background recorder loop task."""
        import asyncio
        if not self.enabled or not self._client:
            return
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self.run_recorder_loop())
        logger.info("TradeMemory service loop started")

    async def stop(self) -> None:
        """Stop the background recorder loop task gracefully."""
        if self._task and not self._task.done():
            logger.info("Stopping TradeMemory recorder loop...")
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            logger.info("TradeMemory recorder loop stopped")
        self._task = None

    # ── internals ────────────────────────────────────────────────────────────

    async def _ensure_collection(self) -> bool:
        if not self._client:
            return False
        if self._collection_ready:
            return True
        try:
            cols = await self._client.get_collections()
            exists = any(c.name == self.collection for c in cols.collections)
            if not exists:
                await self._client.create_collection(
                    collection_name=self.collection,
                    vectors_config=VectorParams(
                        size=self.vector_size, distance=Distance.COSINE
                    ),
                )
                logger.info(f"TradeMemory: created collection {self.collection}")
            self._collection_ready = True
            return True
        except Exception as e:
            logger.error(f"TradeMemory: ensure_collection failed: {e}")
            return False

    async def _embed(self, context: Dict[str, Any]) -> List[float]:
        """Return an embedding for the context. Deterministic feature vector by
        default; optional LiteLLM embedding when TRADE_MEMORY_USE_LLM=true."""
        if not self.use_llm:
            return feature_vector(context, self.vector_size)
        try:
            vec = await self._llm_embed(context)
            if vec:
                return vec
        except Exception as e:
            logger.warning(f"TradeMemory: LLM embed failed, using features: {e}")
        return feature_vector(context, self.vector_size)

    async def _llm_embed(self, context: Dict[str, Any]) -> Optional[List[float]]:
        """Best-effort embedding via OpenRouter (fallback LiteLLM)."""
        text = _context_to_text(context)
        return await generate_text_embedding(
            text, vector_size=self.vector_size, normalize=True
        )

    # ── public: record ────────────────────────────────────────────────────────

    async def record_trade(
        self,
        *,
        symbol: str,
        direction: str,
        pnl: float,
        context: Dict[str, Any],
        entry_price: Optional[float] = None,
        exit_price: Optional[float] = None,
        closed_at: Optional[str] = None,
        trade_id: Optional[int] = None,
        strategy: Optional[str] = None,
    ) -> Optional[int]:
        """Persist a closed trade outcome as a context vector. Returns point id
        or None if disabled/unavailable. Never raises."""
        if not self.enabled or not self._client:
            return None
        try:
            if not await self._ensure_collection():
                return None
            ctx = dict(context or {})
            ctx.setdefault("direction", direction)
            vec = await self._embed(ctx)
            point_id = trade_id if trade_id is not None else int(time.time() * 1_000_000)
            payload = {
                "symbol": symbol,
                "base": symbol.replace("USDT", "").replace("USD", "") or symbol,
                "direction": (direction or "").upper(),
                "pnl": float(pnl or 0.0),
                "win": 1 if (pnl or 0.0) > 0 else 0,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "strategy": strategy,
                "closed_at": closed_at or datetime.now(timezone.utc).isoformat(),
                "context": ctx,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
            await self._client.upsert(
                collection_name=self.collection,
                points=[PointStruct(id=point_id, vector=vec, payload=payload)],
            )
            return point_id
        except Exception as e:
            logger.warning(f"TradeMemory: record_trade failed for {symbol}: {e}")
            return None

    # ── public: recall ─────────────────────────────────────────────────────────

    async def recall_similar(
        self,
        context: Dict[str, Any],
        *,
        symbol: Optional[str] = None,
        limit: Optional[int] = None,
        same_symbol_only: bool = False,
    ) -> RecallResult:
        """Find the most similar historical setups and summarise how they
        resolved into a directional bias. Never raises — returns a neutral
        RecallResult on any error."""
        if not self.enabled or not self._client:
            return RecallResult(reasoning="Trade memory disabled/unavailable")
        try:
            if not await self._ensure_collection():
                return RecallResult(reasoning="Trade memory collection unavailable")

            k = limit or self.recall_k
            vec = await self._embed(context)

            qfilter = None
            if same_symbol_only and symbol:
                base = symbol.replace("USDT", "").replace("USD", "") or symbol
                qfilter = Filter(
                    must=[FieldCondition(key="base", match=MatchValue(value=base))]
                )

            resp = await self._client.query_points(
                collection_name=self.collection,
                query=vec,
                limit=k,
                query_filter=qfilter,
                with_payload=True,
            )
            results = getattr(resp, "points", resp) or []
            return self._summarise(results)
        except Exception as e:
            logger.warning(f"TradeMemory: recall_similar failed: {e}")
            return RecallResult(reasoning=f"Recall error: {e}")

    def _summarise(self, results: List[Any]) -> RecallResult:
        neighbours: List[dict] = []
        for r in results:
            pl = getattr(r, "payload", None) or {}
            neighbours.append({
                "symbol": pl.get("symbol"),
                "direction": pl.get("direction"),
                "pnl": float(pl.get("pnl", 0.0) or 0.0),
                "win": int(pl.get("win", 0) or 0),
                "score": float(getattr(r, "score", 0.0) or 0.0),
                "closed_at": pl.get("closed_at"),
            })

        n = len(neighbours)
        if n < self.min_samples:
            return RecallResult(
                signal="neutral", confidence=0.0, samples=n,
                reasoning=(
                    f"Only {n} comparable trade(s) (need {self.min_samples}) — "
                    "no semantic bias"
                ),
                neighbours=neighbours,
            )

        wins = sum(x["win"] for x in neighbours)
        win_rate = wins / n
        avg_pnl = sum(x["pnl"] for x in neighbours) / n
        avg_sim = sum(x["score"] for x in neighbours) / n

        # Direction: did similar setups make money on average?
        if avg_pnl > 0 and win_rate >= 0.5:
            signal = "bullish"
        elif avg_pnl < 0 and win_rate <= 0.5:
            signal = "bearish"
        else:
            signal = "neutral"

        # Confidence blends edge strength, win-rate decisiveness, neighbour
        # similarity, and sample size. Capped at 0.6 so memory informs rather
        # than dominates the ensemble.
        edge = min(abs(win_rate - 0.5) * 2.0, 1.0)         # 0..1
        sim_factor = max(0.0, min(avg_sim, 1.0))            # cosine 0..1
        sample_factor = min(n / max(self.recall_k, 1), 1.0)
        confidence = round(min(0.6, edge * 0.6 * sim_factor * (0.5 + 0.5 * sample_factor)), 4)

        reasoning = (
            f"{n} similar setups: {wins}W/{n-wins}L "
            f"(win-rate {win_rate:.0%}), avg PnL {avg_pnl:+.4f}, "
            f"mean similarity {avg_sim:.2f} → {signal}"
        )
        return RecallResult(
            signal=signal, confidence=confidence, samples=n,
            win_rate=round(win_rate, 4), avg_pnl=round(avg_pnl, 6),
            avg_similarity=round(avg_sim, 4), reasoning=reasoning,
            neighbours=neighbours,
        )

    # ── public: ops ─────────────────────────────────────────────────────────────

    async def status(self) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "enabled": self.enabled,
            "qdrant_available": _QDRANT_AVAILABLE,
            "collection": self.collection,
            "vector_size": self.vector_size,
            "recall_k": self.recall_k,
            "min_samples": self.min_samples,
            "use_llm": self.use_llm,
            "points_count": 0,
            "client": bool(self._client),
        }
        if self._client:
            try:
                await self._ensure_collection()
                ci = await self._client.get_collection(collection_name=self.collection)
                info["points_count"] = getattr(ci, "points_count", 0)
            except Exception as e:
                info["error"] = str(e)
        return info

    async def record_recent_closed(self, lookback: int = 100) -> Dict[str, Any]:
        """Idempotently upsert the most recently closed trades into Qdrant.
        Designed to be called on a timer so the hot trading path never has to.
        Because point id == trade id, repeated calls are safe no-op upserts."""
        return await self.backfill_from_sql(limit=lookback)

    async def run_recorder_loop(self) -> None:
        """Background loop: periodically sync newly-closed trades into Qdrant so
        recall stays current without touching the trading hot path. Never
        raises out of the loop. Cadence: TRADE_MEMORY_RECORD_INTERVAL_MIN."""
        import asyncio
        if not self.enabled or not self._client:
            logger.info("TradeMemory recorder loop not started (disabled/unavailable)")
            return
        interval = max(1, int(os.getenv("TRADE_MEMORY_RECORD_INTERVAL_MIN", "15")))
        lookback = int(os.getenv("TRADE_MEMORY_RECORD_LOOKBACK", "100"))
        logger.info(
            f"TradeMemory recorder loop started (every {interval}m, lookback {lookback})"
        )
        # One immediate pass so history is available shortly after boot.
        try:
            res = await self.record_recent_closed(lookback)
            logger.info(f"TradeMemory initial sync: {res}")
        except Exception as e:
            logger.warning(f"TradeMemory initial sync failed: {e}")
        while True:
            try:
                await asyncio.sleep(interval * 60)
                res = await self.record_recent_closed(lookback)
                if res.get("stored"):
                    logger.info(f"TradeMemory sync: {res}")
            except asyncio.CancelledError:
                logger.info("TradeMemory recorder loop cancelled")
                raise
            except Exception as e:
                logger.warning(f"TradeMemory recorder loop iteration failed: {e}")

    async def backfill_from_sql(self, limit: int = 1000) -> Dict[str, Any]:
        """Vectorise existing closed trades from the SQL `trades` table so recall
        has history immediately. Idempotent: uses the trade id as the point id,
        so re-running upserts in place. Never raises."""
        if not self.enabled or not self._client:
            return {"ok": False, "reason": "disabled_or_unavailable", "stored": 0}
        stored = 0
        skipped = 0
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import Trade
            import sqlalchemy as sa

            with SessionLocal() as db:
                trades = (
                    db.query(Trade)
                    .filter(sa.and_(
                        Trade.status == "closed",
                        Trade.pnl.isnot(None),
                        Trade.exit_price.isnot(None),
                    ))
                    .order_by(Trade.closed_at.desc())
                    .limit(limit)
                    .all()
                )
            for t in trades:
                ctx = _trade_row_to_context(t)
                pid = await self.record_trade(
                    symbol=t.symbol,
                    direction=t.direction,
                    pnl=float(t.pnl),
                    context=ctx,
                    entry_price=t.entry_price,
                    exit_price=t.exit_price,
                    closed_at=t.closed_at.isoformat() if t.closed_at else None,
                    trade_id=t.id,
                    strategy=t.strategy,
                )
                if pid is not None:
                    stored += 1
                else:
                    skipped += 1
            return {"ok": True, "stored": stored, "skipped": skipped,
                    "total_closed": len(trades)}
        except Exception as e:
            logger.warning(f"TradeMemory: backfill failed: {e}")
            return {"ok": False, "reason": str(e), "stored": stored}


def _trade_row_to_context(t: Any) -> Dict[str, Any]:
    """Derive a minimal market context from a SQL Trade row. Without a stored
    snapshot we only know direction + realised move, which still clusters
    winners vs losers per direction — useful, and enriched going forward when
    live trades record full context."""
    ctx: Dict[str, Any] = {"direction": t.direction}
    try:
        if t.entry_price and t.exit_price:
            move = (t.exit_price - t.entry_price) / t.entry_price
            ctx["kronos_change_pct"] = move * 100.0  # realised move as a proxy feature
    except Exception:
        pass
    if getattr(t, "strategy", None):
        ctx["strategy"] = t.strategy
    return ctx


def _context_to_text(context: Dict[str, Any]) -> str:
    """Human-readable serialisation of a context for the optional LLM embedding."""
    parts = []
    for k in _FEATURE_ORDER:
        if k in context:
            parts.append(f"{k}={context[k]}")
    for k, v in context.items():
        if k not in _FEATURE_ORDER:
            parts.append(f"{k}={v}")
    return "trade setup: " + ", ".join(parts) if parts else "trade setup: neutral"


# Singleton
trade_memory = TradeMemoryService()
