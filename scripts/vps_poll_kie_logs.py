#!/usr/bin/env python3
"""Poll VPS logs for kie errors during trading cycles."""
from __future__ import annotations

import subprocess
import sys
import time

from vps_ssh_common import ssh_cmd

POLL_REMOTE = r"""
CYCLE=$(curl -sf http://127.0.0.1:8001/trading/loop/status | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('cycle_count',0), d.get('last_cycle'))")
PREFILL=$(docker logs ai-trading-backend 2>&1 | grep -c "assistant message prefill" || true)
HTTP400=$(docker logs ai-trading-backend 2>&1 | grep -c "invalid_request_error" || true)
KIE_OK=$(docker logs ai-trading-backend 2>&1 | grep -c "Success using Primary (kie)" || true)
KIE_FAIL=$(docker logs ai-trading-backend 2>&1 | grep "Primary (kie) attempt" | grep -c "failed" || true)
echo "cycle=$CYCLE prefill=$PREFILL http400=$HTTP400 kie_success=$KIE_OK kie_fail=$KIE_FAIL"
docker logs ai-trading-backend 2>&1 | grep -E "LLM Router|assistant message prefill|HTTP 400" | tail -6
"""


def poll(label: str) -> None:
    r = subprocess.run(
        ssh_cmd(POLL_REMOTE),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = (r.stdout or "").encode("ascii", errors="replace").decode("ascii")
    print(f"--- {label} ---")
    print(out)


def main() -> int:
    polls = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    interval = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    for i in range(polls):
        poll(f"poll {i + 1}/{polls}")
        if i < polls - 1:
            time.sleep(interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
