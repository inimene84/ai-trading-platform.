import os
import logging
import numpy as np
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from datetime import datetime, timezone

from backend.services.unified_trading import UnifiedOrder, OrderSide, OrderType
from backend.services.risk_config import RiskConfig
from backend.strategies.combined import CombinedStrategy
from backend.services.market_regime import MarketRegimeService
from backend.agents.opinion_layer import AgentOpinionLayer
from backend.services.kronos_gate import KronosGate

logger = logging.getLogger(__name__)

@dataclass
class Decision:
    action: str  # "BUY", "SELL", "HOLD", "CLOSE_LONG", "CLOSE_SHORT"
    symbol: str
    quantity: float
    entry_price: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    confidence: float = 0.0
    reasoning: str = ""
    is_pyramid: bool = False

class DecisionEngine:
    def __init__(self, risk_config: RiskConfig):
        self.config = risk_config
        self.strategy = CombinedStrategy()
        self.regime_service = MarketRegimeService()
        self.opinion_layer = AgentOpinionLayer() if os.getenv("ENABLE_PERSONAS", "true").lower() == "true" else None
        self.kronos = KronosGate()

    async def evaluate_symbol(
        self,
        symbol: str,
        bars: List[Dict[str, Any]],
        existing_position: Optional[Any],  # DB Trade object or dict
        open_count: int,
        pyramid_layers: List[float],
        cooldown_active: bool
    ) -> Optional[Decision]:
        """
        Evaluate market data and return a trading Decision.
        Does NOT execute trades or interact with the database.
        """
        if not bars or len(bars) < 50:
            return None
            
        current_price = bars[-1]["close"]
        
        # 1. Active position logic
        if existing_position:
            # Check pyramid
            if self.config.pyramid_mode:
                if len(pyramid_layers) < self.config.pyramid_max_layers:
                    # Strategy signal for pyramid
                    regime = self.regime_service.detect_regime(bars)
                    signal = self.strategy.generate_signal(symbol, bars, regime=regime)
                    if signal and signal.signal == existing_position.direction and signal.confidence >= self.config.min_signal_strength:
                        # Pyramid conditions met
                        return self._create_entry_decision(symbol, bars, signal, existing_position.direction, is_pyramid=True)
            return None

        # 2. Cooldown check
        if cooldown_active:
            return None
            
        # 3. Kronos Gate
        if not self.kronos.is_trade_allowed(symbol, "ALL"):
            return None

        # 4. Strategy execution
        regime = self.regime_service.detect_regime(bars)
        signal = self.strategy.generate_signal(symbol, bars, regime=regime)
        if not signal or signal.signal not in ["BUY", "SELL"] or signal.confidence < self.config.min_signal_strength:
            return None

        # 5. AI Opinion Layer
        if self.opinion_layer:
            opinion = await self.opinion_layer.evaluate_signal(signal, bars)
            if opinion and opinion.confidence < self.config.ai_analysis_threshold:
                # Override check
                if signal.confidence > (self.config.ai_analysis_threshold + self.config.opinion_override_margin):
                    pass # Strategy overrides weak AI
                else:
                    return None
            elif not opinion:
                # Fallback if opinion fails
                if signal.confidence < self.config.min_signal_strength + 0.1:
                    return None

        # 6. Max positions check
        if open_count >= self.config.max_positions:
            return None
            
        # 7. Create Decision
        return self._create_entry_decision(symbol, bars, signal, signal.signal, is_pyramid=False)

    def _create_entry_decision(self, symbol: str, bars: List[Dict[str, Any]], signal: Any, direction: str, is_pyramid: bool) -> Decision:
        current_price = bars[-1]["close"]
        entry_price = signal.entry_price or current_price
        
        # Quantity calculation
        trade_usdt = self.config.pyramid_usdt_per_layer if is_pyramid else self.config.trade_usdt_amount
        quantity = trade_usdt / entry_price if entry_price > 0 else 0
        
        # Dynamic ATR SL/TP
        try:
            highs = np.array([b["high"] for b in bars[-15:]])
            lows = np.array([b["low"] for b in bars[-15:]])
            closes = np.array([b["close"] for b in bars[-16:-1]])
            tr = np.maximum(np.maximum(highs - lows, np.abs(highs - closes)), np.abs(lows - closes))
            atr = np.mean(tr)
        except Exception:
            atr = entry_price * 0.02

        if direction == "BUY":
            sl = signal.stop_loss if signal.stop_loss else (entry_price - (atr * self.config.sl_atr_mult))
            tp = signal.take_profit if signal.take_profit else (entry_price + (atr * self.config.tp_atr_mult))
            sl = min(sl, entry_price - (atr * self.config.sl_atr_mult))
            tp = max(tp, entry_price + (atr * self.config.tp_atr_mult))
        else:
            sl = signal.stop_loss if signal.stop_loss else (entry_price + (atr * self.config.sl_atr_mult))
            tp = signal.take_profit if signal.take_profit else (entry_price - (atr * self.config.tp_atr_mult))
            sl = max(sl, entry_price + (atr * self.config.sl_atr_mult))
            tp = min(tp, entry_price - (atr * self.config.tp_atr_mult))

        return Decision(
            action=direction,
            symbol=symbol,
            quantity=quantity,
            entry_price=entry_price,
            stop_loss=sl,
            take_profit=tp,
            confidence=signal.confidence,
            is_pyramid=is_pyramid
        )
