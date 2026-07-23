#!/usr/bin/env bash
# Connect Agent Zero (a0-instance) and Hermes to the trading MCP server.
# Run ON the trading VPS as root.
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/root/ai-trading-platform-v3}"
A0_CONTAINER="${A0_CONTAINER:-a0-instance}"
MCP_CONTAINER="${MCP_CONTAINER:-ai-trading-mcp}"
BACKEND_CONTAINER="${BACKEND_CONTAINER:-ai-trading-backend}"
HERMES_CONTAINER="${HERMES_CONTAINER:-hermes-webui}"
A0_SETTINGS_HOST="${A0_SETTINGS_HOST:-/var/lib/docker/volumes/agent-zero_a0-data/_data/settings.json}"
HERMES_CONFIG="${HERMES_CONFIG:-/root/.hermes/config.yaml}"
QT_MCP_URL="http://${MCP_CONTAINER}:9100/mcp"

echo "=== Resolve trading-net ==="
NETWORK=$(docker inspect "$BACKEND_CONTAINER" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' 2>/dev/null | awk '{print $1}' || true)
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

echo "=== Attach A0 + Hermes to trading-net ==="
for c in "$A0_CONTAINER" "$HERMES_CONTAINER"; do
  if docker ps -a --format '{{.Names}}' | grep -qx "$c"; then
    docker network connect "$NETWORK" "$c" 2>/dev/null || \
      echo "$c already on $NETWORK (or connect skipped)"
  else
    echo "WARN: container $c not found"
  fi
done

echo "=== Write A0 quantumtrade MCP server (if settings volume present) ==="
if [[ -f "$A0_SETTINGS_HOST" ]]; then
  python3 - "$A0_SETTINGS_HOST" "$QT_MCP_URL" <<'PY'
import json, sys
from pathlib import Path

path = Path(sys.argv[1])
url = sys.argv[2]
cfg = json.loads(path.read_text())
raw = cfg.get("mcp_servers") or {}
if isinstance(raw, str):
    raw = json.loads(raw) if raw.strip().startswith("{") else {}
servers = raw.get("mcpServers") if isinstance(raw, dict) and "mcpServers" in raw else raw
if not isinstance(servers, dict):
    servers = {}
# Drop any misplaced sibling from earlier partial writes
servers.pop("mcpServers", None)
servers["quantumtrade"] = {
    "type": "streamable-http",
    "url": url,
    "headers": {},
    "disabled": False,
}
cfg["mcp_servers"] = {"mcpServers": servers}
path.write_text(json.dumps(cfg, indent=2) + "\n")
print("A0 mcp servers:", ", ".join(sorted(servers)))
PY
else
  echo "WARN: A0 settings not found at $A0_SETTINGS_HOST — add quantumtrade manually in A0 UI"
fi

echo "=== Write Hermes quantumtrade MCP server (if config present) ==="
if [[ -f "$HERMES_CONFIG" ]]; then
  python3 - "$HERMES_CONFIG" "$QT_MCP_URL" <<'PY'
from pathlib import Path
import sys

try:
    import yaml
except ImportError:
    print("WARN: PyYAML missing — skip Hermes config write")
    raise SystemExit(0)

path = Path(sys.argv[1])
url = sys.argv[2]
cfg = yaml.safe_load(path.read_text()) or {}
mcp = cfg.get("mcp_servers") or {}
mcp["quantumtrade"] = {
    "url": url,
    "timeout": 120,
    "connect_timeout": 60,
}
cfg["mcp_servers"] = mcp
path.write_text(yaml.safe_dump(cfg, sort_keys=False, default_flow_style=False))
print("Hermes mcp servers:", ", ".join(sorted(mcp)))
PY
  mkdir -p /root/.hermes/skills/trading/quantumtrade-oversight
  cat > /root/.hermes/skills/trading/quantumtrade-oversight/SKILL.md <<'SKILL'
# QuantumTrade Oversight

Prefer MCP server `quantumtrade` (list_positions, get_loop_status, sentry_*, modify_position).
REST fallback on this host: `http://127.0.0.1:8001` or `http://ai-trading-backend:8000`.
Via nginx from other hosts: `http://<trading-vps>:8081/api/...`
Writes need `X-API-Key: $ADMIN_API_KEY`. Never request exchange keys.
SKILL
else
  echo "WARN: Hermes config not found at $HERMES_CONFIG"
fi

echo "=== Restart clients on trading-net ==="
if docker ps -a --format '{{.Names}}' | grep -qx "$A0_CONTAINER"; then
  docker restart "$A0_CONTAINER" >/dev/null || true
fi
if docker ps -a --format '{{.Names}}' | grep -qx "$HERMES_CONTAINER"; then
  docker restart "$HERMES_CONTAINER" >/dev/null || true
fi

echo "=== MCP health from host ==="
curl -sf http://127.0.0.1:9100/health >/dev/null 2>&1 && echo "MCP /health OK" || \
  curl -sf -o /dev/null -w "MCP /mcp HTTP %{http_code}\n" http://127.0.0.1:9100/mcp || \
  echo "WARN: could not probe MCP on 127.0.0.1:9100 (check docker ps)"

echo "=== Verify A0 can resolve MCP (best-effort) ==="
if docker ps --format '{{.Names}}' | grep -qx "$A0_CONTAINER"; then
  sleep 8
  docker logs "$A0_CONTAINER" 2>&1 | grep -i 'quantumtrade' | tail -5 || \
    echo "No quantumtrade log line yet — check A0 UI MCP servers"
fi

ADMIN=$(grep '^ADMIN_API_KEY=' "$PROJECT_DIR/.env" 2>/dev/null | cut -d= -f2- || true)

cat <<EOF

=== Connected ===
A0 MCP URL (inside a0-instance):  $QT_MCP_URL
Hermes MCP URL (on trading-net):   $QT_MCP_URL
Host MCP (localhost only):         http://127.0.0.1:9100/mcp

Remote Hermes (other VPS) cannot reach :9100 (bound to 127.0.0.1).
Use REST:  export QT_BASE=http://<this-vps>:8081/api
           export QT_KEY='${ADMIN:-<ADMIN_API_KEY>}'

Oversight tools:
  list_positions, list_binance_positions, list_trades
  close_position, modify_position
  get_loop_status, start_trading_loop, stop_trading_loop
  sentry_status, sentry_halt, emergency_halt, sentry_resume
  get_portfolio, get_trading_config, backend_health

IMPORTANT: Do NOT bind-mount host /root into hermes-webui (only ~/.hermes).
A full /root mount can break SSH by changing /root ownership.

See docs/ops/A0_HERMES_MCP.md for full wiring.
EOF
