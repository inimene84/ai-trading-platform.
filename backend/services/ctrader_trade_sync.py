"""Mirror live cTrader positions into the SQL Trade table used by the dashboard."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from backend.database.connection import SessionLocal
from backend.database.models import Trade

logger = logging.getLogger(__name__)

_OPEN_STATUSES = ("open", "filled")


def persist_ctrader_execution(
    *,
    symbol: str,
    direction: str,
    quantity: float,
    entry_price: float,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    strategy: Optional[str] = None,
    order_id: Optional[str] = None,
    position_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> Optional[int]:
    """Insert an open Trade row for a successful cTrader dispatch."""
    db = SessionLocal()
    try:
        trade = Trade(
            symbol=str(symbol).upper(),
            direction=str(direction).upper(),
            quantity=float(quantity or 0),
            entry_price=float(entry_price or 0),
            stop_loss=stop_loss,
            take_profit=take_profit,
            status="open",
            strategy=strategy or "signal_candidate",
            broker="ctrader",
            exchange="ctrader",
            broker_order_id=str(order_id) if order_id else None,
            broker_position_id=str(position_id) if position_id else None,
            notes=notes or "cTrader live execution",
        )
        db.add(trade)
        db.commit()
        db.refresh(trade)
        return int(trade.id)
    except Exception as exc:
        logger.warning("Failed to persist cTrader execution to Trade table: %s", exc)
        db.rollback()
        return None
    finally:
        db.close()


def upsert_ctrader_live_trades(db, live_positions: List[Dict[str, Any]]) -> int:
    """Create missing open Trade rows for positions currently on the cTrader book.

    Does not close DB rows that are absent from the live book — a reconnect can
    briefly report an empty book.
    """
    if not live_positions:
        return 0

    open_rows = (
        db.query(Trade)
        .filter(Trade.broker == "ctrader", Trade.status.in_(_OPEN_STATUSES))
        .all()
    )
    by_pid = {
        str(row.broker_position_id): row
        for row in open_rows
        if row.broker_position_id
    }
    unmatched_by_symbol: Dict[str, List[Trade]] = {}
    for row in open_rows:
        if row.broker_position_id:
            continue
        unmatched_by_symbol.setdefault(str(row.symbol).upper(), []).append(row)

    created = 0
    for raw in live_positions:
        pid = str(raw.get("position_id") or "").strip()
        symbol = str(raw.get("symbol") or "").upper()
        side = str(raw.get("side") or raw.get("direction") or "BUY").upper()
        qty = float(raw.get("quantity") or 0)
        entry = float(raw.get("entry_price") or 0)
        if not symbol:
            continue

        if pid and pid in by_pid:
            row = by_pid[pid]
            row.quantity = qty or row.quantity
            if entry:
                row.entry_price = entry
            row.direction = side
            continue

        pending = unmatched_by_symbol.get(symbol) or []
        if pending:
            row = pending.pop(0)
            if pid:
                row.broker_position_id = pid
                by_pid[pid] = row
            row.quantity = qty or row.quantity
            if entry:
                row.entry_price = entry
            row.direction = side
            continue

        db.add(
            Trade(
                symbol=symbol,
                direction=side,
                quantity=qty,
                entry_price=entry or 0.0,
                status="open",
                strategy="ctrader_live",
                broker="ctrader",
                exchange="ctrader",
                broker_position_id=pid or None,
                notes="Synced from cTrader live book",
            )
        )
        created += 1

    db.commit()
    return created


def overlay_live_mark(
    payload: Dict[str, Any],
    live_by_pid: Dict[str, Dict[str, Any]],
    live_by_symbol: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Prefer live-book PnL/qty when the dashboard row is a cTrader trade."""
    if payload.get("broker") != "ctrader":
        return payload
    pid = str(payload.get("broker_position_id") or "")
    live = live_by_pid.get(pid) or live_by_symbol.get(str(payload.get("symbol") or "").upper())
    if not live:
        return payload
    entry = float(live.get("entry_price") or payload.get("entry_price") or 0)
    qty = float(live.get("quantity") or payload.get("quantity") or 0)
    pnl = float(live.get("unrealized_pnl") or payload.get("unrealized_pnl") or 0)
    payload["entry_price"] = entry
    payload["quantity"] = qty
    payload["unrealized_pnl"] = round(pnl, 2)
    # yfinance cannot resolve a bare FX pair, so the generic mark lookup falls
    # back to entry and the card reads a flat 0.00. Use the streamed spot.
    if live.get("current_price"):
        payload["current_price"] = float(live["current_price"])
    if entry and qty:
        notional = abs(entry * qty)
        payload["unrealized_pnl_pct"] = round((pnl / notional) * 100, 2) if notional else 0.0
    return payload
