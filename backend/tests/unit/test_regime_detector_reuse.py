"""Per-symbol MarketRegimeDetector injection: history survives across cycles."""

from backend.services.decision_engine import DecisionEngine
from backend.services.trading_loop import TradingLoopService
from backend.strategies.market_regime import MarketRegimeDetector


def test_detector_for_reuses_instance_per_symbol():
    loop = TradingLoopService()
    eth_a = loop._detector_for("ETHUSDT")
    eth_a._history.append("TRENDING")
    eth_b = loop._detector_for("ETHUSDT")
    btc = loop._detector_for("BTCUSDT")

    assert eth_b is eth_a
    assert eth_b._history == ["TRENDING"]
    assert btc is not eth_a
    assert btc._history == []


def test_injected_detector_is_the_one_decision_engine_uses():
    loop = TradingLoopService()
    engine = DecisionEngine(loop.risk_config)
    fresh = engine.regime_detector
    injected = loop._detector_for("SOLUSDT")
    engine.regime_detector = injected
    assert engine.regime_detector is injected
    assert engine.regime_detector is not fresh
    injected._history.append("RANGING")
    engine2 = DecisionEngine(loop.risk_config)
    engine2.regime_detector = loop._detector_for("SOLUSDT")
    assert engine2.regime_detector._history == ["RANGING"]


def test_fresh_market_regime_detector_starts_empty():
    assert MarketRegimeDetector()._history == []
