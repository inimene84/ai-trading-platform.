"""Min-hold exit guard: AI/technical blocked, emergency still fires."""

from datetime import datetime, timedelta, timezone

import pytest

from backend.services.position_manager import PositionManager
from backend.services.risk_config import RiskConfig


def _cfg(**kwargs) -> RiskConfig:
    defaults = dict(
        min_position_hold_min=20,
        emergency_drawdown_pct=-8.0,
        max_position_hold_hours=72,
        exit_opinion_threshold=0.55,
        enable_personas=False,
    )
    defaults.update(kwargs)
    return RiskConfig(**defaults)


def _pm(monkeypatch, cfg=None) -> PositionManager:
    cfg = cfg or _cfg()
    monkeypatch.setattr(
        "backend.services.position_manager.refresh_risk_config", lambda: cfg
    )
    return PositionManager()


def _trade(minutes_ago=5, opened_at="set", entry=100.0, direction="BUY"):
    if opened_at is None:
        ts = None
    elif opened_at == "set":
        ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    else:
        ts = opened_at
    return {
        "entry_price": entry,
        "direction": direction,
        "opened_at": ts,
        "stop_loss": 95.0,
        "take_profit": 110.0,
    }


def _bars(n=25, close=99.0):
    return [{"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1}
            for _ in range(n)]


@pytest.mark.asyncio
async def test_min_hold_blocks_exit_on_fresh_position(monkeypatch):
    pm = _pm(monkeypatch)
    result = await pm.analyze_open_position(
        "ETHUSDT", _trade(minutes_ago=5), _bars(), 99.0
    )
    assert result.exit is False
    assert "min hold" in result.reasoning.lower()


@pytest.mark.asyncio
async def test_emergency_drawdown_is_exempt_from_min_hold(monkeypatch):
    pm = _pm(monkeypatch)
    result = await pm.analyze_open_position(
        "ETHUSDT", _trade(minutes_ago=2), _bars(close=90.0), 90.0
    )
    assert result.exit is True
    assert result.urgency == "emergency"
    assert "EMERGENCY" in result.reasoning


@pytest.mark.asyncio
async def test_min_hold_does_not_apply_after_window(monkeypatch):
    pm = _pm(monkeypatch)
    # -1% after 30 min: past min-hold, not emergency, not technical (-5%).
    result = await pm.analyze_open_position(
        "ETHUSDT", _trade(minutes_ago=30), _bars(close=99.0), 99.0
    )
    assert result.exit is False
    assert "min hold" not in result.reasoning.lower()
    assert "HOLD" in result.reasoning


@pytest.mark.asyncio
async def test_unknown_duration_does_not_lock_exits(monkeypatch):
    """Missing opened_at used to yield duration=0 and block every non-emergency exit."""
    pm = _pm(monkeypatch)
    result = await pm.analyze_open_position(
        "ETHUSDT", _trade(opened_at=None), _bars(close=99.0), 99.0
    )
    assert "min hold" not in result.reasoning.lower()
