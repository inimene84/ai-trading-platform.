#!/usr/bin/env python3
"""Compare VPS working tree vs origin for Antigravity fixes."""
import subprocess
import sys

from vps_ssh_common import ssh_cmd

REMOTE = r"""
cd /root/ai-trading-platform-v3
echo "=== GIT STATUS ==="
git status -sb
echo ""
echo "=== COMMITS AHEAD OF ORIGIN ==="
git log origin/main..HEAD --oneline 2>/dev/null | head -10 || echo none
echo ""
echo "=== ROUTER PREFILL (kie) ==="
grep -n "is_kie\|assistant\|prefill\|response_json" backend/llm/router.py | head -20
echo ""
echo "=== BINANCE 4067 ==="
grep -n "4067\|margin_type" backend/services/binance_futures_service.py | head -12
echo ""
echo "=== MAKER LOG LEVEL ==="
grep -n "post-only rejected" backend/services/binance_futures_service.py
echo ""
echo "=== SYMBOL GATE LEVEL ==="
grep -n "negative expectancy" backend/services/trading_loop.py
echo ""
echo "=== POSITION MANAGER opened_at ==="
sed -n '58,75p' backend/services/position_manager.py
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
