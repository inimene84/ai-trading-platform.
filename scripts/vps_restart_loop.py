#!/usr/bin/env python3
"""Restart trading loop on VPS to pick up env changes."""
import subprocess
import sys

from vps_ssh_common import ssh_cmd

REMOTE = r"""
cd /root/ai-trading-platform-v3
ADMIN=$(grep '^ADMIN_API_KEY=' .env | cut -d= -f2-)
SYMS_JSON=$(grep '^TRADING_SYMBOLS=' .env | cut -d= -f2- | python3 -c "import sys,json; print(json.dumps([s.strip() for s in sys.stdin.read().split(',') if s.strip()]))")
curl -sf -X POST http://127.0.0.1:8001/trading/loop/stop -H "X-API-Key: $ADMIN" >/dev/null 2>&1 || true
sleep 2
curl -sf -X POST http://127.0.0.1:8001/trading/loop/start \
  -H "X-API-Key: $ADMIN" -H 'Content-Type: application/json' \
  -d "{\"interval_minutes\":15,\"strategy\":\"combined\",\"symbols\":${SYMS_JSON}}" >/dev/null
echo loop_restarted
grep -E 'SYMBOL_BATCH|SYMBOL_CONCURRENCY|BINANCE_WALLET_POLL|BINANCE_ORDER_POLL' .env
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
        print(r.stderr[-500:], file=sys.stderr)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
