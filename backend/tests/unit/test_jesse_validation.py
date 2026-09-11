"""Unit tests for Jesse ML validation gate helpers."""

import pytest

from backend.services.jesse_validation import (
    evaluate_validation_gates,
    extract_validation_metrics,
    jesse_pbo_max,
    jesse_dsr_min,
)


def test_extract_validation_metrics_from_nested_payload():
    metadata = {
        "metrics": {
            "deflated_sharpe_ratio": 0.98,
            "prob_backtest_overfitting": 0.22,
            "holdout_sharpe": 2.5,
        }
    }
    extracted = extract_validation_metrics(metadata)
    assert extracted["dsr"] == pytest.approx(0.98)
    assert extracted["pbo"] == pytest.approx(0.22)
    assert extracted["holdout_sharpe"] == pytest.approx(2.5)


def test_evaluate_validation_gates_pass():
    metadata = {
        "metrics": {
            "deflated_sharpe_ratio": 1.0,
            "prob_backtest_overfitting": 0.15,
        }
    }
    result = evaluate_validation_gates(metadata)
    assert result["deployment_ok"] is True
    assert result["dsr_pass"] is True
    assert result["pbo_pass"] is True
    assert result["reasons"] == []


def test_evaluate_validation_gates_fail_on_high_pbo():
    metadata = {
        "metrics": {
            "deflated_sharpe_ratio": 1.0,
            "prob_backtest_overfitting": 0.636,
        }
    }
    result = evaluate_validation_gates(metadata)
    assert result["deployment_ok"] is False
    assert result["pbo_pass"] is False
    assert any("PBO" in r for r in result["reasons"])


def test_evaluate_validation_gates_override(monkeypatch):
    monkeypatch.setenv("JESSE_ML_PBO_OVERRIDE", "true")
    metadata = {"metrics": {"deflated_sharpe_ratio": 0.5, "prob_backtest_overfitting": 0.9}}
    result = evaluate_validation_gates(metadata)
    assert result["deployment_ok"] is True
    assert any("OVERRIDE" in r for r in result["reasons"])


def test_gate_thresholds_from_env(monkeypatch):
    monkeypatch.setenv("JESSE_ML_PBO_MAX", "0.25")
    monkeypatch.setenv("JESSE_ML_DSR_MIN", "0.99")
    assert jesse_pbo_max() == pytest.approx(0.25)
    assert jesse_dsr_min() == pytest.approx(0.99)
