#!/usr/bin/env python3
"""One-time repair for closed trades whose exit_price is corrupt.

Background: BrokerPositionSyncService used to trust broker.get_exit_price()
unconditionally. On 35 historical ARBUSDT rows that returned a bogus price
(~0.00075 vs ~$0.08 entries), fabricating ~+$856 of phantom P&L that poisoned
the dashboard stats, trade memory, and the skill miner.

This script finds closed trades whose exit_price deviates more than
MAX_DEVIATION (default 50%) from entry_price, and resets exit_price/pnl to
NULL (unknown) with an audit note. It backs up the DB file first.

Usage (inside the backend container):
    python3 scripts/repair_corrupt_trades.py --db /app/data/hedge_fund.db --dry-run
    python3 scripts/repair_corrupt_trades.py --db /app/data/hedge_fund.db
"""
import argparse
import shutil
import sqlite3
from datetime import datetime, timezone

MAX_DEVIATION = 0.5

FIND_SQL = """
SELECT id, symbol, direction, entry_price, exit_price, pnl
FROM trades
WHERE status = 'closed'
  AND exit_price IS NOT NULL
  AND entry_price IS NOT NULL AND entry_price > 0
  AND ABS(exit_price - entry_price) / entry_price > :max_dev
ORDER BY id
"""

REPAIR_SQL = """
UPDATE trades
SET exit_price = NULL,
    pnl = NULL,
    notes = COALESCE(notes, '') || :note
WHERE id = :id
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="/app/data/hedge_fund.db", help="Path to SQLite DB")
    parser.add_argument("--max-deviation", type=float, default=MAX_DEVIATION,
                        help="Max |exit-entry|/entry ratio considered plausible (default 0.5)")
    parser.add_argument("--dry-run", action="store_true", help="Report without modifying")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    rows = cur.execute(FIND_SQL, {"max_dev": args.max_deviation}).fetchall()
    total_phantom_pnl = sum(r["pnl"] or 0 for r in rows)
    print(f"Found {len(rows)} corrupt closed trade(s); phantom P&L total: ${total_phantom_pnl:.2f}")
    for r in rows:
        print(f"  id={r['id']} {r['symbol']} {r['direction']} entry={r['entry_price']} "
              f"exit={r['exit_price']} pnl={r['pnl']}")

    if not rows:
        return
    if args.dry_run:
        print("Dry run — no changes made.")
        return

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = f"{args.db}.pre-exit-price-repair-{stamp}"
    shutil.copy2(args.db, backup)
    print(f"DB backed up to {backup}")

    for r in rows:
        note = (f" | repaired {stamp}: corrupt exit price (was {r['exit_price']}, "
                f"pnl was {r['pnl']}) — exit/pnl reset to unknown")
        cur.execute(REPAIR_SQL, {"id": r["id"], "note": note})
    conn.commit()
    print(f"Repaired {len(rows)} trade(s). Removed ${total_phantom_pnl:.2f} of phantom P&L from stats.")


if __name__ == "__main__":
    main()
