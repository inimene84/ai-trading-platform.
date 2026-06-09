#!/usr/bin/env bash
# Run ON the Hostinger VPS (e.g. /root/ai-trading-platform-v3)
# Production deploy: rebuild backend, refresh nginx DNS, verify live trading loop.
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/root/ai-trading-platform-v3}"
cd "$PROJECT_DIR"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
if [[ ! -f "$COMPOSE_FILE" ]]; then
  COMPOSE_FILE="docker-compose.yml"
fi

echo "=== Hostinger VPS apply ==="
echo "Project: $PROJECT_DIR"
echo "Compose: $COMPOSE_FILE"

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "ERROR: no compose file found"
  exit 1
fi

echo ""
echo "=== 1. Git pull ==="
git pull origin main || git pull || true

echo ""
echo "=== 2. Env sanity (live trading) ==="
if [[ -f .env ]]; then
  # Kill floor: never below $50 on a ~$128 account
  if grep -q '^TRADING_KILL_FLOOR_USDT=20' .env 2>/dev/null; then
    sed -i 's/^TRADING_KILL_FLOOR_USDT=20/TRADING_KILL_FLOOR_USDT=65/' .env
    echo "  Bumped TRADING_KILL_FLOOR_USDT 20 -> 65"
  fi
  grep -q '^NATIVE_TRAILING_ENABLED=' .env || echo 'NATIVE_TRAILING_ENABLED=true' >> .env
  grep -q '^EQUITY_SIZING_ENABLED=' .env || echo 'EQUITY_SIZING_ENABLED=true' >> .env
  grep -q '^PAPER_TRADING=' .env || echo 'PAPER_TRADING=false' >> .env
  grep -q '^DRY_RUN_ALL=' .env || echo 'DRY_RUN_ALL=false' >> .env
  grep -q '^JSON_LOGS=' .env || echo 'JSON_LOGS=true' >> .env
  if ! grep -q '^GRAFANA_URL=' .env; then
    VPS_IP=$(curl -sf --max-time 3 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
    echo "GRAFANA_URL=http://${VPS_IP:-localhost}:3000" >> .env
  fi
  # Expanded symbol universe (20 liquid USDT perps) — more pairs = more signal chances
  EXPANDED_SYMBOLS='BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,ADAUSDT,DOGEUSDT,AVAXUSDT,DOTUSDT,LINKUSDT,POLUSDT,LTCUSDT,UNIUSDT,ATOMUSDT,NEARUSDT,OPUSDT,ARBUSDT,APTUSDT,INJUSDT,SUIUSDT'
  if grep -q '^TRADING_SYMBOLS=' .env; then
    sym_count=$(grep '^TRADING_SYMBOLS=' .env | cut -d= -f2- | tr ',' '\n' | grep -c . || echo 0)
    if [[ "$sym_count" -lt 15 ]]; then
      sed -i "s|^TRADING_SYMBOLS=.*|TRADING_SYMBOLS=${EXPANDED_SYMBOLS}|" .env
      echo "  Expanded TRADING_SYMBOLS to ${sym_count} -> 20 symbols"
    fi
  else
    echo "TRADING_SYMBOLS=${EXPANDED_SYMBOLS}" >> .env
  fi
fi

echo ""
echo "=== 3. Frontend build ==="
if [[ -d frontend ]]; then
  cd frontend
  npm ci 2>/dev/null || npm install
  npm run build
  cd ..
fi

echo ""
echo "=== 4. Docker network ==="
docker network create trading-net 2>/dev/null || true

echo ""
echo "=== 5. Rebuild & start stack ==="
docker compose -f "$COMPOSE_FILE" up -d --build backend litellm nginx

echo ""
echo "=== 6. Wait for backend health ==="
for i in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8001/health >/dev/null 2>&1; then
    echo "Backend healthy on :8001"
    break
  fi
  echo "  waiting backend ($i/30)..."
  sleep 5
done

echo ""
echo "=== 7. Restart nginx (refresh upstream DNS) ==="
docker restart ai-trading-nginx
sleep 3

echo ""
echo "=== 8. Smoke tests ==="
curl -sf http://127.0.0.1:8001/health | head -c 200 || true
echo ""
curl -sf -o /dev/null -w "nginx /api/backend/health: %{http_code}\n" \
  http://127.0.0.1:8081/api/backend/health || true
curl -sf -o /dev/null -w "nginx /api/backend/trading/status: %{http_code}\n" \
  http://127.0.0.1:8081/api/backend/trading/status || true
curl -sf -o /dev/null -w "api/historical: %{http_code}\n" \
  "http://127.0.0.1:8001/api/historical?symbol=BTCUSDT&interval=1h&limit=3" || true

echo ""
echo "=== 9. Ensure trading loop is running with current TRADING_SYMBOLS ==="
SYMS_JSON=$(grep '^TRADING_SYMBOLS=' .env 2>/dev/null | cut -d= -f2- | python3 -c "import sys,json; print(json.dumps([s.strip() for s in sys.stdin.read().split(',') if s.strip()]))" 2>/dev/null || echo '[]')
curl -sf -X POST http://127.0.0.1:8001/trading/loop/stop -H 'Content-Type: application/json' >/dev/null 2>&1 || true
sleep 1
curl -sf -X POST http://127.0.0.1:8001/trading/loop/start \
  -H 'Content-Type: application/json' \
  -d "{\"interval_minutes\":15,\"strategy\":\"combined\",\"symbols\":${SYMS_JSON}}" || true
sleep 2
curl -sf http://127.0.0.1:8001/trading/loop/status | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(f\"  loop_running={d.get('running')} symbols={len(d.get('symbols',[]))} cycle_count={d.get('cycle_count')}\")
print(f\"  pairs={d.get('symbols')}\")
" 2>/dev/null || true

echo ""
echo "=== Done. Dashboard: http://<vps-ip>:8081/  API: :8001/health ==="
