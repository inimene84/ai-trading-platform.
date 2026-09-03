"""cTrader position and order status poller.

Runs as a background task, reconciling DB open trades against live cTrader
positions periodically (every 15–30 seconds) and on execution events.
Marks trades as 'closed' with accurate exit prices and realized P&L when they
hit Stop Loss (SL), Take Profit (TP), or are closed externally on the broker.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from backend.database.connection import SessionLocal
from backend.services.ctrader_service import ctrader_broker
from backend.services.ctrader_trade_sync import reconcile_ctrader_positions

logger = logging.getLogger(__name__)

_task: Optional[asyncio.Task] = None
_stop_event: Optional[asyncio.Event] = None


async def start_ctrader_poller() -> None:
    """Start the background cTrader position polling loop (idempotent)."""
    global _task, _stop_event
    if _task and not _task.done():
        return
    _stop_event = asyncio.Event()
    _task = asyncio.create_task(_poll_loop())
    interval = int(os.getenv("CTRADER_POLL_INTERVAL", "15"))
    logger.info(f"✓ cTrader position poller started (interval={interval}s)")


async def stop_ctrader_poller() -> None:
    """Stop the background cTrader position polling loop."""
    global _task, _stop_event
    if _stop_event:
        _stop_event.set()
    if _task:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):
            pass
        _task = None
    logger.info("cTrader position poller stopped")


async def sync_ctrader_once() -> dict:
    """Execute a single reconciliation pass against cTrader live book."""
    if not ctrader_broker.has_credentials():
        return {"skipped": True, "reason": "no_credentials"}

    db = SessionLocal()
    try:
        if not ctrader_broker.is_connected:
            loop = asyncio.get_running_loop()
            connected = await loop.run_in_executor(None, ctrader_broker.ensure_connected)
            if not connected:
                return {"skipped": True, "reason": "not_connected"}

        loop = asyncio.get_running_loop()
        live_positions = await loop.run_in_executor(None, ctrader_broker.get_positions)
        res = reconcile_ctrader_positions(db, live_positions=live_positions, broker=ctrader_broker)
        return res
    except Exception as exc:
        logger.warning(f"cTrader position sync cycle error: {exc}")
        return {"error": str(exc)}
    finally:
        db.close()


def _on_positions_changed_event(positions: list) -> None:
    """Callback invoked when cTrader receives execution or reconcile events."""
    try:
        db = SessionLocal()
        try:
            reconcile_ctrader_positions(db, live_positions=positions, broker=ctrader_broker)
        finally:
            db.close()
    except Exception as exc:
        logger.warning(f"cTrader event-triggered reconcile error: {exc}")


async def _poll_loop() -> None:
    """Main polling loop — runs continuously while backend is active."""
    # Register event-driven callback for instant SL/TP execution sync
    try:
        ctrader_broker.register_positions_callback(_on_positions_changed_event)
    except Exception as exc:
        logger.warning(f"Failed to register cTrader position change callback: {exc}")

    while True:
        try:
            await sync_ctrader_once()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning(f"cTrader poller loop exception: {exc}")

        interval = int(os.getenv("CTRADER_POLL_INTERVAL", "15"))
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break
