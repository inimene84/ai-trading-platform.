"""Regression tests for the Jul-2026 weekly-edge fixes.

Covers: STEP-TRAIL off by default, late trail activation, underwater pyramid
block, and blacklist that still manages open legs.
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.services.decision_engine import (
    DecisionEngine,
    pyramid_position_underwater,
)
from backend.services.risk_config import RiskConfig
from backend.services.trading_loop import TradingLoopService
from backend.services.trading_loop_helpers import TrailingStopManager
from backend.strategies.base import StrategySignal


def _bars(n: int = 30, base: float = 100.0, step: float = 0.0) -> list:
    bars = []
    for i in range(n):
        c = base + i * step
        bars.append(
            {
                "open": c - 0.1,
                "high": c + 1.0,
                "low": c - 1.0,
                "close": c,
                "volume": 1000.0,
            }
        )
    return bars


def test_pyramid_underwater_helpers():
    assert pyramid_position_underwater("BUY", 100.0, 99.0)
    assert not pyramid_position_underwater("BUY", 100.0, 101.0)
    assert pyramid_position_underwater("SELL", 100.0, 101.0)
    assert not pyramid_position_underwater("SELL", 100.0, 99.0)


@pytest.mark.asyncio
async def test_pyramid_blocked_when_underwater():
    cfg = RiskConfig(
        pyramid_mode=True,
        pyramid_max_layers=2,
        pyramid_block_underwater=True,
        pyramid_min_improvement=0.0,
        min_signal_strength=0.45,
        min_edge_fee_mult=0.0,
        use_risk_reviewer_llm=False,
    )
    engine = DecisionEngine(cfg)
    engine.account_equity = 200.0

    existing = MagicMock()
    existing.direction = "BUY"
    existing.entry_price = 100.0

    bars = _bars(60, base=99.0)
    with patch.object(engine.regime_detector, "detect") as det, patch.object(
        engine.strategy, "generate_signal"
    ) as gen:
        det.return_value = MagicMock(regime="TRENDING", weights=lambda: {})
        gen.return_value = StrategySignal(
            symbol="ETHUSDT",
            signal="BUY",
            confidence=0.9,
            entry_price=99.0,
            strategy="trend_following",
        )
        decision = await engine.evaluate_symbol(
            symbol="ETHUSDT",
            bars=bars,
            existing_position=existing,
            open_count=1,
            pyramid_layers=[100.0],
            cooldown_active=False,
        )
    assert decision is None
    assert "underwater" in (engine.last_evaluation.get("reason") or "")


def test_step_trail_disabled_does_not_move_stop_to_be():
    cfg = RiskConfig(
        trailing_stop_enabled=True,
        native_trailing_enabled=False,
        step_trail_enabled=False,
        trail_activation_atr=2.0,
        trail_atr_mult=0.8,
    )
    bars = _bars(20, base=100.0)
    for b in bars:
        b["high"] = b["close"] + 1.0
        b["low"] = b["close"] - 1.0
    bars[-1]["close"] = 103.0
    bars[-1]["high"] = 104.0

    trade = MagicMock()
    trade.id = 1
    trade.symbol = "ETHUSDT"
    trade.direction = "BUY"
    trade.entry_price = 100.0
    trade.stop_loss = 98.0
    trade.quantity = 1.0

    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [trade]
    high_water: dict = {}

    with patch.object(TrailingStopManager, "_sync_exchange_stop") as sync:
        TrailingStopManager.apply_trailing_stop(
            db, "ETHUSDT", bars, high_water, cfg, MagicMock()
        )
        sync.assert_not_called()
    assert trade.stop_loss == 98.0


def test_full_trail_activates_after_activation_distance():
    cfg = RiskConfig(
        trailing_stop_enabled=True,
        native_trailing_enabled=False,
        step_trail_enabled=False,
        trail_activation_atr=1.0,
        trail_atr_mult=0.5,
    )
    bars = _bars(20, base=100.0)
    for b in bars:
        b["high"] = b["close"] + 1.0
        b["low"] = b["close"] - 1.0
    bars[-1]["close"] = 103.0
    bars[-1]["high"] = 104.0

    trade = MagicMock()
    trade.id = 2
    trade.symbol = "ETHUSDT"
    trade.direction = "BUY"
    trade.entry_price = 100.0
    trade.stop_loss = 98.0
    trade.quantity = 1.0

    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [trade]
    high_water: dict = {}

    with patch.object(TrailingStopManager, "_sync_exchange_stop"):
        TrailingStopManager.apply_trailing_stop(
            db, "ETHUSDT", bars, high_water, cfg, MagicMock()
        )
    assert trade.stop_loss > 98.0


def test_risk_config_field_defaults_for_weekly_edge():
    fields = RiskConfig.model_fields
    assert fields["pyramid_max_layers"].default == 2
    assert fields["pyramid_block_underwater"].default is True
    assert fields["trail_activation_atr"].default == 2.0
    assert fields["trail_atr_mult"].default == 0.8
    assert fields["step_trail_enabled"].default is False
    assert fields["max_same_direction_positions"].default == 3
    assert fields["max_portfolio_drawdown_pct"].default == 20.0
    assert fields["max_daily_loss_pct"].default == 5.0
    bl = fields["symbol_blacklist_raw"].default
    assert "ADAUSDT" in bl
    assert "DOGEUSDT" in bl
    assert "ARBUSDT" in bl
    assert "APTUSDT" in bl


@pytest.mark.asyncio
async def test_blacklist_keeps_open_symbols_for_management():
    loop = TradingLoopService.__new__(TradingLoopService)
    loop.risk_config = MagicMock(
        symbol_blacklist={"DOGEUSDT", "ADAUSDT"},
        min_24h_quote_volume_usdt=0,
        symbol_expectancy_gate_enabled=False,
    )

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.distinct.return_value.all.return_value = [
        ("DOGEUSDT",)
    ]

    with patch("backend.services.trading_loop.SessionLocal", return_value=mock_db):
        kept = await loop._filter_tradeable_symbols(
            ["ETHUSDT", "DOGEUSDT", "ADAUSDT"]
        )

    assert "ETHUSDT" in kept
    assert "DOGEUSDT" in kept  # open → manage
    assert "ADAUSDT" not in kept  # blacklisted, no open leg
