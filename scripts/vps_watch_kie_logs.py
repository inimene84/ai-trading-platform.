#!/usr/bin/env python3
"""Watch VPS backend logs for kie prefill errors and LLM router activity."""
from __future__ import annotations

import subprocess
import sys

from vps_ssh_common import ssh_cmd

REMOTE = r"""
echo "=== LOOP STATUS ==="
curl -sf http://127.0.0.1:8001/trading/loop/status | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('running=', d.get('running'))
print('cycle_count=', d.get('cycle_count'))
print('last_cycle=', d.get('last_cycle'))
print('error=', d.get('error'))
print('symbols=', len(d.get('symbols', [])))
"

echo ""
echo "=== KIE PREFILL ERRORS (since container start) ==="
PREFILL=$(docker logs ai-trading-backend 2>&1 | grep -c "assistant message prefill" || true)
echo "count=$PREFILL"
docker logs ai-trading-backend 2>&1 | grep "assistant message prefill" | tail -3

echo ""
echo "=== HTTP 400 KIE ERRORS (since container start) ==="
HTTP400=$(docker logs ai-trading-backend 2>&1 | grep -c "invalid_request_error.*user message" || true)
echo "count=$HTTP400"

echo ""
echo "=== LLM ROUTER (last 25 lines) ==="
docker logs ai-trading-backend 2>&1 | grep "LLM Router" | tail -25

echo ""
echo "=== KIE / PRIMARY ATTEMPTS (last 20) ==="
docker logs ai-trading-backend 2>&1 | grep -iE "kie|Primary \(kie\)" | tail -20
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
        print(r.stderr[-800:], file=sys.stderr)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
