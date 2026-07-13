#!/usr/bin/env python3
import subprocess
from vps_ssh_common import ssh_cmd, SSH_BASE
REMOTE = r'''
cd /root/ai-trading-platform-v3
ADMIN=$(grep '^ADMIN_API_KEY=' .env | cut -d= -f2-)
echo "=== RESUME TRADING ==="
curl -sf -X POST http://127.0.0.1:8001/sentry/resume \
  -H "X-API-Key: $ADMIN" -H 'Content-Type: application/json' -d '{"note":"post-fix health check"}'
echo
sleep 2
curl -sf http://127.0.0.1:8001/health; echo
curl -sf http://127.0.0.1:8001/sentry/status; echo

echo "=== RESTART LITELLM ==="
docker compose -f docker-compose.prod.yml up -d litellm
sleep 4
curl -sf http://127.0.0.1:4001/health/liveliness; echo

echo "=== START TRADING LOOP ==="
SYMS_JSON=$(grep '^TRADING_SYMBOLS=' .env | cut -d= -f2- | python3 -c "import sys,json; print(json.dumps([s.strip() for s in sys.stdin.read().split(',') if s.strip()]))")
curl -sf -X POST http://127.0.0.1:8001/trading/loop/start \
  -H "X-API-Key: $ADMIN" -H 'Content-Type: application/json' \
  -d "{\"interval_minutes\":15,\"strategy\":\"combined\",\"symbols\":${SYMS_JSON}}"
echo
sleep 2
curl -sf http://127.0.0.1:8001/trading/loop/status; echo

echo "=== FINAL ERROR SCAN (5m) ==="
docker logs ai-trading-backend --since 5m 2>&1 | grep -iE 'CRITICAL|All LLM providers|Failed to send telegram|Traceback' | tail -10 || echo "(none)"
'''
r = subprocess.run(
    ssh_cmd(REMOTE),
    capture_output=True, text=True,
)
print(r.stdout)
if r.stderr:
    print('STDERR:', r.stderr)
