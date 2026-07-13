#!/usr/bin/env python3
"""Check open-position protection on VPS and restore missing SL/TP."""
import subprocess
import sys

from vps_ssh_common import ssh_cmd

CHECK = r'''
docker exec -i ai-trading-backend python3 - <<'PY'
import json, os, sys
sys.path.insert(0, "/app")
from collections import defaultdict
from binance.client import Client
from backend.database.connection import SessionLocal
from backend.database.models import Trade

client = Client(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_SECRET_KEY"))

# Live positions
acct = client.futures_account()
live = []
for p in acct.get("positions", []):
    amt = float(p.get("positionAmt", 0))
    if amt != 0:
        live.append({
            "symbol": p["symbol"],
            "amt": amt,
            "entry": p.get("entryPrice"),
            "notional": p.get("notional"),
        })

print("=== LIVE POSITIONS ===")
for p in live:
    print(f"  {p['symbol']} amt={p['amt']} entry={p['entry']} notional={p['notional']}")

# Open orders (regular + algo) with trigger prices
orders = client.futures_get_open_orders()
try:
    algo = client.futures_get_open_algo_orders()
    algo_list = algo if isinstance(algo, list) else algo.get("orders", [])
except Exception:
    algo_list = []

by_sym = defaultdict(list)
order_detail = defaultdict(list)
for o in orders:
    t = o.get("type", "?")
    sp = o.get("stopPrice") or o.get("price")
    by_sym[o["symbol"]].append(t)
    order_detail[o["symbol"]].append(f"{t}@{sp}")
for o in algo_list:
    sym = o.get("symbol", "?")
    t = o.get("orderType", o.get("type", "ALGO"))
    sp = o.get("triggerPrice")
    by_sym[sym].append(t)
    order_detail[sym].append(f"{t}@{sp}")

print("\n=== PROTECTION STATUS ===")
naked = []
for p in live:
    sym = p["symbol"]
    types = by_sym.get(sym, [])
    details = order_detail.get(sym, [])
    entry = float(p.get("entry") or 0)
    amt = float(p.get("amt") or 0)
    direction = "BUY" if amt > 0 else "SELL"
    has_sl = any("STOP" in str(t).upper() for t in types)
    has_tp = any("TAKE_PROFIT" in str(t).upper() for t in types)
    if not has_sl and not has_tp:
        status = "NAKED (no SL/TP)"
        naked.append(sym)
    elif not has_sl:
        status = "PARTIAL (TP only)"
        naked.append(sym)
    elif not has_tp:
        status = "PARTIAL (SL only)"
    else:
        status = "OK"
    print(f"  {sym}: {status}  orders={details}")
    if has_sl and entry > 0:
        sl_prices = []
        for d in details:
            if "STOP" in d.upper() and "TAKE_PROFIT" not in d.upper():
                try:
                    sl_prices.append(float(d.split("@", 1)[1]))
                except Exception:
                    pass
        for sl in sl_prices:
            if direction == "SELL" and sl < entry:
                print(f"    note: SL {sl} below entry {entry} (trailed profit-lock)")
            elif direction == "BUY" and sl > entry:
                print(f"    note: SL {sl} above entry {entry} (trailed profit-lock)")

print("\n=== DB OPEN TRADES (SL/TP) ===")
db = SessionLocal()
rows = db.query(Trade).filter(Trade.status.in_(["open", "filled"])).all()
for t in rows:
    print(f"  {t.symbol} {t.direction} qty={t.quantity} SL={t.stop_loss} TP={t.take_profit}")
db.close()

if naked:
    print("\nNAKED_SYMBOLS=" + ",".join(naked))
PY
'''

RESTORE = r'''
docker exec -i ai-trading-backend python3 - <<'PY'
import sys
sys.path.insert(0, "/app")
from backend.database.connection import SessionLocal
from backend.database.models import Trade
from backend.services.binance_futures_service import binance_futures_broker

db = SessionLocal()
try:
    trades = db.query(Trade).filter(Trade.status.in_(["open", "filled"])).all()
    restored = 0
    for t in trades:
        if not t.stop_loss and not t.take_profit:
            print(f"  SKIP {t.symbol}: no SL/TP stored in DB")
            continue
        res = binance_futures_broker.ensure_protective_orders(
            t.symbol, t.direction, t.stop_loss, t.take_profit,
        )
        print(f"  RESTORE {t.symbol} {t.direction}: {res}")
        restored += 1
    print(f"restored={restored}")
finally:
    db.close()
PY
'''

VERIFY = r'''
docker exec -i ai-trading-backend python3 - <<'PY'
import os, sys
from collections import defaultdict
from binance.client import Client
sys.path.insert(0, "/app")
client = Client(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_SECRET_KEY"))
acct = client.futures_account()
live = [p for p in acct.get("positions", []) if float(p.get("positionAmt", 0)) != 0]
orders = client.futures_get_open_orders()
try:
    algo = client.futures_get_open_algo_orders()
    algo_list = algo if isinstance(algo, list) else algo.get("orders", [])
except Exception:
    algo_list = []
by_sym = defaultdict(list)
for o in orders:
    by_sym[o["symbol"]].append(o.get("type", "?"))
for o in algo_list:
    by_sym[o.get("symbol", "?")].append(o.get("orderType", o.get("type", "ALGO")))
print("=== POST-RESTORE ===")
for p in live:
    sym = p["symbol"]
    types = by_sym.get(sym, [])
    has_sl = any("STOP" in str(t).upper() for t in types)
    has_tp = any("TAKE_PROFIT" in str(t).upper() for t in types)
    print(f"  {sym}: SL={has_sl} TP={has_tp} orders={types}")
PY
'''


def main() -> int:
    print("=== CHECK ===")
    r = subprocess.run(ssh_cmd(CHECK), capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(r.stdout)
    if r.returncode != 0:
        print("STDERR:", (r.stderr or "")[-800:])
        return r.returncode

    if "NAKED_SYMBOLS=" in (r.stdout or ""):
        print("\n=== RESTORE ===")
        r2 = subprocess.run(ssh_cmd(RESTORE), capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(r2.stdout)
        if r2.returncode != 0:
            print("STDERR:", (r2.stderr or "")[-800:])
            return r2.returncode

        print("\n=== VERIFY ===")
        r3 = subprocess.run(ssh_cmd(VERIFY), capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(r3.stdout)
        return r3.returncode

    print("All positions protected — no restore needed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
