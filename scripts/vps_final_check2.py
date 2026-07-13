#!/usr/bin/env python3
"""Upload final router, rebuild, and run full-system verification."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SSH_KEY = str(Path.home() / ".ssh" / "id_vps_bot")
HOST = "root@72.60.18.113"
PROJECT = "/root/ai-trading-platform-v3"

VERIFY = r'''
cd /root/ai-trading-platform-v3
for i in $(seq 1 15); do
  curl -sf http://127.0.0.1:8001/health >/dev/null && break
  sleep 5
done
ADMIN=$(grep '^ADMIN_API_KEY=' .env | cut -d= -f2-)
curl -sf -X POST http://127.0.0.1:8001/sentry/resume -H "X-API-Key: $ADMIN" -H 'Content-Type: application/json' -d '{"note":"final check"}' >/dev/null || true
sleep 2

echo "======== FULL SYSTEM CHECK ========"
echo "-- Core health --"
curl -sf http://127.0.0.1:8001/health; echo
curl -sf http://127.0.0.1:8081/health; echo
curl -sf http://127.0.0.1:8086/health | head -c 80; echo
curl -sf http://127.0.0.1:6333/healthz; echo
curl -sf http://127.0.0.1:4001/health/liveliness; echo
curl -s -o /dev/null -w "mcp :9100 -> %{http_code}" http://127.0.0.1:9100/health; echo

echo ""
echo "-- Containers (bad states) --"
docker ps -a --format '{{.Names}}|{{.Status}}' | grep -iE 'restart|unhealthy|exited.*(ai-trading|vps-)' || echo "none"

echo ""
echo "-- Trading loop --"
curl -sf http://127.0.0.1:8001/sentry/status | head -c 200; echo
curl -sf http://127.0.0.1:8001/trading/loop/status | head -c 400; echo

echo ""
echo "-- Market alerts x3 (Kie primary) --"
for i in 1 2 3; do
  curl -sf -X POST "http://127.0.0.1:8001/api/news/market-alerts/run?dry_run=true&skip_telegram=true" -H "X-API-Key: $ADMIN" | python3 -c "import sys,json; d=json.load(sys.stdin)['summary']; print(f\"  source={d['source']} bias={d['bias']} conf={d['confidence']}\")"
done

echo ""
echo "-- Router success log --"
docker logs ai-trading-backend --since 5m 2>&1 | grep -E "LLM Router: (Success|All .* attempts failed)" | tail -6

echo ""
echo "-- Error scan since restart --"
echo "parse_errors=$(docker logs ai-trading-backend --since 10m 2>&1 | grep -c 'Failed to parse JSON' || true)"
echo "critical=$(docker logs ai-trading-backend --since 10m 2>&1 | grep -c 'CRITICAL' || true)"
echo "telegram_fail=$(docker logs ai-trading-backend --since 10m 2>&1 | grep -c 'Failed to send telegram' || true)"
echo "tracebacks=$(docker logs ai-trading-backend --since 10m 2>&1 | grep -c 'Traceback' || true)"

echo ""
echo "-- Disk / memory --"
df -h / | tail -1
free -h | head -2 | tail -1

echo ""
echo "-- Leftover .bak files --"
ls backend/routes/*.bak* 2>/dev/null || echo "none"
'''


def main():
    r = subprocess.run(
        ["scp", "-i", SSH_KEY, "-o", "BatchMode=yes",
         str(ROOT / "backend" / "llm" / "router.py"), f"{HOST}:{PROJECT}/backend/llm/router.py"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(r.stderr)
        return r.returncode
    print("router.py uploaded")

    r = subprocess.run(["ssh", "-i", SSH_KEY, "-o", "BatchMode=yes", HOST, VERIFY],
                       capture_output=True, text=True)
    print(r.stdout)
    if r.stderr:
        print("STDERR:", r.stderr[-800:])
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
