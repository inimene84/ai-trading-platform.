"""
Jesse ML validation gate helpers (DSR / PBO institutional deployment thresholds).
"""

import os
from typing import Any, Dict, Optional


def jesse_pbo_max() -> float:
    return float(os.getenv("JESSE_ML_PBO_MAX", "0.30"))


def jesse_dsr_min() -> float:
    return float(os.getenv("JESSE_ML_DSR_MIN", "0.95"))


def jesse_pbo_override_allowed() -> bool:
    return os.getenv("JESSE_ML_PBO_OVERRIDE", "false").lower() == "true"


def extract_validation_metrics(metadata: Optional[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """Pull DSR/PBO/holdout Sharpe from Jesse model metadata payload."""
    if not metadata:
        return {"dsr": None, "pbo": None, "holdout_sharpe": None}

    metrics = metadata.get("metrics") or metadata
    dsr = metrics.get("deflated_sharpe_ratio")
    if dsr is None:
        dsr = metrics.get("dsr")
    pbo = metrics.get("prob_backtest_overfitting")
    if pbo is None:
        pbo = metrics.get("pbo")
    holdout = metrics.get("holdout_sharpe")
    return {
        "dsr": float(dsr) if dsr is not None else None,
        "pbo": float(pbo) if pbo is not None else None,
        "holdout_sharpe": float(holdout) if holdout is not None else None,
    }


def evaluate_validation_gates(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Evaluate institutional deployment gates for a Jesse ML model artifact.

    Returns gate status with explicit pass/fail reasons for DSR and PBO.
    """
    extracted = extract_validation_metrics(metadata)
    dsr = extracted["dsr"]
    pbo = extracted["pbo"]
    dsr_min = jesse_dsr_min()
    pbo_max = jesse_pbo_max()

    dsr_pass = dsr is not None and dsr >= dsr_min
    pbo_pass = pbo is not None and pbo < pbo_max

    reasons = []
    if dsr is None:
        reasons.append("DSR unavailable in model metadata")
    elif not dsr_pass:
        reasons.append(f"DSR {dsr:.4f} below gate {dsr_min:.2f}")

    if pbo is None:
        reasons.append("PBO unavailable in model metadata")
    elif not pbo_pass:
        reasons.append(f"PBO {pbo:.1%} exceeds gate {pbo_max:.0%}")

    deployment_ok = dsr_pass and pbo_pass
    if jesse_pbo_override_allowed() and not deployment_ok:
        deployment_ok = True
        reasons.append("JESSE_ML_PBO_OVERRIDE=true — gates bypassed")

    return {
        "deployment_ok": deployment_ok,
        "dsr": dsr,
        "pbo": pbo,
        "holdout_sharpe": extracted["holdout_sharpe"],
        "dsr_gate": dsr_min,
        "pbo_gate": pbo_max,
        "dsr_pass": dsr_pass,
        "pbo_pass": pbo_pass,
        "reasons": reasons,
    }
