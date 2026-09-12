"""GPU training job helpers: write promotion artifacts, never place orders.

The training path must not import Core Hub trading routes or hold broker
credentials. Advisory verdicts here are informational only; the Decision
Engine is authoritative.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

try:
    import pandas as pd
except ImportError:  # pragma: no cover - parquet is optional on the engine
    pd = None

from backend.ml.artifacts import (
    CPCV_PATHS_NAME,
    ENV_LOCK_NAME,
    FEATURE_SCHEMA_NAME,
    GEOMETRY_NAME,
    METRICS_NAME,
    TRIALS_RETURNS_NAME,
    attach_geometry_hashes,
    write_json,
)
from backend.ml.costs import costed_edge_bps, net_sharpe_after_costs
from backend.ml.geometry import SPEC_VERSION, house_geometry
from backend.ml.hashes import feature_schema_hash, geometry_hash, holdout_id_hash
from backend.ml.promotion_gates import evaluate_promotion_gates
from backend.ml.purgedcv_metrics import (
    compute_dsr,
    compute_pbo,
    resolve_effective_n_trials,
)

logger = logging.getLogger(__name__)

FORBIDDEN_ENV = (
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "BINANCE_SECRET_KEY",
    "CTRADER_CLIENT_SECRET",
    "CTRADER_ACCESS_TOKEN",
    "CTRADER_REFRESH_TOKEN",
)

FORBIDDEN_MODULES = (
    "backend.routes.trading",
    "backend.services.binance_futures_service",
    "backend.services.ctrader_service",
    "backend.services.unified_trading",
)


class TrainingPathViolation(RuntimeError):
    """Training job attempted to touch the live order path or broker secrets."""


def assert_training_path_isolated(modules: Mapping[str, Any] | None = None) -> None:
    """Fail closed if the training process imported Core Hub trading modules."""
    loaded = modules if modules is not None else sys.modules
    for name in FORBIDDEN_MODULES:
        if name in loaded:
            raise TrainingPathViolation(f"GPU training path imported {name}; /trading/* is Core Hub only")
    for key in FORBIDDEN_ENV:
        if os.getenv(key, "").strip():
            raise TrainingPathViolation(
                f"GPU training node must never hold broker credential {key}"
            )


def write_env_lock(directory: Path) -> Path:
    """Best-effort pip freeze into env.lock (empty file if freeze unavailable)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ENV_LOCK_NAME
    try:
        proc = subprocess.run(
            ["python", "-m", "pip", "freeze"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        body = proc.stdout if proc.returncode == 0 else "# pip freeze failed\n"
    except (OSError, TimeoutError):
        body = "# pip freeze unavailable\n"
    path.write_text(body, encoding="utf-8")
    return path


def write_promotion_artifacts(
    directory: Path | str,
    *,
    costed_selected_returns: Sequence[float],
    trial_returns_obs_by_config: Sequence[Sequence[float]] | np.ndarray,
    trial_sharpes: Sequence[float],
    feature_schema: Mapping[str, Any],
    n_trades: int,
    holdout: Mapping[str, Any] | None = None,
    promote_requested: bool = False,
    recorder: Any = None,
    raw_n_trials: int | None = None,
    bars_per_year: float = 24.0 * 365.0,
    pbo_n_splits: int = 16,
    path_sharpe_p10: float | None = None,
    conformal_coverage: float | None = None,
    minbtl_years: float | None = None,
    track_record_years: float | None = None,
    zero_cost_selected_returns: Sequence[float] | None = None,
    geometry: Mapping[str, Any] | None = None,
    completed_trial_count: int | None = None,
    assert_isolated: bool = True,
) -> dict[str, Any]:
    """Write the contract artifact set and return metrics.json (advisory verdict).

    ``trial_returns_obs_by_config`` is the parquet layout (n_obs, n_configs).
    PBO uses every completed trial. Selected-config DSR uses **costed** returns.
    """
    if assert_isolated:
        assert_training_path_isolated()

    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)

    geo = dict(geometry) if geometry is not None else house_geometry()
    holdout_block = dict(holdout or {"spent": False, "spent_this_run": False, "passed": None})
    holdout_id = None
    if all(k in holdout_block for k in ("start", "end", "instrument", "timeframe")):
        holdout_id = holdout_id_hash(
            start=str(holdout_block["start"]),
            end=str(holdout_block["end"]),
            instrument=str(holdout_block["instrument"]),
            timeframe=str(holdout_block["timeframe"]),
        )

    feature_payload = dict(feature_schema)
    geo_with_hashes = attach_geometry_hashes(geo, feature_payload, holdout_id=holdout_id)
    write_json(root / FEATURE_SCHEMA_NAME, feature_payload)
    write_json(root / GEOMETRY_NAME, geo_with_hashes)

    costed = np.asarray(list(costed_selected_returns), dtype=float)
    zero_cost_series = (
        np.asarray(list(zero_cost_selected_returns), dtype=float)
        if zero_cost_selected_returns is not None
        else None
    )

    effective = resolve_effective_n_trials(
        trial_sharpes=trial_sharpes,
        recorder=recorder,
        raw_n_trials=raw_n_trials,
    )
    dsr = compute_dsr(costed, effective=effective, bars_per_year=bars_per_year)
    pbo = compute_pbo(
        trial_returns_obs_by_config,
        n_splits=pbo_n_splits,
        completed_trial_count=completed_trial_count if completed_trial_count is not None else effective.n_raw,
        orientation="obs_by_configs",
    )

    matrix = np.asarray(trial_returns_obs_by_config, dtype=float)
    if pd is not None:
        try:
            pd.DataFrame(matrix).to_parquet(root / TRIALS_RETURNS_NAME)
        except Exception as exc:
            logger.info("trials_returns.parquet not written (%s)", exc)
        cpcv_path = root / CPCV_PATHS_NAME
        if not cpcv_path.exists():
            try:
                pd.DataFrame({"path_id": [], "sharpe": [], "max_dd": []}).to_parquet(cpcv_path)
            except Exception:
                pass

    write_env_lock(root)

    hashes = {
        "geometry_hash": geometry_hash(geo_with_hashes),
        "feature_schema_hash": feature_schema_hash(feature_payload),
    }
    if holdout_id:
        hashes["holdout_id"] = holdout_id

    zero_cost_metrics = {
        "dsr": None,
        "net_sharpe": (
            float(net_sharpe_after_costs(zero_cost_series, bars_per_year=bars_per_year))
            if zero_cost_series is not None
            else None
        ),
        "used_for_promotion": False,
    }

    metrics: dict[str, Any] = {
        "spec_version": SPEC_VERSION,
        "strategy_id": geo_with_hashes.get("strategy_id"),
        "hashes": hashes,
        "n_trials_raw": effective.n_raw,
        "n_trials_effective": effective.n_effective,
        "n_trials_source": effective.source,
        "dsr": dsr.dsr,
        "dsr_is_probability": True,
        "dsr_diagnostics": dsr.diagnostics,
        "pbo": pbo.pbo,
        "pbo_n_configs": pbo.n_configs,
        "pbo_n_obs": pbo.n_obs,
        "pbo_n_splits": pbo.n_splits,
        "pbo_matrix_complete": pbo.matrix_complete,
        "pbo_feasible": pbo.feasible,
        "pbo_reject_reason": pbo.reject_reason,
        "n_trades": int(n_trades),
        "net_sharpe_after_costs": float(net_sharpe_after_costs(costed, bars_per_year=bars_per_year)),
        "costed_edge_bps": float(costed_edge_bps(costed, bars_per_year=bars_per_year)),
        "zero_cost": zero_cost_metrics,
        "holdout": holdout_block,
        "path_sharpe_p10": path_sharpe_p10,
        "conformal_coverage": conformal_coverage,
        "minbtl_years": minbtl_years,
        "track_record_years": track_record_years,
        "promote_requested": bool(promote_requested),
        "production_kill": False,
        "production_drift": False,
    }

    advisory = evaluate_promotion_gates(
        geo_with_hashes,
        metrics,
        live_geometry=geo_with_hashes,
        feature_schema=feature_payload,
    )
    metrics["advisory_verdict"] = advisory.verdict
    metrics["advisory_reason"] = advisory.reason
    write_json(root / METRICS_NAME, metrics)
    logger.info("Wrote promotion artifacts to %s (advisory=%s)", root, advisory.verdict)
    return metrics


def dump_example_metrics_template() -> str:
    """JSON template for metrics.json keys the gates read."""
    return json.dumps(
        {
            "spec_version": SPEC_VERSION,
            "dsr": "<probability from purgedcv.deflated_sharpe_ratio>",
            "dsr_is_probability": True,
            "n_trials_source": "effective_n_trials | TrialSharpeRecorder.n_effective",
            "pbo": "<probability_of_backtest_overfitting>",
            "note": "Decision Engine re-reads these numbers; it does not recompute them.",
        },
        indent=2,
    )
