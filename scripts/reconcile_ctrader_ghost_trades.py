#!/usr/bin/env python3
"""CLI utility to inspect and reconcile ghost/stale cTrader trades in the DB."""

import sys
from pathlib import Path
from dotenv import load_dotenv

# Ensure repo root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=True)

from backend.database.connection import SessionLocal
from backend.database.models import Trade
from backend.services.ctrader_service import ctrader_broker
from backend.services.ctrader_trade_sync import is_ctrader_trade, reconcile_ctrader_positions


def main():
    dry_run = "--dry-run" in sys.argv or "-n" in sys.argv
    print("=" * 60)
    print("cTrader Ghost Trade Reconciliation Tool")
    print(f"Mode: {'DRY RUN (no changes)' if dry_run else 'APPLY CHANGES'}")
    print("=" * 60)

    db = SessionLocal()
    try:
        open_rows = (
            db.query(Trade)
            .filter(Trade.status.in_(["open", "filled"]))
            .all()
        )
        ctrader_rows = [t for t in open_rows if is_ctrader_trade(t)]
        print(f"Found {len(ctrader_rows)} open/filled cTrader trade rows in DB:")
        for t in ctrader_rows:
            print(
                f"  ID={t.id:<4} Symbol={t.symbol:<8} Dir={t.direction:<4} "
                f"Qty={t.quantity:<6} Entry={t.entry_price:<10} "
                f"PosID={t.broker_position_id or 'None':<12} "
                f"Opened={t.timestamp}"
            )

        print("\nChecking cTrader connection and live book...")
        if not ctrader_broker.has_credentials():
            print("⚠ No cTrader credentials configured in .env")
            return

        connected = ctrader_broker.ensure_connected()
        if not connected:
            print("⚠ Could not connect to cTrader Open API")
            return

        live_positions = ctrader_broker.get_positions()
        print(f"cTrader live book has {len(live_positions)} open positions:")
        for p in live_positions:
            print(
                f"  Symbol={p.get('symbol'):<8} Side={p.get('side'):<4} "
                f"Qty={p.get('quantity'):<6} Entry={p.get('entry_price'):<10} "
                f"PosID={p.get('position_id')}"
            )

        if dry_run:
            live_pids = {str(p.get("position_id")) for p in live_positions if p.get("position_id")}
            ghost_count = sum(
                1 for t in ctrader_rows
                if (t.broker_position_id and str(t.broker_position_id) not in live_pids)
                or (not t.broker_position_id and t.symbol not in {p.get('symbol') for p in live_positions})
            )
            print(f"\n[DRY RUN] Would close {ghost_count} ghost trade(s). Run without --dry-run to apply.")
            return

        print("\nReconciling positions in database...")
        res = reconcile_ctrader_positions(db, live_positions=live_positions, broker=ctrader_broker)
        print(f"Reconciliation result: Created={res['created']}, Updated={res['updated']}, Closed={res['closed']}")

        remaining = (
            db.query(Trade)
            .filter(Trade.status.in_(["open", "filled"]))
            .all()
        )
        remaining_ctrader = [t for t in remaining if is_ctrader_trade(t)]
        print(f"Remaining active cTrader trades in DB: {len(remaining_ctrader)}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
