import logging
import os
from datetime import datetime, timezone, timedelta
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.services.risk_config import RiskConfig, get_trading_mode
from backend.database.models import Trade, PortfolioSnapshot

logger = logging.getLogger(__name__)

# Drawdown peak is computed over a rolling window, NOT all-time. An all-time
# peak ratchets up permanently after any deposit and never resets, so the
# drawdown % ends up measured against capital that may since have been
# withdrawn — the guard then either never trips or trips spuriously. A rolling
# window (default 30 days) keeps "peak" anchored to recent equity reality.
PEAK_LOOKBACK_HOURS = float(os.getenv("RISK_PEAK_LOOKBACK_HOURS", "720"))


class RiskBreach(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _directional_exposure_usdt(open_trades: list[Trade]) -> float:
    """Sum the absolute notional (entry_price * quantity) of all open trades.

    Falls back gracefully if a Trade row is missing price/qty data — an
    incomplete row must never make exposure look smaller than it is, so a
    missing field contributes 0 only when genuinely unknown and we log it.
    """
    total = 0.0
    for t in open_trades:
        price = getattr(t, "entry_price", None) or getattr(t, "price", None)
        qty = getattr(t, "quantity", None)
        if price is None or qty is None:
            logger.warning(
                "[RISK GUARD] Open trade %s missing price/qty; excluded from exposure sum",
                getattr(t, "symbol", "?"),
            )
            continue
        try:
            total += abs(float(price) * float(qty))
        except (TypeError, ValueError):
            continue
    return total


def enforce_risk_limits(
    db: Session,
    cfg: RiskConfig,
    open_trades: list[Trade],
    latest_snapshot: PortfolioSnapshot | None,
):
    """Enforces active trading safety guardrails.

    Raises RiskBreach if any boundary (drawdown, daily loss, max open
    positions, directional exposure) is violated. Designed to FAIL CLOSED:
    the caller must treat *any* exception escaping this function as a reason
    to skip the cycle, never to trade unguarded.
    """
    # The DISABLE_RISK_GUARD escape hatch is a foot-gun on a live account: one
    # stray env var would silently strip every protection from real money.
    # It is only honored in PAPER mode now — in LIVE mode it is ignored and a
    # loud error is logged so a misconfiguration cannot disable live guards.
    if os.getenv("DISABLE_RISK_GUARD", "false").lower() == "true":
        if get_trading_mode() == "paper":
            logger.warning(
                "Risk guard disabled via DISABLE_RISK_GUARD (allowed: PAPER mode)."
            )
            return
        logger.error(
            "DISABLE_RISK_GUARD=true IGNORED in LIVE mode — risk guard stays ACTIVE."
        )

    # 1. Max open positions. Use max_positions (the single source of truth for
    #    the concurrent-position cap); max_open_positions is kept only as a
    #    looser legacy ceiling, so enforce the stricter of the two.
    #
    #    Count DISTINCT SYMBOLS, not raw trade rows. With pyramid DCA each entry
    #    layer is its own Trade row (e.g. 4 AVAX layers = 1 position), so
    #    len(open_trades) over-counts and would false-trigger this breach. This
    #    matches how the trading loop itself defines an open position:
    #    func.count(func.distinct(Trade.symbol)).
    open_symbols = {
        s for t in open_trades if (s := getattr(t, "symbol", None))
    }
    position_count = len(open_symbols)
    position_cap = min(cfg.max_positions, cfg.max_open_positions)
    if position_count > position_cap:
        raise RiskBreach(
            f"Max open positions exceeded: {position_count} > {position_cap} "
            f"(symbols: {sorted(open_symbols)})"
        )

    # 2. Max directional exposure (notional). Previously defined in config but
    #    never enforced — an unbounded number of small entries could still pile
    #    into a large aggregate notional.
    if cfg.max_directional_exposure_usdt > 0:
        exposure = _directional_exposure_usdt(open_trades)
        if exposure > cfg.max_directional_exposure_usdt:
            raise RiskBreach(
                f"Max directional exposure exceeded: ${exposure:.2f} > "
                f"${cfg.max_directional_exposure_usdt:.2f}"
            )

    if latest_snapshot:
        current_value = latest_snapshot.total_value

        # 3. Max portfolio drawdown — peak over a ROLLING window (see module note).
        #    Disable entirely by setting max_portfolio_drawdown_pct >= 100
        #    (e.g. RISK_MAX_DRAWDOWN_PCT=99 in .env) — useful for testing the
        #    loop through an existing drawdown. >=100 means "no drawdown can ever
        #    exceed it", so we skip the query work and never raise.
        if cfg.max_portfolio_drawdown_pct < 100:
            window_start = datetime.now(timezone.utc) - timedelta(hours=PEAK_LOOKBACK_HOURS)
            peak_value = (
                db.query(func.max(PortfolioSnapshot.total_value))
                .filter(PortfolioSnapshot.timestamp >= window_start)
                .scalar()
            )
            # Fallback if the window is empty (fresh DB / sparse history): use the
            # all-time peak so we still have *some* drawdown anchor rather than none.
            if peak_value is None:
                peak_value = db.query(func.max(PortfolioSnapshot.total_value)).scalar() or current_value
            if current_value < peak_value:
                drawdown_pct = ((peak_value - current_value) / peak_value) * 100
                if drawdown_pct > cfg.max_portfolio_drawdown_pct:
                    raise RiskBreach(
                        f"Max portfolio drawdown exceeded: {drawdown_pct:.2f}% > {cfg.max_portfolio_drawdown_pct}% "
                        f"(Peak: ${peak_value:.2f}, Current: ${current_value:.2f})"
                    )

        # 4. Max daily loss — relative to the first snapshot of today (UTC).
        #    Disable entirely with max_daily_loss_pct >= 100
        #    (e.g. RISK_MAX_DAILY_LOSS_PCT=100 in .env) — mirrors the drawdown
        #    sentinel so the loop can be tested through a down day.
        if cfg.max_daily_loss_pct < 100:
            start_of_today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            first_snapshot_today = (
                db.query(PortfolioSnapshot)
                .filter(PortfolioSnapshot.timestamp >= start_of_today)
                .order_by(PortfolioSnapshot.timestamp.asc())
                .first()
            )
            if first_snapshot_today:
                start_value = first_snapshot_today.total_value
            else:
                # No snapshot recorded yet today: the original code silently skipped
                # the daily-loss check entirely (fail-open). Instead, fall back to
                # the most recent snapshot strictly before today as the day's
                # baseline so the check still has teeth on the first cycle of a day.
                prev = (
                    db.query(PortfolioSnapshot)
                    .filter(PortfolioSnapshot.timestamp < start_of_today)
                    .order_by(PortfolioSnapshot.timestamp.desc())
                    .first()
                )
                start_value = prev.total_value if prev else None
                if start_value is None:
                    logger.warning(
                        "[RISK GUARD] No baseline snapshot for daily-loss check; skipping (cold start)."
                    )

            if start_value and current_value < start_value:
                daily_loss_pct = ((start_value - current_value) / start_value) * 100
                if daily_loss_pct > cfg.max_daily_loss_pct:
                    raise RiskBreach(
                        f"Max daily loss exceeded: {daily_loss_pct:.2f}% > {cfg.max_daily_loss_pct}% "
                        f"(Daily Start: ${start_value:.2f}, Current: ${current_value:.2f})"
                    )
