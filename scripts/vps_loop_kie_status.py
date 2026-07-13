#!/usr/bin/env python3
"""Detailed VPS loop + kie log status."""
from __future__ import annotations

import subprocess
import sys

from vps_ssh_common import ssh_cmd

REMOTE = r"""
echo "=== FULL LOOP STATUS ==="
curl -sf http://127.0.0.1:8001/trading/loop/status | python3 -m json.tool 2>/dev/null | head -40

echo ""
echo "=== HEALTH / SENTRY ==="
curl -sf http://127.0.0.1:8001/health | python3 -m json.tool 2>/dev/null

echo ""
echo "=== RECENT TRADING LOOP LOGS ==="
docker logs ai-trading-backend --since 15m 2>&1 | grep -iE "trading_loop|cycle|SYMBOL GATE|HALTED|SENTRY" | tail -30

echo ""
echo "=== ALL KIE HTTP REQUESTS ==="
docker logs ai-trading-backend 2>&1 | grep "api.kie.ai" | tail -15

echo ""
echo "=== ERROR SUMMARY ==="
echo -n "prefill_errors="
docker logs ai-trading-backend 2>&1 | grep -c "assistant message prefill" || true
echo -n " http400_errors="
docker logs ai-trading-backend 2>&1 | grep -c "HTTP 400" || true
echo -n " kie_success="
docker logs ai-trading-backend 2>&1 | grep -c "Success using Primary (kie)" || true
echo -n " kie_failed="
docker logs ai-trading-backend 2>&1 | grep "Primary (kie)" | grep -c "failed" || true
"""


def main() -> int:
    r = subprocess.run(
        ssh_cmd(REMOTE),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    print((r.stdout or "").encode("ascii", errors="replace").decode("ascii"))
    if r.stderr:
        print(r.stderr[-500:], file=sys.stderr)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
