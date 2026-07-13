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


def _close_all_positions_sync() -> dict[str, Any]:
    """Flatten all open positions on Binance Futures and update open DB trades."""
    from backend.database.connection import SessionLocal
    from backend.database.models import Trade
    from backend.services.binance_futures_service import binance_futures_broker
    from datetime import datetime, timezone

    broker_name = os.getenv("ACTIVE_BROKER", "ctrader")
    if broker_name != "binance_futures":
        return {"skipped": True, "reason": f"broker={broker_name}"}

    paper = os.getenv("PAPER_TRADING", "false").lower() == "true"
    dry_run = os.getenv("BINANCE_DRY_RUN", "false").lower() == "true"
    if paper or dry_run:
        return {"skipped": True, "reason": "paper_or_dry_run"}

    db = SessionLocal()
    closed_trades = 0
    closed_orphans = 0
    errors = []
    
    try:
        # 1. Close open DB trades
        open_trades = db.query(Trade).filter(Trade.status.in_(["open", "filled"])).all()
        for trade in open_trades:
            try:
                res = binance_futures_broker.place_order(
                    symbol=trade.symbol,
                    direction=trade.direction,
                    action='close',
                    quantity=trade.quantity,
                    comment='Sentry emergency close'
                )
                exit_price = res.get('price') or res.get('filled_price')
                if not exit_price:
                    try:
                        client = binance_futures_broker._get_client()
                        ticker = client.futures_symbol_ticker(symbol=binance_futures_broker._to_futures_symbol(trade.symbol))
                        exit_price = float(ticker['price'])
                    except Exception:
                        exit_price = trade.entry_price
                
                if trade.direction == "BUY":
                    pnl = (exit_price - trade.entry_price) * trade.quantity
                else:
                    pnl = (trade.entry_price - exit_price) * trade.quantity
                
                trade.status = "closed"
                trade.exit_price = exit_price
                trade.pnl = round(pnl, 2)
                trade.closed_at = datetime.now(timezone.utc)
                trade.notes = (trade.notes or "") + " | Closed by Sentry Emergency Halt"
                db.add(trade)
                closed_trades += 1
            except Exception as e:
                errors.append(f"DB trade {trade.id} ({trade.symbol}): {e}")

        db.commit()

        # 2. Close any remaining broker positions (orphan cleanup)
        try:
            broker_positions = binance_futures_broker.get_positions()
            for pos in broker_positions:
                symbol = pos['symbol']
                qty = pos['quantity']
                side = pos['side']
                if qty > 0:
                    try:
                        binance_futures_broker.place_order(
                            symbol=symbol,
                            direction=side,
                            action='close',
                            quantity=qty,
                            comment='Sentry emergency close orphan'
                        )
                        closed_orphans += 1
                    except Exception as e:
                        errors.append(f"Orphan {symbol}: {e}")
        except Exception as e:
            errors.append(f"Broker position check: {e}")
            
    finally:
        db.close()
        
    return {
        "closed_trades": closed_trades,
        "closed_orphans": closed_orphans,
        "errors": errors
    }


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
            binance_futures_broker.cancel_non_protective_orders(sym)
            cancelled += 1
        except Exception as exc:
            errors.append(f"{sym}: {exc}")
            logger.warning("Emergency cancel failed for symbol", symbol=sym, error=str(exc))

    return {
        "symbols_seen": len(symbols),
        "symbols_cancelled": cancelled,
        "errors": errors,
        "mode": "non_protective_only",
    }


async def emergency_halt(*, reason: str, source: str, manual: bool = False) -> dict[str, Any]:
    """
    Halt trading immediately: persist flag, cancel orders, alert Telegram.
    Safe to call multiple times (idempotent flag write).
    """
    state = halt_trading(reason=reason, halted_by=source, manual=manual)
    cancel_result: dict[str, Any] = {}
    close_result: dict[str, Any] = {}
    try:
        close_result = await asyncio.get_event_loop().run_in_executor(
            None, _close_all_positions_sync
        )
        cancel_result = await asyncio.get_event_loop().run_in_executor(
            None, _cancel_all_open_orders_sync
        )
    except Exception as exc:
        logger.error("Emergency halt executor failed", error=str(exc))
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
    if close_result:
        msg_lines.append(
            f"Positions closed: DB={close_result.get('closed_trades', 0)}, "
            f"Orphans={close_result.get('closed_orphans', 0)}"
        )
        if close_result.get("errors"):
            msg_lines.append(f"Close Errors: {', '.join(close_result['errors'])[:200]}")

    try:
        await send_telegram_message("\n".join(msg_lines), parse_mode="HTML")
    except Exception as exc:
        logger.warning("Telegram alert failed during emergency halt", error=str(exc))

    return {
        "state": read_state(),
        "cancel": cancel_result,
    }
