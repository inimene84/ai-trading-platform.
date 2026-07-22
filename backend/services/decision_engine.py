import os
import logging
from typing import Optional, Dict, Any, List, Never
from dataclasses import dataclass

from backend.services.risk_config import RiskConfig
from backend.services.trading_mode import TradingMode, get_trading_mode
from backend.strategies.combined import CombinedStrategy
from backend.strategies.market_regime import MarketRegimeDetector
from backend.services.opinion_layer import analyze_symbol as opinion_analyze
from backend.services.kronos_gate import apply_kronos_gate
from backend.services import kronos_service

logger = logging.getLogger(__name__)


def pyramid_price_improved(
    direction: str, current_price: float, last_layer_price: float, minimum: float,
) -> bool:
    """Pyramid only in the profitable direction by at least `minimum`."""
    if direction == "BUY":
        return current_price >= last_layer_price * (1 + minimum)
    if direction == "SELL":
        return current_price <= last_layer_price * (1 - minimum)
    return False


def pyramid_position_underwater(
    direction: str, entry_price: float, current_price: float,
) -> bool:
    """True when mark has moved against the open position vs its entry."""
    if not entry_price or not current_price:
        return False
    if direction == "BUY":
        return current_price < entry_price
    if direction == "SELL":
        return current_price > entry_price
    return False


def _reviewer_gate_fail_open() -> bool:
    """Whether an unexpected error in the risk-reviewer GATE may let a trade through.

    Mirrors risk_reviewer._reviewer_outage_fail_open: fail-open is fine for
    paper/backtest, but in LIVE mode an errored veto gate must block the trade
    instead of silently approving it. RISK_REVIEWER_FAIL_OPEN=true overrides.
    """
    if os.getenv("RISK_REVIEWER_FAIL_OPEN", "false").lower() == "true":
        return True
    return get_trading_mode() != TradingMode.LIVE


def atr_from_bars(bars: List[Dict[str, Any]], fallback_price: float, periods: int = 14) -> float:
    """True-range ATR using aligned prev-close windows (avoids length mismatch on short series)."""
    if not bars:
        return fallback_price * 0.02
    window = bars[-(periods + 1):] if len(bars) >= periods + 1 else bars
    if len(window) < 2:
        return fallback_price * 0.02
    trs = []
    for i in range(1, len(window)):
        h = window[i]["high"]
        l_val = window[i]["low"]
        prev_c = window[i - 1]["close"]
        trs.append(max(h - l_val, abs(h - prev_c), abs(l_val - prev_c)))
    return sum(trs) / len(trs) if trs else fallback_price * 0.02


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
        atr = atr_from_bars(bars, entry_price)
    except Exception:
        atr = entry_price * 0.02

    if direction == "BUY":
        sl = signal_sl if signal_sl is not None else (entry_price - (atr * config.sl_atr_mult))
        tp = signal_tp if signal_tp is not None else (entry_price + (atr * config.tp_atr_mult))
        sl = min(sl, entry_price - (atr * config.sl_atr_mult))
        tp = max(tp, entry_price + (atr * config.tp_atr_mult))
    else:
        sl = signal_sl if signal_sl is not None else (entry_price + (atr * config.sl_atr_mult))
        tp = signal_tp if signal_tp is not None else (entry_price - (atr * config.tp_atr_mult))
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
        self.enable_kronos = os.getenv("ENABLE_KRONOS", "true").lower() == "true"
        # Snapshot of the most recent evaluation so the loop can persist a
        # signal row for EVERY symbol it scans (not just executed trades).
        self.last_evaluation: Dict[str, Any] = {}
        # Live account equity (set by the loop each cycle) for risk-based sizing.
        # 0 → fall back to fixed trade_usdt_amount notional.
        self.account_equity: float = 0.0

    def _record_eval(self, symbol, direction, confidence, reason, entry=None, sl=None, tp=None, approved=False):
        self.last_evaluation = {
            "symbol": symbol,
            "direction": (direction or "HOLD").upper(),
            "confidence": float(confidence or 0.0),
            "reason": reason,
            "entry_price": entry,
            "stop_loss": sl,
            "take_profit": tp,
            "approved": approved,
        }

    async def evaluate_symbol(
        self,
        symbol: str,
        bars: List[Dict[str, Any]],
        existing_position: Optional[Any],  # DB Trade object or dict
        open_count: int,
        pyramid_layers: List[float],
        cooldown_active: bool,
        current_funding_rate: float = 0.0
    ) -> Optional[Decision]:
        """
        Evaluate market data and return a trading Decision.
        Does NOT execute trades or interact with the database.
        """
        if not bars or len(bars) < 50:
            self._record_eval(symbol, "HOLD", 0.0, "insufficient bars")
            return None
            
        self._record_eval(symbol, "HOLD", 0.0, "evaluating")
        
        # 1. Active position logic
        if existing_position:
            # Check pyramid
            if self.config.pyramid_mode:
                regime_result = self.regime_detector.detect(bars)
                if regime_result.regime == "RANGING":
                    logger.info(f"[{symbol}] Pyramiding blocked: market is in RANGING/CHOP regime.")
                    return None
                if len(pyramid_layers) < self.config.pyramid_max_layers:
                    cur_px = bars[-1]["close"]
                    entry_px = float(getattr(existing_position, "entry_price", 0) or 0)
                    direction = getattr(existing_position, "direction", None)
                    if (
                        getattr(self.config, "pyramid_block_underwater", True)
                        and direction
                        and pyramid_position_underwater(direction, entry_px, cur_px)
                    ):
                        self._record_eval(
                            symbol, direction, 0.0,
                            f"pyramid blocked: underwater vs entry {entry_px} (mark {cur_px})",
                        )
                        logger.info(
                            f"[{symbol}] Pyramiding blocked: position underwater "
                            f"(entry={entry_px}, mark={cur_px})."
                        )
                        return None
                    # Strategy signal for pyramid
                    signal = self.strategy.generate_signal(
                        symbol,
                        bars,
                        regime=regime_result.regime,
                        regime_weights=regime_result.weights()
                    )
                    if signal and signal.signal == existing_position.direction and signal.confidence >= self.config.min_signal_strength:
                        # Optional: require confidence to rise vs prior layer
                        if pyramid_layers and self.config.pyramid_min_conf_increase > 0:
                            # pyramid_layers length tracks layers; no per-layer conf stored —
                            # gate is best-effort on current signal only.
                            pass
                        # Optional price gate (PYRAMID_MIN_IMPROVEMENT=0 → add every cycle).
                        if pyramid_layers and self.config.pyramid_min_improvement > 0:
                            last_px = pyramid_layers[-1]
                            imp = self.config.pyramid_min_improvement
                            # LONG pyramid: add on strength (price higher).
                            # SHORT pyramid/DCA: add when price moved vs last layer.
                            blocked = not pyramid_price_improved(
                                signal.signal, cur_px, last_px, imp,
                            )
                            if blocked:
                                self._record_eval(
                                    symbol, signal.signal, signal.confidence,
                                    f"pyramid: price gate {imp:.1%} not met vs layer @ {last_px}",
                                )
                                return None
                        decision = self._create_entry_decision(
                            symbol, bars, signal, existing_position.direction, is_pyramid=True
                        )
                        if decision:
                            self._record_eval(
                                symbol, decision.action, decision.confidence, "pyramid entry",
                                entry=decision.entry_price, sl=decision.stop_loss,
                                tp=decision.take_profit, approved=True,
                            )
                        if decision and getattr(self.config, "use_risk_reviewer_llm", True):
                            try:
                                from backend.services.risk_reviewer import fetch_news_summary, review_trade_decision
                                news_summary = await fetch_news_summary(symbol)
                                approved, reasoning = await review_trade_decision(
                                    symbol=symbol,
                                    action=decision.action,
                                    quantity=decision.quantity,
                                    entry_price=decision.entry_price,
                                    stop_loss=decision.stop_loss,
                                    take_profit=decision.take_profit,
                                    confidence=decision.confidence,
                                    funding_rate=current_funding_rate,
                                    news_summary=news_summary
                                )
                                if not approved:
                                    logger.warning(f"[{symbol}] Pyramid add VETOED by Risk Reviewer: {reasoning}")
                                    return None
                                logger.info(f"[{symbol}] Pyramid add APPROVED by Risk Reviewer: {reasoning}")
                                decision.reasoning += f" | Risk Reviewer: {reasoning}"
                            except Exception as e:
                                logger.error(f"[{symbol}] Error in LLM Risk Reviewer gate for pyramid add: {e}")
                                if not _reviewer_gate_fail_open():
                                    self._record_eval(symbol, decision.action, decision.confidence,
                                                      "pyramid add blocked: risk reviewer gate errored (fail-closed in live)")
                                    return None
                        return decision
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
        if not signal or signal.signal not in ["BUY", "SELL"]:
            self._record_eval(
                symbol,
                signal.signal if signal else "HOLD",
                signal.confidence if signal else 0.0,
                "no strategy signal",
            )
            return None

        # 3b. Early RANGING regime block — skip BEFORE paying for Kronos/LLM.
        # The _create_entry_decision also blocks RANGING, but that runs AFTER
        # all the expensive AI analysis. Blocking early saves API costs.
        if regime_result.regime == "RANGING":
            self._record_eval(
                symbol, signal.signal, signal.confidence,
                "RANGING regime: blocked early (saves Kronos/LLM cost)",
            )
            return None

        # Adjust signal confidence based on the perp funding rate
        # Funding rate units on Binance: 0.0001 = 0.01% per 8h.
        # Clamp the adjustment: unclamped, a routine 0.01% funding moved
        # confidence by ±0.10, systematically flipping marginal signals toward
        # shorts (measured live: BUY win rate 22.7% vs SELL 53%). Funding is a
        # carry-cost nudge, not a directional signal — cap its influence.
        funding_adj = current_funding_rate * 1000.0
        funding_cap = getattr(self.config, "funding_conf_adj_cap", 0.05)
        funding_adj = max(-funding_cap, min(funding_cap, funding_adj))
        if signal.signal == "SELL":
            # Boost shorts if positive funding (we get paid to hold)
            old_conf = signal.confidence
            signal.confidence = max(0.0, min(1.0, signal.confidence + funding_adj))
            if funding_adj != 0:
                logger.info(f"[{symbol}] SHORT confidence adjusted by funding rate ({current_funding_rate*100:.4f}%): {old_conf:.2f} -> {signal.confidence:.2f}")
        elif signal.signal == "BUY":
            # Dampen longs if positive funding (we pay to hold)
            old_conf = signal.confidence
            signal.confidence = max(0.0, min(1.0, signal.confidence - funding_adj))
            if funding_adj != 0:
                logger.info(f"[{symbol}] LONG confidence adjusted by funding rate ({current_funding_rate*100:.4f}%): {old_conf:.2f} -> {signal.confidence:.2f}")

        # Regime-aware confidence gate
        required_gate = self.config.min_signal_strength + (0.15 if regime_result.regime == "RANGING" else 0.0)
        if signal.confidence < required_gate:
            self._record_eval(
                symbol,
                signal.signal,
                signal.confidence,
                f"strategy confidence below threshold ({required_gate:.2f}) in {regime_result.regime} regime",
            )
            return None

        # Block BUYs if funding rate is over cap
        fr_cap = getattr(self.config, "funding_rate_cap", 0.0)
        if signal.signal == "BUY" and fr_cap > 0 and current_funding_rate > fr_cap:
            logger.info(f"[{symbol}] BUY signal blocked by Funding Rate Gate ({current_funding_rate*100:.3f}% > {fr_cap*100:.3f}%)")
            self._record_eval(symbol, "HOLD", signal.confidence, "blocked by funding rate")
            return None

        # 4. Multi-model Pre-Execution Gating (Kronos Sidecar + Heuristic Timing Guard)
        kronos_result = {}
        if self.enable_kronos and bars:
            try:
                kronos_result = await kronos_service.predict(bars, symbol)
            except Exception as e:
                logger.warning(f"Kronos prediction failed for {symbol}: {e}")
                kronos_result = {}

        # Optional Vision timing verification if enabled
        vision_approved = None
        if bars:
            try:
                from backend.services.vision_timing import evaluate_vision_timing_optional
                vision_approved = await evaluate_vision_timing_optional(
                    bars=bars,
                    symbol=symbol,
                    proposed_signal=signal.signal,
                )
            except Exception as e:
                logger.debug(f"Vision timing check notice for {symbol}: {e}")

        # 4b. Apply Pre-Execution Gate (shadow-aware; FLIP removed)
        gate_result = apply_kronos_gate(
            strategy_signal=signal.signal,
            strategy_confidence=signal.confidence,
            kronos_result=kronos_result,
            bars=bars,
            vision_approved=vision_approved,
            symbol=symbol,
        )
        if gate_result.action == "veto":
            if gate_result.final_signal == "NEUTRAL":
                logger.info(f"[{symbol}] PreExecutionGate ACTIVE VETO: {gate_result.reasoning}")
                self._record_eval(symbol, "HOLD", signal.confidence, f"vetoed: {gate_result.reasoning}")
                return None
            logger.info(f"[{symbol}] PreExecutionGate SHADOW VETO (allowed): {gate_result.reasoning}")
            self._record_eval(
                symbol, "SHADOW_VETO", signal.confidence, f"shadow_vetoed: {gate_result.reasoning}",
            )
        elif gate_result.action == "boost":
            logger.info(f"[{symbol}] PreExecutionGate BOOST: {gate_result.reasoning}")
            signal.confidence = gate_result.confidence
        elif gate_result.action == "dampen":
            logger.info(f"[{symbol}] PreExecutionGate DAMPEN: {gate_result.reasoning}")
            signal.confidence = gate_result.confidence
        elif gate_result.action == "pass":
            pass
        elif gate_result.action == "flip":
            # Legacy: FLIP removed — treat as active veto.
            logger.info(f"[{symbol}] PreExecutionGate FLIP→VETO (legacy): {gate_result.reasoning}")
            return None
        else:
            _unreachable: Never = gate_result.action  # type: ignore[assignment]
            raise AssertionError(f"Unhandled PreExecutionGate action: {_unreachable}")

        # Re-check after gate modification
        if signal.signal not in ["BUY", "SELL"] or signal.confidence < self.config.min_signal_strength:
            return None


        # 5. AI Opinion Layer — multi-agent weighted consensus
        if self.config.enable_personas:
            try:
                opinion = await opinion_analyze(
                    symbol=symbol,
                    bars=bars,
                    # Kronos already ran above; reuse cache via sidecar client,
                    # but skip a second opinion-layer forecast call.
                    include_kronos=False,
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
        decision = self._create_entry_decision(symbol, bars, signal, signal.signal, is_pyramid=False, regime=regime_result.regime)
        if not decision:
            return None

        # 8. Single LLM Risk Reviewer (Veto Gate)
        if getattr(self.config, "use_risk_reviewer_llm", True):
            try:
                from backend.services.risk_reviewer import fetch_news_summary, review_trade_decision
                news_summary = await fetch_news_summary(symbol)
                approved, reasoning = await review_trade_decision(
                    symbol=symbol,
                    action=decision.action,
                    quantity=decision.quantity,
                    entry_price=decision.entry_price,
                    stop_loss=decision.stop_loss,
                    take_profit=decision.take_profit,
                    confidence=decision.confidence,
                    funding_rate=current_funding_rate,
                    news_summary=news_summary
                )
                if not approved:
                    logger.warning(f"[{symbol}] VETOED by Risk Reviewer: {reasoning}")
                    self._record_eval(symbol, decision.action, decision.confidence, f"vetoed by risk reviewer: {reasoning}")
                    return None
                else:
                    logger.info(f"[{symbol}] APPROVED by Risk Reviewer: {reasoning}")
                    decision.reasoning += f" | Risk Reviewer: {reasoning}"
            except Exception as e:
                logger.error(f"[{symbol}] Error in LLM Risk Reviewer gate: {e}")
                if not _reviewer_gate_fail_open():
                    self._record_eval(symbol, decision.action, decision.confidence,
                                      "entry blocked: risk reviewer gate errored (fail-closed in live)")
                    return None

        if decision:
            self._record_eval(symbol, decision.action, decision.confidence, "entry decision",
                              entry=decision.entry_price, sl=decision.stop_loss,
                              tp=decision.take_profit, approved=True)
        return decision

    def _create_entry_decision(self, symbol: str, bars: List[Dict[str, Any]], signal: Any, direction: str, is_pyramid: bool, regime: str = "UNKNOWN") -> Optional[Decision]:
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
        
        # Block NEW entries in RANGING regime (choppy = no new positions)
        # Pyramid adds are already separately blocked by the pyramid RANGING check.
        # Uses the regime passed in from the caller to avoid redundant detection.
        if regime == "RANGING" and not is_pyramid:
            logger.info(f"[{symbol}] RANGING regime: blocking new entry (flat $25 sizing preserved)")
            return None

        # Floor at Binance MIN_NOTIONAL ($20 for most symbols, $100 for BTC)
        # BTC uses $100 flat to match Binance min notional requirement.
        _bn_min = 100.0 if 'BTC' in symbol else 20.0
        notional = max(notional, _bn_min)
        quantity = notional / entry_price if entry_price > 0 else 0

        # Min-edge / fee-churn gate: reject trades whose *captured* move can't clear cost.
        if not self._passes_min_edge(symbol, entry_price, tp, quantity, bars):
            return None

        return Decision(
            action=direction,
            symbol=symbol,
            quantity=quantity,
            entry_price=entry_price,
            stop_loss=sl,
            take_profit=tp,
            confidence=signal.confidence,
            reasoning=f"Regime: {regime}",
            is_pyramid=is_pyramid
        )

    def _passes_min_edge(
        self,
        symbol: str,
        entry_price: float,
        tp: float,
        quantity: float,
        bars: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """Min-edge / fee-churn gate.

        A trade is only worth taking if its gross *expected captured* move
        clears a multiple of the round-trip cost (fees + slippage).

        When trailing is enabled, winners are typically scratched near the
        trail lock (activation − trail distance), not at full TP. Gate on
        that captured move so fee-unsafe trail scratches are rejected.

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

            tp_distance = abs(tp - entry_price)
            expected_move = tp_distance
            if getattr(self.config, "trailing_stop_enabled", False):
                atr = atr_from_bars(bars or [], entry_price)
                if atr <= 0:
                    atr = entry_price * 0.02
                activation = float(getattr(self.config, "trail_activation_atr", 0.0) or 0.0)
                trail_mult = float(getattr(self.config, "trail_atr_mult", 0.0) or 0.0)
                captured_atr = max(0.0, activation - trail_mult)
                expected_move = min(tp_distance, captured_atr * atr)

            gross_expected = expected_move * quantity
            roundtrip_cost = self.config.roundtrip_cost_rate * notional
            required = mult * roundtrip_cost
            if gross_expected < required:
                logger.info(
                    f"  [ {symbol} ] SKIP (min-edge): expected capture ${gross_expected:.4f} "
                    f"< {mult:.1f}x round-trip cost ${roundtrip_cost:.4f} "
                    f"(need >= ${required:.4f}; tp_move=${tp_distance * quantity:.4f})"
                )
                return False
            return True
        except Exception as e:
            logger.warning(f"  [ {symbol} ] min-edge gate error (allowing trade): {e}")
            return True
