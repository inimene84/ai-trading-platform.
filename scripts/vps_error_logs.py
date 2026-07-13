#!/usr/bin/env python3
"""Scan VPS container logs for recent errors and warnings."""
import subprocess
import sys

from vps_ssh_common import ssh_cmd

REMOTE = r"""
echo "=== BACKEND ERRORS (last 2h) ==="
docker logs ai-trading-backend --since 2h 2>&1 \
  | grep -iE 'ERROR|CRITICAL|Traceback|Exception|failed|FAILED' \
  | grep -viE 'GET /health|GET /sentry/status|GET /trading/price|GET /trading/positions|GET /trading/status|GET /trading/loop|GET /trading/portfolio|GET /trading/trades|GET /trading/signals' \
  | tail -40 || echo "(none)"

echo ""
echo "=== BACKEND WARNINGS (last 2h, top 25) ==="
docker logs ai-trading-backend --since 2h 2>&1 \
  | grep -i 'WARNING' \
  | grep -viE 'GET /health|GET /sentry/status|GET /trading/' \
  | tail -25 || echo "(none)"

echo ""
echo "=== SENTRY WATCHDOG (last 2h) ==="
docker logs ai-trading-sentry-watchdog --since 2h 2>&1 | tail -20

echo ""
echo "=== IP BAN / RATE LIMIT (last 2h) ==="
docker logs ai-trading-backend --since 2h 2>&1 \
  | grep -iE 'IP ban|418|-1003|rate limit|ban active' \
  | head -5 || echo "(none)"
docker logs ai-trading-backend --since 2h 2>&1 \
  | grep -iE 'IP ban|418|-1003' \
  | tail -3 || echo "(cleared)"

echo ""
echo "=== LLM ERRORS (last 2h) ==="
docker logs ai-trading-backend --since 2h 2>&1 \
  | grep -iE 'LLM Router.*failed|All LLM providers|HTTP 400|prefill' \
  | tail -8 || echo "(none)"
curl -sf http://127.0.0.1:8001/health; echo
curl -sf http://127.0.0.1:8001/sentry/status; echo
curl -sf http://127.0.0.1:8001/trading/loop/status | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps({k:d[k] for k in ['state','running','cycle_count','error','equity','last_cycle'] if k in d}, indent=2))"; echo

echo ""
echo "=== CONTAINER STATUS ==="
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -E 'ai-trading|NAMES'
"""


def main() -> int:
    r = subprocess.run(
        ssh_cmd(REMOTE),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = (r.stdout or "").encode("ascii", errors="replace").decode("ascii")
    print(out)
    if r.stderr:
        print(r.stderr[-500:], file=sys.stderr)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
