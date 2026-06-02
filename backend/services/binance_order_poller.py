"""
Binance Futures order status poller.
Runs as a background task, syncs open DB trades against Binance order status every 30 seconds.
Updates trade.status and trade.pnl when orders are FILLED, CANCELED, or EXPIRED on Binance.
Independent of the trading loop - always active when backend starts (only for binance_futures broker).
"""
import asyncio
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 30  # seconds
_task = None


async def start_order_poller():
    """Start the background order polling loop (idempotent)."""
    global _task
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_poll_loop())
    logger.info("Binance order poller started (interval=30s)")


async def _poll_loop():
    """Main polling loop - runs until process exits."""
    try:
        from backend.services.binance_futures_service import BinanceFuturesService
        svc = BinanceFuturesService()
        logger.info("Order poller: BinanceFuturesService initialized")
    except Exception as e:
        logger.error(f"Order poller: failed to init BinanceFuturesService: {e}")
        return

    while True:
        try:
            # Only run when Binance Futures is the active broker
            broker_name = os.getenv("ACTIVE_BROKER", "ctrader")
            if broker_name == "binance_futures":
                await _sync_open_orders(svc)
        except Exception as e:
            logger.warning(f"Order poller cycle error: {e}")
        await asyncio.sleep(_POLL_INTERVAL)


async def _sync_open_orders(svc):
    """Compare DB open trades against live Binance order status and sync."""
    from backend.database.connection import SessionLocal
    from backend.database.models import Trade
    from backend.services.influxdb_writer import influx

    db = SessionLocal()
    try:
        # 1. Query DB for open trades that have a real binance_order_id (not paper trades)
        open_trades = (
            db.query(Trade)
            .filter(
                Trade.status == "open",
                Trade.binance_order_id.isnot(None),
                Trade.binance_order_id != "",
                ~Trade.binance_order_id.like("paper_%"),  # Skip paper orders
            )
            .all()
        )

        if not open_trades:
            return

        # 2. Fetch currently open orders from Binance (set of order_ids still open)
        try:
            live_open = svc.get_open_orders()
            live_open_ids = {o["order_id"] for o in live_open}
        except Exception as e:
            logger.warning(f"Order poller: get_open_orders failed: {e}")
            return

        # 3. For each DB trade, check if its order is still open on Binance
        updated = 0
        for trade in open_trades:
            if trade.binance_order_id in live_open_ids:
                # Still open on Binance - no action needed
                continue

            # Order is no longer open → fetch precise status from Binance
            try:
                order_status = await _get_order_status(svc, trade)
                if order_status is None:
                    continue

                binance_status = order_status.get("status", "UNKNOWN")
                logger.info(
                    f"Order poller: trade.id={trade.id} {trade.symbol} "
                    f"order={trade.binance_order_id} status={binance_status}"
                )

                if binance_status == "FILLED":
                    filled_price = float(order_status.get("avgPrice") or order_status.get("price") or trade.entry_price or 0)
                    filled_qty = float(order_status.get("executedQty") or trade.quantity or 0)

                    # Update fill details if more precise than what we stored
                    if filled_price and filled_price != trade.entry_price:
                        trade.filled_price = filled_price
                        trade.entry_price = filled_price  # update to actual fill price

                    if filled_qty and filled_qty != trade.quantity:
                        trade.quantity = filled_qty

                    # Mark as 'filled' so the poller stops re-querying this order.
                    # Position is now live; exit will close it later.
                    trade.status = "filled"
                    logger.info(
                        f"  -> Order FILLED: trade.id={trade.id} fill_price={filled_price} qty={filled_qty}"
                    )

                elif binance_status in ("CANCELED", "EXPIRED", "REJECTED"):
                    trade.status = "failed"
                    trade.closed_at = datetime.now(timezone.utc)
                    trade.notes = (trade.notes or "") + f" | Order {binance_status} on Binance"
                    logger.warning(
                        f"  -> Order {binance_status}: trade.id={trade.id} marked as failed"
                    )
                    # Write failed status to InfluxDB
                    await influx.write_trade(
                        symbol=trade.symbol,
                        direction=trade.direction,
                        quantity=trade.quantity,
                        entry_price=trade.entry_price or 0.0,
                        status="failed",
                        strategy=trade.strategy or "",
                        pnl=0.0,
                    )

                elif binance_status == "PARTIALLY_FILLED":
                    filled_qty = float(order_status.get("executedQty") or 0)
                    filled_price = float(order_status.get("avgPrice") or trade.entry_price or 0)
                    if filled_price:
                        trade.filled_price = filled_price
                    logger.info(
                        f"  -> Order PARTIALLY_FILLED: trade.id={trade.id} "
                        f"executed={filled_qty}/{trade.quantity}"
                    )

                updated += 1

            except Exception as e:
                logger.warning(f"Order poller: error processing trade.id={trade.id}: {e}")
                continue

        if updated:
            db.commit()
            logger.info(f"Order poller: synced {updated} trade(s)")

    finally:
        db.close()


async def _get_order_status(svc, trade) -> dict | None:
    """Fetch a specific order's status from Binance Futures."""
    try:
        client = await asyncio.get_event_loop().run_in_executor(
            None, svc._get_client
        )
        # Convert symbol to futures format
        futures_sym = svc._to_futures_symbol(trade.symbol)
        if not futures_sym:
            futures_sym = trade.symbol  # try raw symbol as fallback

        order = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: client.futures_get_order(
                symbol=futures_sym,
                orderId=int(trade.binance_order_id)
            )
        )
        return order
    except Exception as e:
        logger.warning(
            f"_get_order_status: trade.id={trade.id} order={trade.binance_order_id}: {e}"
        )
        return None
