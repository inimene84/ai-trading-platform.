import json
import os
from pathlib import Path

import pytest

from backend.services.sentry_state import (
    TradingStatus,
    halt_trading,
    is_trading_allowed,
    read_state,
    resume_trading,
    write_state,
)


@pytest.fixture
def sentry_state_dir(tmp_path, monkeypatch):
    state_dir = tmp_path / "sentry"
    monkeypatch.setenv("SENTRY_STATE_DIR", str(state_dir))
    return state_dir


def test_default_state_is_active(sentry_state_dir):
    assert is_trading_allowed() is True
    state = read_state()
    assert state["status"] == TradingStatus.ACTIVE.value


def test_halt_and_resume(sentry_state_dir):
    halt_trading(reason="test_crash", halted_by="unit_test")
    assert is_trading_allowed() is False
    assert read_state()["status"] == TradingStatus.HALTED_BY_SENTRY.value

    resume_trading(resumed_by="operator")
    assert is_trading_allowed() is True
    assert read_state()["status"] == TradingStatus.ACTIVE.value


def test_manual_halt(sentry_state_dir):
    halt_trading(reason="maintenance", halted_by="operator", manual=True)
    assert read_state()["status"] == TradingStatus.HALTED_MANUAL.value


def test_atomic_write(sentry_state_dir):
    write_state(TradingStatus.HALTED_BY_SENTRY, reason="x", halted_by="test")
    path = sentry_state_dir / "trading_status.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["reason"] == "x"


def test_corrupt_state_fails_closed(sentry_state_dir, monkeypatch):
    path = sentry_state_dir / "trading_status.json"
    sentry_state_dir.mkdir(parents=True)
    path.write_text("not-json", encoding="utf-8")
    state = read_state()
    assert state["status"] == TradingStatus.HALTED_BY_SENTRY.value
