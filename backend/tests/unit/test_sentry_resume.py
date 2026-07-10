import asyncio

import pytest

from backend.services.sentry_state import TradingStatus, halt_trading, read_state
from backend.services import sentry_resume


@pytest.fixture
def sentry_state_dir(tmp_path, monkeypatch):
    state_dir = tmp_path / "sentry"
    monkeypatch.setenv("SENTRY_STATE_DIR", str(state_dir))
    monkeypatch.setenv("SENTRY_RESUME_REQUIRE_RECONCILE", "false")
    return state_dir


def test_safe_resume_after_sentry_halt(sentry_state_dir, monkeypatch):
    async def _noop_telegram(*_args, **_kwargs):
        return True

    async def _noop_reconcile():
        return {"db_closed": 0, "exchange_only_symbols": [], "error": None}

    monkeypatch.setattr(sentry_resume, "send_telegram_message", _noop_telegram)
    monkeypatch.setattr(sentry_resume, "reconcile_positions", _noop_reconcile)

    halt_trading(reason="crash", halted_by="test")
    result = asyncio.run(sentry_resume.safe_resume(resumed_by="watchdog"))
    assert result["ok"] is True
    assert read_state()["status"] == TradingStatus.ACTIVE.value


def test_auto_resume_skips_manual_halt(sentry_state_dir):
    halt_trading(reason="maintenance", halted_by="operator", manual=True)
    result = asyncio.run(sentry_resume.safe_resume(resumed_by="watchdog", allow_manual=False))
    assert result["ok"] is False
    assert result["reason"] == "manual_halt_requires_operator_resume"


def test_operator_resume_clears_manual_halt(sentry_state_dir, monkeypatch):
    async def _noop_telegram(*_args, **_kwargs):
        return True

    async def _noop_reconcile():
        return {"db_closed": 0, "exchange_only_symbols": [], "error": None}

    monkeypatch.setattr(sentry_resume, "send_telegram_message", _noop_telegram)
    monkeypatch.setattr(sentry_resume, "reconcile_positions", _noop_reconcile)

    halt_trading(reason="maintenance", halted_by="operator", manual=True)
    result = asyncio.run(sentry_resume.safe_resume(resumed_by="operator", allow_manual=True))
    assert result["ok"] is True
    assert read_state()["status"] == TradingStatus.ACTIVE.value
