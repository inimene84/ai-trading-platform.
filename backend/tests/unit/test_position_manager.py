"""Min-hold enforcement on LLM/technical exits; emergency drawdown stays exempt."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.services.position_manager import PositionManager


def _bars(n=20, base=100.0):
    return [
        {
            "open": base,
            "high": base + 1,
            "low": base - 1,
            "close": base,
            "volume": 1000,
        }
        for _ in range(n)
    ]


def _trade(entry=100.0, direction="BUY", held_minutes=5):
    opened = datetime.now(timezone.utc) - timedelta(minutes=held_minutes)
    return {
        "entry_price": entry,
        "direction": direction,
        "opened_at": opened.isoformat(),
        "stop_loss": 90.0,
        "take_profit": 120.0,
    }


@pytest.mark.asyncio
async def test_min_hold_blocks_ai_reversal_exit():
    mgr = PositionManager()
    mgr.config.min_position_hold_min = 20
    mgr.config.exit_opinion_threshold = 0.65
    ai = SimpleNamespace(direction="SELL", confidence=0.90)

    result = await mgr.analyze_open_position(
        "ETHUSDT",
        _trade(held_minutes=5),
        _bars(),
        current_price=99.0,
        opinion_layer_fn=AsyncMock(return_value=ai),
    )
    assert result.exit is False
    assert result.direction == "HOLD"


@pytest.mark.asyncio
async def test_ai_reversal_exits_after_min_hold():
    mgr = PositionManager()
    mgr.config.min_position_hold_min = 20
    mgr.config.exit_opinion_threshold = 0.65
    ai = SimpleNamespace(direction="SELL", confidence=0.90)

    result = await mgr.analyze_open_position(
        "ETHUSDT",
        _trade(held_minutes=45),
        _bars(),
        current_price=99.0,
        opinion_layer_fn=AsyncMock(return_value=ai),
    )
    assert result.exit is True
    assert "AI REVERSAL" in result.reasoning


@pytest.mark.asyncio
async def test_emergency_drawdown_bypasses_min_hold():
    mgr = PositionManager()
    mgr.config.min_position_hold_min = 20
    mgr.config.emergency_drawdown_pct = -8.0

    result = await mgr.analyze_open_position(
        "ETHUSDT",
        _trade(entry=100.0, held_minutes=2),
        _bars(),
        current_price=91.0,  # -9%
    )
    assert result.exit is True
    assert result.urgency == "emergency"
    assert "EMERGENCY" in result.reasoning


@pytest.mark.asyncio
async def test_min_hold_blocks_technical_exit():
    mgr = PositionManager()
    mgr.config.min_position_hold_min = 20
    # Downtrend bars with a loss > 5%
    bars = []
    for i in range(20):
        c = 100.0 - i * 0.6
        bars.append({"open": c, "high": c + 0.2, "low": c - 0.2, "close": c, "volume": 1000})

    result = await mgr.analyze_open_position(
        "ETHUSDT",
        _trade(entry=100.0, held_minutes=5),
        bars,
        current_price=bars[-1]["close"],
    )
    assert result.exit is False
