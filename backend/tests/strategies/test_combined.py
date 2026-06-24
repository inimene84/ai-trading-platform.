from unittest.mock import MagicMock
from backend.strategies.combined import CombinedStrategy
from backend.strategies.base import StrategySignal

def test_combined_strategy_trending_regime():
    """Test that in a TRENDING regime, a strong trend signal overrides others."""
    strategy = CombinedStrategy()
    
    # Mock underlying strategies
    strategy._trend.generate_signal = MagicMock(return_value=StrategySignal(
        symbol="BTCUSDT", signal="BUY", confidence=0.70, entry_price=1000, strategy="trend_following"
    ))
    strategy._mean.generate_signal = MagicMock(return_value=StrategySignal(
        symbol="BTCUSDT", signal="SELL", confidence=0.90, entry_price=1000, strategy="mean_reversion"
    ))
    strategy._break.generate_signal = MagicMock(return_value=StrategySignal(
        symbol="BTCUSDT", signal="SELL", confidence=0.50, entry_price=1000, strategy="breakout"
    ))

    # Even though mean reversion has 0.90 SELL confidence, TRENDING regime priority
    # favors trend_following which has 0.70 (> 0.52 threshold).
    bars = [{"close": 1000}]
    result = strategy.generate_signal("BTCUSDT", bars, regime="TRENDING")
    
    assert result.signal == "BUY"
    assert result.confidence == 0.70
    assert "STRONG[TRENDING] trend_following" in result.reasoning

def test_combined_strategy_ranging_regime():
    """Test that in a RANGING regime, mean reversion takes priority."""
    strategy = CombinedStrategy()
    
    strategy._trend.generate_signal = MagicMock(return_value=StrategySignal(
        symbol="BTCUSDT", signal="BUY", confidence=0.70, entry_price=1000, strategy="trend_following"
    ))
    strategy._mean.generate_signal = MagicMock(return_value=StrategySignal(
        symbol="BTCUSDT", signal="SELL", confidence=0.60, entry_price=1000, strategy="mean_reversion"
    ))
    strategy._break.generate_signal = MagicMock(return_value=StrategySignal(
        symbol="BTCUSDT", signal="BUY", confidence=0.50, entry_price=1000, strategy="breakout"
    ))

    # In RANGING regime, mean reversion has priority. Its threshold is 0.78, so 0.60 is NOT strong.
    # Trend following has 0.70, but the threshold for RANGING is 0.78, so it is NOT strong.
    # It falls back to consensus. 
    # BUY score = w_trend(0.15)*0.70 + w_break(0.25)*0.50 = 0.105 + 0.125 = 0.23
    # SELL score = w_mean(0.60)*0.60 = 0.36
    # SELL score > BUY score, and > 0.20 consensus threshold.
    bars = [{"close": 1000}]
    weights = {"trend_following": 0.15, "mean_reversion": 0.60, "breakout": 0.25}
    result = strategy.generate_signal("BTCUSDT", bars, regime="RANGING", regime_weights=weights)
    
    assert result.signal == "SELL"
    assert "CONSENSUS[RANGING]" in result.reasoning

def test_combined_strategy_volatile_regime():
    """Test that in a VOLATILE regime, breakout strategy has priority."""
    strategy = CombinedStrategy()
    
    strategy._trend.generate_signal = MagicMock(return_value=StrategySignal(
        symbol="BTCUSDT", signal="NEUTRAL", confidence=0.0, entry_price=1000, strategy="trend_following"
    ))
    strategy._mean.generate_signal = MagicMock(return_value=StrategySignal(
        symbol="BTCUSDT", signal="SELL", confidence=0.90, entry_price=1000, strategy="mean_reversion"
    ))
    strategy._break.generate_signal = MagicMock(return_value=StrategySignal(
        symbol="BTCUSDT", signal="BUY", confidence=0.70, entry_price=1000, strategy="breakout"
    ))

    # In VOLATILE, breakout has threshold 0.62. 0.70 > 0.62, so it overrides the 0.90 mean reversion!
    bars = [{"close": 1000}]
    result = strategy.generate_signal("BTCUSDT", bars, regime="VOLATILE")
    
    assert result.signal == "BUY"
    assert result.confidence == 0.70
    assert "STRONG[VOLATILE] breakout" in result.reasoning

def test_combined_strategy_consensus_boost():
    """Test that multiple strategies agreeing boosts the confidence."""
    strategy = CombinedStrategy()
    
    strategy._trend.generate_signal = MagicMock(return_value=StrategySignal(
        symbol="BTCUSDT", signal="BUY", confidence=0.45, entry_price=1000, strategy="trend_following"
    ))
    strategy._mean.generate_signal = MagicMock(return_value=StrategySignal(
        symbol="BTCUSDT", signal="BUY", confidence=0.45, entry_price=1000, strategy="mean_reversion"
    ))
    strategy._break.generate_signal = MagicMock(return_value=StrategySignal(
        symbol="BTCUSDT", signal="BUY", confidence=0.45, entry_price=1000, strategy="breakout"
    ))

    # No single signal >= 0.55 (UNKNOWN regime threshold).
    # Consensus: BUY score = 0.40*0.45 + 0.30*0.45 + 0.30*0.45 = 0.45.
    # 3 strategies agree, so boost = 1.5. Conf = 0.45 * 1.5 = 0.675
    bars = [{"close": 1000}]
    result = strategy.generate_signal("BTCUSDT", bars, regime="UNKNOWN")
    
    assert result.signal == "BUY"
    assert result.confidence > 0.67
    assert "CONSENSUS[UNKNOWN](3)" in result.reasoning
