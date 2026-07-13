#!/usr/bin/env python3
"""Check VPS LiteLLM config, git state, and kie/risk-reviewer logs."""
from __future__ import annotations

import subprocess
import sys

from vps_ssh_common import ssh_cmd

REMOTE = r"""
echo "=== VPS GIT ==="
cd /root/ai-trading-platform-v3
git log --oneline -5
git status -sb
echo ""
echo "=== LITELLM CONFIG (model_list) ==="
grep -A6 "gemini-3-flash\|claude-sonnet" litellm-config.yaml | head -30
echo ""
echo "=== LITELLM LOGS (startup errors) ==="
docker logs ai-trading-litellm 2>&1 | grep -iE "BadRequest|Provider NOT|gemini-3-flash|claude-sonnet|error" | tail -15
echo ""
echo "=== LITELLM RECENT ==="
docker logs ai-trading-litellm --since 10m 2>&1 | tail -20
echo ""
echo "=== LOOP STATUS ==="
curl -sf http://127.0.0.1:8001/trading/loop/status | python3 -c "import sys,json; d=json.load(sys.stdin); print('cycle_count=',d.get('cycle_count')); print('state=',d.get('state')); print('last_cycle=',d.get('last_cycle'))"
echo ""
echo "=== KIE / RISK REVIEWER (since container start) ==="
echo -n "kie_200="
docker logs ai-trading-backend 2>&1 | grep "api.kie.ai" | grep -c "200 OK" || true
echo -n " prefill_errors="
docker logs ai-trading-backend 2>&1 | grep -c "assistant message prefill" || true
echo -n " risk_reviewer="
docker logs ai-trading-backend 2>&1 | grep -ci "risk.review\|risk_reviewer" || true
echo -n " router_kie_success="
docker logs ai-trading-backend 2>&1 | grep -c "Success using Primary (kie)" || true
docker logs ai-trading-backend 2>&1 | grep -iE "LLM Router|risk.review|risk_reviewer|api.kie.ai" | tail -20
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
