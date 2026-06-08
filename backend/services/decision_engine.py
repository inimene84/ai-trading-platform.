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


def compute_sl_tp_levels(
    bars: List[Dict[str, Any]],
    direction: str,
    entry_price: float,
    config: RiskConfig,
    signal_sl: Optional[float] = None,
    signal_tp: Optional[float] = None,
) -> tuple[float, float]:
    """ATR-based stop-loss and take-profit for an entry (shared by loop + manual orders)."""
    try:
        highs = np.array([b["high"] for b in bars[-15:]])
        lows = np.array([b["low"] for b in bars[-15:]])
        closes = np.array([b["close"] for b in bars[-16:-1]])
        tr = np.maximum(np.maximum(highs - lows, np.abs(highs - closes)), np.abs(lows - closes))
        atr = float(np.mean(tr))
    except Exception:
        atr = entry_price * 0.02

    if direction == "BUY":
        sl = signal_sl if signal_sl else (entry_price - (atr * config.sl_atr_mult))
        tp = signal_tp if signal_tp else (entry_price + (atr * config.tp_atr_mult))
        sl = min(sl, entry_price - (atr * config.sl_atr_mult))
        tp = max(tp, entry_price + (atr * config.tp_atr_mult))
    else:
        sl = signal_sl if signal_sl else (entry_price + (atr * config.sl_atr_mult))
        tp = signal_tp if signal_tp else (entry_price - (atr * config.tp_atr_mult))
        sl = max(sl, entry_price + (atr * config.sl_atr_mult))
        tp = min(tp, entry_price - (atr * config.tp_atr_mult))
    return sl, tp


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
        # Snapshot of the most recent evaluation so the loop can persist a
        # signal row for EVERY symbol it scans (not just executed trades).
        self.last_evaluation: Dict[str, Any] = {}
        # Live account equity (set by the loop each cycle) for risk-based sizing.
        # 0 → fall back to fixed trade_usdt_amount notional.
        self.account_equity: float = 0.0

    def _record_eval(self, symbol, direction, confidence, reason, entry=None, sl=None, tp=None, executed=False):
        self.last_evaluation = {
            "symbol": symbol,
            "direction": (direction or "HOLD").upper(),
            "confidence": float(confidence or 0.0),
            "reason": reason,
            "entry_price": entry,
            "stop_loss": sl,
            "take_profit": tp,
            "executed": executed,
        }

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
            self._record_eval(symbol, "HOLD", 0.0, "insufficient bars")
            return None
            
        current_price = bars[-1]["close"]
        self._record_eval(symbol, "HOLD", 0.0, "evaluating")
        
        # 1. Active position logic
        if existing_position:
            # Check pyramid
            if self.config.pyramid_mode:
                if len(pyramid_layers) < self.config.pyramid_max_layers:
                    # Strategy signal for pyramid
                    regime_result = self.regime_detector.detect(bars)
                    signal = self.strategy.generate_signal(
                        symbol,
                        bars,
                        regime=regime_result.regime,
                        regime_weights=regime_result.weights()
                    )
                    if signal and signal.signal == existing_position.direction and signal.confidence >= self.config.min_signal_strength:
                        # Pyramid conditions met
                        return self._create_entry_decision(symbol, bars, signal, existing_position.direction, is_pyramid=True)
            return None

        # 2. Cooldown check
        if cooldown_active:
            return None

        # 3. Strategy execution — detect regime and generate signal.
        #    Runs FIRST so the expensive Kronos/opinion calls below are only
        #    paid when there's an actual directional setup (most pairs are
        #    NEUTRAL and short-circuit here).
        regime_result = self.regime_detector.detect(bars)
        signal = self.strategy.generate_signal(
            symbol,
            bars,
            regime=regime_result.regime,
            regime_weights=regime_result.weights()
        )
        if not signal or signal.signal not in ["BUY", "SELL"] or signal.confidence < self.config.min_signal_strength:
            self._record_eval(
                symbol,
                signal.signal if signal else "HOLD",
                signal.confidence if signal else 0.0,
                f"strategy below threshold ({self.config.min_signal_strength})",
            )
            return None

        # 4. Kronos foundation-model prediction (only for directional signals)
        kronos_result = {}
        if self.enable_kronos:
            try:
                import pandas as pd
                df = pd.DataFrame(bars)
                kronos_result = await kronos_service.predict(df, symbol)
            except Exception as e:
                logger.warning(f"Kronos prediction failed for {symbol}: {e}")
                kronos_result = {}

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
                if opinion:
                    self._record_eval(symbol, opinion.direction, opinion.confidence,
                                      "AI opinion evaluated")
                if opinion and opinion.confidence < self.config.ai_analysis_threshold:
                    # Strategy can override weak AI if its own confidence is high enough
                    if signal.confidence > (self.config.ai_analysis_threshold + self.config.opinion_override_margin):
                        logger.info(f"[{symbol}] Strategy overrides weak AI opinion (strategy conf={signal.confidence:.2f})")
                    else:
                        logger.info(
                            f"[{symbol}] AI opinion too weak (conf={opinion.confidence:.2f} < {self.config.ai_analysis_threshold}), skipping"
                        )
                        self._record_eval(symbol, opinion.direction, opinion.confidence,
                                          f"AI opinion too weak (<{self.config.ai_analysis_threshold})")
                        return None
            except Exception as e:
                logger.warning(f"[{symbol}] Opinion layer error: {e}")
                # Fallback: require higher strategy confidence if opinion layer fails
                if signal.confidence < self.config.min_signal_strength + 0.1:
                    return None

        # 6. Max positions check
        if open_count >= self.config.max_positions:
            self._record_eval(symbol, signal.signal, signal.confidence,
                              f"max positions reached ({self.config.max_positions})")
            return None
            
        # 7. Create Decision
        decision = self._create_entry_decision(symbol, bars, signal, signal.signal, is_pyramid=False)
        if decision:
            self._record_eval(symbol, decision.action, decision.confidence, "entry decision",
                              entry=decision.entry_price, sl=decision.stop_loss,
                              tp=decision.take_profit, executed=True)
        return decision

    def _create_entry_decision(self, symbol: str, bars: List[Dict[str, Any]], signal: Any, direction: str, is_pyramid: bool) -> Optional[Decision]:
        current_price = bars[-1]["close"]
        entry_price = signal.entry_price or current_price

        # SL/TP first — needed for risk-based position sizing.
        sl, tp = compute_sl_tp_levels(
            bars, direction, entry_price, self.config,
            signal_sl=signal.stop_loss, signal_tp=signal.take_profit,
        )

        # ── Position sizing ──
        # Risk-based: size so a SL hit costs ~risk_per_trade_pct of equity.
        # Falls back to the fixed trade_usdt_amount notional when equity or the
        # SL distance aren't usable. Pyramid layers keep their fixed notional.
        trade_usdt = self.config.pyramid_usdt_per_layer if is_pyramid else self.config.trade_usdt_amount
        notional = trade_usdt
        if (not is_pyramid and getattr(self.config, "equity_sizing_enabled", False)
                and self.account_equity > 0 and sl and entry_price > 0):
            per_unit_risk = abs(entry_price - sl)
            if per_unit_risk > 0:
                risk_amount = self.account_equity * self.config.risk_per_trade_pct
                qty_by_risk = risk_amount / per_unit_risk
                notional = qty_by_risk * entry_price
                # Cap per-trade notional at a multiple of equity (post-leverage)
                max_notional = self.account_equity * self.config.max_trade_notional_equity_mult
                notional = max(trade_usdt, min(notional, max_notional))
        # Floor at Binance MIN_NOTIONAL ($20 for most symbols, $100 for BTC)
        _bn_min = 100.0 if 'BTC' in symbol else 20.0
        notional = max(notional, _bn_min)
        quantity = notional / entry_price if entry_price > 0 else 0

        # Min-edge / fee-churn gate: reject trades whose TP can't clear cost.
        if not self._passes_min_edge(symbol, entry_price, tp, quantity):
            return None

        return Decision(
            action=direction,
            symbol=symbol,
            quantity=quantity,
            entry_price=entry_price,
            stop_loss=sl,
            take_profit=tp,
            confidence=signal.confidence,
            reasoning=f"Regime: {self.regime_detector.detect(bars).regime if bars else 'N/A'}",
            is_pyramid=is_pyramid
        )

    def _passes_min_edge(self, symbol: str, entry_price: float, tp: float, quantity: float) -> bool:
        """Min-edge / fee-churn gate.

        A trade is only worth taking if its gross expected move to TP clears a
        multiple of the round-trip cost (fees + slippage). Tight-TP scratch
        entries that can never out-earn their own fees are rejected here.
        FAILS OPEN: any bad input / disabled config -> allow the trade.
        """
        try:
            mult = getattr(self.config, "min_edge_fee_mult", 0.0) or 0.0
            if mult <= 0:
                return True  # gate disabled
            if not entry_price or not quantity or tp is None:
                return True  # missing data -> don't block
            notional = entry_price * quantity
            if notional <= 0:
                return True
            gross_tp_profit = abs(tp - entry_price) * quantity
            roundtrip_cost = self.config.roundtrip_cost_rate * notional
            required = mult * roundtrip_cost
            if gross_tp_profit < required:
                logger.info(
                    f"  [ {symbol} ] SKIP (min-edge): TP profit ${gross_tp_profit:.4f} "
                    f"< {mult:.1f}x round-trip cost ${roundtrip_cost:.4f} "
                    f"(need >= ${required:.4f})"
                )
                return False
            return True
        except Exception as e:
            logger.warning(f"  [ {symbol} ] min-edge gate error (allowing trade): {e}")
            return True
