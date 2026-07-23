# QuantumTrade Pro — MCP Server

A lightweight [FastMCP](https://gofastmcp.com) server that exposes the live
QuantumTrade Pro trading backend to MCP-compatible agents — **Claude Desktop,
Cursor, Agent Zero, Continue, etc.** It is a thin HTTP proxy in front of the
FastAPI backend: it owns **no** database, exchange keys, or model credentials.
All it needs is network access to the backend and (optionally) a bearer token.

## What it exposes

By default the server publishes a **curated** set of high-signal tools with clear
names and docstrings so LLM agents call them reliably:

| Tool | Backend route | Purpose |
| --- | --- | --- |
| `get_trading_opinion(symbol, bars?)` | `POST /trading/opinion/analyze` | Multi-agent opinion |
| `get_opinion_weights()` / `set_opinion_weight` | `/trading/opinion/weights` | Tune agent weights |
| `trade_memory_*` / `*_skills` / `match_skill` | `/trading/trade-memory/*`, `/trading/skills/*` | Learning loop |
| `get_portfolio()` | `GET /trading/portfolio` | Equity / PnL |
| `list_positions()` / `list_binance_positions()` / `list_trades()` | positions & trades | Oversight |
| `close_position(id)` / `modify_position(id, sl, tp)` | close / modify | Intervene |
| `get_loop_status()` / `start_trading_loop` / `stop_trading_loop` | `/trading/loop/*` | Loop control |
| `get_trading_config()` / `update_trading_config` | `/trading/config*` | Risk/sizing |
| `sentry_status` / `sentry_halt` / `emergency_halt` / `sentry_resume` | `/sentry/*` | Safety |
| `sentiment_loop_status()` / `run_sentiment_loop` | `/news/sentiment-loop/*` | Sentiment |
| `backend_health()` | `GET /health` | Liveness |
| `call_backend(method, path, …)` | GET/POST/**PUT** | Escape hatch |

See also: [`docs/ops/A0_HERMES_MCP.md`](../docs/ops/A0_HERMES_MCP.md) for Agent Zero + Hermes wiring.

Set `FULL_API=true` to instead **auto-generate one tool per FastAPI route** from
the backend's live `/openapi.json`. Comprehensive but noisy — prefer the curated
set for day-to-day agent use.

## Configuration (env)

| Var | Default | Notes |
| --- | --- | --- |
| `BACKEND_BASE_URL` | `http://ai-trading-backend:8000` | Docker-internal. External: `http://72.60.18.113:8001/api` |
| `BACKEND_API_PREFIX` | `/api` | FastAPI mounts routes under this prefix |
| `MCP_API_TOKEN` | _(empty)_ | Sent as `Authorization: Bearer …` to the backend |
| `MCP_TRANSPORT` | `http` | `stdio` \| `http` \| `sse` |
| `MCP_HOST` | `0.0.0.0` | HTTP/SSE bind host |
| `MCP_PORT` | `9100` | HTTP/SSE bind port |
| `MCP_HTTP_TIMEOUT` | `20` | Per-request timeout (seconds) |
| `FULL_API` | `false` | `true` → auto-generate from OpenAPI |

> **Note on `BACKEND_API_PREFIX`:** the curated tools pass paths like
> `/trading/opinion/BTCUSDT`. `_url()` automatically prepends the prefix, so the
> request hits `…/api/trading/opinion/BTCUSDT`. If you point at the **external**
> base `http://72.60.18.113:8001/api`, set `BACKEND_API_PREFIX=""` to avoid a
> double `/api`.

## Run locally (stdio — for Claude Desktop / Cursor)

```bash
cd mcp_server
pip install -r requirements.txt

# stdio transport — the host app spawns the process
MCP_TRANSPORT=stdio \
BACKEND_BASE_URL=http://72.60.18.113:8001 \
BACKEND_API_PREFIX=/api \
python -m mcp_server.server
```

## Run as an HTTP service (VPS / shared)

```bash
MCP_TRANSPORT=http MCP_HOST=0.0.0.0 MCP_PORT=9100 \
BACKEND_BASE_URL=http://ai-trading-backend:8000 \
python -m mcp_server.server
# serves MCP over HTTP at http://<host>:9100/mcp
```

Or with the FastMCP CLI:

```bash
fastmcp run mcp_server/server.py:mcp --transport http --host 0.0.0.0 --port 9100
```

## Docker

```bash
docker build -t quantumtrade-mcp -f mcp_server/Dockerfile .
docker run --rm -p 9100:9100 \
  -e BACKEND_BASE_URL=http://ai-trading-backend:8000 \
  -e MCP_TRANSPORT=http \
  --network <your_compose_network> \
  quantumtrade-mcp
```

To run inside the existing compose stack, add a service on the same network as
`ai-trading-backend` and keep `BACKEND_BASE_URL=http://ai-trading-backend:8000`.

## Claude Desktop config

Edit `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "quantumtrade": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/absolute/path/to/ai-trading-platform",
      "env": {
        "MCP_TRANSPORT": "stdio",
        "BACKEND_BASE_URL": "http://72.60.18.113:8001",
        "BACKEND_API_PREFIX": "/api"
      }
    }
  }
}
```

For an already-running **HTTP** server, point the client at
`http://<host>:9100/mcp` instead of spawning a process.

## Cursor config

`~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "quantumtrade": {
      "url": "http://72.60.18.113:9100/mcp"
    }
  }
}
```

## Security

- The server forwards `MCP_API_TOKEN` as a bearer token to the backend — keep the
  backend behind auth/firewall; the MCP layer adds no auth of its own.
- Several tools are **write** actions (`set_opinion_weight`, `mine_skills`,
  `trade_memory_backfill`, `run_sentiment_loop`). Only expose this server to
  trusted agents, and prefer `dry_run=true` where available.
