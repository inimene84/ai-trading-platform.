"""Unit tests for QTP Promotion Contract v1.0.0 gate walker + example fixtures."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.ml.geometry import HOUSE_PT_ATR_MULT, HOUSE_SL_ATR_MULT, house_geometry
from backend.ml.hashes import feature_schema_hash, geometry_hash
from backend.ml.promotion_gates import evaluate_promotion_gates

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = REPO_ROOT / "docs/ml/qtp-promotion-contract/examples"
EVALUATOR = REPO_ROOT / "docs/ml/qtp-promotion-contract/reference/evaluate_gates.py"


def _load(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


@pytest.fixture
def geometry() -> dict:
    return _load("geometry.json")


@pytest.fixture
def feature_schema() -> dict:
    return _load("feature_schema.json")


@pytest.fixture
def pass_metrics() -> dict:
    return _load("metrics.pass.json")


def test_example_hashes_are_self_consistent(geometry, feature_schema, pass_metrics):
    assert geometry_hash(geometry) == pass_metrics["hashes"]["geometry_hash"]
    assert feature_schema_hash(feature_schema) == pass_metrics["hashes"]["feature_schema_hash"]
    assert geometry["triple_barrier"]["sl_atr_mult"] == HOUSE_SL_ATR_MULT
    assert geometry["triple_barrier"]["pt_atr_mult"] == HOUSE_PT_ATR_MULT


def test_pass_metrics_promote(geometry, feature_schema, pass_metrics):
    result = evaluate_promotion_gates(
        geometry,
        pass_metrics,
        live_geometry=geometry,
        feature_schema=feature_schema,
    )
    assert result.verdict == "PROMOTE"
    assert result.failed_gate is None
    assert result.ok


def test_shadow_when_promote_not_requested(geometry, feature_schema):
    metrics = _load("metrics.shadow.json")
    result = evaluate_promotion_gates(
        geometry, metrics, live_geometry=geometry, feature_schema=feature_schema
    )
    assert result.verdict == "SHADOW"
    assert result.ok


@pytest.mark.parametrize(
    "filename,gate",
    [
        ("metrics.fail_dsr.json", "DSR_FLOOR"),
        ("metrics.fail_pbo.json", "PBO_CEILING"),
        ("metrics.fail_pbo_infeasible.json", "PBO_INFEASIBLE"),
        ("metrics.fail_n_trials.json", "N_TRIALS_NOT_EFFECTIVE"),
        ("metrics.fail_zero_cost.json", "ZERO_COST_ONLY"),
        ("metrics.fail_geometry.json", "GEOMETRY_HASH"),
    ],
)
def test_fail_fixtures_reject(geometry, feature_schema, filename, gate):
    metrics = _load(filename)
    result = evaluate_promotion_gates(
        geometry, metrics, live_geometry=geometry, feature_schema=feature_schema
    )
    assert result.verdict == "REJECT"
    assert result.failed_gate == gate
    assert result.ok is False


def test_dsr_zscore_is_missing(geometry, feature_schema, pass_metrics):
    metrics = copy.deepcopy(pass_metrics)
    metrics["dsr"] = 3.2
    metrics["dsr_is_probability"] = False
    result = evaluate_promotion_gates(
        geometry, metrics, live_geometry=geometry, feature_schema=feature_schema
    )
    assert result.verdict == "REJECT"
    assert result.failed_gate == "DSR_MISSING"


def test_pbo_winner_only_matrix(geometry, feature_schema, pass_metrics):
    metrics = copy.deepcopy(pass_metrics)
    metrics["pbo_matrix_complete"] = False
    result = evaluate_promotion_gates(
        geometry, metrics, live_geometry=geometry, feature_schema=feature_schema
    )
    assert result.failed_gate == "PBO_MATRIX_INCOMPLETE"


def test_geometry_live_lock_rejects_2_6_barriers(geometry, feature_schema, pass_metrics):
    live = copy.deepcopy(geometry)
    live["triple_barrier"]["sl_atr_mult"] = 2.0
    live["triple_barrier"]["pt_atr_mult"] = 6.0
    result = evaluate_promotion_gates(
        geometry, pass_metrics, live_geometry=live, feature_schema=feature_schema
    )
    assert result.verdict == "REJECT"
    assert result.failed_gate in {"GEOMETRY_HASH", "GEOMETRY_LIVE_LOCK"}


def test_holdout_spent_reuse(geometry, feature_schema, pass_metrics):
    result = evaluate_promotion_gates(
        geometry,
        pass_metrics,
        live_geometry=geometry,
        feature_schema=feature_schema,
        holdout_already_spent=True,
    )
    assert result.failed_gate == "HOLDOUT_SPENT"


def test_holdout_fail(geometry, feature_schema, pass_metrics):
    metrics = copy.deepcopy(pass_metrics)
    metrics["holdout"] = {**metrics["holdout"], "spent_this_run": True, "passed": False}
    result = evaluate_promotion_gates(
        geometry, metrics, live_geometry=geometry, feature_schema=feature_schema
    )
    assert result.failed_gate == "HOLDOUT_FAIL"


def test_production_kill_rollback(geometry, feature_schema, pass_metrics):
    result = evaluate_promotion_gates(
        geometry,
        pass_metrics,
        live_geometry=geometry,
        feature_schema=feature_schema,
        production_kill=True,
    )
    assert result.verdict == "ROLLBACK"


def test_first_hard_fail_wins_spec_version(geometry, feature_schema, pass_metrics):
    metrics = copy.deepcopy(pass_metrics)
    metrics["spec_version"] = "0.9.0"
    metrics["dsr"] = 0.1
    result = evaluate_promotion_gates(
        geometry, metrics, live_geometry=geometry, feature_schema=feature_schema
    )
    assert result.failed_gate == "SPEC_VERSION"


def test_warn_path_left_tail_does_not_reject(geometry, feature_schema, pass_metrics):
    metrics = copy.deepcopy(pass_metrics)
    metrics["path_sharpe_p10"] = -0.90
    result = evaluate_promotion_gates(
        geometry, metrics, live_geometry=geometry, feature_schema=feature_schema
    )
    assert result.verdict == "PROMOTE"
    assert any("PATH_LEFT_TAIL" in w for w in result.warnings)


def test_optional_hard_path_left_tail(geometry, feature_schema, pass_metrics):
    geo = copy.deepcopy(geometry)
    geo["gates"]["path_left_tail_hard"] = True
    # Changing gates changes geometry_hash — rebuild metrics hash to isolate this gate.
    metrics = copy.deepcopy(pass_metrics)
    metrics["hashes"]["geometry_hash"] = geometry_hash(geo)
    metrics["path_sharpe_p10"] = -0.90
    result = evaluate_promotion_gates(
        geo, metrics, live_geometry=geo, feature_schema=feature_schema
    )
    assert result.failed_gate == "PATH_LEFT_TAIL"


def test_nan_pbo_and_costed_metrics_reject(geometry, feature_schema, pass_metrics):
    for field, gate in (
        ("pbo", "PBO_CEILING"),
        ("net_sharpe_after_costs", "NET_SHARPE"),
        ("costed_edge_bps", "COSTED_EDGE"),
        ("dsr", "DSR_MISSING"),
    ):
        metrics = copy.deepcopy(pass_metrics)
        metrics[field] = float("nan")
        result = evaluate_promotion_gates(
            geometry, metrics, live_geometry=geometry, feature_schema=feature_schema
        )
        assert result.verdict == "REJECT", field
        assert result.failed_gate == gate


def test_missing_feature_schema_required(geometry, pass_metrics):
    result = evaluate_promotion_gates(
        geometry, pass_metrics, live_geometry=geometry, require_feature_schema=True
    )
    assert result.failed_gate == "SCHEMA_HASH"


def test_zero_cost_dsr_clone_rejects(geometry, feature_schema, pass_metrics):
    metrics = copy.deepcopy(pass_metrics)
    metrics["zero_cost"] = {"dsr": metrics["dsr"], "net_sharpe": 1.2, "used_for_promotion": False}
    result = evaluate_promotion_gates(
        geometry, metrics, live_geometry=geometry, feature_schema=feature_schema
    )
    assert result.failed_gate == "ZERO_COST_ONLY"


def test_house_geometry_lock():
    geo = house_geometry()
    assert geo["triple_barrier"]["sl_atr_mult"] == 1.75
    assert geo["triple_barrier"]["pt_atr_mult"] == 5.5


def _run_evaluator(metrics_name: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(EVALUATOR),
            "--geometry",
            str(EXAMPLES / "geometry.json"),
            "--metrics",
            str(EXAMPLES / metrics_name),
            "--live-geometry",
            str(EXAMPLES / "geometry.json"),
            "--feature-schema",
            str(EXAMPLES / "feature_schema.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_reference_evaluator_pass_exits_zero():
    proc = _run_evaluator("metrics.pass.json")
    assert proc.returncode == 0, proc.stderr
    assert "PROMOTE" in proc.stdout


def test_reference_evaluator_shadow_exits_zero():
    proc = _run_evaluator("metrics.shadow.json")
    assert proc.returncode == 0, proc.stderr
    assert "SHADOW" in proc.stdout


def test_reference_evaluator_reject_exits_two():
    proc = _run_evaluator("metrics.fail_dsr.json")
    assert proc.returncode == 2
    assert "REJECT" in proc.stdout


def test_reference_evaluator_usage_exits_three():
    proc = subprocess.run(
        [sys.executable, str(EVALUATOR)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 3
