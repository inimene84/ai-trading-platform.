"""
Unit tests for Heuristic Timing Guard and PreExecutionGate (WP4)
"""

import pytest
from backend.services.timing_guard import evaluate_heuristic_timing, TimingGuardResult
from backend.services.kronos_gate import apply_pre_execution_gate, PreExecutionGateResult


def create_rejection_bars(n=30):
    bars = []
    base_price = 50000.0
    for i in range(n):
        open_p = base_price + i * 10
        close_p = open_p + 5
        high_p = close_p + 2
        low_p = open_p - 2
        bars.append({
            "date": f"2026-01-01T{i:02d}:00:00+00:00",
            "open": open_p,
            "high": high_p,
            "low": low_p,
            "close": close_p,
            "volume": 100.0
        })

    # Last candle: massive upper rejection wick
    bars[-1]["open"] = 50300.0
    bars[-1]["close"] = 50310.0
    bars[-1]["high"] = 50450.0  # 140pt upper wick!
    bars[-1]["low"] = 50295.0
    return bars


def test_heuristic_timing_rejection():
    bars = create_rejection_bars(30)
    res = evaluate_heuristic_timing(bars, proposed_signal="BUY")

    assert isinstance(res, TimingGuardResult)
    assert res.risk_score > 0.3
    assert len(res.warning_signals) > 0


def test_gate_kronos_cum5_veto():
    kronos_res = {
        "signal": "SELL",
        "confidence": 0.8,
        "cum_change_5_pct": -1.8,
        "max_adverse_excursion_pct": -2.2,
        "reversal_risk": True
    }

    res = apply_pre_execution_gate(
        strategy_signal="BUY",
        strategy_confidence=0.75,
        kronos_result=kronos_res,
        symbol="BTCUSDT"
    )

    assert res.action == "veto"
    assert res.final_signal == "NEUTRAL"
    assert "opposing drop" in res.reasoning


def test_gate_vision_veto():
    res = apply_pre_execution_gate(
        strategy_signal="BUY",
        strategy_confidence=0.70,
        kronos_result={"signal": "BUY", "confidence": 0.6, "cum_change_5_pct": 1.0},
        vision_approved=False,
        symbol="ETHUSDT"
    )

    assert res.action == "veto"
    assert res.final_signal == "NEUTRAL"
    assert "Vision LLM rejected" in res.reasoning


def test_gate_boost():
    kronos_res = {
        "signal": "BUY",
        "confidence": 0.80,
        "cum_change_5_pct": 2.2,
        "max_adverse_excursion_pct": 0.1,
        "reversal_risk": False
    }

    res = apply_pre_execution_gate(
        strategy_signal="BUY",
        strategy_confidence=0.60,
        kronos_result=kronos_res,
        symbol="SOLUSDT"
    )

    assert res.action == "boost"
    assert res.final_signal == "BUY"
    assert res.confidence > 0.60
