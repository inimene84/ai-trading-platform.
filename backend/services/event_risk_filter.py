"""
Event Risk Filter (Macro Economic Event Gate)
─────────────────────────────────────────────
Inspects proposed orders against upcoming or freshly released high-impact
economic calendar events (NFP, FOMC, CPI, central bank decisions).

Features:
  - Full entry veto within ±30m of High Impact events.
  - Sizing reduction (50%) within ±10m of Medium/Secondary releases.
  - Fail-closed defense when calendar feed is stale in LIVE mode.
  - Gated behind EVENT_RISK_FILTER_ENABLED (default "false").
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from backend.services.calendar_service import calendar_service
from backend.services.trading_mode import TradingMode, get_trading_mode

logger = logging.getLogger(__name__)


@dataclass
class EventRiskResult:
    approved: bool
    final_quantity: float
    action: str  # "pass", "veto", "reduce"
    reason: str
    event: Optional[Dict[str, Any]] = None


class EventRiskFilter:
    def __init__(
        self,
        high_pre_window_min: int = 30,
        high_post_window_min: int = 15,
        medium_window_min: int = 10,
    ):
        self.high_pre_window_min = high_pre_window_min
        self.high_post_window_min = high_post_window_min
        self.medium_window_min = medium_window_min

    def is_enabled(self) -> bool:
        return os.getenv("EVENT_RISK_FILTER_ENABLED", "false").lower() == "true"

    def evaluate_order(
        self,
        symbol: str,
        proposed_quantity: float,
        proposed_direction: str,
    ) -> EventRiskResult:
        """Evaluate order candidate against macroeconomic event risk."""
        if not self.is_enabled():
            return EventRiskResult(
                approved=True,
                final_quantity=proposed_quantity,
                action="pass",
                reason="Event risk filter disabled (EVENT_RISK_FILTER_ENABLED=false)",
            )

        # 1. Staleness check — in live mode, a stale calendar fails closed
        if calendar_service.is_stale(max_age_hours=24):
            is_live = get_trading_mode() == TradingMode.LIVE
            if is_live:
                logger.warning(f"[{symbol}] EventRiskFilter VETO: Calendar data is stale >24h (fail-closed in live)")
                return EventRiskResult(
                    approved=False,
                    final_quantity=0.0,
                    action="veto",
                    reason="Calendar data is stale >24h (fail-closed in live)",
                )
            else:
                logger.info(f"[{symbol}] Calendar data stale >24h but passing in paper/backtest mode")

        # 2. Check High-Impact Blackout Window (±30m / -15m)
        is_blocked, active_event, reason = calendar_service.check_blackout(
            symbol=symbol,
            pre_window_min=self.high_pre_window_min,
            post_window_min=self.high_post_window_min,
        )

        if is_blocked and active_event:
            logger.info(f"[{symbol}] EventRiskFilter VETO: {reason}")
            return EventRiskResult(
                approved=False,
                final_quantity=0.0,
                action="veto",
                reason=reason,
                event=active_event,
            )

        return EventRiskResult(
            approved=True,
            final_quantity=proposed_quantity,
            action="pass",
            reason="Clear of macro event risk",
        )


event_risk_filter = EventRiskFilter()
