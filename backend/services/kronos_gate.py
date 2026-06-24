"""
Kronos Gate — Foundation Model Veto / Boost Layer
=================================================
Runs BEFORE the Opinion Layer aggregator.
Kronos forecasts the next candle close; if it strongly disagrees with
the strategy signal, it can VETO (neutralize) or FLIP the signal.
If it strongly agrees, it BOOSTS confidence.

Logic:
  • Kronos confidence ≥ 0.70 and same direction as strategy → BOOST
  • Kronos confidence ≥ 0.60 and opposite direction → VETO or FLIP
  • Kronos confidence < 0.40 → no action (pass-through)
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class KronosGateResult:
    action: str              # "boost" | "veto" | "flip" | "pass"
    original_signal: str     # "BUY" | "SELL" | "NEUTRAL"
    final_signal: str
    confidence: float
    reasoning: str
    kronos_prediction: dict


def apply_kronos_gate(
    strategy_signal: str,
    strategy_confidence: float,
    kronos_result: dict,
    symbol: str = "",
) -> KronosGateResult:
    """
    Apply Kronos foundation model as a gate before multi-agent aggregation.

    Args:
        strategy_signal: "BUY" | "SELL" | "NEUTRAL"
        strategy_confidence: 0.0 – 1.0
        kronos_result: dict from kronos_service.predict()
        symbol: for logging

    Returns:
        KronosGateResult with action taken and modified signal
    """
    if not kronos_result:
        return KronosGateResult(
            action="pass",
            original_signal=strategy_signal,
            final_signal=strategy_signal,
            confidence=strategy_confidence,
            reasoning="Kronos unavailable – passing through",
            kronos_prediction={},
        )

    k_sig = kronos_result.get("signal", "NEUTRAL").upper()
    k_conf = kronos_result.get("confidence", 0.0)
    k_change = kronos_result.get("predicted_change_pct", 0.0)

    # Normalize Kronos signal
    if k_sig == "UP":
        k_direction = "BUY"
    elif k_sig == "DOWN":
        k_direction = "SELL"
    elif k_sig == "BUY":
        k_direction = "BUY"
    elif k_sig == "SELL":
        k_direction = "SELL"
    else:
        k_direction = "NEUTRAL"

    # If strategy is already neutral, nothing to gate
    if strategy_signal == "NEUTRAL":
        return KronosGateResult(
            action="pass",
            original_signal="NEUTRAL",
            final_signal="NEUTRAL",
            confidence=0.0,
            reasoning="Strategy neutral – no gating needed",
            kronos_prediction=kronos_result,
        )

    # ── BOOST: Kronos agrees strongly ──
    if k_direction == strategy_signal and k_conf >= 0.70:
        boost_amount = min(k_conf * 0.25, 0.20)
        new_conf = min(strategy_confidence + boost_amount, 1.0)
        reasoning = (
            f"Kronos BOOST: predicts {k_change:+.2f}% (conf={k_conf:.2f}) "
            f"aligns with {strategy_signal}. Confidence {strategy_confidence:.2f} → {new_conf:.2f}"
        )
        logger.info(f"KronosGate [{symbol}]: BOOST {strategy_signal} conf={new_conf:.2f}")
        return KronosGateResult(
            action="boost",
            original_signal=strategy_signal,
            final_signal=strategy_signal,
            confidence=new_conf,
            reasoning=reasoning,
            kronos_prediction=kronos_result,
        )

    # ── FLIP: Kronos opposes strongly ──
    if k_direction != strategy_signal and k_conf >= 0.60 and k_direction in ("BUY", "SELL"):
        flip_conf = max(k_conf, strategy_confidence * 0.8)
        reasoning = (
            f"Kronos FLIP: predicts {k_change:+.2f}% (conf={k_conf:.2f}) "
            f"opposes {strategy_signal}. Flipping to {k_direction} with conf={flip_conf:.2f}"
        )
        logger.info(f"KronosGate [{symbol}]: FLIP {strategy_signal} → {k_direction} conf={flip_conf:.2f}")
        return KronosGateResult(
            action="flip",
            original_signal=strategy_signal,
            final_signal=k_direction,
            confidence=flip_conf,
            reasoning=reasoning,
            kronos_prediction=kronos_result,
        )

    # ── VETO: Kronos opposes moderately ──
    if k_direction != strategy_signal and k_conf >= 0.45:
        reasoning = (
            f"Kronos VETO: predicts {k_change:+.2f}% (conf={k_conf:.2f}) "
            f"opposes {strategy_signal}. Neutralizing signal."
        )
        logger.info(f"KronosGate [{symbol}]: VETO {strategy_signal} → NEUTRAL")
        return KronosGateResult(
            action="veto",
            original_signal=strategy_signal,
            final_signal="NEUTRAL",
            confidence=0.0,
            reasoning=reasoning,
            kronos_prediction=kronos_result,
        )

    # ── DAMPEN: Kronos opposes weakly ──
    if k_direction != strategy_signal and k_conf > 0.25:
        dampen = max(0.5, 1.0 - k_conf * 0.5)
        new_conf = strategy_confidence * dampen
        reasoning = (
            f"Kronos DAMPEN: predicts {k_change:+.2f}% (conf={k_conf:.2f}) "
            f"weakly opposes {strategy_signal}. Confidence {strategy_confidence:.2f} → {new_conf:.2f}"
        )
        logger.info(f"KronosGate [{symbol}]: DAMPEN {strategy_signal} conf={new_conf:.2f}")
        return KronosGateResult(
            action="dampen",
            original_signal=strategy_signal,
            final_signal=strategy_signal,
            confidence=new_conf,
            reasoning=reasoning,
            kronos_prediction=kronos_result,
        )

    # ── PASS: no strong signal from Kronos ──
    reasoning = (
        f"Kronos PASS: predicts {k_change:+.2f}% (conf={k_conf:.2f}) "
        f"– no decisive action. Keeping {strategy_signal} conf={strategy_confidence:.2f}"
    )
    return KronosGateResult(
        action="pass",
        original_signal=strategy_signal,
        final_signal=strategy_signal,
        confidence=strategy_confidence,
        reasoning=reasoning,
        kronos_prediction=kronos_result,
    )
