#!/usr/bin/env bash
set -euo pipefail
docker exec ai-trading-backend python3 - <<'PY'
import json
import urllib.request
from collections import defaultdict

with urllib.request.urlopen("http://127.0.0.1:8000/trading/binance/status") as resp:
    d = json.load(resp)

orders = d.get("open_orders", [])
positions = d.get("positions", [])
print("=== OPEN POSITIONS ===")
for p in positions:
    print(f"  {p['symbol']} {p['side']} qty={p['quantity']} entry={p.get('entry_price')}")

print("\n=== PROTECTION STATUS ===")
by = defaultdict(list)
for o in orders:
    by[o["symbol"]].append(o["type"])

for sym in sorted({p["symbol"] for p in positions}):
    types = by.get(sym, [])
    sl = any("STOP" in t for t in types)
    tp = any("TAKE_PROFIT" in t for t in types)
    if not sl:
        status = "NAKED (no SL)"
    elif not tp:
        status = "PARTIAL (SL only)"
    else:
        status = "OK"
    print(f"  {sym}: {status}  orders={types}")

w = d.get("wallet", {})
print(f"\n=== WALLET equity={w.get('equity')} available={w.get('available')} ===")
PY
