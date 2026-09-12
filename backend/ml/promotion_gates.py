"""Authoritative QTP promotion gate walker (Decision Engine + reference CLI).

Deterministic order; first hard fail wins REJECT. The engine re-reads
metrics.json and verifies hashes — it never recomputes DSR/PBO from prices.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Optional

from backend.ml.geometry import (
    DEFAULT_GATES,
    SPEC_VERSION,
    house_geometry,
    locked_fields_match,
    matches_house_lock,
)
from backend.ml.hashes import feature_schema_hash, geometry_hash, is_sha256_hex

Verdict = Literal["REJECT", "SHADOW", "PROMOTE", "ROLLBACK"]

HARD_GATE_ORDER = (
    "SPEC_VERSION",
    "SCHEMA_HASH",
    "GEOMETRY_HASH",
    "GEOMETRY_LIVE_LOCK",
    "N_TRIALS_NOT_EFFECTIVE",
    "DSR_MISSING",
    "DSR_FLOOR",
    "PBO_INFEASIBLE",
    "PBO_MATRIX_INCOMPLETE",
    "PBO_CEILING",
    "MIN_TRADES",
    "NET_SHARPE",
    "COSTED_EDGE",
    "ZERO_COST_ONLY",
    "HOLDOUT_SPENT",
    "HOLDOUT_FAIL",
)

WARN_GATES = ("PATH_LEFT_TAIL", "CONFORMAL_COVERAGE", "MINBTL")

EFFECTIVE_N_TRIAL_SOURCES = frozenset(
    {
        "effective_n_trials",
        "TrialSharpeRecorder.n_effective",
        "TrialSharpeRecorder.n_effective()",
        "recorder.n_effective",
        "recorder.n_effective()",
    }
)

RAW_N_TRIAL_SOURCES = frozenset(
    {
        "study.n_trials",
        "raw",
        "n_trials",
        "optuna",
        "optuna.n_trials",
    }
)


@dataclass
class GateResult:
    verdict: Verdict
    reason: str
    failed_gate: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.verdict in ("SHADOW", "PROMOTE")


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _gates_from_geometry(geometry: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(DEFAULT_GATES)
    merged.update(_as_mapping(geometry.get("gates")))
    return merged


def _metrics_hashes(metrics: Mapping[str, Any]) -> dict[str, Any]:
    return _as_mapping(metrics.get("hashes"))


def _holdout_block(metrics: Mapping[str, Any]) -> dict[str, Any]:
    return _as_mapping(metrics.get("holdout"))


def _zero_cost_block(metrics: Mapping[str, Any]) -> dict[str, Any]:
    return _as_mapping(metrics.get("zero_cost"))


def _float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _reject(gate: str, reason: str, details: dict[str, Any], warnings: list[str]) -> GateResult:
    return GateResult(
        verdict="REJECT",
        reason=reason,
        failed_gate=gate,
        warnings=warnings,
        details=details,
    )


def evaluate_promotion_gates(
    geometry: Mapping[str, Any],
    metrics: Mapping[str, Any],
    *,
    live_geometry: Mapping[str, Any] | None = None,
    feature_schema: Mapping[str, Any] | list[Any] | None = None,
    live_feature_schema_hash: str | None = None,
    live_geometry_hash: str | None = None,
    holdout_already_spent: bool = False,
    production_kill: bool = False,
    production_drift: bool = False,
    require_feature_schema: bool = False,
) -> GateResult:
    """Walk hard gates in contract order, then assign SHADOW / PROMOTE / ROLLBACK."""
    details: dict[str, Any] = {
        "spec_version": metrics.get("spec_version") or geometry.get("spec_version"),
        "strategy_id": metrics.get("strategy_id") or geometry.get("strategy_id"),
    }
    warnings: list[str] = []
    gates = _gates_from_geometry(geometry)
    hashes = _metrics_hashes(metrics)
    live_geo = dict(live_geometry) if live_geometry is not None else house_geometry()

    expected_geometry_hash = geometry_hash(geometry)
    metrics_geometry_hash = hashes.get("geometry_hash")
    recomputed_live_hash = geometry_hash(live_geo)
    if live_geometry_hash:
        recomputed_live_hash = str(live_geometry_hash)

    expected_feature_hash = None
    if feature_schema is not None:
        expected_feature_hash = feature_schema_hash(feature_schema)
    metrics_feature_hash = hashes.get("feature_schema_hash")
    live_feat_hash = live_feature_schema_hash

    # 1. SPEC_VERSION
    spec = str(metrics.get("spec_version") or geometry.get("spec_version") or "")
    if spec != SPEC_VERSION:
        return _reject(
            "SPEC_VERSION",
            f"spec_version {spec!r} != {SPEC_VERSION}",
            details,
            warnings,
        )

    # 2. SCHEMA_HASH
    if require_feature_schema and feature_schema is None:
        return _reject("SCHEMA_HASH", "feature_schema.json is required for promotion", details, warnings)
    if not is_sha256_hex(metrics_feature_hash):
        return _reject("SCHEMA_HASH", "feature_schema_hash missing or not SHA-256 hex", details, warnings)
    if expected_feature_hash and expected_feature_hash != metrics_feature_hash:
        return _reject(
            "SCHEMA_HASH",
            "feature_schema_hash does not match provided feature_schema.json",
            details,
            warnings,
        )
    if live_feat_hash and live_feat_hash != metrics_feature_hash:
        return _reject(
            "SCHEMA_HASH",
            "feature_schema_hash on the VPS != metrics.hashes.feature_schema_hash",
            details,
            warnings,
        )

    # 3. GEOMETRY_HASH
    if not is_sha256_hex(metrics_geometry_hash):
        return _reject("GEOMETRY_HASH", "geometry_hash missing or not SHA-256 hex", details, warnings)
    if metrics_geometry_hash != expected_geometry_hash:
        return _reject(
            "GEOMETRY_HASH",
            "GEOMETRY_MISMATCH: geometry_hash != hash(geometry.json minus hashes)",
            details,
            warnings,
        )
    if recomputed_live_hash != metrics_geometry_hash:
        return _reject(
            "GEOMETRY_HASH",
            "GEOMETRY_MISMATCH: live geometry_hash != metrics.hashes.geometry_hash",
            details,
            warnings,
        )

    # 4. GEOMETRY_LIVE_LOCK
    if not matches_house_lock(geometry) or not matches_house_lock(live_geo):
        return _reject(
            "GEOMETRY_LIVE_LOCK",
            "locked triple-barrier fields do not match house lock SL 1.75 / PT 5.5 ATR",
            details,
            warnings,
        )
    if not locked_fields_match(geometry, live_geo):
        return _reject(
            "GEOMETRY_LIVE_LOCK",
            "training geometry locked fields != live strategy config",
            details,
            warnings,
        )

    # 5. N_TRIALS_NOT_EFFECTIVE
    n_source = str(metrics.get("n_trials_source") or "")
    n_raw = _float_or_none(metrics.get("n_trials_raw"))
    n_eff = _float_or_none(metrics.get("n_trials_effective"))
    if n_source in RAW_N_TRIAL_SOURCES or n_source not in EFFECTIVE_N_TRIAL_SOURCES:
        return _reject(
            "N_TRIALS_NOT_EFFECTIVE",
            "n_trials MUST be effective_n_trials(...) or TrialSharpeRecorder.n_effective()",
            details,
            warnings,
        )
    if n_eff is None or n_eff <= 0:
        return _reject(
            "N_TRIALS_NOT_EFFECTIVE",
            "n_trials_effective missing or not positive",
            details,
            warnings,
        )
    if n_raw is not None and n_eff > n_raw + 1e-9:
        return _reject(
            "N_TRIALS_NOT_EFFECTIVE",
            "n_trials_effective cannot exceed raw trial count",
            details,
            warnings,
        )

    # 6. DSR_MISSING  — probability in [0, 1], not a z-score
    dsr = _float_or_none(metrics.get("dsr"))
    dsr_is_prob = _bool(metrics.get("dsr_is_probability"), default=False)
    if dsr is None or not dsr_is_prob or not (0.0 <= dsr <= 1.0):
        return _reject(
            "DSR_MISSING",
            "DSR must be a probability in [0, 1] from deflated_sharpe_ratio(_full)",
            details,
            warnings,
        )

    # 7. DSR_FLOOR
    dsr_min = float(gates.get("dsr_min", 0.95))
    if dsr < dsr_min:
        return _reject("DSR_FLOOR", f"dsr {dsr:.4f} < gates.dsr_min {dsr_min:.4f}", details, warnings)

    # 8. PBO_INFEASIBLE
    n_splits = int(metrics.get("pbo_n_splits") or _as_mapping(geometry.get("validation")).get("pbo_n_splits") or 16)
    n_configs = int(metrics.get("pbo_n_configs") or 0)
    n_obs = int(metrics.get("pbo_n_obs") or 0)
    pbo_feasible = _bool(metrics.get("pbo_feasible"), default=n_configs >= 2 and n_obs >= n_splits)
    if (not pbo_feasible) or n_configs < 2 or n_obs < n_splits:
        return _reject(
            "PBO_INFEASIBLE",
            f"PBO infeasible (n_configs={n_configs}, n_obs={n_obs}, n_splits={n_splits})",
            details,
            warnings,
        )

    # 9. PBO_MATRIX_INCOMPLETE
    if not _bool(metrics.get("pbo_matrix_complete"), default=False):
        return _reject(
            "PBO_MATRIX_INCOMPLETE",
            "PBO requires every completed trial; winner-only matrix is incomplete",
            details,
            warnings,
        )

    # 10. PBO_CEILING
    pbo = _float_or_none(metrics.get("pbo"))
    pbo_max = float(gates.get("pbo_max", 0.30))
    if pbo is None or pbo < 0.0 or pbo > 1.0 or pbo >= pbo_max:
        return _reject(
            "PBO_CEILING",
            f"pbo {pbo} is missing, outside [0, 1], or >= gates.pbo_max {pbo_max:.4f}",
            details,
            warnings,
        )

    # 11. MIN_TRADES
    n_trades = int(metrics.get("n_trades") or 0)
    min_trades = int(gates.get("min_trades", 80))
    if n_trades < min_trades:
        return _reject("MIN_TRADES", f"n_trades {n_trades} < min_trades {min_trades}", details, warnings)

    # 12. NET_SHARPE (costed)
    net_sharpe = _float_or_none(metrics.get("net_sharpe_after_costs"))
    min_net = float(gates.get("min_net_sharpe_after_costs", 0.0))
    if net_sharpe is None or net_sharpe < min_net:
        return _reject(
            "NET_SHARPE",
            f"net_sharpe_after_costs {net_sharpe} < min_net_sharpe_after_costs {min_net}",
            details,
            warnings,
        )

    # 13. COSTED_EDGE
    edge = _float_or_none(metrics.get("costed_edge_bps"))
    min_edge = float(gates.get("min_costed_edge_bps", 0.0))
    if edge is None or edge < min_edge:
        return _reject(
            "COSTED_EDGE",
            f"costed_edge_bps {edge} < min_costed_edge_bps {min_edge}",
            details,
            warnings,
        )

    # 14. ZERO_COST_ONLY — costed series is mandatory; zero-cost is diagnostics only
    zero_cost = _zero_cost_block(metrics)
    if not zero_cost:
        return _reject("ZERO_COST_ONLY", "metrics.zero_cost block required (used_for_promotion must be false)", details, warnings)
    if "used_for_promotion" not in zero_cost:
        return _reject("ZERO_COST_ONLY", "metrics.zero_cost.used_for_promotion is required", details, warnings)
    if _bool(zero_cost.get("used_for_promotion"), default=False):
        return _reject("ZERO_COST_ONLY", "zero-cost DSR/Sharpe cannot be a promotion input", details, warnings)
    if _bool(metrics.get("promoted_on_zero_cost"), default=False):
        return _reject("ZERO_COST_ONLY", "zero-cost DSR/Sharpe cannot be a promotion input", details, warnings)
    zero_dsr = _float_or_none(zero_cost.get("dsr"))
    if zero_dsr is not None and abs(zero_dsr - dsr) < 1e-12:
        return _reject("ZERO_COST_ONLY", "metrics.dsr equals zero_cost.dsr — promote on costed DSR only", details, warnings)

    # 15. HOLDOUT_SPENT — refuse a second peek of a sealed holdout_id
    holdout = _holdout_block(metrics)
    spent_this_run = _bool(holdout.get("spent_this_run"), default=False)
    holdout_spent_flag = _bool(holdout.get("spent"), default=False)
    if holdout_already_spent or (holdout_spent_flag and not spent_this_run):
        return _reject("HOLDOUT_SPENT", "sealed holdout_id already spent; refuse a second peek", details, warnings)

    # 16. HOLDOUT_FAIL
    holdout_passed = holdout.get("passed")
    if spent_this_run and holdout_passed is False:
        return _reject("HOLDOUT_FAIL", "sealed holdout was spent this run and failed", details, warnings)

    # Warn (or optional hard) gates
    path_p10 = _float_or_none(metrics.get("path_sharpe_p10"))
    min_p10 = float(gates.get("min_path_sharpe_p10", -0.50))
    if path_p10 is not None and path_p10 < min_p10:
        msg = f"PATH_LEFT_TAIL: path_sharpe_p10 {path_p10} < {min_p10}"
        if _bool(gates.get("path_left_tail_hard"), default=False):
            return _reject("PATH_LEFT_TAIL", msg, details, warnings)
        warnings.append(msg)

    coverage = _float_or_none(metrics.get("conformal_coverage"))
    min_cov = float(gates.get("min_conformal_coverage", 0.70))
    if coverage is not None and coverage < min_cov:
        msg = f"CONFORMAL_COVERAGE: {coverage} < {min_cov}"
        if _bool(gates.get("conformal_coverage_hard"), default=False):
            return _reject("CONFORMAL_COVERAGE", msg, details, warnings)
        warnings.append(msg)

    minbtl = _float_or_none(metrics.get("minbtl_years"))
    track_years = _float_or_none(metrics.get("track_record_years"))
    if minbtl is not None and track_years is not None and track_years < minbtl:
        msg = f"MINBTL: track_record_years {track_years} < minbtl_years {minbtl}"
        if _bool(gates.get("minbtl_hard"), default=False):
            return _reject("MINBTL", msg, details, warnings)
        warnings.append(msg)

    details.update(
        {
            "dsr": dsr,
            "pbo": pbo,
            "n_trials_effective": n_eff,
            "n_trades": n_trades,
            "net_sharpe_after_costs": net_sharpe,
            "costed_edge_bps": edge,
            "warnings": list(warnings),
        }
    )

    if production_kill or production_drift or _bool(metrics.get("production_kill")) or _bool(metrics.get("production_drift")):
        return GateResult(
            verdict="ROLLBACK",
            reason="production drift or kill switch",
            failed_gate="ROLLBACK",
            warnings=warnings,
            details=details,
        )

    promote_requested = _bool(metrics.get("promote_requested"), default=False)
    holdout_required = _bool(gates.get("holdout_required_for_promote"), default=True)
    holdout_ok = (not holdout_required) or (spent_this_run and holdout_passed is True)

    if promote_requested and holdout_ok:
        return GateResult(verdict="PROMOTE", reason="all hard gates passed; holdout spent this run", warnings=warnings, details=details)

    return GateResult(
        verdict="SHADOW",
        reason="all hard gates passed; holdout unspent or promote_requested=false",
        warnings=warnings,
        details=details,
    )
