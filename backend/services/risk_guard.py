import logging
from datetime import datetime, timezone
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.services.risk_config import RiskConfig
from backend.database.models import Trade, PortfolioSnapshot

logger = logging.getLogger(__name__)

class RiskBreach(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason

def enforce_risk_limits(
    db: Session,
    cfg: RiskConfig,
    open_trades: list[Trade],
    latest_snapshot: PortfolioSnapshot | None,
):
    """Enforces active trading safety guardrails.
    Raises RiskBreach if any boundaries (drawdown, daily loss, max open positions) are violated.
    """
    import os
    if os.getenv("DISABLE_RISK_GUARD", "false").lower() == "true":
        logger.warning("Risk guard is disabled via DISABLE_RISK_GUARD environment variable.")
        return

    # 1. Max open positions
    if len(open_trades) > cfg.max_open_positions:
        raise RiskBreach(f"Max open positions exceeded: {len(open_trades)} > {cfg.max_open_positions}")

    if latest_snapshot:
        current_value = latest_snapshot.total_value
        
        # 2. Max portfolio drawdown check (calculated relative to peak total_value)
        peak_value = db.query(func.max(PortfolioSnapshot.total_value)).scalar() or 10000.0
        if current_value < peak_value:
            drawdown_pct = ((peak_value - current_value) / peak_value) * 100
            if drawdown_pct > cfg.max_portfolio_drawdown_pct:
                raise RiskBreach(
                    f"Max portfolio drawdown exceeded: {drawdown_pct:.2f}% > {cfg.max_portfolio_drawdown_pct}% "
                    f"(Peak: ${peak_value:.2f}, Current: ${current_value:.2f})"
                )

        # 3. Max daily loss check (calculated relative to the first snapshot of today in UTC)
        start_of_today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        first_snapshot_today = (
            db.query(PortfolioSnapshot)
            .filter(PortfolioSnapshot.timestamp >= start_of_today)
            .order_by(PortfolioSnapshot.timestamp.asc())
            .first()
        )
        if first_snapshot_today:
            start_value = first_snapshot_today.total_value
            if current_value < start_value:
                daily_loss_pct = ((start_value - current_value) / start_value) * 100
                if daily_loss_pct > cfg.max_daily_loss_pct:
                    raise RiskBreach(
                        f"Max daily loss exceeded: {daily_loss_pct:.2f}% > {cfg.max_daily_loss_pct}% "
                        f"(Daily Start: ${start_value:.2f}, Current: ${current_value:.2f})"
                    )
