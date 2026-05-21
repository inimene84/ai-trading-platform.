
from backend.database.connection import SessionLocal
from backend.database.models import Trade
from datetime import datetime, timezone

db = SessionLocal()
try:
    open_trades = db.query(Trade).filter(Trade.status.in_(["open", "filled"])).all()
    print(f"Closing {len(open_trades)} open trades in DB.")
    for t in open_trades:
        t.status = "closed"
        t.closed_at = datetime.now(timezone.utc)
        t.notes = (t.notes or "") + " | Manually closed to clear ghost positions"
    db.commit()
    print("Done.")
finally:
    db.close()
