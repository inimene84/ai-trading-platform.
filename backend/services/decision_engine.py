import os
import logging
import numpy as np
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from datetime import datetime, timezone

from backend.services.unified_trading import UnifiedOrder, OrderSide, OrderType
from backend.services.risk_config import RiskConfig
from backend.strategies.combined import CombinedStrategy
from backend.strategies.market_regime import MarketRegimeDetector
from backend.services.opinion_layer import analyze_symbol as opinion_analyze
from backend.services.kronos_gate import apply_kronos_gate
from backend.services import kronos_service

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
        self.regime_detector = MarketRegimeDetector()
        self.enable_personas = os.getenv("ENABLE_PERSONAS", "true").lower() == "true"
        self.enable_kronos = os.getenv("ENABLE_KRONOS", "true").lower() == "true"

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
                    import pandas as pd
                    df = pd.DataFrame(bars)
                    regime = self.regime_detector.detect(df)
                    signal = self.strategy.generate_signal(symbol, bars, regime=regime)
                    if signal and signal.signal == existing_position.direction and signal.confidence >= self.config.min_signal_strength:
                        # Pyramid conditions met
                        return self._create_entry_decision(symbol, bars, signal, existing_position.direction, is_pyramid=True)
            return None

        # 2. Cooldown check
        if cooldown_active:
            return None
            
        # 3. Kronos Gate — pre-filter using foundation model prediction
        if self.enable_kronos:
            try:
                import pandas as pd
                df = pd.DataFrame(bars)
                kronos_result = await kronos_service.predict(df, symbol)
                # We'll use the gate result to potentially modify signal later
            except Exception as e:
                logger.warning(f"Kronos prediction failed for {symbol}: {e}")
                kronos_result = {}
        else:
            kronos_result = {}

        # 4. Strategy execution — detect regime and generate signal
        import pandas as pd
        df = pd.DataFrame(bars)
        regime = self.regime_detector.detect(df)
        signal = self.strategy.generate_signal(symbol, bars, regime=regime)
        if not signal or signal.signal not in ["BUY", "SELL"] or signal.confidence < self.config.min_signal_strength:
            return None

        # 4b. Apply Kronos Gate to the strategy signal
        if kronos_result:
            gate_result = apply_kronos_gate(
                strategy_signal=signal.signal,
                strategy_confidence=signal.confidence,
                kronos_result=kronos_result,
                symbol=symbol,
            )
            if gate_result.action == "veto":
                logger.info(f"[{symbol}] Kronos VETOED signal: {gate_result.reasoning}")
                return None
            elif gate_result.action == "flip":
                logger.info(f"[{symbol}] Kronos FLIPPED signal: {gate_result.reasoning}")
                # Update signal direction and confidence from gate
                signal.signal = gate_result.final_signal
                signal.confidence = gate_result.confidence
            elif gate_result.action == "boost":
                logger.info(f"[{symbol}] Kronos BOOSTED signal: {gate_result.reasoning}")
                signal.confidence = gate_result.confidence
            elif gate_result.action == "dampen":
                logger.info(f"[{symbol}] Kronos DAMPENED signal: {gate_result.reasoning}")
                signal.confidence = gate_result.confidence

        # Re-check after gate modification
        if signal.signal not in ["BUY", "SELL"] or signal.confidence < self.config.min_signal_strength:
            return None

        # 5. AI Opinion Layer — multi-agent weighted consensus
        if self.enable_personas:
            try:
                opinion = await opinion_analyze(
                    symbol=symbol,
                    bars=bars,
                    include_kronos=True,
                    include_social=True,
                    include_alerts=True,
                    include_personas=True,
                )
                if opinion and opinion.confidence < self.config.ai_analysis_threshold:
                    # Strategy can override weak AI if its own confidence is high enough
                    if signal.confidence > (self.config.ai_analysis_threshold + self.config.opinion_override_margin):
                        logger.info(f"[{symbol}] Strategy overrides weak AI opinion (strategy conf={signal.confidence:.2f})")
                    else:
                        logger.info(
                            f"[{symbol}] AI opinion too weak (conf={opinion.confidence:.2f} < {self.config.ai_analysis_threshold}), skipping"
                        )
                        return None
            except Exception as e:
                logger.warning(f"[{symbol}] Opinion layer error: {e}")
                # Fallback: require higher strategy confidence if opinion layer fails
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
            reasoning=f"Regime: {self.regime_detector.detect(pd.DataFrame(bars)) if bars else 'N/A'}",
            is_pyramid=is_pyramid
        )
