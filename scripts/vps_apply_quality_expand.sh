#!/usr/bin/env bash
# Quality expand: more concurrent positions, but only on recent positive-edge symbols.
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

echo "=== Apply quality-expand mode ==="

# Majors + 7d/14d net-positive names. Keep ADA/ARB/DOGE/APT/UNI/XRP/OP/etc out.
_upsert_env TRADING_SYMBOLS "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,NEARUSDT,LTCUSDT,LINKUSDT"

# More seats, still capped — quality over spray.
_upsert_env MAX_POSITIONS 6
_upsert_env MAX_SAME_DIRECTION_POSITIONS 3

# Still no pyramids (stacked stops killed last week).
_upsert_env PYRAMID_MODE false
_upsert_env PYRAMID_MAX_LAYERS 1
_upsert_env PYRAMID_BLOCK_UNDERWATER true

# Slightly more fills than recovery 0.60, still selective.
_upsert_env MIN_SIGNAL_STRENGTH 0.55
_upsert_env AI_ANALYSIS_THRESHOLD 0.35
_upsert_env RISK_PER_TRADE_PCT 0.007
_upsert_env MAX_TRADE_NOTIONAL_EQUITY_MULT 1.0

# Daily brake with room for a few concurrent losers; stop if day turns.
_upsert_env RISK_MAX_DAILY_LOSS_PCT 3
_upsert_env RISK_MAX_DRAWDOWN_PCT 20
_upsert_env RISK_PEAK_LOOKBACK_HOURS 72

# Fast expectancy gate: block symbols that bleed over the last 2 weeks.
_upsert_env SYMBOL_EXPECTANCY_GATE_ENABLED true
_upsert_env SYMBOL_EXPECTANCY_LOOKBACK_DAYS 14
_upsert_env SYMBOL_EXPECTANCY_MIN_TRADES 8

# Winner-friendly trail geometry.
_upsert_env TRAILING_STOP_ENABLED true
_upsert_env STEP_TRAIL_ENABLED false
_upsert_env TRAIL_ACTIVATION_ATR 2.0
_upsert_env TRAIL_ATR_MULT 0.8
_upsert_env NATIVE_TRAILING_ENABLED false

# Hard blacklist: confirmed recent + structural losers.
_upsert_env SYMBOL_BLACKLIST "ADAUSDT,ARBUSDT,DOGEUSDT,APTUSDT,UNIUSDT,XRPUSDT,OPUSDT,INJUSDT,ATOMUSDT,POLUSDT,AVAXUSDT,ADAUSDC,ARBUSDC,DOGEUSDC,APTUSDC,SIRENUSDT,AIGENSYNUSDT,INUSDT,BUSDT,BRUSDT,SPKUSDT,MAGMAUSDT,VELVETUSDT,HOMEUSDT,BTWUSDT,LABUSDT"

echo "Env:"
grep -E '^(TRADING_SYMBOLS|MAX_POSITIONS|MAX_SAME_DIRECTION_POSITIONS|PYRAMID_MODE|MIN_SIGNAL_STRENGTH|RISK_PER_TRADE_PCT|RISK_MAX_DAILY_LOSS_PCT|SYMBOL_EXPECTANCY_)=' .env

ADMIN=$(grep '^ADMIN_API_KEY=' .env | cut -d= -f2-)

echo ""
echo "=== Restart backend to reload RiskConfig ==="
docker compose -f docker-compose.prod.yml up -d backend
for i in $(seq 1 30); do
  curl -sf http://127.0.0.1:8001/health >/dev/null && break
  sleep 3
done
sleep 2

curl -sf -X POST http://127.0.0.1:8001/sentry/resume \
  -H "X-API-Key: ${ADMIN}" -H 'Content-Type: application/json' \
  -d '{"note":"quality-expand: more seats on positive-edge symbols"}' >/dev/null 2>&1 || true

SYMS_JSON='["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","NEARUSDT","LTCUSDT","LINKUSDT"]'
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
curl -sf http://127.0.0.1:8001/trading/account/summary; echo
docker exec ai-trading-backend python3 -c '
from backend.services.risk_config import refresh_risk_config
rc = refresh_risk_config()
print("max_positions", rc.max_positions)
print("same_dir", rc.max_same_direction_positions)
print("min_signal", rc.min_signal_strength)
print("risk_pct", rc.risk_per_trade_pct)
print("daily_loss", rc.max_daily_loss_pct)
print("pyramid", rc.pyramid_mode)
print("expectancy", rc.symbol_expectancy_gate_enabled, rc.symbol_expectancy_lookback_days, rc.symbol_expectancy_min_trades)
'
echo "=== Quality-expand live ==="
