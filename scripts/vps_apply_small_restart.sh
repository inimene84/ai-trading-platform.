#!/usr/bin/env bash
# Small-size restart after flattening positions.
# Enables timing-gate shadow mode + conservative USDC majors sizing.
# Run ON the VPS from the project dir.
set -euo pipefail
cd "${PROJECT_DIR:-/root/ai-trading-platform-v3}"

_upsert_env() {
  local key="$1" value="$2"
  if grep -qE "^${key}=" .env; then
    local escaped
    escaped=$(printf '%s' "$value" | sed -e 's/[&|\\]/\\&/g')
    sed -i "s|^${key}=.*|${key}=${escaped}|" .env
  else
    echo "${key}=${value}" >> .env
  fi
}

echo "=== Apply small-amount restart + timing shadow ==="

# MiCA-friendly USDC majors only (thin book → keep notional tiny).
_upsert_env TRADING_SYMBOLS "BTCUSDC,ETHUSDC,SOLUSDC,BNBUSDC"

# Tiny size restart
_upsert_env TRADE_USDT_AMOUNT "25"
_upsert_env RISK_PER_TRADE_PCT "0.003"
_upsert_env MAX_TRADE_NOTIONAL_EQUITY_MULT "0.5"
_upsert_env MAX_POSITIONS "2"
_upsert_env MAX_SAME_DIRECTION_POSITIONS "1"
_upsert_env PYRAMID_MODE "false"
_upsert_env PYRAMID_MAX_LAYERS "1"
_upsert_env PYRAMID_BLOCK_UNDERWATER "true"

# Selective entries
_upsert_env MIN_SIGNAL_STRENGTH "0.60"
_upsert_env AI_ANALYSIS_THRESHOLD "0.40"
_upsert_env RISK_MAX_DAILY_LOSS_PCT "2"
_upsert_env RISK_MAX_DRAWDOWN_PCT "15"
_upsert_env RISK_PEAK_LOOKBACK_HOURS "72"

# Trail geometry (do not regress weekly-edge fix)
_upsert_env TRAILING_STOP_ENABLED "true"
_upsert_env STEP_TRAIL_ENABLED "false"
_upsert_env TRAIL_ACTIVATION_ATR "2.0"
_upsert_env TRAIL_ATR_MULT "0.8"

# Timing gate: log vetoes, do not hard-block yet (WP5 calibration)
_upsert_env TIMING_GATE_SHADOW "true"
_upsert_env ENABLE_VISION_TIMING "false"
_upsert_env ENABLE_KRONOS "true"
_upsert_env KRONOS_SIDECAR_URL "http://kronos-infer:8001"
_upsert_env KRONOS_ALLOW_LOCAL_STUB "false"
_upsert_env KRONOS_ALLOW_ANALYTICAL_FALLBACK "false"

echo "Env applied:"
grep -E '^(TRADING_SYMBOLS|TRADE_USDT_AMOUNT|RISK_PER_TRADE_PCT|MAX_POSITIONS|TIMING_GATE_SHADOW|ENABLE_KRONOS|KRONOS_SIDECAR_URL|PYRAMID_MODE)=' .env || true

ADMIN=$(grep '^ADMIN_API_KEY=' .env | cut -d= -f2- || true)
COMPOSE_FILE=docker-compose.prod.yml
if [[ ! -f "$COMPOSE_FILE" ]]; then
  COMPOSE_FILE=docker-compose.yml
fi

echo ""
echo "=== Restart backend + mcp + kronos (if defined) ==="
docker compose -f "$COMPOSE_FILE" up -d --build backend mcp-server 2>/dev/null || \
  docker compose -f "$COMPOSE_FILE" up -d --build backend
docker compose -f "$COMPOSE_FILE" up -d --build kronos-infer 2>/dev/null || true

for i in $(seq 1 40); do
  curl -sf http://127.0.0.1:8001/health >/dev/null && break
  sleep 3
done

BASE="http://127.0.0.1:8001"
AUTH_H=()
if [[ -n "${ADMIN:-}" ]]; then
  AUTH_H=(-H "X-API-Key: ${ADMIN}" -H "Authorization: Bearer ${ADMIN}")
fi

# Clear any leftover halt
if [[ -n "${ADMIN:-}" ]]; then
  curl -sf -X POST "$BASE/sentry/resume" "${AUTH_H[@]}" -H 'Content-Type: application/json' \
    -d '{"note":"small restart + timing shadow"}' >/dev/null 2>&1 || true
fi

SYMS_JSON='["BTCUSDC","ETHUSDC","SOLUSDC","BNBUSDC"]'
curl -sf -X POST "$BASE/trading/loop/stop" "${AUTH_H[@]}" -H 'Content-Type: application/json' >/dev/null || true
sleep 1
curl -sf -X POST "$BASE/trading/loop/start" "${AUTH_H[@]}" -H 'Content-Type: application/json' \
  -d "{\"interval_minutes\":15,\"strategy\":\"combined\",\"symbols\":${SYMS_JSON}}"
echo

sleep 2
echo "=== VERIFY ==="
curl -sf "$BASE/health"; echo
curl -sf "$BASE/trading/loop/status"; echo
curl -sf "$BASE/trading/positions"; echo
curl -sf "$BASE/sentry/status"; echo
docker ps --format 'table {{.Names}}\t{{.Status}}' | rg -i 'ai-trading|kronos|mcp' || true

echo "=== Small restart live (shadow timing gate ON) ==="
