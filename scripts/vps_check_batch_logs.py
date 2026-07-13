#!/usr/bin/env python3
"""Check VPS logs for symbol batch scanning."""
import subprocess
import sys

from vps_ssh_common import ssh_cmd

REMOTE = r"""
echo "=== SYMBOL BATCH LOGS ==="
docker logs ai-trading-backend --since 30m 2>&1 \
  | grep -E 'Symbol scan:|Symbol batch pause' \
  | tail -15 || echo "(waiting for first cycle)"

echo ""
echo "=== RECENT CYCLE LOGS ==="
docker logs ai-trading-backend --since 30m 2>&1 \
  | grep -i 'symbol scan' \
  | tail -10 || echo "(none)"

echo ""
echo "=== RATE LIMIT (last 30m) ==="
docker logs ai-trading-backend --since 30m 2>&1 \
  | grep -iE 'IP ban|418|-1003|429' \
  | tail -5 || echo "(none)"

echo ""
curl -sf http://127.0.0.1:8001/trading/loop/status -H "X-API-Key: $(grep '^ADMIN_API_KEY=' /root/ai-trading-platform-v3/.env | cut -d= -f2-)" 2>/dev/null || echo loop_status_unavailable
"""


def main() -> int:
    r = subprocess.run(
        ssh_cmd(REMOTE),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    print(r.stdout)
    if r.stderr:
        print("STDERR:", r.stderr[-500:], file=sys.stderr)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
