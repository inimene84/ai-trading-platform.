#!/usr/bin/env python3
"""Reset VPS to clean main, pull, rebuild stack incl. watchdog, verify."""
import subprocess
import sys

REMOTE = r'''
cd /root/ai-trading-platform-v3

echo "=== 1. Clean drift and pull main ==="
rm -f backend/routes/trading.py.bak "backend/routes/trading.py.bak."
git checkout -- .
git fetch origin main
git checkout main
git pull origin main
git log -1 --oneline
git status -sb | head -5

echo ""
echo "=== 2. Remove invalid GOOGLE_API_KEY / XAI stale check ==="
python3 - <<'PY'
from pathlib import Path
p = Path('.env')
lines = p.read_text().splitlines(keepends=True)
out = []
for line in lines:
    if line.startswith('GOOGLE_API_KEY='):
        val = line.split('=', 1)[1].strip()
        if len(val) < 30:
            print(f'Removing invalid GOOGLE_API_KEY (len={len(val)})')
            continue
    out.append(line)
p.write_text(''.join(out))
print('env cleaned')
PY

echo ""
echo "=== 3. Rebuild stack (backend + watchdog + litellm + nginx) ==="
docker compose -f docker-compose.prod.yml up -d --build backend sentry-watchdog litellm nginx 2>&1 | tail -5

echo ""
echo "=== 4. Wait for backend ==="
for i in $(seq 1 20); do
  curl -sf http://127.0.0.1:8001/health >/dev/null && { echo "backend healthy"; break; }
  sleep 5
done

ADMIN=$(grep '^ADMIN_API_KEY=' .env | cut -d= -f2-)
curl -sf -X POST http://127.0.0.1:8001/sentry/resume -H "X-API-Key: $ADMIN" -H 'Content-Type: application/json' -d '{"note":"clean main deploy"}' >/dev/null || true
sleep 2

echo ""
echo "=== 5. Restart trading loop ==="
SYMS_JSON=$(grep '^TRADING_SYMBOLS=' .env | cut -d= -f2- | python3 -c "import sys,json; print(json.dumps([s.strip() for s in sys.stdin.read().split(',') if s.strip()]))")
curl -sf -X POST http://127.0.0.1:8001/trading/loop/start \
  -H "X-API-Key: $ADMIN" -H 'Content-Type: application/json' \
  -d "{\"interval_minutes\":15,\"strategy\":\"combined\",\"symbols\":${SYMS_JSON}}" >/dev/null || true
sleep 3
'''

VERIFY = r'''
cd /root/ai-trading-platform-v3
ADMIN=$(grep '^ADMIN_API_KEY=' .env | cut -d= -f2-)
echo "======== POST-DEPLOY VERIFICATION ========"
echo "-- git --"
git log -1 --oneline
git status -s | head -3 || true

echo "-- health --"
curl -sf http://127.0.0.1:8001/health; echo
curl -sf http://127.0.0.1:8081/health >/dev/null && echo "nginx ok"
curl -sf http://127.0.0.1:4001/health/liveliness; echo

echo "-- watchdog --"
docker ps --format '{{.Names}}|{{.Status}}' | grep sentry-watchdog || echo "WATCHDOG MISSING"
docker logs ai-trading-sentry-watchdog --tail 3 2>&1

echo "-- trading --"
curl -sf http://127.0.0.1:8001/sentry/status | head -c 150; echo
curl -sf http://127.0.0.1:8001/trading/loop/status | head -c 250; echo

echo "-- LLM smoke x2 --"
for i in 1 2; do
  curl -sf -X POST "http://127.0.0.1:8001/api/news/market-alerts/run?dry_run=true&skip_telegram=true" -H "X-API-Key: $ADMIN" | python3 -c "import sys,json; d=json.load(sys.stdin)['summary']; print(f\"  source={d['source']} bias={d['bias']} conf={d['confidence']}\")"
done

echo "-- errors since deploy --"
echo "parse=$(docker logs ai-trading-backend --since 10m 2>&1 | grep -c 'Failed to parse JSON' || true) critical=$(docker logs ai-trading-backend --since 10m 2>&1 | grep -c CRITICAL || true) tg_fail=$(docker logs ai-trading-backend --since 10m 2>&1 | grep -c 'Failed to send telegram' || true)"

echo "-- containers --"
docker ps -a --format '{{.Names}}|{{.Status}}' | grep -E 'ai-trading|vps-'
'''

SSH = ['ssh', '-i', r'C:\Users\thori\.ssh\id_vps_bot', '-o', 'BatchMode=yes', 'root@72.60.18.113']


def main():
    r = subprocess.run(SSH + [REMOTE], capture_output=True, text=True, encoding='utf-8', errors='replace')
    print(r.stdout)
    if r.returncode != 0:
        print('STDERR:', (r.stderr or '')[-1500:])
        return r.returncode
    r = subprocess.run(SSH + [VERIFY], capture_output=True, text=True, encoding='utf-8', errors='replace')
    print(r.stdout)
    if r.stderr:
        print('STDERR:', r.stderr[-600:])
    return r.returncode


if __name__ == '__main__':
    sys.exit(main())
