from vps_ssh_common import ssh_cmd, SSH_BASE
#!/usr/bin/env python3
"""Pull main on VPS, rebuild backend, restart loop with 20-coin universe, verify."""
import subprocess
import sys

SSH = list(SSH_BASE)

DEPLOY = r'''
cd /root/ai-trading-platform-v3
git pull origin main
git log -1 --oneline
docker compose -f docker-compose.prod.yml up -d --build backend 2>&1 | tail -3
for i in $(seq 1 20); do
  curl -sf http://127.0.0.1:8001/health >/dev/null && { echo "backend healthy"; break; }
  sleep 5
done
ADMIN=$(grep '^ADMIN_API_KEY=' .env | cut -d= -f2-)
curl -sf -X POST http://127.0.0.1:8001/sentry/resume -H "X-API-Key: $ADMIN" -H 'Content-Type: application/json' -d '{"note":"universe expansion deploy"}' >/dev/null || true
sleep 2
SYMS_JSON=$(grep '^TRADING_SYMBOLS=' .env | cut -d= -f2- | python3 -c "import sys,json; print(json.dumps([s.strip() for s in sys.stdin.read().split(',') if s.strip()]))")
echo "symbols: $SYMS_JSON"
curl -sf -X POST http://127.0.0.1:8001/trading/loop/stop -H "X-API-Key: $ADMIN" >/dev/null 2>&1 || true
sleep 2
curl -sf -X POST http://127.0.0.1:8001/trading/loop/start \
  -H "X-API-Key: $ADMIN" -H 'Content-Type: application/json' \
  -d "{\"interval_minutes\":15,\"strategy\":\"combined\",\"symbols\":${SYMS_JSON}}" >/dev/null
echo "loop restarted"
'''

VERIFY = r'''
cd /root/ai-trading-platform-v3
ADMIN=$(grep '^ADMIN_API_KEY=' .env | cut -d= -f2-)
echo "======== VERIFY ========"
sleep 120
curl -sf http://127.0.0.1:8001/trading/loop/status | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f\"state={d.get('state')} running={d.get('running')} symbols={len(d.get('symbols', []))}\")
print(f\"pairs={d.get('symbols')}\")
print(f\"cycle_count={d.get('cycle_count')} equity={d.get('equity')} margin_used={d.get('margin_used')}\")
"
echo ""
echo "-- config sanity via API --"
curl -sf http://127.0.0.1:8001/trading/config -H "X-API-Key: $ADMIN" 2>/dev/null | head -c 600 || true
echo ""
echo "-- prompt-cache usage in logs --"
docker logs ai-trading-backend --since 10m 2>&1 | grep -iE "cache" | tail -4 || echo "(no cache log lines)"
echo ""
echo "-- errors --"
echo "parse=$(docker logs ai-trading-backend --since 10m 2>&1 | grep -c 'Failed to parse JSON' || true) critical=$(docker logs ai-trading-backend --since 10m 2>&1 | grep -c CRITICAL || true) rate_limit=$(docker logs ai-trading-backend --since 10m 2>&1 | grep -ciE 'rate.?limit|-1003|418|too many requests' || true)"
echo ""
echo "-- LLM smoke --"
curl -sf -X POST "http://127.0.0.1:8001/api/news/market-alerts/run?dry_run=true&skip_telegram=true" -H "X-API-Key: $ADMIN" | python3 -c "import sys,json; d=json.load(sys.stdin)['summary']; print(f\"source={d['source']} bias={d['bias']} conf={d['confidence']}\")"
'''


def main():
    r = subprocess.run(SSH + [DEPLOY], capture_output=True, text=True, encoding='utf-8', errors='replace')
    print(r.stdout)
    if r.returncode != 0:
        print('STDERR:', (r.stderr or '')[-1200:])
        return r.returncode
    r = subprocess.run(SSH + [VERIFY], capture_output=True, text=True, encoding='utf-8', errors='replace')
    print(r.stdout)
    if r.stderr:
        print('STDERR:', r.stderr[-600:])
    return r.returncode


if __name__ == '__main__':
    sys.exit(main())
