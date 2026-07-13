#!/usr/bin/env python3
"""Check Binance IP ban state, env tuning, and trading data on VPS."""
import subprocess
import sys

from vps_ssh_common import ssh_cmd

REMOTE = r"""
echo "=== BAN TRIGGER (last 2h) ==="
docker logs ai-trading-backend --since 2h 2>&1 \
  | grep -iE 'IP ban|418|-1003|ban active|banned until|rate limit' \
  | head -15 || echo "(none)"

echo ""
echo "=== CURRENT BINANCE ENV ==="
grep -E 'SYMBOL_BATCH|SYMBOL_CONCURRENCY|TICKER_CACHE|BINANCE_WALLET_POLL|BINANCE_ORDER_POLL' \
  /root/ai-trading-platform-v3/.env | head -20 || echo "(none)"

echo ""
echo "=== TRADING STATUS ==="
ADMIN=$(grep '^ADMIN_API_KEY=' /root/ai-trading-platform-v3/.env | cut -d= -f2-)
curl -sf http://127.0.0.1:8001/trading/status -H "X-API-Key: $ADMIN" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print("equity=", d.get("equity"), "balance=", d.get("balance"), "positions=", len(d.get("positions") or []), "broker=", d.get("broker"))' \
  2>/dev/null || echo status_failed

echo ""
echo "=== LAST 2 MIN LOGS ==="
docker logs ai-trading-backend --since 2m 2>&1 \
  | grep -iE 'get_balance|get_positions|IP ban|Wallet poller|BINANCE IP|Too many requests' \
  | tail -15 || echo "(none)"

echo ""
echo "=== BINANCE STATUS ENDPOINT ==="
curl -sf http://127.0.0.1:8001/trading/binance/status -H "X-API-Key: $ADMIN" \
  | python3 -m json.tool 2>/dev/null | head -45 || echo binance_status_failed

echo ""
echo "=== PORTFOLIO ENDPOINT ==="
curl -sf http://127.0.0.1:8001/trading/portfolio -H "X-API-Key: $ADMIN" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print("equity=", d.get("equity"), "balance=", d.get("balance"), "db_positions=", len(d.get("positions") or []))' \
  2>/dev/null || echo portfolio_failed
"""


def main() -> int:
    r = subprocess.run(
        ssh_cmd(REMOTE),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    print(r.stdout or r.stderr)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
