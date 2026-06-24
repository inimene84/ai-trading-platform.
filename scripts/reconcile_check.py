"""One-shot read-only reconciliation check: broker positions vs DB trades.
Does NOT modify anything. Prints a comparison so we can decide next steps.
"""
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / '.env', override=True)

from backend.services.binance_futures_service import BinanceFuturesService
from backend.database.connection import SessionLocal
from backend.database.models import Trade

# --- Broker side ---
bfs = BinanceFuturesService()
broker_raw = bfs.get_positions()
broker = {}
for bp in broker_raw:
    amt = float(bp.get('quantity') or bp.get('positionAmt') or 0)
    if amt != 0:
        sym = bp['symbol']
        broker[sym] = {
            'amt': amt,
            'side': 'BUY' if amt > 0 else 'SELL',
            'entry': float(bp.get('entryPrice') or bp.get('entry_price') or 0),
            'upnl': float(bp.get('unrealizedProfit') or bp.get('unrealized_pnl') or 0),
        }

# --- DB side ---
db = SessionLocal()
try:
    open_trades = db.query(Trade).filter(Trade.status.in_(["open", "filled"])).all()
finally:
    db.close()

db_syms = {}
for t in open_trades:
    db_syms.setdefault(t.symbol, []).append({
        'id': t.id, 'dir': t.direction, 'entry': t.entry_price, 'qty': t.quantity, 'status': t.status
    })

print("=" * 60)
print(f"BROKER open positions (non-zero): {len(broker)}")
for s, p in sorted(broker.items()):
    print(f"  {s:10s} {p['side']:4s} amt={p['amt']:<12} entry={p['entry']:<12} uPnL={p['upnl']:.4f}")

print("=" * 60)
print(f"DB open/filled trade rows: {len(open_trades)}  across {len(db_syms)} symbols")
for s, rows in sorted(db_syms.items()):
    print(f"  {s:10s} rows={len(rows)} dirs={set(r['dir'] for r in rows)}")

print("=" * 60)
broker_set = set(broker.keys())
db_set = set(db_syms.keys())
print("ON BROKER but NOT in DB (untracked real positions!):", sorted(broker_set - db_set) or "none")
print("IN DB but NOT on broker (ghost/closed in DB only):", sorted(db_set - broker_set) or "none")
print("BOTH (tracked):", sorted(broker_set & db_set) or "none")
print("=" * 60)
