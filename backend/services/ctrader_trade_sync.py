"""Mirror and reconcile live cTrader positions into the SQL Trade table used by the dashboard."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.database.connection import SessionLocal
from backend.database.models import Trade

logger = logging.getLogger(__name__)

_OPEN_STATUSES = ("open", "filled")

_CTRADER_EXTRA_SYMBOLS = {
    "XAUUSD", "XAGUSD", "GOLD", "SILVER", "OIL", "WTI", "BRENT",
    "XTIUSD", "XBRUSD", "US30", "NAS100", "US500", "GER40", "UK100",
    "JP225", "SPX500", "WS30", "NDX", "DAX", "BTCUSD", "ETHUSD", "SOLUSD",
}


def is_ctrader_trade(trade: Any) -> bool:
    """True for trades originating from or belonging to cTrader."""
    broker = (getattr(trade, "broker", None) or getattr(trade, "exchange", None) or "").lower()
    if "ctrader" in broker:
        return True
    if getattr(trade, "broker_position_id", None):
        return True
    sym = str(getattr(trade, "symbol", "") or "").upper().strip()
    clean_sym = sym.split(".")[0].split("_")[0].replace("/", "").replace("-", "")
    if clean_sym in _CTRADER_EXTRA_SYMBOLS or sym in _CTRADER_EXTRA_SYMBOLS:
        return True
    return len(clean_sym) == 6 and clean_sym.isalpha() and not clean_sym.endswith("USDT")


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


def reconcile_ctrader_positions(
    db: Any,
    live_positions: Optional[List[Dict[str, Any]]] = None,
    broker: Optional[Any] = None,
) -> Dict[str, int]:
    """Reconcile DB open trades against live cTrader positions.

    - Creates missing DB rows for positions active on cTrader.
    - Updates entry prices / quantities for existing open rows.
    - Marks DB rows as 'closed' with accurate exit prices and P&L when they are
      absent from the live book (e.g. hit SL/TP or were closed).
    """
    if broker is None:
        try:
            from backend.services.ctrader_service import ctrader_broker
            broker = ctrader_broker
        except Exception:
            broker = None

    if live_positions is None:
        if broker is not None:
            try:
                live_positions = broker.get_positions()
            except Exception as exc:
                logger.warning("Failed to fetch cTrader live positions for reconcile: %s", exc)
                return {"created": 0, "updated": 0, "closed": 0}
        else:
            return {"created": 0, "updated": 0, "closed": 0}

    live_positions_list = list(live_positions or [])

    open_rows = (
        db.query(Trade)
        .filter(Trade.status.in_(_OPEN_STATUSES))
        .all()
    )
    ctrader_open_rows = [row for row in open_rows if is_ctrader_trade(row)]

    by_pid = {
        str(row.broker_position_id): row
        for row in ctrader_open_rows
        if row.broker_position_id
    }
    unmatched_by_symbol: Dict[str, List[Trade]] = {}
    for row in ctrader_open_rows:
        if row.broker_position_id:
            continue
        unmatched_by_symbol.setdefault(str(row.symbol).upper(), []).append(row)

    matched_db_trade_ids = set()
    created = 0
    updated = 0

    for raw in live_positions_list:
        pid = str(raw.get("position_id") or "").strip()
        symbol = str(raw.get("symbol") or "").upper()
        side = str(raw.get("side") or raw.get("direction") or "BUY").upper()
        qty = float(raw.get("quantity") or 0)
        entry = float(raw.get("entry_price") or 0)
        if not symbol:
            continue

        if pid and pid in by_pid:
            row = by_pid[pid]
            matched_db_trade_ids.add(row.id)
            row.quantity = qty or row.quantity
            if entry > 0:
                row.entry_price = entry
            row.direction = side
            updated += 1
            continue

        pending = unmatched_by_symbol.get(symbol) or []
        if pending:
            row = pending.pop(0)
            matched_db_trade_ids.add(row.id)
            if pid:
                row.broker_position_id = pid
                by_pid[pid] = row
            row.quantity = qty or row.quantity
            if entry > 0:
                row.entry_price = entry
            row.direction = side
            updated += 1
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

    # Reconcile absent trades (e.g. hit SL/TP or closed on cTrader)
    closed = 0
    now = datetime.now(timezone.utc)
    for row in ctrader_open_rows:
        if row.id in matched_db_trade_ids:
            continue

        # In-flight grace period: don't close newly created DB rows (<15s old) with no PID yet
        if not row.broker_position_id and getattr(row, "timestamp", None):
            ts = row.timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if (now - ts).total_seconds() < 15.0:
                continue

        row.status = "closed"
        row.closed_at = now

        # Best effort exit price and P&L resolution
        exit_px = None
        pnl = None
        recent_deal = None
        if broker is not None:
            try:
                recent_deal = broker.get_recent_deal(
                    position_id=row.broker_position_id,
                    symbol=row.symbol,
                )
            except Exception:
                recent_deal = None

            if recent_deal:
                if recent_deal.get("execution_price"):
                    exit_px = float(recent_deal["execution_price"])
                if recent_deal.get("gross_profit") is not None:
                    pnl = float(recent_deal["gross_profit"])

            if exit_px is None:
                try:
                    exit_px = broker.get_exit_price(
                        row.symbol, row.direction, row.broker_position_id
                    )
                except Exception:
                    exit_px = None

        if pnl is None and exit_px and row.entry_price and row.quantity and broker is not None:
            try:
                lots = float(row.quantity or 0)
                units_fn = getattr(broker, "units_per_lot", None)
                units = lots * (
                    float(units_fn(row.symbol))
                    if callable(units_fn)
                    else float(getattr(broker, "CONTRACT_UNITS_PER_LOT", 100_000))
                )
                direction_mult = 1 if str(row.direction or "BUY").upper() in ("BUY", "LONG") else -1
                quote_pnl = (exit_px - float(row.entry_price)) * units * direction_mult
                rate = broker.quote_to_usd_rate(row.symbol, exit_px)
                pnl = round(quote_pnl * rate, 2) if rate else round(quote_pnl, 2)
            except Exception:
                pnl = None

        row.exit_price = exit_px
        row.pnl = pnl
        close_reason = (recent_deal.get("close_type") if recent_deal else None) or "SL/TP hit / closed on cTrader"
        current_notes = getattr(row, "notes", "") or ""
        try:
            row.notes = current_notes + f" | Closed externally ({close_reason})"
        except AttributeError:
            pass
        closed += 1
        logger.info(
            "cTrader ghost trade reconciled: id=%s symbol=%s pos_id=%s exit=%s pnl=%s reason=%s",
            row.id, row.symbol, row.broker_position_id, exit_px, pnl, close_reason,
        )

    db.commit()
    return {"created": created, "updated": updated, "closed": closed}


def upsert_ctrader_live_trades(db, live_positions: List[Dict[str, Any]]) -> int:
    """Create missing open Trade rows and reconcile closed cTrader positions.

    Maintains backwards compatibility by returning the count of newly created rows.
    """
    res = reconcile_ctrader_positions(db, live_positions)
    return res.get("created", 0)


def overlay_live_mark(
    payload: Dict[str, Any],
    live_by_pid: Dict[str, Dict[str, Any]],
    live_by_symbol: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Prefer live-book PnL/qty when the dashboard row is a cTrader trade."""
    broker = (payload.get("broker") or "").lower()
    sym = str(payload.get("symbol") or "").upper()
    is_ctrader = (
        broker == "ctrader"
        or payload.get("broker_position_id")
        or (len(sym) == 6 and sym.isalpha() and not sym.endswith("USDT"))
        or sym in ("XAUUSD", "XAGUSD")
    )
    if not is_ctrader:
        return payload
    if broker != "ctrader":
        payload["broker"] = "ctrader"

    pid = str(payload.get("broker_position_id") or "")
    live = live_by_pid.get(pid) or live_by_symbol.get(sym)
    if not live:
        entry = float(payload.get("entry_price") or 0)
        if not payload.get("current_price") and entry > 0:
            payload["current_price"] = entry
        return payload

    entry = float(live.get("entry_price") or payload.get("entry_price") or 0)
    qty = float(live.get("quantity") or payload.get("quantity") or 0)
    pnl = float(live.get("unrealized_pnl") or payload.get("unrealized_pnl") or 0)
    payload["entry_price"] = entry
    payload["quantity"] = qty
    payload["unrealized_pnl"] = round(pnl, 2)
    live_mark = live.get("current_price")
    payload["current_price"] = _positive_price(live_mark, payload.get("current_price"), entry)
    # Show the protection the broker actually holds, not what was requested.
    for level in ("stop_loss", "take_profit"):
        if live.get(level) is not None:
            payload[level] = float(live[level])
    payload["unrealized_pnl_pct"] = position_pnl_pct(
        entry=entry,
        mark=float(payload.get("current_price") or entry),
        direction=str(payload.get("direction") or "BUY"),
        is_ctrader=True,
    )
    return payload


def _positive_price(*candidates: Any) -> float:
    for raw in candidates:
        if raw is None:
            continue
        val = float(raw)
        if val > 0:
            return val
    return 0.0


def position_pnl_pct(
    *,
    entry: float,
    mark: float,
    direction: str,
    unrealized_pnl: float = 0.0,
    quantity: float = 0.0,
    is_ctrader: bool = False,
) -> float:
    """Return position return % for the dashboard.

    cTrader FX quantity is lots (0.1 = 10k units). Dividing dollar P&L by
    ``entry * lots`` (~0.12 on EURUSD) produced -1980% on a -$2 move. FX rows
    use mark vs entry price change instead, matching crypto semantics.
    """
    entry_f = float(entry or 0)
    mark_f = float(mark or 0)
    if entry_f <= 0:
        return 0.0
    if is_ctrader:
        if mark_f <= 0:
            return 0.0
        if str(direction or "BUY").upper() in ("BUY", "LONG"):
            return round((mark_f - entry_f) / entry_f * 100, 2)
        return round((entry_f - mark_f) / entry_f * 100, 2)
    qty = float(quantity or 0)
    if qty <= 0:
        return 0.0
    return round(float(unrealized_pnl or 0) / (entry_f * qty) * 100, 2)
