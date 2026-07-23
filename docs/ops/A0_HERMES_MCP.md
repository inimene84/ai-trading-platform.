# Connect Agent Zero (A0) + Hermes to QuantumTrade

Goal: let **A0** (MCP/A2A client) and **Hermes** oversee live trades — inspect positions, adjust SL/TP, halt/resume, and stop the loop — without giving them exchange API keys.

## Architecture

```text
A0 (a0-instance) ──MCP HTTP──► ai-trading-mcp:9100/mcp ──HTTP──► ai-trading-backend:8000
Hermes           ──REST/MCP──► 127.0.0.1:8001  /  127.0.0.1:9100/mcp
```

- MCP server is already in `docker-compose.yml` / `docker-compose.prod.yml` as `ai-trading-mcp`.
- Bound to `127.0.0.1:9100` on the host (not public).
- `MCP_API_TOKEN` = `ADMIN_API_KEY` so write tools work.

A2A is **not** required for this platform: A0 should use MCP. Hermes can use MCP or REST.

## One-shot VPS wiring

```bash
cd /root/ai-trading-platform-v3
chmod +x scripts/vps_connect_a0_hermes_mcp.sh scripts/vps_apply_small_restart.sh
./scripts/vps_connect_a0_hermes_mcp.sh
```

That script:
1. Finds the backend Docker network (`*trading-net*`)
2. Ensures `mcp-server` is up
3. Attaches `a0-instance` to that network
4. Prints A0 MCP URL + Hermes curl examples

## A0 configuration

Inside Agent Zero, add an MCP server:

| Field | Value |
| --- | --- |
| URL | `http://ai-trading-mcp:9100/mcp` |
| Transport | HTTP |
| Auth | Bearer / API key = `ADMIN_API_KEY` (if client supports headers) |

Suggested A0 system prompt snippet:

> You are a trading oversight agent for QuantumTrade. Prefer `list_positions`, `list_binance_positions`, `get_loop_status`, and `sentry_status` before intervening. Use `modify_position` for SL/TP fixes, `close_position` only when necessary, and `sentry_halt` / `emergency_halt` for risk events. Never ask for Binance keys.

## Hermes configuration

### Option A — MCP

Point Hermes at `http://127.0.0.1:9100/mcp` (host) or `http://ai-trading-mcp:9100/mcp` if Hermes runs on `trading-net`.

### Option B — REST skill

```bash
export QT_BASE=http://127.0.0.1:8001
export QT_KEY=<ADMIN_API_KEY>

curl -s "$QT_BASE/trading/positions"
curl -s "$QT_BASE/trading/loop/status"
curl -s "$QT_BASE/sentry/status"

curl -s -X POST "$QT_BASE/trading/positions/<ID>/close" -H "X-API-Key: $QT_KEY"
curl -s -X PUT  "$QT_BASE/trading/positions/<ID>/modify" \
  -H "X-API-Key: $QT_KEY" -H 'Content-Type: application/json' \
  -d '{"stop_loss":12345.0,"take_profit":13000.0}'
```

## Curated MCP oversight tools

| Tool | Purpose |
| --- | --- |
| `list_positions` / `list_binance_positions` / `list_trades` | Inspect |
| `close_position` / `modify_position` | Fix / flatten |
| `get_loop_status` / `start_trading_loop` / `stop_trading_loop` | Loop control |
| `sentry_status` / `sentry_halt` / `emergency_halt` / `sentry_resume` | Safety |
| `get_portfolio` / `get_trading_config` / `backend_health` | Health |
| `get_trading_opinion` / trade-memory / skills tools | Research |

## Small-amount restart (after flattening)

```bash
./scripts/vps_apply_small_restart.sh
```

Sets:
- Symbols: `BTCUSDC,ETHUSDC,SOLUSDC,BNBUSDC`
- `TRADE_USDT_AMOUNT=25`, `MAX_POSITIONS=2`, no pyramids
- `TIMING_GATE_SHADOW=true` (log timing vetoes, do not hard-block yet)
- Restarts backend / MCP / Kronos sidecar when present

## Security notes

1. Do **not** publish `:9100` to the public internet without auth/VPN.
2. Never put exchange secrets into A0/Hermes — only `ADMIN_API_KEY`.
3. Prefer `sentry_halt` before mass closes when unsure.
4. Keep `TIMING_GATE_SHADOW=true` until WP5 calibration looks sane.
