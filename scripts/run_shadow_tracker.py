#!/usr/bin/env python3
"""Score blocked/vetoed signals hypothetically (SL/TP walk on 1h bars).

Not part of the live trading cycle — run from cron or by hand:

    PYTHONPATH=. python scripts/run_shadow_tracker.py
    PYTHONPATH=. python scripts/run_shadow_tracker.py --report-only
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database.connection import SessionLocal
from backend.services.binance_market_data import binance_market_data
from backend.services.ctrader_service import ctrader_service
from backend.services.multi_asset_bars import classify_symbol
from backend.services.shadow_tracker import (
    ensure_table,
    format_report,
    gate_report,
    update_shadows,
)


def _fetch_h1_bars_sync(symbol: str) -> List[Dict]:
    """Binance 1h klines for crypto; cTrader H1 for FX/metals."""
    asset = classify_symbol(symbol)
    if asset == "crypto":
        return asyncio.run(
            binance_market_data.get_klines(symbol, interval="1h", limit=200)
        )
    return ctrader_service.get_trendbars(symbol, "H1", count=200) or []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Print the per-gate report without scoring new signals",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=2000,
        help="Max trading_signals rows to scan (newest first)",
    )
    args = parser.parse_args()

    ensure_table()
    db = SessionLocal()
    try:
        if not args.report_only:
            stats = update_shadows(
                db, _fetch_h1_bars_sync, lookback_limit=args.lookback,
            )
            print(
                f"scored={stats['scored']} skipped={stats['skipped']} "
                f"failed={stats['failed']}"
            )
        print(format_report(gate_report(db)))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
