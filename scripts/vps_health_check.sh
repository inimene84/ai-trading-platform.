#!/usr/bin/env bash
set -euo pipefail

echo "=== HEALTH ==="
curl -sf http://127.0.0.1:8001/health || echo "FAIL"
echo

echo "=== SENTRY ==="
curl -sf http://127.0.0.1:8001/sentry/status || echo "FAIL"
echo

echo "=== TRADING LOOP (key fields) ==="
curl -sf http://127.0.0.1:8001/trading/status | python3 - <<'PY'
import json, sys
d = json.load(sys.stdin)
tl = d.get("trading_loop", {})
print("state:", tl.get("state"))
print("running:", tl.get("running"))
print("cycle_count:", tl.get("cycle_count"))
print("last_cycle:", tl.get("last_cycle"))
print("equity:", tl.get("equity"))
print("error:", tl.get("error"))
print("trading_status:", tl.get("trading_status"))
print("trading_allowed:", tl.get("trading_allowed"))
print("symbols:", len(tl.get("symbols") or []))
PY
echo

echo "=== BINANCE ==="
curl -sf http://127.0.0.1:8001/trading/binance/status | python3 - <<'PY'
import json, sys
d = json.load(sys.stdin)
w = d.get("wallet", {})
print("active:", d.get("active"))
print("equity:", w.get("equity"))
print("available:", w.get("available"))
print("positions:", d.get("positions_count"))
print("orders:", d.get("orders_count"))
PY
echo

echo "=== BACKEND ERRORS (last 200 lines) ==="
docker logs ai-trading-backend --tail 200 2>&1 | grep -iE 'error|critical|exception|traceback|banned|failed|warning.*\[SENTRY\]|KILL SWITCH' | tail -30 || echo "(no matches)"

echo
echo "=== SENTRY WATCHDOG (last 15) ==="
docker logs ai-trading-sentry-watchdog --tail 15 2>&1 || echo "no watchdog logs"

echo
echo "=== MARKET ALERTS ==="
curl -sf http://127.0.0.1:8001/api/news/market-alerts/status || echo "FAIL"
