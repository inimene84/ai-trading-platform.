from vps_ssh_common import ssh_cmd, SSH_BASE
#!/usr/bin/env python3
"""Deploy direction-cap fix to VPS and verify entries are no longer blocked."""
import subprocess
import sys

SSH = list(SSH_BASE)

DEPLOY = r'''
cd /root/ai-trading-platform-v3
git pull origin main 2>&1 | tail -2
docker compose -f docker-compose.prod.yml up -d --build backend 2>&1 | tail -2
for i in $(seq 1 20); do
  curl -sf http://127.0.0.1:8001/health >/dev/null && { echo "backend healthy"; break; }
  sleep 5
done
ADMIN=$(grep '^ADMIN_API_KEY=' .env | cut -d= -f2-)
curl -sf -X POST http://127.0.0.1:8001/sentry/resume -H "X-API-Key: $ADMIN" -H 'Content-Type: application/json' -d '{"note":"direction cap fix"}' >/dev/null || true
sleep 2
SYMS_JSON=$(grep '^TRADING_SYMBOLS=' .env | cut -d= -f2- | python3 -c "import sys,json; print(json.dumps([s.strip() for s in sys.stdin.read().split(',') if s.strip()]))")
curl -sf -X POST http://127.0.0.1:8001/trading/loop/stop -H "X-API-Key: $ADMIN" >/dev/null 2>&1 || true
sleep 2
curl -sf -X POST http://127.0.0.1:8001/trading/loop/start \
  -H "X-API-Key: $ADMIN" -H 'Content-Type: application/json' \
  -d "{\"interval_minutes\":15,\"strategy\":\"combined\",\"symbols\":${SYMS_JSON}}" >/dev/null
echo "loop restarted with $(echo $SYMS_JSON | python3 -c 'import sys,json; print(len(json.load(sys.stdin)))') symbols"
'''

VERIFY = r'''
cd /root/ai-trading-platform-v3
echo "=== waiting for first cycle (3 min) ==="
sleep 180
echo "=== cap blocks since restart ==="
docker logs ai-trading-backend --since 5m 2>&1 | grep -cE "correlation cap|Correlation limit|direction notional cap" || true
docker logs ai-trading-backend --since 5m 2>&1 | grep -E "blocked" | tail -6 || echo "(no blocks)"
echo ""
echo "=== loop status ==="
curl -sf http://127.0.0.1:8001/trading/loop/status | head -c 300; echo
echo ""
echo "=== errors ==="
echo "critical=$(docker logs ai-trading-backend --since 5m 2>&1 | grep -c CRITICAL || true) parse=$(docker logs ai-trading-backend --since 5m 2>&1 | grep -c 'Failed to parse JSON' || true)"
'''


def main():
    r = subprocess.run(SSH + [DEPLOY], capture_output=True, text=True, encoding='utf-8', errors='replace')
    print(r.stdout)
    if r.returncode != 0:
        print('STDERR:', (r.stderr or '')[-1000:])
        return r.returncode
    r = subprocess.run(SSH + [VERIFY], capture_output=True, text=True, encoding='utf-8', errors='replace')
    print(r.stdout)
    if r.stderr:
        print('STDERR:', r.stderr[-400:])
    return r.returncode


if __name__ == '__main__':
    sys.exit(main())
