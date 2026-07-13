#!/usr/bin/env python3
import subprocess, time
SSH=['ssh','-i',r'C:\Users\thori\.ssh\id_vps_bot','-o','BatchMode=yes','root@72.60.18.113']
VERIFY=r'''
cd /root/ai-trading-platform-v3
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -sf http://127.0.0.1:8001/health >/dev/null; then echo "backend ready"; break; fi
  echo "waiting $i"; sleep 5
done
echo "=== HEALTH ==="
curl -sf http://127.0.0.1:8001/health; echo
curl -sf http://127.0.0.1:8081/health; echo
curl -sf http://127.0.0.1:8001/sentry/status; echo

echo "=== TELEGRAM TEST ==="
python3 <<'PY'
import json, urllib.request
from pathlib import Path
env={}
for line in Path('.env').read_text().splitlines():
    if '=' in line and not line.strip().startswith('#'):
        k,v=line.split('=',1); env[k.strip()]=v.strip().strip('"').strip("'")
tg=env['TELEGRAM_BOT_TOKEN']; chat=env['TELEGRAM_CHAT_ID']
try:
    with urllib.request.urlopen(f'https://api.telegram.org/bot{tg}/getMe', timeout=15) as r:
        print('getMe', r.status, r.read(120).decode())
except Exception as e:
    print('getMe fail', getattr(e,'code',e))
payload=json.dumps({'chat_id':chat,'text':'VPS health check OK - trading system verified'}).encode()
req=urllib.request.Request(f'https://api.telegram.org/bot{tg}/sendMessage', data=payload, headers={'Content-Type':'application/json'})
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        print('sendMessage', r.status)
except Exception as e:
    print('sendMessage fail', getattr(e,'code',e))
PY

echo "=== MARKET ALERTS DRY RUN ==="
ADMIN=$(grep '^ADMIN_API_KEY=' .env | cut -d= -f2-)
curl -sf -X POST "http://127.0.0.1:8001/api/news/market-alerts/run?dry_run=true&skip_telegram=true" -H "X-API-Key: $ADMIN"; echo

echo "=== ERRORS LAST 15m ==="
docker logs ai-trading-backend --since 15m 2>&1 | grep -iE 'CRITICAL|All LLM providers|Failed to send telegram|Traceback' | tail -20 || echo "(none)"

echo "=== CONTAINERS ==="
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -E 'ai-trading|vps-'
'''
r=subprocess.run(SSH+[VERIFY], capture_output=True, text=True)
print(r.stdout)
if r.stderr: print('STDERR', r.stderr)
print('exit', r.returncode)
