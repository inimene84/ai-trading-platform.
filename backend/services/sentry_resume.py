"""
Safe auto-resume after sentry halt: reconcile positions, restore ACTIVE, notify.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

import structlog

from backend.services.sentry_state import (
    TradingStatus,
    get_trading_status,
    read_state,
    resume_trading,
)
from backend.utils.telegram import send_telegram_message

logger = structlog.get_logger(__name__)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


async def reconcile_positions() -> dict[str, Any]:
    """Sync DB open trades with live broker; report exchange-only positions."""
    from backend.database.connection import SessionLocal
    from backend.database.models import Trade
    from backend.services.trading_loop import get_active_broker
    from backend.services.trading_loop_helpers import BrokerPositionSyncService

    broker = get_active_broker()
    db = SessionLocal()
    summary: dict[str, Any] = {
        "db_closed": 0,
        "exchange_only_symbols": [],
        "error": None,
    }
    try:
        synced = await BrokerPositionSyncService.sync_positions(db, broker, {}, {})
        summary["db_closed"] = synced

        broker_raw = await asyncio.get_event_loop().run_in_executor(
            None, lambda: broker.get_positions(raise_on_error=False)
        )
        live_symbols = {
            p["symbol"]
            for p in (broker_raw or [])
            if abs(float(p.get("quantity") or p.get("positionAmt") or 0)) > 0
        }
        db_symbols = {
            t.symbol
            for t in db.query(Trade).filter(Trade.status.in_(["open", "filled"])).all()
        }
        summary["exchange_only_symbols"] = sorted(live_symbols - db_symbols)
    except Exception as exc:
        summary["error"] = str(exc)
        logger.warning("Sentry reconciliation failed", error=str(exc))
    finally:
        db.close()
    return summary


async def safe_resume(
    *,
    resumed_by: str,
    reconcile: bool = True,
    allow_manual: bool = False,
) -> dict[str, Any]:
    """
    Resume trading after sentry halt.

    Auto-resume (allow_manual=False) only clears HALTED_BY_SENTRY.
    Operator /resume (allow_manual=True) clears any halt state.
    """
    current = get_trading_status()
    state = read_state()

    if current == TradingStatus.ACTIVE:
        return {"ok": True, "already_active": True, "state": state}

    if current == TradingStatus.HALTED_MANUAL and not allow_manual:
        return {
            "ok": False,
            "skipped": True,
            "reason": "manual_halt_requires_operator_resume",
            "state": state,
        }

    reconcile_result: dict[str, Any] | None = None
    if reconcile:
        reconcile_result = await reconcile_positions()
        require_ok = os.getenv("SENTRY_RESUME_REQUIRE_RECONCILE", "true").lower() == "true"
        if require_ok and reconcile_result.get("error"):
            return {
                "ok": False,
                "skipped": True,
                "reason": "reconciliation_failed",
                "reconcile": reconcile_result,
                "state": state,
            }

    new_state = resume_trading(resumed_by=resumed_by)
    halted_at = state.get("halted_at")
    halt_reason = state.get("reason") or "unknown"

    msg_lines = [
        "✅ <b>TRADING RESUMED</b>",
        f"Resumed by: {resumed_by}",
        f"Previous halt: {halt_reason}",
    ]
    if halted_at:
        msg_lines.append(f"Halted at: {halted_at}")
    if reconcile_result:
        msg_lines.append(f"DB positions closed: {reconcile_result.get('db_closed', 0)}")
        orphans = reconcile_result.get("exchange_only_symbols") or []
        if orphans:
            msg_lines.append(f"⚠️ Exchange-only positions: {', '.join(orphans)}")
        if reconcile_result.get("error"):
            msg_lines.append(f"Reconcile note: {reconcile_result['error']}")

    try:
        await send_telegram_message("\n".join(msg_lines), parse_mode="HTML")
    except Exception as exc:
        logger.warning("Telegram alert failed during resume", error=str(exc))

    return {
        "ok": True,
        "state": new_state,
        "reconcile": reconcile_result,
    }


def seconds_since_halt() -> float | None:
    state = read_state()
    halted_at = _parse_iso(state.get("halted_at"))
    if not halted_at:
        return None
    return (datetime.now(timezone.utc) - halted_at).total_seconds()
