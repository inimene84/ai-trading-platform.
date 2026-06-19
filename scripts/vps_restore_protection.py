#!/usr/bin/env python3
"""Restore missing Binance SL/TP from open DB trades. Run inside backend container."""
import os
import sys

sys.path.insert(0, "/app")

from backend.database.connection import SessionLocal
from backend.database.models import Trade
from backend.services.binance_futures_service import binance_futures_broker


def main():
    db = SessionLocal()
    try:
        trades = db.query(Trade).filter(Trade.status.in_(["open", "filled"])).all()
        if not trades:
            print("No open trades in DB.")
            return 0
        for t in trades:
            if not t.stop_loss and not t.take_profit:
                print(f"  SKIP {t.symbol}: no SL/TP in DB")
                continue
            res = binance_futures_broker.ensure_protective_orders(
                t.symbol, t.direction, t.stop_loss, t.take_profit,
            )
            print(f"  {t.symbol} {t.direction}: {res}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
