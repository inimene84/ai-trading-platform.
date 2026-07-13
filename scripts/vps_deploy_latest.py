#!/usr/bin/env python3
"""Deploy latest main to VPS backend and restart trading loop."""
import subprocess
import sys

from vps_ssh_common import ssh_cmd

DEPLOY = r"""
cd /root/ai-trading-platform-v3
git pull origin main 2>&1 | tail -3
docker compose -f docker-compose.prod.yml up -d --build backend sentry-watchdog 2>&1 | tail -8
for i in $(seq 1 24); do
  curl -sf http://127.0.0.1:8001/health >/dev/null && { echo backend_healthy; break; }
  sleep 5
done
curl -sf http://127.0.0.1:8001/health; echo
ADMIN=$(grep '^ADMIN_API_KEY=' .env | cut -d= -f2-)
curl -sf -X POST http://127.0.0.1:8001/sentry/resume \
  -H "X-API-Key: $ADMIN" -H 'Content-Type: application/json' \
  -d '{"note":"starlette + ops scripts deploy"}' >/dev/null || true
SYMS_JSON=$(grep '^TRADING_SYMBOLS=' .env | cut -d= -f2- | python3 -c "import sys,json; print(json.dumps([s.strip() for s in sys.stdin.read().split(',') if s.strip()]))")
curl -sf -X POST http://127.0.0.1:8001/trading/loop/stop -H "X-API-Key: $ADMIN" >/dev/null 2>&1 || true
sleep 2
curl -sf -X POST http://127.0.0.1:8001/trading/loop/start \
  -H "X-API-Key: $ADMIN" -H 'Content-Type: application/json' \
  -d "{\"interval_minutes\":15,\"strategy\":\"combined\",\"symbols\":${SYMS_JSON}}" >/dev/null
echo loop_restarted
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -E 'ai-trading|NAMES'
"""


def main() -> int:
    print("=== DEPLOY ===")
    r = subprocess.run(
        ssh_cmd(DEPLOY),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    print(r.stdout)
    if r.stderr:
        print("STDERR:", r.stderr[-1000:], file=sys.stderr)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
