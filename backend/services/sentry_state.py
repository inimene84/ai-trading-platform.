"""
Persistent trading halt flag shared between backend and sentry_watchdog.

Stored on the app-data volume so both containers see the same state.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

DEFAULT_STATE_DIR = "/app/data/sentry"
STATE_FILENAME = "trading_status.json"


class TradingStatus(str, Enum):
    ACTIVE = "ACTIVE"
    HALTED_BY_SENTRY = "HALTED_BY_SENTRY"
    HALTED_MANUAL = "HALTED_MANUAL"


def _state_dir() -> Path:
    return Path(os.getenv("SENTRY_STATE_DIR", DEFAULT_STATE_DIR))


def _state_path() -> Path:
    return _state_dir() / STATE_FILENAME


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_state() -> dict[str, Any]:
    return {
        "status": TradingStatus.ACTIVE.value,
        "reason": None,
        "halted_at": None,
        "halted_by": None,
        "resumed_at": None,
        "updated_at": _utc_now(),
    }


def _ensure_dir() -> None:
    _state_dir().mkdir(parents=True, exist_ok=True)


def read_state() -> dict[str, Any]:
    """Return current sentry state, defaulting to ACTIVE if missing."""
    path = _state_path()
    if not path.exists():
        return _default_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("state file is not a JSON object")
        status = data.get("status", TradingStatus.ACTIVE.value)
        if status not in {s.value for s in TradingStatus}:
            logger.warning("Unknown trading status in state file; treating as HALTED_BY_SENTRY", status=status)
            data["status"] = TradingStatus.HALTED_BY_SENTRY.value
        return data
    except Exception as exc:
        logger.error("Failed to read sentry state; fail-closed to HALTED_BY_SENTRY", error=str(exc))
        return {
            **_default_state(),
            "status": TradingStatus.HALTED_BY_SENTRY.value,
            "reason": f"state_read_error: {exc}",
            "halted_at": _utc_now(),
            "halted_by": "sentry_state",
        }


def write_state(
    status: TradingStatus,
    *,
    reason: str | None = None,
    halted_by: str | None = None,
    resumed_by: str | None = None,
) -> dict[str, Any]:
    """Atomically persist trading status."""
    _ensure_dir()
    current = read_state()
    now = _utc_now()
    payload: dict[str, Any] = {
        "status": status.value,
        "reason": reason if status != TradingStatus.ACTIVE else None,
        "halted_at": now if status != TradingStatus.ACTIVE else current.get("halted_at"),
        "halted_by": halted_by if status != TradingStatus.ACTIVE else current.get("halted_by"),
        "resumed_at": now if status == TradingStatus.ACTIVE else None,
        "resumed_by": resumed_by if status == TradingStatus.ACTIVE else None,
        "updated_at": now,
    }
    path = _state_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)
    logger.info("Sentry trading status updated", **payload)
    return payload


def get_trading_status() -> TradingStatus:
    raw = read_state().get("status", TradingStatus.ACTIVE.value)
    return TradingStatus(raw)


def is_trading_allowed() -> bool:
    return get_trading_status() == TradingStatus.ACTIVE


def halt_trading(*, reason: str, halted_by: str, manual: bool = False) -> dict[str, Any]:
    status = TradingStatus.HALTED_MANUAL if manual else TradingStatus.HALTED_BY_SENTRY
    return write_state(status, reason=reason, halted_by=halted_by)


def resume_trading(*, resumed_by: str) -> dict[str, Any]:
    return write_state(TradingStatus.ACTIVE, resumed_by=resumed_by)
