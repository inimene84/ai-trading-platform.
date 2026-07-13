#!/usr/bin/env python3
"""One-off: close DB trade rows whose symbol is verified flat on the exchange."""
import subprocess

REMOTE = r'''
docker exec -i ai-trading-backend python3 - <<'PY'
from datetime import datetime, timezone
import os

from binance.client import Client

from backend.database.connection import SessionLocal
from backend.database.models import Trade

client = Client(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_SECRET_KEY"))
acct = client.futures_account()
live = {
    p["symbol"]: float(p.get("positionAmt", 0))
    for p in acct.get("positions", [])
    if float(p.get("positionAmt", 0)) != 0
}
print("live positions:", live)

db = SessionLocal()
rows = db.query(Trade).filter(Trade.status.in_(["open", "filled"])).all()
closed = 0
for t in rows:
    sym = t.symbol.replace("-", "").upper()
    if sym not in live:
        print(f"closing stale DB row: {t.symbol} {t.direction} qty={t.quantity} entry={t.entry_price}")
        t.status = "closed"
        t.closed_at = datetime.now(timezone.utc)
        t.notes = (t.notes or "") + " | RECONCILE: verified flat on exchange (stale row cleanup)"
        db.add(t)
        closed += 1
db.commit()
print(f"closed {closed} stale rows")

rows = db.query(Trade).filter(Trade.status.in_(["open", "filled"])).all()
print(f"remaining open rows: {len(rows)}")
for t in rows:
    print(f"  {t.symbol} {t.direction} qty={t.quantity} notional=${(t.quantity or 0) * (t.entry_price or 0):.2f}")
db.close()
PY
'''
r = subprocess.run(
    ['ssh', '-i', r'C:\Users\thori\.ssh\id_vps_bot', '-o', 'BatchMode=yes', 'root@72.60.18.113', REMOTE],
    capture_output=True, text=True, encoding='utf-8', errors='replace',
)
print(r.stdout)
if r.stderr:
    print('STDERR:', r.stderr[-600:])
