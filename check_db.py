
from backend.database.connection import SessionLocal
from backend.database.models import Trade

db = SessionLocal()
try:
    open_trades = db.query(Trade).filter(Trade.status.in_(["open", "filled"])).all()
    print(f"Found {len(open_trades)} open trades in DB.")
    for t in open_trades:
        print(f"  - ID: {t.id} | {t.symbol} {t.direction} at {t.entry_price} | Time: {t.timestamp} | Exchange: {t.exchange} | OrderID: {t.binance_order_id}")
finally:
    db.close()
