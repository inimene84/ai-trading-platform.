"""Paper vs live ledger helpers.

SQL Trade.mode defaults to 'paper', so live cTrader/Binance fills that never
stamped mode look like paper. Venue IDs and paper_ order prefixes are the
reliable split. Risk, portfolio, and broker sync must never treat a paper
crypto row as live Binance size.
"""

from __future__ import annotations

from typing import Any


ORPHAN_STATUS = "orphaned"
PAPER_ORDER_PREFIX = "paper_"


def _s(value: Any) -> str:
    return str(value or "").strip()


def is_ctrader_owned(trade: Any) -> bool:
    broker = _s(getattr(trade, "broker", None) or getattr(trade, "exchange", None)).lower()
    return "ctrader" in broker or broker in {"ic", "icmarkets"}


def is_binance_paper_fill(trade: Any) -> bool:
    """True when the row is a local paper crypto fill, not an exchange position."""
    if is_ctrader_owned(trade):
        return False
    oid = _s(
        getattr(trade, "binance_order_id", None) or getattr(trade, "broker_order_id", None)
    )
    if oid.startswith(PAPER_ORDER_PREFIX):
        return True
    mode = _s(getattr(trade, "mode", None)).lower()
    if mode == "live":
        return False
    if mode == "paper" and not has_live_venue_id(trade):
        return True
    return False


def has_live_venue_id(trade: Any) -> bool:
    pid = _s(getattr(trade, "broker_position_id", None))
    if pid:
        return True
    oid = _s(
        getattr(trade, "binance_order_id", None) or getattr(trade, "broker_order_id", None)
    )
    return bool(oid) and not oid.startswith(PAPER_ORDER_PREFIX)


def binance_position_key(symbol: str, direction: str) -> str:
    side = "LONG" if str(direction or "").upper() in {"BUY", "LONG"} else "SHORT"
    return f"{str(symbol).upper()}:{side}"


def is_binance_position_key(value: Any) -> bool:
    """True for hedge-mode keys like BTCUSDT:LONG written by live Binance fills."""
    raw = _s(value)
    if ":" not in raw:
        return False
    side = raw.rsplit(":", 1)[-1]
    return side in {"LONG", "SHORT"}


def fill_mode_from_order(order_mode: str | None, trading_mode_value: str) -> str:
    raw = _s(order_mode).lower() or _s(trading_mode_value).lower()
    if raw in {"live", "paper", "backtest"}:
        return raw
    return "paper"
