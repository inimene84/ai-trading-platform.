"""USDT/USDC perp aliases, opposing-position flatten, and wrong-side stop guard."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.services.decision_engine import DecisionEngine
from backend.services.risk_config import RiskConfig
from backend.services.signal_candidate_engine import SignalCandidateEngine
from backend.services.symbol_aliases import (
    crypto_perp_base,
    first_matching_open,
    is_flatten_action,
    perp_alias_set,
    same_crypto_perp_leg,
    stop_on_correct_side,
)
from backend.services.trading_loop_helpers import TrailingStopManager
from backend.strategies.base import StrategySignal


def _bars(n: int = 60, base: float = 77000.0) -> list:
    bars = []
    for i in range(n):
        c = base + i * 0.5
        bars.append(
            {
                "open": c - 10,
                "high": c + 40,
                "low": c - 40,
                "close": c,
                "volume": 100.0,
            }
        )
    return bars


@pytest.fixture
def risk_config():
    return RiskConfig(
        min_signal_strength=0.58,
        pyramid_mode=False,
        enable_jesse_ml=False,
        use_risk_reviewer_llm=False,
        enable_personas=False,
        max_binance_positions=10,
        min_edge_fee_mult=0.0,
    )


def test_usdt_usdc_are_same_btc_leg():
    assert crypto_perp_base("BTCUSDT") == "BTC"
    assert crypto_perp_base("BTCUSDC") == "BTC"
    assert same_crypto_perp_leg("BTCUSDT", "BTCUSDC")
    assert same_crypto_perp_leg("ETHUSDT", "ETH-USD") is False  # no USDT/USDC suffix after strip? ETH-USD → ETHUSD
    assert "BTCUSDC" in perp_alias_set("BTCUSDT")
    assert not same_crypto_perp_leg("BTCUSDT", "ETHUSDT")
    assert not same_crypto_perp_leg("USDCAD", "USDCHF")
    assert same_crypto_perp_leg("USDCAD", "USDCAD")


def test_first_matching_open_prefers_exact_then_alias():
    rows = [
        SimpleNamespace(symbol="ETHUSDT", direction="BUY"),
        SimpleNamespace(symbol="BTCUSDT", direction="BUY"),
    ]
    hit = first_matching_open(rows, "BTCUSDC")
    assert hit.symbol == "BTCUSDT"


def test_stop_on_correct_side_rejects_avax_style_short():
    # Short mark 7.462, DB SL 7.459 is below mark — would stop them out.
    assert stop_on_correct_side("SELL", 7.459, mark=7.462, entry=7.482) is False
    assert stop_on_correct_side("SELL", 7.579, mark=7.462, entry=7.482) is True
    assert stop_on_correct_side("BUY", 75000.4, mark=77338.0, entry=77320.0) is True
    assert stop_on_correct_side("BUY", 78000.0, mark=77338.0, entry=77320.0) is False


@pytest.mark.asyncio
async def test_opposing_usdc_sell_flattens_usdt_long(risk_config):
    engine = DecisionEngine(risk_config)
    engine.enable_kronos = False
    engine.enable_jesse_ml = False
    engine.config.enable_personas = False
    engine.config.use_risk_reviewer_llm = False
    bars = _bars()
    existing = SimpleNamespace(
        direction="BUY", quantity=0.01, symbol="BTCUSDT", entry_price=77320.0
    )
    signal = StrategySignal(
        symbol="BTCUSDC",
        signal="SELL",
        confidence=0.85,
        entry_price=bars[-1]["close"],
        strategy="combined",
    )
    engine.strategy.generate_signal = MagicMock(return_value=signal)
    engine.regime_detector.detect = MagicMock(
        return_value=MagicMock(regime="TRENDING", weights=MagicMock(return_value={}))
    )

    result = await engine.evaluate_symbol(
        "BTCUSDC",
        bars,
        existing,
        open_count=10,
        pyramid_layers=[],
        cooldown_active=False,
    )
    assert result is not None
    assert is_flatten_action(result.action)
    assert result.action == "CLOSE_LONG"
    assert result.symbol == "BTCUSDT"
    assert result.quantity == 0.01
    assert result.confidence == pytest.approx(0.85)
    assert engine.last_evaluation["reason"] == "opposing flatten"


@pytest.mark.asyncio
async def test_same_direction_does_not_flatten(risk_config):
    engine = DecisionEngine(risk_config)
    engine.enable_kronos = False
    engine.config.enable_personas = False
    engine.config.use_risk_reviewer_llm = False
    bars = _bars()
    existing = SimpleNamespace(
        direction="BUY", quantity=0.01, symbol="BTCUSDT", entry_price=77320.0
    )
    signal = StrategySignal(
        symbol="BTCUSDC",
        signal="BUY",
        confidence=0.85,
        entry_price=bars[-1]["close"],
        strategy="combined",
    )
    engine.strategy.generate_signal = MagicMock(return_value=signal)
    engine.regime_detector.detect = MagicMock(
        return_value=MagicMock(regime="TRENDING", weights=MagicMock(return_value={}))
    )

    result = await engine.evaluate_symbol(
        "BTCUSDC", bars, existing, 10, [], False
    )
    assert result is None
    assert "same-direction" in (engine.last_evaluation.get("reason") or "")


def test_admission_aliases_same_direction_and_one_per_leg():
    engine = SignalCandidateEngine()
    with patch.object(
        engine,
        "_open_position_keys",
        return_value={("BTCUSDT", "BUY")},
    ):
        assert engine._has_open_position("binance_futures", "BTCUSDC", "BUY") is True
        assert engine._has_open_position("binance_futures", "BTCUSDC", "SELL") is False
        assert engine._has_open_position("binance_futures", "ETHUSDT", "BUY") is False

    with patch.object(
        engine,
        "_try_open_binance_positions",
        return_value=[{"symbol": "BTCUSDT", "side": "BUY", "quantity": 0.01}],
    ):
        engine.execution_config["one_position_per_symbol"] = True
        engine.execution_config["max_binance_positions"] = 20
        reason = engine._binance_position_cap_breach("BTCUSDC")
        assert reason is not None
        assert "BTCUSDT" in reason
        assert engine._binance_position_cap_breach("ETHUSDT") is None


def test_sync_exchange_stop_dual_live_binance_row(monkeypatch):
    monkeypatch.setenv("ACTIVE_BROKER", "ctrader")
    trade = SimpleNamespace(
        symbol="AVAXUSDT",
        direction="SELL",
        quantity=46.0,
        entry_price=7.482,
        broker="binance_futures",
        broker_position_id="AVAXUSDT:SHORT",
    )
    broker = MagicMock()
    broker.replace_stop_loss.return_value = {"status": "replaced"}

    with patch(
        "backend.services.trading_loop_helpers.live_binance_orders_allowed",
        return_value=True,
    ), patch(
        "backend.services.trading_loop_helpers.is_ctrader_trade",
        return_value=False,
    ):
        TrailingStopManager._sync_exchange_stop(trade, 7.579, broker, mark=7.462)

    broker.replace_stop_loss.assert_called_once()
    assert broker.replace_stop_loss.call_args.kwargs["new_stop_price"] == 7.579


def test_sync_exchange_stop_refuses_wrong_side_short(monkeypatch):
    monkeypatch.setenv("ACTIVE_BROKER", "ctrader")
    trade = SimpleNamespace(
        symbol="AVAXUSDT",
        direction="SELL",
        quantity=46.0,
        entry_price=7.482,
        broker="binance_futures",
        broker_position_id="AVAXUSDT:SHORT",
    )
    broker = MagicMock()

    with patch(
        "backend.services.trading_loop_helpers.live_binance_orders_allowed",
        return_value=True,
    ), patch(
        "backend.services.trading_loop_helpers.is_ctrader_trade",
        return_value=False,
    ):
        TrailingStopManager._sync_exchange_stop(trade, 7.459, broker, mark=7.462)

    broker.replace_stop_loss.assert_not_called()


def test_trail_does_not_write_wrong_side_short_stop():
    cfg = RiskConfig(
        trailing_stop_enabled=True,
        native_trailing_enabled=False,
        step_trail_enabled=True,
        trail_activation_atr=0.5,
        trail_atr_mult=0.5,
    )
    bars = _bars(20, base=7.48)
    for b in bars:
        b["high"] = 7.50
        b["low"] = 7.40
        b["close"] = 7.462
    trade = MagicMock()
    trade.id = 10127
    trade.symbol = "AVAXUSDT"
    trade.direction = "SELL"
    trade.entry_price = 7.482
    trade.stop_loss = 7.579
    trade.quantity = 46.0
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [trade]
    high_water = {10127: 7.40}

    with patch.object(TrailingStopManager, "_sync_exchange_stop") as sync:
        TrailingStopManager.apply_trailing_stop(
            db, "AVAXUSDT", bars, high_water, cfg, MagicMock()
        )

    # Step-trail BE at entry-fee (~7.46) is at/below mark — must not land in DB.
    assert trade.stop_loss == 7.579
    sync.assert_not_called()
