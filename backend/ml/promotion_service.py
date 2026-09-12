"""Decision Engine adapter: re-read artifacts, verify hashes, apply gates.

SHADOW loads the artifact, logs predictions, and forces size=0 from this
model. Only Core Hub may hit /trading/*.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from backend.ml.artifacts import PromotionBundle, holdout_registry_for_bundle, load_promotion_bundle
from backend.ml.geometry import house_geometry, locked_fields_match
from backend.ml.hashes import feature_schema_hash
from backend.ml.holdout_registry import HoldoutRegistry
from backend.ml.live_signal import LiveFourNumberDecision, evaluate_live_four_numbers
from backend.ml.promotion_gates import GateResult, evaluate_promotion_gates

logger = logging.getLogger(__name__)


def _truthy_flag(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def live_geometry_from_risk_config(config: Any) -> dict[str, Any]:
    """Map live RiskConfig locked fields onto the geometry.json shape."""
    geo = house_geometry()
    sl = float(getattr(config, "sl_atr_mult", geo["triple_barrier"]["sl_atr_mult"]))
    pt = float(getattr(config, "tp_atr_mult", geo["triple_barrier"]["pt_atr_mult"]))
    atr_period = int(getattr(config, "atr_period", geo["triple_barrier"]["atr_period"]))
    geo["triple_barrier"]["sl_atr_mult"] = sl
    geo["triple_barrier"]["pt_atr_mult"] = pt
    geo["triple_barrier"]["atr_period"] = atr_period
    geo["costs"]["taker_fee_rate"] = float(getattr(config, "taker_fee_rate", geo["costs"]["taker_fee_rate"]))
    geo["costs"]["slippage_rate"] = float(getattr(config, "slippage_rate", geo["costs"]["slippage_rate"]))
    return geo


@dataclass
class PromotionState:
    result: GateResult
    bundle: Optional[PromotionBundle] = None
    live_check: Optional[LiveFourNumberDecision] = None
    logged_predictions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        return self.result.verdict

    @property
    def shadow(self) -> bool:
        return self.result.verdict == "SHADOW"

    @property
    def promote(self) -> bool:
        return self.result.verdict == "PROMOTE"

    @property
    def reject_model(self) -> bool:
        return self.result.verdict in {"REJECT", "ROLLBACK"}


def evaluate_bundle_for_engine(
    bundle: PromotionBundle,
    *,
    risk_config: Any = None,
    holdout_registry: HoldoutRegistry | None = None,
    production_kill: bool = False,
    production_drift: bool = False,
) -> PromotionState:
    # Hash the VPS copy of geometry.json / feature_schema.json. Locked ATR
    # fields are compared to live RiskConfig separately — reconstructing a
    # geometry object from RiskConfig alone would produce a different hash.
    live_f_hash = feature_schema_hash(bundle.feature_schema) if bundle.feature_schema is not None else None
    if risk_config is not None:
        rc_geo = live_geometry_from_risk_config(risk_config)
        if not locked_fields_match(bundle.geometry, rc_geo):
            mismatch = GateResult(
                verdict="REJECT",
                reason="GEOMETRY_LIVE_LOCK: training geometry locked fields != live RiskConfig",
                failed_gate="GEOMETRY_LIVE_LOCK",
            )
            logger.info("QTP promotion gate verdict=%s reason=%s", mismatch.verdict, mismatch.reason)
            return PromotionState(result=mismatch, bundle=bundle)

    hashes = dict(bundle.metrics.get("hashes") or {})
    holdout_id = str(hashes.get("holdout_id") or "")
    already_spent = False
    if holdout_registry is not None and holdout_id:
        already_spent = holdout_registry.is_second_peek(
            holdout_id,
            geometry_hash=str(hashes.get("geometry_hash") or ""),
            feature_schema_hash=str(hashes.get("feature_schema_hash") or ""),
        )

    result = evaluate_promotion_gates(
        bundle.geometry,
        bundle.metrics,
        live_geometry=bundle.geometry,
        feature_schema=bundle.feature_schema,
        live_feature_schema_hash=live_f_hash,
        holdout_already_spent=already_spent,
        production_kill=production_kill,
        production_drift=production_drift,
        require_feature_schema=True,
    )
    holdout = dict(bundle.metrics.get("holdout") or {})
    # Persist only the first spend. Second-peek REJECT must not overwrite
    # the sealed hashes (GET /jesse/promotion-status also hits this path).
    if (
        holdout_registry is not None
        and holdout_id
        and _truthy_flag(holdout.get("spent_this_run"))
        and not already_spent
        and not holdout_registry.is_spent(holdout_id)
    ):
        holdout_registry.mark_spent(
            holdout_id,
            meta={
                "spent_this_run": True,
                "geometry_hash": hashes.get("geometry_hash"),
                "feature_schema_hash": hashes.get("feature_schema_hash"),
            },
        )
    logger.info("QTP promotion gate verdict=%s reason=%s", result.verdict, result.reason)
    return PromotionState(result=result, bundle=bundle)


def resolve_promotion(
    risk_config: Any = None,
    *,
    artifact_dir: Path | None = None,
) -> Optional[PromotionState]:
    """Load artifacts if configured. None → existing Jesse ML path (no contract files)."""
    bundle = load_promotion_bundle(artifact_dir)
    if bundle is None:
        if promotion_required():
            return PromotionState(
                result=GateResult(
                    verdict="REJECT",
                    reason="QTP_PROMOTION_REQUIRED but geometry.json/metrics.json were not found",
                    failed_gate="SCHEMA_HASH",
                )
            )
        return None
    registry = holdout_registry_for_bundle(bundle)
    return evaluate_bundle_for_engine(bundle, risk_config=risk_config, holdout_registry=registry)


def log_shadow_prediction(state: PromotionState, symbol: str, prediction: Mapping[str, Any]) -> None:
    """SHADOW: record the artifact prediction; size from this model stays 0."""
    row = {"symbol": symbol, **dict(prediction)}
    state.logged_predictions.append(row)
    logger.info(
        "[%s] SHADOW model prediction logged (size=0 from this model): %s",
        symbol,
        {k: prediction.get(k) for k in ("signal", "confidence", "probabilities", "gated")},
    )


def apply_live_signal_check(
    state: PromotionState,
    prediction: Mapping[str, Any],
) -> LiveFourNumberDecision:
    geometry = state.bundle.geometry if state.bundle else house_geometry()
    decision = evaluate_live_four_numbers(prediction, geometry=geometry)
    state.live_check = decision
    if decision.applied and not decision.allowed:
        logger.info("Promoted model live four-number check blocked: %s", decision.reason)
    return decision


def promotion_required() -> bool:
    return os.getenv("QTP_PROMOTION_REQUIRED", "false").lower() == "true"
