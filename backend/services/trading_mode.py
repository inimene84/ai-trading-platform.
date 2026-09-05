import os
from enum import Enum

_DEFAULT_PAPER_BALANCE = 100_000.0


class TradingMode(str, Enum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


def get_trading_mode() -> TradingMode:
    """Resolve trading mode (backtest | paper | live) based on TRADING_MODE env var.
    Falls back to PAPER_TRADING env configuration for backwards compatibility.
    """
    raw = os.getenv("TRADING_MODE", "").lower()
    if raw in {m.value for m in TradingMode}:
        return TradingMode(raw)

    # Check legacy PAPER_TRADING flag if TRADING_MODE is unset
    paper_trading = os.getenv("PAPER_TRADING", "true").lower() == "true"
    dry_run = os.getenv("DRY_RUN_ALL", "true").lower() == "true"

    return TradingMode.PAPER if (paper_trading or dry_run) else TradingMode.LIVE


def paper_leverage_for_broker(broker: str) -> float:
    """Leverage used by the paper book for a broker session.

    Binance paper simulates USDT-M futures, so it follows BINANCE_LEVERAGE
    (default 10x). A 1x paper book rejects risk-based sizes that a tight
    stop produces (1% equity risk / ~0.9% stop ≈ 1.07x notional).
    """
    name = (broker or "").strip().lower()
    if name in {"binance_futures", "binance"}:
        raw = os.getenv("BINANCE_LEVERAGE", "10")
    elif name == "ctrader":
        raw = os.getenv("CTRADER_PAPER_LEVERAGE", "1")
    else:
        raw = os.getenv("PAPER_LEVERAGE", "1")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 1.0
    return value if value > 0 else 1.0


def paper_starting_balance() -> float:
    """Simulated cash for local paper mode (not a Binance wallet).

    PAPER_BALANCE env, else $100_000. Used when no in-memory / last paper
    book is available, and as the reset floor after a wiped paper book.
    """
    raw = os.getenv("PAPER_BALANCE", str(_DEFAULT_PAPER_BALANCE))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_PAPER_BALANCE
    return value if value > 0 else _DEFAULT_PAPER_BALANCE


def live_exchange_orders_allowed() -> bool:
    """Whether any exchange adapter may send live orders.

    Single source of truth: only TRADING_MODE=live (or the legacy
    PAPER_TRADING/DRY_RUN_ALL fallback resolving to live) allows them.
    Unset TRADING_MODE with default PAPER_TRADING=true refuses them.
    """
    return get_trading_mode() == TradingMode.LIVE


def live_binance_orders_allowed() -> bool:
    """Whether BinanceFuturesService may hit the live/testnet private API."""
    return live_exchange_orders_allowed()


def live_ctrader_orders_allowed() -> bool:
    """Whether CTraderService may send ProtoOANewOrder / close / amend."""
    return live_exchange_orders_allowed()
