# Skill Miner & MCP Server

Two features that sit on top of the semantic trade memory (Track C):

1. **Skill Miner** — distills individual remembered trades into named, scored,
   reusable **strategy skills** that the opinion layer votes with.
2. **MCP Server** — exposes the live backend to MCP-compatible agents (Claude
   Desktop, Cursor, Agent Zero) as a curated set of tools.

---

## 1. Skill Miner

### Why

Track C (`trade_memory`) remembers *individual* trades as vectors. The skill
miner is the next abstraction up: it groups those trades into recurring
**market-setup archetypes** ("skills"), scores each one's realised edge, names
it, and persists it. At decision time the agent matches the *current* setup to
its best-fitting learned skill and votes with that skill's historical bias — a
compact, inspectable, leaderboard-able distillation of "what has actually worked
for this agent."

### Design principles (consistent with Track C)

- **No heavy ML.** Clustering is a deterministic greedy cosine grouping over the
  same feature vectors `trade_memory` already produces. A "skill" is simply a
  cluster of trades whose market context was similar.
- **Pure, testable core.** All clustering/scoring/naming logic is pure functions
  (no DB), unit-tested in `backend/tests/unit/test_skill_miner.py`.
- **Resilience-first.** Mining never raises into any caller — it returns a
  structured summary. Fully env-toggleable. No mandatory external dependency.

### Pipeline

```
closed trades (SQL)
   │  _load_samples()          → TradeSample(symbol, direction, pnl, context)
   ▼
feature_vector(context)        → reuses trade_memory's deterministic embedding
   ▼
cluster_samples()              → greedy cosine clustering (threshold 0.80)
   │   seeds clusters with the strongest |pnl| outcomes first (deterministic)
   ▼
score_cluster()                → direction, win_rate, avg/total pnl, sharpe, edge
   ▼
name_skill()                   → "Trending-up bullish-momentum overbought → bullish [BTCUSDT]"
   ▼
skill_key_for()                → stable 16-char key (0.25-quantised centroid + direction)
   ▼
_upsert()                      → idempotent write to strategy_skills; stale skills deactivated
```

### Scoring

`edge_score ∈ [0,1] = decisiveness × sample-support × consistency`

- **decisiveness** = `min(|win_rate − 0.5| × 2, 1)` — how far from a coin flip.
- **sample-support** = `min(n / 10, 1)` — more trades → more trustworthy.
- **consistency** = `min(|sharpe| / 2, 1)` — tighter PnL distribution → higher.

Direction is `bullish` (avg_pnl>0 & win_rate≥0.5), `bearish` (avg_pnl<0 &
win_rate≤0.5), else `neutral`.

### Persistence — `strategy_skills` table

`StrategySkill` in `backend/database/models.py`: `skill_key` (unique), `name`,
`description`, `centroid` (JSON), `feature_summary` (JSON), `direction`,
`sample_count`, `win_rate`, `avg_pnl`, `total_pnl`, `sharpe`, `edge_score`,
`symbols` (JSON), `active`, timestamps. Auto-created via
`Base.metadata.create_all` at startup.

The `skill_key` quantises the leading centroid dims into 0.25 buckets, so
re-mining a slightly drifted version of the same archetype **updates the same
row** rather than forking a new skill. Archetypes that stop appearing are marked
`active = False`.

### Opinion-layer integration

`opinion_layer.py` adds a `learned_skill` agent vote: it builds the live market
context, calls `skill_miner.match_skill()` (cosine match against active skills),
and — if the best match is bullish/bearish with positive confidence — votes in
that direction. `confidence = min(0.5, edge_score × similarity)`. Default weight
`learned_skill: 0.10` (alongside `semantic_trade_memory: 0.10`). The match runs
via `asyncio.to_thread` and never blocks/raises into the decision path.

### Background loop

`main.py` startup launches `skill_miner.run_miner_loop()` (gated by
`SKILL_MINER_ENABLED`): an initial mine, then a re-mine every
`SKILL_MINE_INTERVAL_MIN` (default 6h).

### HTTP API (prefix `/trading`)

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/skills/status` | Miner config + active/total skill counts |
| GET | `/skills?active_only=&limit=` | List skills (edge-ranked) |
| GET | `/skills/leaderboard?limit=` | Top skills by edge score |
| POST | `/skills/mine?limit=` | Trigger a mining pass (idempotent) |
| POST | `/skills/match` `{context}` | Best learned skill for a context |

### Environment

| Var | Default | Meaning |
| --- | --- | --- |
| `SKILL_MINER_ENABLED` | `true` | Master toggle |
| `SKILL_MIN_CLUSTER_SIZE` | `4` | Trades needed to form a skill |
| `SKILL_SIMILARITY_THRESHOLD` | `0.80` | Cosine cutoff for same cluster / match |
| `SKILL_MINE_INTERVAL_MIN` | `360` | Background re-mine cadence (min) |
| `SKILL_MINE_LOOKBACK` | `2000` | Closed trades scanned per mine |
| `TRADE_MEMORY_VECTOR_SIZE` | `64` | Embedding width (shared with Track C) |

### Tests

`backend/tests/unit/test_skill_miner.py` — 14 mockless test functions covering
`_cosine`, `_normalise`, `_centroid`, `_stdev`, `cluster_samples` (grouping +
determinism), `score_cluster` (bullish/bearish/neutral/zero-variance),
`name_skill`, `skill_key_for` (stability + direction sensitivity + drift
bucketing), and `mine_skills` (min-cluster filtering, edge sorting, empty input).

```bash
PYTHONPATH=/home/user/workspace/atp python backend/tests/unit/test_skill_miner.py
```

---

## 2. MCP Server

### Why

Lets an LLM agent (Claude Desktop / Cursor / Agent Zero) drive the trading
backend directly — inspect the multi-agent opinion, query trade memory and mined
skills, view the portfolio, and control the loops — through the Model Context
Protocol.

### Design

A thin [FastMCP](https://gofastmcp.com) HTTP proxy in `mcp_server/server.py`. It
owns **no** database/exchange/model credentials; it only needs network access to
the backend (and an optional bearer token). Runs as its own lightweight
process/container.

Two modes:

- **Curated (default)** — 16 hand-picked `@mcp.tool` functions with clear names
  and docstrings so agents call them reliably (see table in
  `mcp_server/README.md`). Includes a `call_backend` escape hatch for routes
  without a dedicated tool.
- **Full API (`FULL_API=true`)** — auto-generate one tool per FastAPI route from
  the backend's live `/openapi.json` via `FastMCP.from_openapi`. Comprehensive
  but noisy; falls back to the curated server on any failure.

### Run

```bash
# stdio (Claude Desktop / Cursor spawn the process)
MCP_TRANSPORT=stdio BACKEND_BASE_URL=http://72.60.18.113:8001 BACKEND_API_PREFIX=/api \
  python -m mcp_server.server

# HTTP service (VPS, shared) — serves at http://<host>:9100/mcp
MCP_TRANSPORT=http MCP_PORT=9100 BACKEND_BASE_URL=http://ai-trading-backend:8000 \
  python -m mcp_server.server
```

Docker: `docker build -t quantumtrade-mcp -f mcp_server/Dockerfile .` then run on
the same compose network as `ai-trading-backend`. Full env table, Claude Desktop
and Cursor configs, and security notes are in `mcp_server/README.md`.

### Files

- `mcp_server/server.py` — the server (curated tools + optional full-API).
- `mcp_server/requirements.txt` — `fastmcp`, `httpx`.
- `mcp_server/README.md` — run/config/integration guide.
- `mcp_server/Dockerfile` — containerised HTTP deployment.
- `mcp_server/__init__.py` — package marker.

### Verified

Server imports cleanly under fastmcp 3.x and registers all 16 curated tools;
URL prefixing avoids a double `/api` when callers pass either `/trading/...` or
`/api/trading/...`.
