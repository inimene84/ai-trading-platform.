#!/usr/bin/env bash
set -euo pipefail
cd /root/ai-trading-platform-v3

echo "=== CONTAINERS ==="
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -E 'ai-trading|vps-influx|vps-qdrant|NAME' || true

echo ""
echo "=== HEALTH ==="
curl -sf http://127.0.0.1:8001/health
echo

echo ""
echo "=== TRADING STATUS ==="
curl -sf http://127.0.0.1:8001/trading/status | python3 <<'PY'
import sys, json
d = json.load(sys.stdin)
tl = d.get("trading_loop", {})
rc = d.get("risk_config", {})
print("mode:", d.get("mode"))
print("dry_run:", d.get("dry_run"))
print("loop_running:", tl.get("running"))
print("cycle_count:", tl.get("cycle_count"))
print("last_cycle:", tl.get("last_cycle"))
print("loop_error:", tl.get("error"))
print("symbols:", len(tl.get("symbols", [])))
print("use_risk_reviewer_llm:", rc.get("use_risk_reviewer_llm"))
print("enable_personas:", rc.get("enable_personas"))
PY

echo ""
echo "=== BINANCE / WALLET ==="
curl -sf http://127.0.0.1:8001/trading/binance/status | python3 <<'PY'
import sys, json
d = json.load(sys.stdin)
w = d.get("wallet", {})
print("testnet:", d.get("testnet"))
print("equity:", w.get("equity"))
print("available:", w.get("available"))
print("unrealized_pnl:", w.get("unrealized_pnl"))
print("positions_count:", d.get("positions_count"))
print("orders_count:", d.get("orders_count"))
if d.get("error"):
    print("ERROR:", d.get("error"))
PY

echo ""
echo "=== OPEN POSITIONS ==="
curl -sf http://127.0.0.1:8001/trading/positions | python3 <<'PY'
import sys, json
d = json.load(sys.stdin)
print("count:", d.get("count", 0))
for p in d.get("positions", []):
    print(f"  {p.get('symbol')} {p.get('direction')} qty={p.get('quantity')} entry={p.get('entry_price')} pnl={p.get('unrealized_pnl')}")
PY

echo ""
echo "=== ENV (live flags) ==="
grep -E '^(BINANCE_TESTNET|DRY_RUN|PAPER_TRADING|DISABLE_RISK_GUARD|TRADING_SYMBOLS)=' .env 2>/dev/null | head -6

echo ""
echo "=== RECENT LOGS (2h) ==="
docker logs ai-trading-backend --since 2h 2>&1 | grep -E 'Trading Cycle|SUCCESS|vetoed|APPROVED|ERROR|Exception|filled @|MARGIN GATE|UnifiedOrder' | tail -20 || echo "(no matching log lines)"

echo ""
echo "=== FRONTEND / NGINX ==="
curl -sf -o /dev/null -w "dashboard :8081 -> %{http_code}\n" http://127.0.0.1:8081/
curl -sf -o /dev/null -w "api via nginx -> %{http_code}\n" http://127.0.0.1:8081/api/backend/trading/status

echo ""
echo "=== INFLUX (recent write check) ==="
TOKEN=$(grep '^INFLUXDB_TOKEN=' .env | cut -d= -f2-)
curl -sf -H "Authorization: Token ${TOKEN}" \
  'http://127.0.0.1:8086/api/v2/query?org=hedge-fund' \
  -H 'Content-Type: application/vnd.flux' \
  -d 'from(bucket:"trading-system") |> range(start: -6h) |> filter(fn: (r) => r._measurement == "binance_wallet") |> last()' | head -c 200 || echo "no recent wallet writes"
