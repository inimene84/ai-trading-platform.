"""
Emergency halt: set sentry flag, cancel open Binance orders, notify operators.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import structlog

from backend.services.sentry_state import halt_trading, read_state
from backend.utils.telegram import send_telegram_message

logger = structlog.get_logger(__name__)


def _cancel_all_open_orders_sync() -> dict[str, Any]:
    """Cancel every open order on Binance Futures (regular + algo)."""
    broker_name = os.getenv("ACTIVE_BROKER", "ctrader")
    if broker_name != "binance_futures":
        return {"skipped": True, "reason": f"broker={broker_name}"}

    paper = os.getenv("PAPER_TRADING", "false").lower() == "true"
    dry_run = os.getenv("BINANCE_DRY_RUN", "false").lower() == "true"
    if paper or dry_run:
        return {"skipped": True, "reason": "paper_or_dry_run"}

    from backend.services.binance_futures_service import binance_futures_broker

    symbols: set[str] = set()
    try:
        orders = binance_futures_broker.get_open_orders(raise_on_error=True)
        for order in orders:
            sym = order.get("symbol")
            if sym:
                symbols.add(sym)
    except Exception as exc:
        logger.error("Emergency cancel: failed to list open orders", error=str(exc))
        return {"error": str(exc), "symbols_cancelled": 0}

    cancelled = 0
    errors: list[str] = []
    for sym in sorted(symbols):
        try:
            binance_futures_broker.cancel_all_orders(sym)
            cancelled += 1
        except Exception as exc:
            errors.append(f"{sym}: {exc}")
            logger.warning("Emergency cancel failed for symbol", symbol=sym, error=str(exc))

    return {
        "symbols_seen": len(symbols),
        "symbols_cancelled": cancelled,
        "errors": errors,
    }


async def emergency_halt(*, reason: str, source: str, manual: bool = False) -> dict[str, Any]:
    """
    Halt trading immediately: persist flag, cancel orders, alert Telegram.
    Safe to call multiple times (idempotent flag write).
    """
    state = halt_trading(reason=reason, halted_by=source, manual=manual)
    cancel_result: dict[str, Any] = {}
    try:
        cancel_result = await asyncio.get_event_loop().run_in_executor(
            None, _cancel_all_open_orders_sync
        )
    except Exception as exc:
        logger.error("Emergency cancel executor failed", error=str(exc))
        cancel_result = {"error": str(exc)}

    msg_lines = [
        "🛑 <b>SENTRY HALT</b>",
        f"Source: {source}",
        f"Reason: {reason}",
        f"Status: {state.get('status')}",
    ]
    if cancel_result.get("skipped"):
        msg_lines.append(f"Orders: skipped ({cancel_result.get('reason')})")
    elif cancel_result.get("error"):
        msg_lines.append(f"Orders: ERROR — {cancel_result['error']}")
    else:
        msg_lines.append(
            f"Orders cancelled on {cancel_result.get('symbols_cancelled', 0)} symbol(s)"
        )

    try:
        await send_telegram_message("\n".join(msg_lines), parse_mode="HTML")
    except Exception as exc:
        logger.warning("Telegram alert failed during emergency halt", error=str(exc))

    return {
        "state": read_state(),
        "cancel": cancel_result,
    }
