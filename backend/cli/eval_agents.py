"""
Agent Evaluation CLI
====================
Sanity-check persona agent behaviour on historical crypto data.

Usage:
    python -m backend.cli.eval_agents --symbol BTCUSDT
    python -m backend.cli.eval_agents --symbol ETHUSDT --personas warren_buffett,cathie_wood
    python -m backend.cli.eval_agents --list-personas

Requires the backend environment (poetry shell) and API keys configured.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

# Ensure project root is on the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def _parse_args():
    p = argparse.ArgumentParser(description="Evaluate persona agents on crypto data")
    p.add_argument("--symbol", default="BTCUSDT", help="Trading symbol (default: BTCUSDT)")
    p.add_argument("--personas", default=None, help="Comma-separated list of persona IDs to run (default: all active)")
    p.add_argument("--list-personas", action="store_true", help="List all available personas and exit")
    p.add_argument("--bars", type=int, default=100, help="Number of bars to fetch (default: 100)")
    return p.parse_args()


async def _fetch_bars(symbol: str, limit: int) -> list[dict]:
    """Fetch recent OHLCV bars from Binance."""
    try:
        from backend.services.binance_market_data import get_klines
        bars = await get_klines(symbol, interval="1h", limit=limit)
        return bars
    except ImportError:
        logger.warning("binance_market_data not available, generating synthetic bars")
        import random
        price = 100.0
        bars = []
        for i in range(limit):
            o = price
            h = price * (1 + random.uniform(0, 0.02))
            l_val = price * (1 - random.uniform(0, 0.02))
            c = price * (1 + random.uniform(-0.015, 0.015))
            bars.append({"open": o, "high": h, "low": l_val, "close": c, "volume": random.uniform(1000, 50000)})
            price = c
        return bars


async def _run_eval(args):
    from backend.services.persona_adapter import PersonaRegistry, run_all_personas

    registry = PersonaRegistry()

    if args.list_personas:
        print("\n=== Available Personas ===\n")
        for pid, cfg in registry.all().items():
            print(f"  {pid:<28} weight={cfg.weight:.2f}  style={cfg.style:<24} horizon={cfg.time_horizon}")
        print(f"\n  Total: {len(registry.all())} personas\n")
        return

    # Fetch bars
    print(f"\n📊 Fetching {args.bars} bars for {args.symbol}...")
    bars = await _fetch_bars(args.symbol, args.bars)
    if not bars:
        print("❌ No bar data available. Check symbol and network.")
        return

    print(f"   Got {len(bars)} bars. Last close: ${bars[-1]['close']:,.2f}\n")

    # Select personas
    selected = None
    if args.personas:
        selected = [p.strip() for p in args.personas.split(",")]
        print(f"🎭 Running selected personas: {', '.join(selected)}")
    else:
        selected = registry.active_ids()
        print(f"🎭 Running all {len(selected)} active personas")

    print(f"{'─' * 72}")
    print(f"  {'Persona':<28} {'Signal':<10} {'Confidence':<12} {'Reasoning'}")
    print(f"{'─' * 72}")

    results = await run_all_personas(args.symbol, bars, metrics={}, selected=selected)

    bullish = 0
    bearish = 0
    for r in results:
        sig_icon = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(r.signal, "⚪")
        reasoning_short = r.reasoning[:50] + "..." if len(r.reasoning) > 50 else r.reasoning
        print(f"  {r.persona:<28} {sig_icon} {r.signal:<8} {r.confidence:>8.1%}    {reasoning_short}")
        if r.signal == "bullish":
            bullish += 1
        elif r.signal == "bearish":
            bearish += 1

    print(f"{'─' * 72}")
    neutral = len(results) - bullish - bearish
    print(f"\n  Summary: 🟢 {bullish} bullish | 🔴 {bearish} bearish | ⚪ {neutral} neutral")
    print(f"  Avg confidence: {sum(r.confidence for r in results) / max(len(results), 1):.1%}")
    print()


def main():
    args = _parse_args()
    asyncio.run(_run_eval(args))


if __name__ == "__main__":
    main()
