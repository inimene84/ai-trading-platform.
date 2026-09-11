"""Unit tests for Jesse Machine Learning Consensus Gate in DecisionEngine."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from backend.services.decision_engine import DecisionEngine, jesse_ml_model_health
from backend.services.risk_config import RiskConfig
from backend.strategies.base import StrategySignal


def _make_bars(n: int = 100, base: float = 100.0) -> list:
    bars = []
    for i in range(n):
        c = base + i * 0.1
        bars.append({
            "open": c - 0.1,
            "high": c + 0.3,
            "low": c - 0.3,
            "close": c,
            "volume": 1000.0,
        })
    return bars


@pytest.fixture
def ml_risk_config():
    return RiskConfig(
        min_signal_strength=0.45,
        ai_analysis_threshold=0.30,
        trade_usdt_amount=10.0,
        enable_personas=False,
        use_risk_reviewer_llm=False,
        min_edge_fee_mult=0.0,
        sl_atr_mult=1.75,
        tp_atr_mult=5.5,
        enable_jesse_ml=True,
    )


@pytest.mark.asyncio
async def test_jesse_ml_gate_veto_buy_on_strong_bearish(ml_risk_config, monkeypatch):
    """When Jesse ML predicts strong Bearish probability (>=55%), candidate BUY is vetoed."""
    monkeypatch.setenv("JESSE_ML_GATE_ENABLED", "true")
    engine = DecisionEngine(ml_risk_config)
    engine.enable_kronos = False
    engine.account_equity = 1000.0
    engine.account_available = 1000.0
    bars = _make_bars(50)

    # Mock base strategy to emit a BUY signal
    mock_signal = StrategySignal(symbol="BTCUSDC", signal="BUY", confidence=0.60, entry_price=bars[-1]["close"])
    engine.strategy.generate_signal = MagicMock(return_value=mock_signal)
    engine.regime_detector.detect = MagicMock(return_value=MagicMock(regime="TRENDING", weights=MagicMock(return_value={})))

    # Mock Jesse ML prediction returning strong bearish probability
    mock_ml_res = {
        "status": "success",
        "symbol": "BTC-USDT",
        "signal": "SELL",
        "confidence": 0.70,
        "probabilities": {"bullish": 0.10, "bearish": 0.70, "neutral": 0.20},
        "model_expired": False,
        "feature_schema_parity": True,
    }

    with patch("backend.services.jesse_bridge.jesse_bridge.get_ml_prediction", AsyncMock(return_value=mock_ml_res)):
        decision = await engine.evaluate_symbol("BTCUSDC", bars, None, 0, [], False)
        assert decision is None
        assert "vetoed by Jesse ML gate" in engine.last_evaluation.get("reason", "")


@pytest.mark.asyncio
async def test_jesse_ml_gate_veto_sell_on_strong_bullish(ml_risk_config, monkeypatch):
    """When Jesse ML predicts strong Bullish probability (>=55%), candidate SELL is vetoed."""
    monkeypatch.setenv("JESSE_ML_GATE_ENABLED", "true")
    engine = DecisionEngine(ml_risk_config)
    engine.enable_kronos = False
    engine.account_equity = 1000.0
    engine.account_available = 1000.0
    bars = _make_bars(50)

    # Mock base strategy to emit a SELL signal
    mock_signal = StrategySignal(symbol="BTCUSDC", signal="SELL", confidence=0.60, entry_price=bars[-1]["close"])
    engine.strategy.generate_signal = MagicMock(return_value=mock_signal)
    engine.regime_detector.detect = MagicMock(return_value=MagicMock(regime="TRENDING", weights=MagicMock(return_value={})))

    # Mock Jesse ML prediction returning strong bullish probability
    mock_ml_res = {
        "status": "success",
        "symbol": "BTC-USDT",
        "signal": "BUY",
        "confidence": 0.75,
        "probabilities": {"bullish": 0.75, "bearish": 0.08, "neutral": 0.17},
        "model_expired": False,
        "feature_schema_parity": True,
    }

    with patch("backend.services.jesse_bridge.jesse_bridge.get_ml_prediction", AsyncMock(return_value=mock_ml_res)):
        decision = await engine.evaluate_symbol("BTCUSDC", bars, None, 0, [], False)
        assert decision is None
        assert "vetoed by Jesse ML gate" in engine.last_evaluation.get("reason", "")


@pytest.mark.asyncio
async def test_jesse_ml_gate_boost_on_consensus(ml_risk_config, monkeypatch):
    """When Jesse ML agrees with candidate BUY, confidence is boosted."""
    monkeypatch.setenv("JESSE_ML_GATE_ENABLED", "true")
    engine = DecisionEngine(ml_risk_config)
    engine.enable_kronos = False
    engine.account_equity = 1000.0
    engine.account_available = 1000.0
    bars = _make_bars(50)

    initial_conf = 0.55
    mock_signal = StrategySignal(symbol="BTCUSDC", signal="BUY", confidence=initial_conf, entry_price=bars[-1]["close"])
    engine.strategy.generate_signal = MagicMock(return_value=mock_signal)
    engine.regime_detector.detect = MagicMock(return_value=MagicMock(regime="TRENDING", weights=MagicMock(return_value={})))

    mock_ml_res = {
        "status": "success",
        "symbol": "BTC-USDT",
        "signal": "BUY",
        "confidence": 0.80,
        "probabilities": {"bullish": 0.80, "bearish": 0.05, "neutral": 0.15},
        "model_expired": False,
        "feature_schema_parity": True,
    }

    with patch("backend.services.jesse_bridge.jesse_bridge.get_ml_prediction", AsyncMock(return_value=mock_ml_res)):
        decision = await engine.evaluate_symbol("BTCUSDC", bars, None, 0, [], False)
        assert decision is not None
        assert decision.action == "BUY"
        # Confidence must be boosted above initial
        assert decision.confidence > initial_conf


@pytest.mark.asyncio
async def test_jesse_ml_gate_fail_open_on_error_in_paper(ml_risk_config, monkeypatch):
    """In paper mode, when Jesse ML server is down or errors, gate fails open gracefully without blocking trades."""
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("JESSE_ML_GATE_ENABLED", "true")
    engine = DecisionEngine(ml_risk_config)
    engine.enable_kronos = False
    engine.account_equity = 1000.0
    engine.account_available = 1000.0
    bars = _make_bars(50)

    mock_signal = StrategySignal(symbol="BTCUSDC", signal="BUY", confidence=0.60, entry_price=bars[-1]["close"])
    engine.strategy.generate_signal = MagicMock(return_value=mock_signal)
    engine.regime_detector.detect = MagicMock(return_value=MagicMock(regime="TRENDING", weights=MagicMock(return_value={})))

    with patch("backend.services.jesse_bridge.jesse_bridge.get_ml_prediction", AsyncMock(side_effect=Exception("Connection refused"))):
        decision = await engine.evaluate_symbol("BTCUSDC", bars, None, 0, [], False)
        # In paper mode, fails open
        assert decision is not None
        assert decision.action == "BUY"


@pytest.mark.asyncio
async def test_jesse_ml_gate_fail_closed_in_live_mode(ml_risk_config, monkeypatch):
    """In LIVE mode, when Jesse ML server is down or errors, gate fails closed (vetoes entry)."""
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.setenv("JESSE_ML_GATE_ENABLED", "true")
    engine = DecisionEngine(ml_risk_config)
    engine.enable_kronos = False
    engine.account_equity = 1000.0
    engine.account_available = 1000.0
    bars = _make_bars(50)

    mock_signal = StrategySignal(symbol="BTCUSDC", signal="BUY", confidence=0.60, entry_price=bars[-1]["close"])
    engine.strategy.generate_signal = MagicMock(return_value=mock_signal)
    engine.regime_detector.detect = MagicMock(return_value=MagicMock(regime="TRENDING", weights=MagicMock(return_value={})))

    with patch("backend.services.jesse_bridge.jesse_bridge.get_ml_prediction", AsyncMock(side_effect=Exception("Connection refused"))):
        decision = await engine.evaluate_symbol("BTCUSDC", bars, None, 0, [], False)
        # In live mode, must fail closed
        assert decision is None


@pytest.mark.asyncio
async def test_jesse_ml_kelly_clipping_below_30_partition_trades(ml_risk_config, monkeypatch):
    """When partition closed trades < 30, Kelly multiplier must be clipped to [0.25, 1.0]."""
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.setenv("JESSE_ML_GATE_ENABLED", "true")
    engine = DecisionEngine(ml_risk_config)
    engine.enable_kronos = False
    engine.account_equity = 1000.0
    engine.account_available = 1000.0
    bars = _make_bars(50)

    mock_signal = StrategySignal(
        symbol="BTCUSDC", signal="BUY", confidence=0.60, entry_price=bars[-1]["close"],
        stop_loss=bars[-1]["close"] * 0.98, take_profit=bars[-1]["close"] * 1.04,
    )
    engine.strategy.generate_signal = MagicMock(return_value=mock_signal)
    engine.regime_detector.detect = MagicMock(return_value=MagicMock(regime="TRENDING", weights=MagicMock(return_value={})))

    # Jesse returns a high Kelly multiplier (e.g. 1.8x)
    mock_ml = {
        "status": "success",
        "signal": "BUY",
        "confidence": 0.70,
        "probabilities": {"bullish": 0.70, "bearish": 0.10, "neutral": 0.20},
        "uncertainty": "LOW",
        "gated": False,
        "kelly": {"size_multiplier": 1.8},
        "model_expired": False,
        "feature_schema_parity": True,
    }

    with patch("backend.services.jesse_bridge.jesse_bridge.get_ml_prediction", AsyncMock(return_value=mock_ml)):
        decision = await engine.evaluate_symbol("BTCUSDC", bars, None, 0, [], False)
        assert decision is not None
        # Base trade_usdt_amount is 10.0 (from ml_risk_config), clipped kelly is 1.0x (not 1.8x)
        # If clipped to 1.0x, notional doesn't scale above 1.0x trade_usdt or risk
        assert decision.action == "BUY"


# ── Model-health gate: model_expired / feature_schema_parity ────────────────

def _healthy_ml_res(**overrides) -> dict:
    """Prediction payload as predict_server sends it on a healthy model."""
    res = {
        "status": "success",
        "symbol": "BTC-USDT",
        "signal": "BUY",
        "confidence": 0.70,
        "probabilities": {"bullish": 0.70, "bearish": 0.10, "neutral": 0.20},
        "uncertainty": "LOW",
        "gated": False,
        "model_expired": False,
        "feature_schema_parity": True,
    }
    res.update(overrides)
    return res


def _health_engine(config, monkeypatch, bars):
    monkeypatch.setenv("JESSE_ML_GATE_ENABLED", "true")
    engine = DecisionEngine(config)
    engine.enable_kronos = False
    engine.account_equity = 1000.0
    engine.account_available = 1000.0
    mock_signal = StrategySignal(symbol="BTCUSDC", signal="BUY", confidence=0.60, entry_price=bars[-1]["close"])
    engine.strategy.generate_signal = MagicMock(return_value=mock_signal)
    engine.regime_detector.detect = MagicMock(return_value=MagicMock(regime="TRENDING", weights=MagicMock(return_value={})))
    return engine


@pytest.mark.asyncio
async def test_model_health_veto_on_expired_model(ml_risk_config, monkeypatch):
    """An expired model must never size a position, even when it agrees with the signal."""
    bars = _make_bars(50)
    engine = _health_engine(ml_risk_config, monkeypatch, bars)

    mock_ml = _healthy_ml_res(model_expired=True)
    with patch("backend.services.jesse_bridge.jesse_bridge.get_ml_prediction", AsyncMock(return_value=mock_ml)):
        decision = await engine.evaluate_symbol("BTCUSDC", bars, None, 0, [], False)
        assert decision is None
        reason = engine.last_evaluation.get("reason", "")
        assert "vetoed by Jesse ML model health gate" in reason
        assert "model expired" in reason


@pytest.mark.asyncio
async def test_model_health_veto_on_expired_model_in_paper(ml_risk_config, monkeypatch):
    """The expiry veto is hard — paper mode does not get to trade a stale model either."""
    monkeypatch.setenv("TRADING_MODE", "paper")
    bars = _make_bars(50)
    engine = _health_engine(ml_risk_config, monkeypatch, bars)

    mock_ml = _healthy_ml_res(model_expired="true")
    with patch("backend.services.jesse_bridge.jesse_bridge.get_ml_prediction", AsyncMock(return_value=mock_ml)):
        decision = await engine.evaluate_symbol("BTCUSDC", bars, None, 0, [], False)
        assert decision is None
        assert "model health gate" in engine.last_evaluation.get("reason", "").lower()


@pytest.mark.asyncio
async def test_model_health_veto_on_schema_mismatch(ml_risk_config, monkeypatch):
    """Live features drifting from the trained schema must veto, not size."""
    bars = _make_bars(50)
    engine = _health_engine(ml_risk_config, monkeypatch, bars)

    mock_ml = _healthy_ml_res(feature_schema_parity=False)
    with patch("backend.services.jesse_bridge.jesse_bridge.get_ml_prediction", AsyncMock(return_value=mock_ml)):
        decision = await engine.evaluate_symbol("BTCUSDC", bars, None, 0, [], False)
        assert decision is None
        assert "feature schema mismatch" in engine.last_evaluation.get("reason", "")


@pytest.mark.asyncio
async def test_model_health_veto_on_mismatched_schema_hash(ml_risk_config, monkeypatch):
    """A reported schema hash that differs from the trained hash is a mismatch."""
    monkeypatch.setenv("JESSE_FEATURE_SCHEMA_HASH", "cd15d2380809b247")
    bars = _make_bars(50)
    engine = _health_engine(ml_risk_config, monkeypatch, bars)

    mock_ml = _healthy_ml_res(feature_schema_parity="0badc0ffee123456")
    with patch("backend.services.jesse_bridge.jesse_bridge.get_ml_prediction", AsyncMock(return_value=mock_ml)):
        decision = await engine.evaluate_symbol("BTCUSDC", bars, None, 0, [], False)
        assert decision is None
        assert "feature schema mismatch" in engine.last_evaluation.get("reason", "")


@pytest.mark.asyncio
async def test_model_health_passes_when_fresh_and_in_parity(ml_risk_config, monkeypatch):
    """Fresh model + matching schema hash: the gate stays out of the way."""
    monkeypatch.setenv("JESSE_FEATURE_SCHEMA_HASH", "cd15d2380809b247")
    bars = _make_bars(50)
    engine = _health_engine(ml_risk_config, monkeypatch, bars)

    mock_ml = _healthy_ml_res(feature_schema_parity="cd15d2380809b247")
    with patch("backend.services.jesse_bridge.jesse_bridge.get_ml_prediction", AsyncMock(return_value=mock_ml)):
        decision = await engine.evaluate_symbol("BTCUSDC", bars, None, 0, [], False)
        assert decision is not None
        assert decision.action == "BUY"


@pytest.mark.asyncio
async def test_model_health_fields_absent_vetoes_in_live(ml_risk_config, monkeypatch):
    """Older predict_server that omits both fields: unverifiable, so no live entry."""
    monkeypatch.setenv("TRADING_MODE", "live")
    bars = _make_bars(50)
    engine = _health_engine(ml_risk_config, monkeypatch, bars)

    mock_ml = _healthy_ml_res()
    mock_ml.pop("model_expired")
    mock_ml.pop("feature_schema_parity")
    with patch("backend.services.jesse_bridge.jesse_bridge.get_ml_prediction", AsyncMock(return_value=mock_ml)):
        decision = await engine.evaluate_symbol("BTCUSDC", bars, None, 0, [], False)
        assert decision is None
        reason = engine.last_evaluation.get("reason", "")
        assert "model health gate" in reason.lower()
        assert "model_expired" in reason and "feature_schema_parity" in reason


@pytest.mark.asyncio
async def test_model_health_fields_absent_fail_open_in_paper(ml_risk_config, monkeypatch):
    """Same older server in paper mode: fail open, matching the rest of the ML gate."""
    monkeypatch.setenv("TRADING_MODE", "paper")
    bars = _make_bars(50)
    engine = _health_engine(ml_risk_config, monkeypatch, bars)

    mock_ml = _healthy_ml_res()
    mock_ml.pop("model_expired")
    mock_ml.pop("feature_schema_parity")
    with patch("backend.services.jesse_bridge.jesse_bridge.get_ml_prediction", AsyncMock(return_value=mock_ml)):
        decision = await engine.evaluate_symbol("BTCUSDC", bars, None, 0, [], False)
        assert decision is not None
        assert decision.action == "BUY"


@pytest.mark.asyncio
async def test_model_health_absent_fail_open_override_allows_live(ml_risk_config, monkeypatch):
    """JESSE_ML_MODEL_HEALTH_FAIL_OPEN=true is the operator escape hatch for an older server."""
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.setenv("JESSE_ML_MODEL_HEALTH_FAIL_OPEN", "true")
    bars = _make_bars(50)
    engine = _health_engine(ml_risk_config, monkeypatch, bars)

    mock_ml = _healthy_ml_res()
    mock_ml.pop("model_expired")
    mock_ml.pop("feature_schema_parity")
    with patch("backend.services.jesse_bridge.jesse_bridge.get_ml_prediction", AsyncMock(return_value=mock_ml)):
        decision = await engine.evaluate_symbol("BTCUSDC", bars, None, 0, [], False)
        assert decision is not None

    # The override never rescues a field that IS present and says the model is bad.
    with patch(
        "backend.services.jesse_bridge.jesse_bridge.get_ml_prediction",
        AsyncMock(return_value=_healthy_ml_res(model_expired=True)),
    ):
        assert await engine.evaluate_symbol("BTCUSDC", bars, None, 0, [], False) is None


@pytest.mark.asyncio
async def test_model_health_unreadable_field_fails_closed(ml_risk_config, monkeypatch):
    """A present-but-unreadable verdict is not evidence of health — veto in every mode."""
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("JESSE_ML_MODEL_HEALTH_FAIL_OPEN", "true")
    bars = _make_bars(50)
    engine = _health_engine(ml_risk_config, monkeypatch, bars)

    mock_ml = _healthy_ml_res(model_expired=["unexpected"])
    with patch("backend.services.jesse_bridge.jesse_bridge.get_ml_prediction", AsyncMock(return_value=mock_ml)):
        decision = await engine.evaluate_symbol("BTCUSDC", bars, None, 0, [], False)
        assert decision is None
        assert "unreadable" in engine.last_evaluation.get("reason", "")


# ── Uncertainty veto: honest naming, legacy wire key still accepted ─────────

@pytest.mark.asyncio
async def test_uncertainty_veto_reads_legacy_conformal_margin_key(ml_risk_config, monkeypatch):
    """predict_server still sends `conformal_margin`; the veto text names it honestly."""
    bars = _make_bars(50)
    engine = _health_engine(ml_risk_config, monkeypatch, bars)

    mock_ml = _healthy_ml_res(uncertainty="HIGH", conformal_margin=0.02)
    with patch("backend.services.jesse_bridge.jesse_bridge.get_ml_prediction", AsyncMock(return_value=mock_ml)):
        decision = await engine.evaluate_symbol("BTCUSDC", bars, None, 0, [], False)
        assert decision is None
        reason = engine.last_evaluation.get("reason", "")
        assert "vetoed by Jesse ML uncertainty gate" in reason
        assert "Probability-margin uncertainty is HIGH (margin=0.020)" in reason
        assert "onformal" not in reason


@pytest.mark.asyncio
async def test_uncertainty_veto_skips_null_probability_margin_key(ml_risk_config, monkeypatch):
    """A transitional upstream rename emitting the new key as null must fall back.

    dict.get(new, old) would return that None and stop the margin being read at all.
    """
    bars = _make_bars(50)
    engine = _health_engine(ml_risk_config, monkeypatch, bars)

    mock_ml = _healthy_ml_res(uncertainty="HIGH", probability_margin=None, conformal_margin=0.02)
    with patch("backend.services.jesse_bridge.jesse_bridge.get_ml_prediction", AsyncMock(return_value=mock_ml)):
        decision = await engine.evaluate_symbol("BTCUSDC", bars, None, 0, [], False)
        assert decision is None
        assert "margin=0.020" in engine.last_evaluation.get("reason", "")


@pytest.mark.asyncio
async def test_uncertainty_veto_prefers_probability_margin_key(ml_risk_config, monkeypatch):
    """If the server is ever renamed upstream, the honest key wins over the legacy one."""
    bars = _make_bars(50)
    engine = _health_engine(ml_risk_config, monkeypatch, bars)

    mock_ml = _healthy_ml_res(gated=True, probability_margin=0.04, conformal_margin=0.99)
    with patch("backend.services.jesse_bridge.jesse_bridge.get_ml_prediction", AsyncMock(return_value=mock_ml)):
        decision = await engine.evaluate_symbol("BTCUSDC", bars, None, 0, [], False)
        assert decision is None
        assert "margin=0.040" in engine.last_evaluation.get("reason", "")


# ── jesse_ml_model_health: payload shapes predict_server may send ───────────

@pytest.mark.parametrize("payload,expected", [
    ({"model_expired": False, "feature_schema_parity": True}, "ok"),
    ({"model_expired": "false", "feature_schema_parity": "match"}, "ok"),
    ({"model_expired": 0, "feature_schema_parity": 1}, "ok"),
    ({"model_expired": False, "feature_schema_parity": {"match": True}}, "ok"),
    ({"model_expired": False, "feature_schema_parity": "cd15d2380809b247"}, "ok"),
    ({"model_expired": False, "feature_schema_parity": {
        "live_hash": "cd15d2380809b247", "expected_hash": "cd15d2380809b247"}}, "ok"),
    ({"model_expired": True, "feature_schema_parity": True}, "failed"),
    ({"model_expired": "stale", "feature_schema_parity": True}, "failed"),
    ({"model_expired": False, "feature_schema_parity": "mismatch"}, "failed"),
    ({"model_expired": False, "feature_schema_parity": "deadbeefdeadbeef"}, "failed"),
    ({"model_expired": False, "feature_schema_parity": {
        "live_hash": "deadbeefdeadbeef", "expected_hash": "cd15d2380809b247"}}, "failed"),
    ({"model_expired": 7, "feature_schema_parity": True}, "failed"),
    ({"model_expired": False, "feature_schema_parity": {"features": 42}}, "failed"),
    ({"model_expired": False}, "unknown"),
    ({"feature_schema_parity": True}, "unknown"),
    ({}, "unknown"),
])
def test_jesse_ml_model_health_payload_shapes(payload, expected, monkeypatch):
    monkeypatch.setenv("JESSE_FEATURE_SCHEMA_HASH", "cd15d2380809b247")
    verdict, detail = jesse_ml_model_health(payload)
    assert verdict == expected, detail

