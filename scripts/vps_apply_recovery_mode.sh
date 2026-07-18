#!/usr/bin/env bash
# Recovery / "green-day" mode for Hostinger VPS.
# Goal: cut overtrading, ban pyramids, trade liquid majors only, tight daily halt.
# Run ON the VPS as root from the project dir, or via SSH.
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

echo "=== Apply recovery / green-day mode ==="

# Liquid majors only — drop mid/small alts that drove last week's bleed.
_upsert_env TRADING_SYMBOLS "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT"

# No stacking — last week had 53 multi-close pyramid clusters.
_upsert_env PYRAMID_MODE false
_upsert_env PYRAMID_MAX_LAYERS 1
_upsert_env PYRAMID_BLOCK_UNDERWATER true

# Fewer concurrent bets; alts are highly correlated.
_upsert_env MAX_POSITIONS 3
_upsert_env MAX_SAME_DIRECTION_POSITIONS 2

# Higher bar for entries; half-size risk.
_upsert_env MIN_SIGNAL_STRENGTH 0.60
_upsert_env AI_ANALYSIS_THRESHOLD 0.40
_upsert_env RISK_PER_TRADE_PCT 0.005
_upsert_env MAX_TRADE_NOTIONAL_EQUITY_MULT 1.0

# Tight daily brake (~$3.4 on $170). Keep 20% portfolio DD / 72h peak.
_upsert_env RISK_MAX_DAILY_LOSS_PCT 2
_upsert_env RISK_MAX_DRAWDOWN_PCT 20
_upsert_env RISK_PEAK_LOOKBACK_HOURS 72

# Keep winner-friendly trail geometry from weekly-edge fix.
_upsert_env TRAILING_STOP_ENABLED true
_upsert_env STEP_TRAIL_ENABLED false
_upsert_env TRAIL_ACTIVATION_ATR 2.0
_upsert_env TRAIL_ATR_MULT 0.8
_upsert_env NATIVE_TRAILING_ENABLED false

# Blacklist remains (weak alts + memes).
_upsert_env SYMBOL_BLACKLIST "ADAUSDT,ARBUSDT,DOGEUSDT,APTUSDT,ADAUSDC,ARBUSDC,DOGEUSDC,APTUSDC,SIRENUSDT,AIGENSYNUSDT,INUSDT,BUSDT,BRUSDT,SPKUSDT,MAGMAUSDT,VELVETUSDT,HOMEUSDT,BTWUSDT,LABUSDT"

echo "Env applied:"
grep -E '^(TRADING_SYMBOLS|PYRAMID_MODE|PYRAMID_MAX_LAYERS|MAX_POSITIONS|MAX_SAME_DIRECTION_POSITIONS|MIN_SIGNAL_STRENGTH|RISK_PER_TRADE_PCT|RISK_MAX_DAILY_LOSS_PCT|TRAIL_ACTIVATION_ATR|STEP_TRAIL_ENABLED)=' .env

ADMIN=$(grep '^ADMIN_API_KEY=' .env | cut -d= -f2-)

echo ""
echo "=== Close non-majors / blacklisted open legs ==="
# Flatten anything outside the recovery universe so we don't orphan SL/TP
# management when the scan list shrinks to BTC/ETH/SOL/BNB.
MAJORS='BTCUSDT ETHUSDT SOLUSDT BNBUSDT BTCUSDC ETHUSDC SOLUSDC BNBUSDC'
for pid in $(curl -sf http://127.0.0.1:8001/trading/positions | MAJORS="$MAJORS" python3 -c '
import os, sys, json
majors = set(os.environ.get("MAJORS", "").split())
d = json.load(sys.stdin)
for p in d.get("positions", []):
    sym = str(p.get("symbol", "")).upper()
    if sym and sym not in majors:
        print(p["id"])
'); do
  echo "Closing position id=$pid ..."
  curl -sf -X POST "http://127.0.0.1:8001/trading/positions/${pid}/close" \
    -H "X-API-Key: ${ADMIN}" -H 'Content-Type: application/json' || \
  curl -sf -X POST "http://127.0.0.1:8001/trading/positions/${pid}/close" \
    -H 'Content-Type: application/json' || true
  echo
done

echo ""
echo "=== Restart backend to reload RiskConfig env ==="
docker compose -f docker-compose.prod.yml up -d backend
for i in $(seq 1 30); do
  curl -sf http://127.0.0.1:8001/health >/dev/null && break
  sleep 3
done
sleep 2

# Clear deploy heartbeat halt if watchdog tripped
curl -sf -X POST http://127.0.0.1:8001/sentry/resume \
  -H "X-API-Key: ${ADMIN}" -H 'Content-Type: application/json' \
  -d '{"note":"recovery green-day mode applied"}' >/dev/null 2>&1 || true

SYMS_JSON='["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT"]'
curl -sf -X POST http://127.0.0.1:8001/trading/loop/stop \
  -H "X-API-Key: ${ADMIN}" -H 'Content-Type: application/json' >/dev/null || true
sleep 1
curl -sf -X POST http://127.0.0.1:8001/trading/loop/start \
  -H "X-API-Key: ${ADMIN}" -H 'Content-Type: application/json' \
  -d "{\"interval_minutes\":15,\"strategy\":\"combined\",\"symbols\":${SYMS_JSON}}"
echo

sleep 3
echo "=== VERIFY ==="
curl -sf http://127.0.0.1:8001/health; echo
curl -sf http://127.0.0.1:8001/trading/loop/status; echo
curl -sf http://127.0.0.1:8001/trading/positions; echo
curl -sf http://127.0.0.1:8001/trading/account/summary; echo

docker exec ai-trading-backend python3 -c '
from backend.services.risk_config import refresh_risk_config
rc = refresh_risk_config()
print("pyramid_mode", rc.pyramid_mode)
print("pyramid_max_layers", rc.pyramid_max_layers)
print("min_signal_strength", rc.min_signal_strength)
print("risk_per_trade_pct", rc.risk_per_trade_pct)
print("max_positions", rc.max_positions)
print("max_same_direction", rc.max_same_direction_positions)
print("daily_loss_pct", rc.max_daily_loss_pct)
print("trail_act", rc.trail_activation_atr, "step", rc.step_trail_enabled)
'

echo "=== Recovery mode live ==="
