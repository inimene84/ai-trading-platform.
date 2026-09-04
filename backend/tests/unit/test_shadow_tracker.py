"""Shadow tracker: classify_gate needles, conservative walks, persist fields."""

from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import Integer, cast, create_engine
from sqlalchemy.orm import sessionmaker

from backend.database.models import Base, ShadowOutcome, TradingSignal
from backend.services.shadow_tracker import (
    classify_gate,
    is_crypto_symbol,
    reconstruct_levels,
    score_hypothetical,
    shadow_report,
)
from backend.services import shadow_tracker as shadow_mod


def _bar(h, l, c, date=None):
    out = {"open": c, "high": h, "low": l, "close": c, "volume": 1}
    if date is not None:
        out["date"] = date
    return out


# ── classify_gate matches THIS branch's persisted reason strings ─────────────

def test_classify_gate_from_deploy_branch_reason_strings():
    assert classify_gate(
        "RANGING regime: blocked early (saves Kronos/LLM cost)"
    ) == "ranging_block"
    assert classify_gate(
        "vetoed: PreExecutionGate VETO [ETHUSDT]: Kronos forecasts opposing drop"
    ) == "kronos_veto"
    assert classify_gate(
        "vetoed: PreExecutionGate VETO [ETHUSDT]: Heuristic timing risk score high (0.82)."
    ) == "kronos_veto"
    assert classify_gate(
        "shadow_vetoed: PreExecutionGate SHADOW VETO [ETHUSDT]: Vision LLM rejected entry timing"
    ) == "kronos_veto"
    assert classify_gate("blocked by funding rate") == "funding_gate"
    assert classify_gate(
        "vetoed by risk reviewer: too much event risk"
    ) == "llm_risk_reviewer"
    assert classify_gate(
        "strategy confidence below threshold (0.60) in TRENDING regime"
    ) == "confidence_gate"
    assert classify_gate(
        "strategy confidence below threshold (0.60) in RANGING regime"
    ) == "confidence_gate"
    assert classify_gate(
        "entry decision | LONG direction notional cap (4.0x equity)"
    ) == "correlation_cap"
    assert classify_gate("BUY blocked: LONG exposure cap reached") == "exposure_cap"
    assert classify_gate("max positions reached (10)") == "max_positions"
    assert classify_gate(
        "SKIP (min-edge): expected capture below round-trip cost"
    ) == "min_edge_gate"
    assert classify_gate("AI opinion too weak (<0.30)") == "ai_opinion_gate"
    assert classify_gate("Insufficient margin: available=1.2 < 5.0") == "margin_gate"
    assert classify_gate("entry decision", "executed") == "other"


def test_classify_gate_skipped_status_is_order_failed():
    assert classify_gate("entry decision | order failed: timeout", "skipped") == "order_failed"


# ── score_hypothetical: SL-first, direction-aware MFE/MAE ────────────────────

def test_same_bar_touching_sl_and_tp_is_stop_first():
    bars = [_bar(110, 90, 105)]
    buy = score_hypothetical("BUY", 100.0, 95.0, 108.0, bars)
    assert buy.exit_reason == "sl"
    assert buy.exit_price == 95.0
    assert buy.pnl_r < 0
    sell = score_hypothetical("SELL", 100.0, 108.0, 92.0, bars)
    assert sell.exit_reason == "sl"
    assert sell.pnl_r < 0


def test_buy_tp_and_sell_sl():
    tp = score_hypothetical("BUY", 100.0, 95.0, 110.0, [_bar(111, 99, 110)])
    assert tp.exit_reason == "tp"
    assert tp.pnl_r > 0
    sl = score_hypothetical("SELL", 100.0, 104.0, 90.0, [_bar(105, 99, 104)])
    assert sl.exit_reason == "sl"
    assert sl.pnl_r < 0


def test_timeout_uses_last_close():
    bars = [_bar(101, 99, 100.5) for _ in range(5)]
    out = score_hypothetical("BUY", 100.0, 90.0, 120.0, bars, horizon=5)
    assert out.exit_reason == "timeout"
    assert out.exit_price == 100.5
    assert out.bars_elapsed == 5


def test_mfe_mae_direction_aware_and_persist_field_names():
    buy = score_hypothetical("BUY", 100.0, 95.0, 110.0, [_bar(104, 98, 101)])
    assert buy.mfe_r == pytest.approx((104 - 100) / 5.0)
    assert buy.mae_r == pytest.approx((98 - 100) / 5.0)
    assert buy.mae_r < 0

    # Short: favorable is the low, adverse is the high. Old sign*(high-entry)
    # inverted these to ~0.
    sell = score_hypothetical("SELL", 100.0, 105.0, 90.0, [_bar(103, 96, 99)])
    assert sell.mfe_r == pytest.approx((100 - 96) / 5.0)
    assert sell.mae_r == pytest.approx((100 - 103) / 5.0)
    assert sell.mfe_r > 0
    assert sell.mae_r < 0


def test_reconstruct_levels_are_direction_aware():
    bars = [_bar(101 + i, 99 + i, 100 + i) for i in range(20)]
    sl_b, tp_b = reconstruct_levels("BUY", 100.0, bars)
    sl_s, tp_s = reconstruct_levels("SELL", 100.0, bars)
    assert sl_b < 100.0 < tp_b
    assert tp_s < 100.0 < sl_s


def test_is_crypto_symbol_does_not_route_ctrader_btcusd_to_binance():
    assert is_crypto_symbol("ETHUSDT") is True
    assert is_crypto_symbol("BTCUSD") is False
    assert is_crypto_symbol("EURUSD") is False
    assert is_crypto_symbol("XAUUSD") is False


# ── persist: unique signal_id, mfe_r/mae_r columns, sqlalchemy.cast Integer ──

def test_shadow_outcome_idempotent_signal_id_and_report_cast():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    now = datetime.now(timezone.utc)

    db = Session()
    db.add(ShadowOutcome(
        signal_id=42, symbol="ETHUSDT", direction="BUY", gate="ranging_block",
        signal_time=now, confidence=0.6, entry_price=100.0, stop_loss=95.0,
        take_profit=110.0, exit_price=95.0, exit_reason="sl",
        pnl_pct=-5.0, pnl_r=-1.0, mfe_r=0.2, mae_r=-0.4, bars_elapsed=3,
    ))
    db.commit()
    db.add(ShadowOutcome(
        signal_id=42, symbol="ETHUSDT", direction="BUY", gate="ranging_block",
        signal_time=now, confidence=0.6, entry_price=100.0, stop_loss=95.0,
        take_profit=110.0, exit_price=95.0, exit_reason="sl",
        pnl_pct=-5.0, pnl_r=-1.0, mfe_r=0.2, mae_r=-0.4, bars_elapsed=3,
    ))
    with pytest.raises(Exception):
        db.commit()
    db.rollback()
    assert db.query(ShadowOutcome).count() == 1
    row = db.query(ShadowOutcome).filter(
        cast(ShadowOutcome.signal_id, Integer) == 42
    ).one()
    assert row.mfe_r == 0.2
    assert row.mae_r == -0.4
    db.close()

    db = Session()
    db.add(ShadowOutcome(
        signal_id=7, symbol="ETHUSDT", direction="BUY", gate="kronos_veto",
        signal_time=now, confidence=0.6, entry_price=100.0, stop_loss=95.0,
        take_profit=110.0, exit_price=110.0, exit_reason="tp",
        pnl_pct=10.0, pnl_r=2.0, mfe_r=2.0, mae_r=-0.1, bars_elapsed=4,
    ))
    db.commit()
    db.close()

    from backend.database import connection as conn_mod
    prev = conn_mod.SessionLocal
    conn_mod.SessionLocal = Session
    try:
        report = {r["gate"]: r for r in shadow_report(days=30)}
    finally:
        conn_mod.SessionLocal = prev

    assert report["ranging_block"]["verdict"] == "SAVING"
    assert report["kronos_veto"]["verdict"] == "COSTING"
    assert "avg_mfe_r" in report["kronos_veto"]


@pytest.mark.asyncio
async def test_run_shadow_update_skips_executed_and_is_idempotent(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr("backend.database.connection.SessionLocal", Session)

    now = datetime.now(timezone.utc)
    db = Session()
    db.add(TradingSignal(
        symbol="ETHUSDT", strategy="combined", direction="BUY",
        confidence=0.7, entry_price=100.0, stop_loss=95.0, take_profit=110.0,
        status="evaluated",
        reasoning="RANGING regime: blocked early (saves Kronos/LLM cost)",
        timestamp=now - timedelta(hours=2),
    ))
    db.add(TradingSignal(
        symbol="ETHUSDT", strategy="combined", direction="BUY",
        confidence=0.8, entry_price=100.0, stop_loss=95.0, take_profit=110.0,
        status="executed", reasoning="entry decision",
        timestamp=now - timedelta(hours=2),
    ))
    db.commit()
    db.close()

    start = now - timedelta(hours=3)
    bars = []
    for i in range(10):
        ts = (start + timedelta(hours=i)).isoformat()
        bars.append(_bar(102, 99, 101, date=ts))

    async def fetch(_sym):
        return bars

    stats = await shadow_mod.run_shadow_update(fetch, lookback_hours=96, horizon=8)
    assert stats["scored"] == 1
    stats2 = await shadow_mod.run_shadow_update(fetch, lookback_hours=96, horizon=8)
    assert stats2["scored"] == 0
    db = Session()
    assert db.query(ShadowOutcome).count() == 1
    row = db.query(ShadowOutcome).one()
    assert row.gate == "ranging_block"
    assert row.mfe_r is not None and row.mae_r is not None
    db.close()
