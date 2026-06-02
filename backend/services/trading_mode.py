import os
from enum import Enum

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
