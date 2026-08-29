"""
Pre-Execution Gating Engine (WP4 + Shadow Mode)
===============================================
Composes:
  1. Kronos Sidecar 5/10-bar trajectory & path metrics
  2. Heuristic Timing Guard (candlestick micro-structure scoring)
  3. Optional Vision LLM (secondary veto)

Features:
  - Shadow Mode (TIMING_GATE_SHADOW=true by default): Logs vetoes &
    counterfactuals without blocking execution during calibration.
  - Hard Veto (TIMING_GATE_SHADOW=false): Blocks signal execution on timing risk.
  - Environment-configurable threshold rules.
  - FLIP removed: strong disagreement vetoes (stand aside), never reverses.
"""

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from backend.services.timing_guard import TimingGuardResult, evaluate_heuristic_timing

logger = logging.getLogger(__name__)

KRONOS_VETO_CUM5_PCT = float(os.getenv("KRONOS_VETO_CUM5_PCT", "1.2"))
KRONOS_VETO_MAE_PCT = float(os.getenv("KRONOS_VETO_MAE_PCT", "2.0"))
TIMING_GATE_SHADOW = os.getenv("TIMING_GATE_SHADOW", "true").lower() == "true"


@dataclass
class PreExecutionGateResult:
    action: str              # "boost" | "veto" | "dampen" | "pass"
    original_signal: str     # "BUY" | "SELL" | "NEUTRAL"
    final_signal: str
    confidence: float
    reasoning: str
    kronos_prediction: Dict[str, Any]
    timing_result: Optional[TimingGuardResult] = None
    vision_approved: Optional[bool] = None
    is_shadow_veto: bool = False


# Backward-compatible name used by older tests/imports.
KronosGateResult = PreExecutionGateResult


def apply_pre_execution_gate(
    strategy_signal: str,
    strategy_confidence: float,
    kronos_result: Dict[str, Any],
    bars: Optional[list] = None,
    vision_approved: Optional[bool] = None,
    shadow_mode: Optional[bool] = None,
    symbol: str = "",
) -> PreExecutionGateResult:
    """
    Evaluate strategy signal against Kronos path metrics and micro-structure timing.
    Supports TIMING_GATE_SHADOW mode.
    """
    if strategy_signal == "NEUTRAL":
        return PreExecutionGateResult(
            action="pass",
            original_signal="NEUTRAL",
            final_signal="NEUTRAL",
            confidence=0.0,
            reasoning="Strategy neutral — no gating required",
            kronos_prediction=kronos_result or {},
        )

    use_shadow = TIMING_GATE_SHADOW if shadow_mode is None else shadow_mode

    # 1. Heuristic Timing Guard
    timing_res = None
    if bars and len(bars) >= 15:
        timing_res = evaluate_heuristic_timing(bars, proposed_signal=strategy_signal)

    # 2. Veto conditions
    veto_reason = None
    if timing_res and not timing_res.approved:
        veto_reason = (
            f"Heuristic timing risk score high ({timing_res.risk_score:.2f}). "
            f"{timing_res.reasoning}"
        )
    elif vision_approved is False:
        veto_reason = "Vision LLM rejected entry timing"

    k_res = kronos_result or {}
    k_sig = str(k_res.get("signal", "NEUTRAL")).upper()
    if k_sig == "UP":
        k_sig = "BUY"
    elif k_sig == "DOWN":
        k_sig = "SELL"
    k_conf = float(k_res.get("confidence", 0.0) or 0.0)
    k_cum5 = float(k_res.get("cum_change_5_pct", k_res.get("predicted_change_pct", 0.0)) or 0.0)
    k_mae = float(k_res.get("max_adverse_excursion_pct", 0.0) or 0.0)
    k_reversal = bool(k_res.get("reversal_risk", False))

    if not veto_reason:
        if strategy_signal == "BUY":
            if k_cum5 <= -KRONOS_VETO_CUM5_PCT or k_mae <= -KRONOS_VETO_MAE_PCT or k_reversal:
                veto_reason = (
                    f"Kronos forecasts opposing drop "
                    f"(cum_5={k_cum5:+.2f}%, MAE={k_mae:+.2f}%)."
                )
            elif k_sig == "SELL" and k_conf >= 0.45:
                veto_reason = (
                    f"Kronos direction opposes BUY "
                    f"(sig={k_sig}, conf={k_conf:.2f}, cum_5={k_cum5:+.2f}%)."
                )
        elif strategy_signal == "SELL":
            if k_cum5 >= KRONOS_VETO_CUM5_PCT or k_mae >= KRONOS_VETO_MAE_PCT or k_reversal:
                veto_reason = (
                    f"Kronos forecasts opposing pump "
                    f"(cum_5={k_cum5:+.2f}%, MAE={k_mae:+.2f}%)."
                )
            elif k_sig == "BUY" and k_conf >= 0.45:
                veto_reason = (
                    f"Kronos direction opposes SELL "
                    f"(sig={k_sig}, conf={k_conf:.2f}, cum_5={k_cum5:+.2f}%)."
                )

    # 3. Handle veto (shadow vs active)
    if veto_reason:
        if use_shadow:
            reason = (
                f"PreExecutionGate SHADOW VETO [{symbol}]: {veto_reason} "
                f"(Signal allowed in Shadow Mode)"
            )
            logger.info(reason)
            return PreExecutionGateResult(
                action="veto",
                original_signal=strategy_signal,
                final_signal=strategy_signal,
                confidence=strategy_confidence,
                reasoning=reason,
                kronos_prediction=k_res,
                timing_result=timing_res,
                vision_approved=vision_approved,
                is_shadow_veto=True,
            )

        reason = f"PreExecutionGate VETO [{symbol}]: {veto_reason}"
        logger.info(reason)
        return PreExecutionGateResult(
            action="veto",
            original_signal=strategy_signal,
            final_signal="NEUTRAL",
            confidence=0.0,
            reasoning=reason,
            kronos_prediction=k_res,
            timing_result=timing_res,
            vision_approved=vision_approved,
            is_shadow_veto=False,
        )

    # 4. BOOST
    if k_sig == strategy_signal and k_conf >= 0.70:
        boost = min(k_conf * 0.25, 0.20)
        new_conf = min(strategy_confidence + boost, 1.0)
        reason = (
            f"PreExecutionGate BOOST [{symbol}]: Kronos aligns with {strategy_signal} "
            f"(cum_5={k_cum5:+.2f}%, conf={k_conf:.2f}). "
            f"Confidence {strategy_confidence:.2f} → {new_conf:.2f}"
        )
        logger.info(reason)
        return PreExecutionGateResult(
            action="boost",
            original_signal=strategy_signal,
            final_signal=strategy_signal,
            confidence=new_conf,
            reasoning=reason,
            kronos_prediction=k_res,
            timing_result=timing_res,
            vision_approved=vision_approved,
        )

    # 5. DAMPEN (weak opposition that did not meet hard veto)
    if k_sig not in ("NEUTRAL", strategy_signal) and k_conf > 0.25:
        dampen = max(0.5, 1.0 - k_conf * 0.5)
        new_conf = strategy_confidence * dampen
        reason = (
            f"PreExecutionGate DAMPEN [{symbol}]: Kronos weakly opposes {strategy_signal} "
            f"(cum_5={k_cum5:+.2f}%). Confidence {strategy_confidence:.2f} → {new_conf:.2f}"
        )
        logger.info(reason)
        return PreExecutionGateResult(
            action="dampen",
            original_signal=strategy_signal,
            final_signal=strategy_signal,
            confidence=new_conf,
            reasoning=reason,
            kronos_prediction=k_res,
            timing_result=timing_res,
            vision_approved=vision_approved,
        )

    # 6. PASS
    reason = (
        f"PreExecutionGate PASS [{symbol}]: Timing metrics acceptable "
        f"(Kronos cum_5={k_cum5:+.2f}%)."
    )
    return PreExecutionGateResult(
        action="pass",
        original_signal=strategy_signal,
        final_signal=strategy_signal,
        confidence=strategy_confidence,
        reasoning=reason,
        kronos_prediction=k_res,
        timing_result=timing_res,
        vision_approved=vision_approved,
    )


# Backward compatibility alias
apply_kronos_gate = apply_pre_execution_gate
