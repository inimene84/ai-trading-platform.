import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from backend.services.decision_engine import DecisionEngine, compute_sl_tp_levels
from backend.services.risk_config import RiskConfig
from backend.strategies.base import StrategySignal


def _make_bars(n: int = 60, base: float = 100.0) -> list:
    """Synthetic OHLCV bars with gentle uptrend."""
    bars = []
    for i in range(n):
        c = base + i * 0.5
        bars.append({
            "open": c - 0.2,
            "high": c + 0.5,
            "low": c - 0.5,
            "close": c,
            "volume": 1000.0,
        })
    return bars


@pytest.fixture
def risk_config():
    return RiskConfig(
        min_signal_strength=0.45,
        ai_analysis_threshold=0.30,
        trade_usdt_amount=10.0,
        equity_sizing_enabled=True,
        risk_per_trade_pct=0.01,
        max_trade_notional_equity_mult=2.0,
        min_edge_fee_mult=0.0,  # disable min-edge gate for sizing tests
        sl_atr_mult=1.5,
        tp_atr_mult=3.0,
    )


def test_compute_sl_tp_buy_direction(risk_config):
    bars = _make_bars(20, base=100.0)
    sl, tp = compute_sl_tp_levels(bars, "BUY", 100.0, risk_config)
    assert sl < 100.0
    assert tp > 100.0


def test_compute_sl_tp_sell_direction(risk_config):
    bars = _make_bars(20, base=100.0)
    sl, tp = compute_sl_tp_levels(bars, "SELL", 100.0, risk_config)
    assert sl > 100.0
    assert tp < 100.0


def test_create_entry_floors_notional_at_20_usdt(risk_config):
    engine = DecisionEngine(risk_config)
    engine.account_equity = 50.0  # tiny equity → risk sizing would be < $20
    bars = _make_bars(60, base=50000.0)  # expensive symbol
    signal = StrategySignal(
        symbol="ETHUSDT", signal="BUY", confidence=0.8,
        entry_price=bars[-1]["close"], strategy="trend_following",
    )
    decision = engine._create_entry_decision("ETHUSDT", bars, signal, "BUY", is_pyramid=False)
    assert decision is not None
    notional = decision.quantity * decision.entry_price
    assert notional >= 20.0


def test_create_entry_floors_btc_at_100_usdt(risk_config):
    engine = DecisionEngine(risk_config)
    engine.account_equity = 80.0
    bars = _make_bars(60, base=60000.0)
    signal = StrategySignal(
        symbol="BTCUSDT", signal="BUY", confidence=0.8,
        entry_price=bars[-1]["close"], strategy="trend_following",
    )
    decision = engine._create_entry_decision("BTCUSDT", bars, signal, "BUY", is_pyramid=False)
    assert decision is not None
    notional = decision.quantity * decision.entry_price
    assert notional >= 100.0


def test_equity_sizing_scales_with_account(risk_config):
    engine = DecisionEngine(risk_config)
    bars = _make_bars(60, base=2000.0)
    signal = StrategySignal(
        symbol="ETHUSDT", signal="BUY", confidence=0.8,
        entry_price=bars[-1]["close"], strategy="trend_following",
    )

    engine.account_equity = 100.0
    small = engine._create_entry_decision("ETHUSDT", bars, signal, "BUY", is_pyramid=False)

    engine.account_equity = 500.0
    large = engine._create_entry_decision("ETHUSDT", bars, signal, "BUY", is_pyramid=False)

    assert small is not None and large is not None
    assert large.quantity > small.quantity


@pytest.mark.asyncio
async def test_evaluate_symbol_insufficient_bars(risk_config):
    engine = DecisionEngine(risk_config)
    result = await engine.evaluate_symbol("ETHUSDT", _make_bars(10), None, 0, [], False)
    assert result is None
    assert engine.last_evaluation["reason"] == "insufficient bars"


@pytest.mark.asyncio
async def test_evaluate_symbol_below_threshold(risk_config):
    engine = DecisionEngine(risk_config)
    bars = _make_bars(60)

    weak_signal = StrategySignal(
        symbol="ETHUSDT", signal="BUY", confidence=0.20,
        entry_price=bars[-1]["close"], strategy="trend_following",
    )
    engine.strategy.generate_signal = MagicMock(return_value=weak_signal)
    engine.regime_detector.detect = MagicMock(return_value=MagicMock(
        regime="TRENDING", weights=MagicMock(return_value={})
    ))

    result = await engine.evaluate_symbol("ETHUSDT", bars, None, 0, [], False)
    assert result is None
    assert "below threshold" in engine.last_evaluation["reason"]


@pytest.mark.asyncio
async def test_evaluate_symbol_skips_when_cooldown_active(risk_config):
    engine = DecisionEngine(risk_config)
    bars = _make_bars(60)
    result = await engine.evaluate_symbol("ETHUSDT", bars, None, 0, [], cooldown_active=True)
    assert result is None
