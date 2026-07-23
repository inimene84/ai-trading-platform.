#!/usr/bin/env bash
# Connect Agent Zero (a0-instance) and Hermes to the trading MCP server.
# Run ON the VPS as root.
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/root/ai-trading-platform-v3}"
A0_CONTAINER="${A0_CONTAINER:-a0-instance}"
MCP_CONTAINER="${MCP_CONTAINER:-ai-trading-mcp}"
BACKEND_CONTAINER="${BACKEND_CONTAINER:-ai-trading-backend}"

echo "=== Resolve trading-net ==="
NETWORK=$(docker inspect "$BACKEND_CONTAINER" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' 2>/dev/null || true)
if [[ -z "$NETWORK" ]]; then
  NETWORK=$(docker network ls --format '{{.Name}}' | rg -m1 'trading-net' || true)
fi
if [[ -z "$NETWORK" ]]; then
  echo "ERROR: could not find trading-net. Is ai-trading-backend running?"
  exit 1
fi
echo "Using network: $NETWORK"

echo "=== Ensure MCP server is up ==="
cd "$PROJECT_DIR"
if [[ -f docker-compose.prod.yml ]]; then
  docker compose -f docker-compose.prod.yml up -d mcp-server || true
else
  docker compose up -d mcp-server || true
fi

echo "=== Attach A0 to trading-net (if present) ==="
if docker ps -a --format '{{.Names}}' | grep -qx "$A0_CONTAINER"; then
  docker network connect "$NETWORK" "$A0_CONTAINER" 2>/dev/null || \
    echo "A0 already on $NETWORK (or connect skipped)"
  docker restart "$A0_CONTAINER" >/dev/null || true
  echo "A0 MCP URL (from inside a0-instance): http://${MCP_CONTAINER}:9100/mcp"
else
  echo "WARN: container $A0_CONTAINER not found — configure A0 manually."
fi

echo "=== MCP health from host ==="
curl -sf http://127.0.0.1:9100/health >/dev/null 2>&1 && echo "MCP /health OK" || \
  curl -sf http://127.0.0.1:9100/mcp >/dev/null 2>&1 && echo "MCP /mcp reachable" || \
  echo "WARN: could not probe MCP on 127.0.0.1:9100 (check docker ps)"

ADMIN=$(grep '^ADMIN_API_KEY=' "$PROJECT_DIR/.env" 2>/dev/null | cut -d= -f2- || true)

cat <<EOF

=== Agent Zero (A0) MCP config ===
Add / point A0 at:
  URL:  http://${MCP_CONTAINER}:9100/mcp
  Auth: use backend ADMIN_API_KEY as bearer if your A0 MCP client supports headers
        (compose already injects MCP_API_TOKEN into ai-trading-mcp → backend)

Useful oversight tools now available:
  list_positions, list_binance_positions, list_trades
  close_position, modify_position
  get_loop_status, start_trading_loop, stop_trading_loop
  sentry_status, sentry_halt, emergency_halt, sentry_resume
  get_portfolio, get_trading_config, backend_health

=== Hermes operator skill (REST) ===
Hermes can call the same surfaces without MCP:

  export QT_BASE=http://127.0.0.1:8001
  export QT_KEY='${ADMIN:-<ADMIN_API_KEY>}'

  # Inspect
  curl -s "\$QT_BASE/trading/positions" | jq .
  curl -s "\$QT_BASE/trading/loop/status" | jq .
  curl -s "\$QT_BASE/sentry/status" | jq .

  # Intervene
  curl -s -X POST "\$QT_BASE/trading/positions/<ID>/close" -H "X-API-Key: \$QT_KEY"
  curl -s -X PUT  "\$QT_BASE/trading/positions/<ID>/modify" \\
    -H "X-API-Key: \$QT_KEY" -H 'Content-Type: application/json' \\
    -d '{"stop_loss":12345,"take_profit":13000}'
  curl -s -X POST "\$QT_BASE/sentry/halt" -H "X-API-Key: \$QT_KEY" \\
    -H 'Content-Type: application/json' \\
    -d '{"reason":"hermes intervention","source":"hermes","manual":true}'

Or point Hermes at MCP HTTP too:
  http://127.0.0.1:9100/mcp

See docs/ops/A0_HERMES_MCP.md for full wiring.
EOF
