#!/usr/bin/env python3
"""
Shadow PnL Tracker CLI — score blocked/vetoed signals and report gate effectiveness.

Usage (from repo root):
    PYTHONPATH=. python scripts/run_shadow_tracker.py [--days 30] [--lookback 96]

Run it periodically (e.g. every few hours via cron / task scheduler). Each run
scores newly-resolved blocked signals (idempotent — a signal is scored once)
and prints the per-gate report:

    avg_shadow_r > 0  → the gate blocked net winners  → COSTING money
    avg_shadow_r < 0  → the gate blocked net losers   → SAVING money
"""

import argparse
import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("shadow_tracker_cli")


def _is_crypto(symbol: str) -> bool:
    s = symbol.upper()
    return s.endswith(("USDT", "USDC", "BUSD")) or s.startswith(("BTC", "ETH", "SOL"))


async def make_bars_fetcher():
    """Bars fetcher routed per asset class (Binance klines / cTrader trendbars)."""
    from backend.services.binance_market_data import binance_market_data
    from backend.services.ctrader_service import ctrader_service

    async def fetch(symbol: str):
        if _is_crypto(symbol):
            return await binance_market_data.get_klines(symbol, interval="1h", limit=1500)
        # cTrader trendbars are synchronous in this codebase
        return ctrader_service.get_trendbars(symbol, "H1", count=500)

    return fetch


async def main() -> int:
    parser = argparse.ArgumentParser(description="Shadow PnL tracker")
    parser.add_argument("--days", type=int, default=30, help="report window in days")
    parser.add_argument("--lookback", type=int, default=96, help="signal lookback in hours")
    parser.add_argument("--report-only", action="store_true", help="skip scoring, just print the report")
    args = parser.parse_args()

    from backend.services import shadow_tracker

    shadow_tracker.ensure_shadow_table()

    if not args.report_only:
        fetch = await make_bars_fetcher()
        stats = await shadow_tracker.run_shadow_update(fetch, lookback_hours=args.lookback)
        logger.info(f"shadow update: {stats}")

    report = shadow_tracker.shadow_report(days=args.days)
    if not report:
        print("\nNo shadow outcomes yet — blocked signals need elapsed bars before they can be scored.")
        return 0

    print(f"\n=== SHADOW GATE REPORT (last {args.days}d) ===")
    print(f"{'gate':<22} {'n':>5} {'win%':>7} {'avgR':>8} {'avgPnL%':>9} {'mfeR':>7}  verdict")
    for r in report:
        print(
            f"{r['gate']:<22} {r['blocked_signals']:>5} "
            f"{r['win_rate_if_taken'] * 100:>6.1f}% "
            f"{r['avg_shadow_r']:>+8.3f} {r['avg_shadow_pnl_pct']:>+8.2f}% "
            f"{r['avg_mfe_r']:>+7.2f}  {r['verdict']}"
        )
    print("\nReading: avgR > +0.05 → gate is COSTING (blocked winners); "
          "avgR < -0.05 → gate is SAVING (blocked losers).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
