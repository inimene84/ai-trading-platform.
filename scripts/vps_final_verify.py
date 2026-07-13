#!/usr/bin/env python3
import subprocess
from vps_ssh_common import ssh_cmd, SSH_BASE
REMOTE = r'''
cd /root/ai-trading-platform-v3
docker compose -f docker-compose.prod.yml up -d --build backend
for i in $(seq 1 12); do
  curl -sf http://127.0.0.1:8001/health >/dev/null && break
  sleep 5
done

ADMIN=$(grep '^ADMIN_API_KEY=' .env | cut -d= -f2-)
curl -sf -X POST http://127.0.0.1:8001/sentry/resume -H "X-API-Key: $ADMIN" -H 'Content-Type: application/json' -d '{"note":"final verify"}' >/dev/null || true

echo "======== FINAL SYSTEM STATUS ========"
echo "-- Health --"
curl -sf http://127.0.0.1:8001/health; echo
curl -sf http://127.0.0.1:8081/health; echo
curl -sf http://127.0.0.1:8086/health; echo
curl -sf http://127.0.0.1:6333/healthz; echo
curl -sf http://127.0.0.1:4001/health/liveliness; echo

echo "-- Containers --"
docker ps --format '{{.Names}}|{{.Status}}' | grep -E 'ai-trading|vps-' | grep -viE 'restart|unhealthy|exited' || true
bad=$(docker ps -a --format '{{.Names}}|{{.Status}}' | grep -iE 'restart|unhealthy|exited' | grep -E 'ai-trading|vps-' || true)
[ -n "$bad" ] && echo "BAD: $bad" || echo "No bad containers"

echo "-- Trading --"
curl -sf http://127.0.0.1:8001/sentry/status; echo
curl -sf http://127.0.0.1:8001/trading/loop/status; echo

echo "-- LLM test --"
curl -sf -X POST "http://127.0.0.1:8001/api/news/market-alerts/run?dry_run=true&skip_telegram=true" -H "X-API-Key: $ADMIN"; echo

echo "-- Telegram test --"
python3 <<'PY'
import json, urllib.request
from pathlib import Path
env={}
for line in Path('.env').read_text().splitlines():
    if '=' in line and not line.strip().startswith('#'):
        k,v=line.split('=',1); env[k.strip()]=v.strip().strip('"').strip("'")
tg,chat=env['TELEGRAM_BOT_TOKEN'],env['TELEGRAM_CHAT_ID']
with urllib.request.urlopen(f'https://api.telegram.org/bot{tg}/getMe', timeout=15) as r:
    print('telegram_getMe', r.status)
PY

echo "-- Critical errors (15m) --"
crit=$(docker logs ai-trading-backend --since 15m 2>&1 | grep -ci 'CRITICAL\|All LLM providers\|Failed to send telegram\|Traceback' || true)
echo "critical_error_lines=$crit"
docker logs ai-trading-backend --since 15m 2>&1 | grep -iE 'CRITICAL|All LLM providers|Failed to send telegram|Traceback' | tail -5 || echo "(none recent)"
'''
r=subprocess.run(ssh_cmd(REMOTE),capture_output=True,text=True)
print(r.stdout)
if r.stderr: print('STDERR', r.stderr[-2000:])
