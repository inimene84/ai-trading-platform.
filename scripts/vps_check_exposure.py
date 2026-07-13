from vps_ssh_common import ssh_cmd, SSH_BASE
#!/usr/bin/env python3
"""Compare DB open trades vs live exchange positions and compute notionals."""
import subprocess

REMOTE = r'''
cd /root/ai-trading-platform-v3
docker exec -i ai-trading-backend python3 - <<'PY'
from backend.database.connection import SessionLocal
from backend.database.models import Trade

db = SessionLocal()
rows = db.query(Trade).filter(Trade.status.in_(["open", "filled"])).all()
print(f"DB open/filled rows: {len(rows)}")
by_dir = {}
for t in rows:
    notional = (t.quantity or 0) * (t.entry_price or 0)
    by_dir.setdefault(t.direction, []).append((t.symbol, round(notional, 2)))
for d, items in by_dir.items():
    syms = {s for s, _ in items}
    total = sum(n for _, n in items)
    print(f"{d}: rows={len(items)} distinct_symbols={len(syms)} total_notional=${total:.2f}")
    for s, n in items:
        print(f"   {s} ${n}")
db.close()
PY
echo "---EXCHANGE---"
docker exec -i ai-trading-backend python3 - <<'PY'
import os
from binance.client import Client
c = Client(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_SECRET_KEY"))
acct = c.futures_account()
print(f"equity={acct.get('totalMarginBalance')} available={acct.get('availableBalance')} margin_used={acct.get('totalPositionInitialMargin')}")
for p in acct.get("positions", []):
    amt = float(p.get("positionAmt", 0))
    if amt != 0:
        print(f"  {p['symbol']} amt={amt} notional={p.get('notional')}")
PY
'''
r = subprocess.run(
    ssh_cmd(REMOTE),
    capture_output=True, text=True, encoding='utf-8', errors='replace',
)
print(r.stdout)
if r.stderr:
    print('STDERR:', r.stderr[-600:])
