import pytest
from unittest.mock import MagicMock

from backend.services.decision_engine import (
    DecisionEngine,
    compute_sl_tp_levels,
    atr_from_bars,
    pyramid_price_improved,
)
from backend.services.risk_config import RiskConfig
from backend.strategies.base import StrategySignal


def _make_bars(n: int = 200, base: float = 100.0) -> list:
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


def test_compute_sl_tp_honors_zero_signal_sl(risk_config):
    bars = _make_bars(20, base=100.0)
    sl, tp = compute_sl_tp_levels(bars, "BUY", 100.0, risk_config, signal_sl=0.0, signal_tp=120.0)
    assert sl == 0.0
    assert tp == 120.0


def test_atr_from_bars_handles_short_series():
    bars = _make_bars(15, base=100.0)
    atr = atr_from_bars(bars, 100.0)
    assert atr > 0


def test_short_pyramid_requires_price_decline():
    assert pyramid_price_improved("SELL", 99.4, 100.0, 0.005)
    assert not pyramid_price_improved("SELL", 100.0, 100.0, 0.005)
    assert not pyramid_price_improved("SELL", 101.0, 100.0, 0.005)


def test_create_entry_floors_notional_at_20_usdt(risk_config):
    engine = DecisionEngine(risk_config)
    engine.account_equity = 50.0  # tiny equity → risk sizing would be < $20
    bars = _make_bars(200, base=50000.0)  # expensive symbol
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
    bars = _make_bars(200, base=60000.0)
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
    bars = _make_bars(200, base=2000.0)
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
    bars = _make_bars(200)

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
    bars = _make_bars(200)
    result = await engine.evaluate_symbol("ETHUSDT", bars, None, 0, [], cooldown_active=True)
    assert result is None

@pytest.mark.asyncio
async def test_evaluate_symbol_funding_rate_adjustments(risk_config):
    engine = DecisionEngine(risk_config)
    engine.enable_kronos = False
    engine.config.enable_personas = False
    engine.config.use_risk_reviewer_llm = False
    bars = _make_bars(200)

    # SELL signal with positive funding boosts confidence, but the boost is
    # clamped to funding_conf_adj_cap (default 0.05): a routine 0.01% funding
    # must nudge, not flip, a signal (raw adj would be +0.10 here).
    signal = StrategySignal(
        symbol="ETHUSDT", signal="SELL", confidence=0.42,
        entry_price=bars[-1]["close"], strategy="trend_following",
    )
    engine.strategy.generate_signal = MagicMock(return_value=signal)
    engine.regime_detector.detect = MagicMock(return_value=MagicMock(
        regime="TRENDING", weights=MagicMock(return_value={})
    ))

    result = await engine.evaluate_symbol("ETHUSDT", bars, None, 0, [], False, current_funding_rate=0.0001)
    assert result is not None
    assert result.confidence == pytest.approx(0.47)  # 0.42 + capped 0.05


@pytest.mark.asyncio
async def test_funding_adjustment_clamped_for_extreme_rates(risk_config):
    """A huge funding rate must not add more than funding_conf_adj_cap."""
    engine = DecisionEngine(risk_config)
    engine.enable_kronos = False
    engine.config.enable_personas = False
    engine.config.use_risk_reviewer_llm = False
    bars = _make_bars(200)

    signal = StrategySignal(
        symbol="ETHUSDT", signal="SELL", confidence=0.50,
        entry_price=bars[-1]["close"], strategy="trend_following",
    )
    engine.strategy.generate_signal = MagicMock(return_value=signal)
    engine.regime_detector.detect = MagicMock(return_value=MagicMock(
        regime="TRENDING", weights=MagicMock(return_value={})
    ))

    # 0.1% funding -> raw adjustment would be +1.0 (saturating confidence)
    result = await engine.evaluate_symbol("ETHUSDT", bars, None, 0, [], False, current_funding_rate=0.001)
    assert result is not None
    assert result.confidence == pytest.approx(0.55)  # 0.50 + capped 0.05

@pytest.mark.asyncio
async def test_ranging_regime_sizing_and_gate(risk_config):
    engine = DecisionEngine(risk_config)
    engine.enable_kronos = False
    engine.config.enable_personas = False
    engine.config.use_risk_reviewer_llm = False
    engine.config.trade_usdt_amount = 100.0
    bars = _make_bars(200)
    
    signal = StrategySignal(
        symbol="ETHUSDT", signal="BUY", confidence=0.50,
        entry_price=bars[-1]["close"], strategy="mean_reversion",
    )
    engine.strategy.generate_signal = MagicMock(return_value=signal)
    
    # 1. RANGING regime check: should fail because confidence 0.50 < 0.60 required (0.45 + 0.15)
    engine.regime_detector.detect = MagicMock(return_value=MagicMock(
        regime="RANGING", weights=MagicMock(return_value={})
    ))
    result_ranging = await engine.evaluate_symbol("ETHUSDT", bars, None, 0, [], False)
    assert result_ranging is None
    
    # 2. TRENDING regime check: should pass because confidence 0.50 >= 0.45
    engine.regime_detector.detect = MagicMock(return_value=MagicMock(
        regime="TRENDING", weights=MagicMock(return_value={})
    ))
    result_trending = await engine.evaluate_symbol("ETHUSDT", bars, None, 0, [], False)
    assert result_trending is not None
    
    # 3. Block new entries in RANGING regime (as per design in commit af175970)
    decision_trending = engine._create_entry_decision("ETHUSDT", bars, signal, "BUY", is_pyramid=False, regime="TRENDING")
    decision_ranging = engine._create_entry_decision("ETHUSDT", bars, signal, "BUY", is_pyramid=False, regime="RANGING")
    
    assert decision_trending is not None
    assert decision_ranging is None
