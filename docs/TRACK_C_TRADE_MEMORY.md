# Track C — Semantic Trade Memory (Closed Learning Loop)

Gives the agent a memory of its *own* track record. Every closed trade is stored
in a dedicated Qdrant collection as a **market-context vector** + its realised
outcome. At decision time the opinion layer asks **"what happened the last time
the market looked like this?"** and turns the answer into a directional bias.

This is different from the existing `trade_memory` agent, which only looks at the
most *recent* N trades for the same symbol (a simple SQL win/loss aggregate). The
new `semantic_trade_memory` agent finds the most *similar* historical setups
across all symbols and weights the decision by how those analogues resolved.

## Components

| File | What it does |
|------|--------------|
| `backend/services/trade_memory.py` | `TradeMemoryService` singleton `trade_memory`. Embedding, store, recall, backfill, recorder loop. |
| `backend/services/opinion_layer.py` | New `semantic_trade_memory` `AgentOpinion` + `_build_market_context()` helper. |
| `backend/main.py` | Auto-starts the recorder loop on startup (gated by `TRADE_MEMORY_ENABLED`). |
| `backend/routes/trading.py` | `GET /trade-memory/status`, `POST /trade-memory/backfill`, `POST /trade-memory/recall`. |
| `backend/tests/unit/test_trade_memory.py` | 7 unit tests (mocked Qdrant). |

## How the embedding works (no external API required)

The default embedding is a **deterministic numeric feature vector** built from the
trade's own market context — not a text embedding. For trades, "similar" means
"similar market regime", so the meaningful features are:

`direction, regime, trend, momentum, mean_reversion, volatility, rsi, funding,
sentiment, kronos, confidence`

Each is normalised to roughly `[-1, 1]`, the vector is padded to a fixed width
(`TRADE_MEMORY_VECTOR_SIZE`, default 64) with a low-amplitude deterministic hash
expansion, then L2-normalised for cosine similarity. This means:

- **No network dependency** — recall works even if every LLM/embedding API is down.
- **Deterministic** — the same setup always maps to the same vector.

An optional richer path (`TRADE_MEMORY_USE_LLM=true`) calls the LiteLLM proxy's
`/embeddings` endpoint and falls back silently to the feature vector on any error.

## Decision integration

In `analyze_symbol()`, after the existing aggregate trade-memory block:

1. `_build_market_context()` assembles the current setup from the opinions already
   computed this cycle (technical sub-signals, Kronos forecast, social sentiment)
   plus `metrics` (funding_rate, regime, rsi).
2. `trade_memory.recall_similar(ctx, symbol=symbol)` returns the K nearest
   historical setups and summarises them:
   - `signal`: bullish if similar setups were net-profitable with win-rate ≥ 50%,
     bearish if net-losing with win-rate ≤ 50%, else neutral.
   - `confidence`: blends edge strength (|win_rate − 0.5|), neighbour similarity,
     and sample size. **Capped at 0.6** so memory informs but never dominates the
     ensemble.
3. Only emitted as an opinion when `samples ≥ TRADE_MEMORY_MIN_SAMPLES` and
   confidence > 0. Always written to `metrics["semantic_trade_memory"]` for
   observability even when not voting.

The new agent flows through the existing weighted aggregation — tune its weight
with the existing `POST /trading/opinion/weights` endpoint (agent name:
`semantic_trade_memory`).

## Recording (never touches the hot trading path)

Rather than editing every trade-close site, a background **recorder loop**
(`run_recorder_loop`) periodically upserts recently-closed trades from SQL into
Qdrant. Because the Qdrant point id **equals the SQL trade id**, re-running is an
idempotent no-op upsert. Cadence: `TRADE_MEMORY_RECORD_INTERVAL_MIN` (default 15).

Trades recorded this way carry a minimal context (direction + realised move). Live
trades get richer context automatically as the system evolves; you can also call
`record_trade(..., context=<full snapshot>)` directly from a close site later if
you want full-fidelity context per trade.

## Operations

```bash
# Status (collection name, points_count, config)
curl -s localhost:8001/api/trading/trade-memory/status | jq

# One-time backfill of all historical closed trades
curl -s -X POST 'localhost:8001/api/trading/trade-memory/backfill?limit=5000' | jq

# Debug a recall for an arbitrary setup
curl -s -X POST localhost:8001/api/trading/trade-memory/recall \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"BTCUSDT","context":{"regime":"TRENDING","momentum_signal":"bullish","rsi":62,"funding_rate":0.0001}}' | jq
```

## Environment variables

| Var | Default | Meaning |
|-----|---------|---------|
| `TRADE_MEMORY_ENABLED` | `true` | Master toggle (recorder loop + recall). |
| `QDRANT_COLLECTION_TRADE_MEMORY` | `trade-memory` | Collection name (separate from `crypto-news`). |
| `TRADE_MEMORY_VECTOR_SIZE` | `64` | Feature-vector width. |
| `TRADE_MEMORY_RECALL_K` | `8` | Neighbours considered per recall. |
| `TRADE_MEMORY_MIN_SAMPLES` | `3` | Min neighbours before emitting a bias. |
| `TRADE_MEMORY_USE_LLM` | `false` | Use LiteLLM embeddings instead of the feature vector. |
| `TRADE_MEMORY_EMBED_MODEL` | `text-embedding-3-small` | Embedding model when LLM path is on. |
| `TRADE_MEMORY_RECORD_INTERVAL_MIN` | `15` | Recorder loop cadence. |
| `TRADE_MEMORY_RECORD_LOOKBACK` | `100` | Trades synced per recorder pass. |

## Rollout

1. Deploy backend. On startup the recorder loop runs one immediate sync.
2. `POST /trading/trade-memory/backfill?limit=5000` once to vectorise full history.
3. Watch `GET /trade-memory/status` → `points_count` climbs.
4. The `semantic_trade_memory` agent starts voting once a symbol's setups have
   ≥ `TRADE_MEMORY_MIN_SAMPLES` similar historical analogues.

## Safety properties

- Never raises into the trading path — every method degrades to a neutral result.
- No external API dependency by default.
- Confidence hard-capped at 0.6.
- Hot trading path untouched (recording is out-of-band).
