#!/usr/bin/env python3
"""Post-cycle kie verification."""
from __future__ import annotations

import subprocess
import sys

from vps_ssh_common import ssh_cmd

REMOTE = r"""
echo "=== SINCE CYCLE 1 START (11:22 UTC) ==="
docker logs ai-trading-backend --since 2026-07-13T11:22:00 2>&1 | grep -E "LLM Router|api.kie.ai|assistant message prefill|HTTP 400|invalid_request" | tail -40

echo ""
echo "=== COUNTS SINCE CONTAINER START ==="
echo -n "kie_http_200="
docker logs ai-trading-backend 2>&1 | grep "api.kie.ai" | grep -c "200 OK" || true
echo -n " kie_http_400="
docker logs ai-trading-backend 2>&1 | grep "api.kie.ai" | grep -c "400" || true
echo -n " prefill_errors="
docker logs ai-trading-backend 2>&1 | grep -c "assistant message prefill" || true
echo -n " router_failures="
docker logs ai-trading-backend 2>&1 | grep "LLM Router" | grep -c "failed" || true
echo -n " router_success_kie="
docker logs ai-trading-backend 2>&1 | grep -c "Success using Primary (kie)" || true

echo ""
echo "=== CONTEXT OF KIE SUCCESS ==="
docker logs ai-trading-backend 2>&1 | grep -B8 "Success using Primary" | head -20

echo ""
echo "=== LOOP ==="
curl -sf http://127.0.0.1:8001/trading/loop/status | python3 -c "import sys,json; d=json.load(sys.stdin); print('cycle_count=',d.get('cycle_count')); print('state=',d.get('state')); print('last_cycle=',d.get('last_cycle'))"
"""


def main() -> int:
    r = subprocess.run(ssh_cmd(REMOTE), capture_output=True, text=True, encoding="utf-8", errors="replace")
    print((r.stdout or "").encode("ascii", errors="replace").decode("ascii"))
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
