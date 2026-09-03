"""Shadow tracker: hypothetical SL/TP walks for blocked/vetoed signals."""

from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import Integer, cast, create_engine
from sqlalchemy.orm import sessionmaker

from backend.database.models import Base, ShadowOutcome, TradingSignal
from backend.services.shadow_tracker import (
    ShadowScore,
    classify_gate,
    gate_report,
    is_blocked_signal,
    persist_score,
    reconstruct_brackets,
    score_signal,
    update_shadows,
    walk_until_exit,
)


def _bar(o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c, "volume": 1}


def test_classify_gate_from_real_reason_strings():
    assert classify_gate("RANGING regime: blocked early (saves Kronos/LLM cost)") == "ranging"
    assert classify_gate("vetoed: PreExecutionGate ACTIVE VETO: Kronos forecasts opposing drop") == "kronos"
    assert classify_gate("shadow_vetoed: Heuristic timing risk score high") == "kronos"
    assert classify_gate("blocked by funding rate") == "funding"
    assert classify_gate("vetoed by risk reviewer: too much event risk") == "llm_risk"
    assert classify_gate("strategy confidence below threshold (0.60) in TRENDING regime") == "confidence"
    assert classify_gate("BUY blocked: LONG notional $x > 4.0x equity") == "correlation"
    assert classify_gate("entry decision | LONG direction notional cap (4.0x equity)") == "correlation"
    assert classify_gate("max positions reached (10)") == "max_positions"
    assert classify_gate("  [ETHUSDT] SKIP (min-edge): expected capture") == "min_edge"
    assert classify_gate("entry decision", "executed") == "other"


def test_is_blocked_signal_skips_taken_trades():
    assert is_blocked_signal("executed", "entry decision") is False
    assert is_blocked_signal("rejected", "LONG direction notional cap") is True
    assert is_blocked_signal("evaluated", "RANGING regime: blocked early") is True
    assert is_blocked_signal("evaluated", "no strategy signal") is False


def test_walk_stop_hit_buy():
    bars = [_bar(100, 101, 97, 98), _bar(98, 99, 96, 97)]
    out = walk_until_exit("BUY", 100.0, 97.5, 105.0, bars)
    assert out["exit_reason"] == "sl"
    assert out["exit_price"] == 97.5
    assert out["pnl_r"] < 0
    assert out["bars_elapsed"] == 1


def test_walk_tp_hit_sell():
    bars = [_bar(100, 101, 96, 97)]
    out = walk_until_exit("SELL", 100.0, 104.0, 96.5, bars)
    assert out["exit_reason"] == "tp"
    assert out["pnl_r"] > 0


def test_walk_conservative_same_bar_assumes_stop_first():
    # Bar trades through both SL and TP.
    bars = [_bar(100, 110, 90, 105)]
    buy = walk_until_exit("BUY", 100.0, 95.0, 108.0, bars)
    assert buy["exit_reason"] == "sl"
    assert buy["pnl_r"] < 0
    sell = walk_until_exit("SELL", 100.0, 108.0, 92.0, bars)
    assert sell["exit_reason"] == "sl"
    assert sell["pnl_r"] < 0


def test_walk_timeout_uses_last_close():
    bars = [_bar(100, 101, 99, 100.5) for _ in range(5)]
    out = walk_until_exit("BUY", 100.0, 90.0, 120.0, bars, max_bars=5)
    assert out["exit_reason"] == "timeout"
    assert out["exit_price"] == 100.5
    assert out["bars_elapsed"] == 5
    assert out["mfe_r"] > 0


def test_walk_records_mfe_r_mae_r_field_names():
    bars = [_bar(100, 104, 98, 101)]
    out = walk_until_exit("BUY", 100.0, 95.0, 110.0, bars)
    assert "mfe_r" in out and "mae_r" in out
    assert "mfe" not in out and "mae" not in out
    assert out["mfe_r"] == (104 - 100) / 5.0
    assert out["mae_r"] == (100 - 98) / 5.0


def test_reconstruct_brackets_direction_aware():
    bars = [_bar(100 + i, 101 + i, 99 + i, 100 + i) for i in range(20)]
    sl_b, tp_b = reconstruct_brackets("BUY", 100.0, bars)
    sl_s, tp_s = reconstruct_brackets("SELL", 100.0, bars)
    assert sl_b < 100.0 < tp_b
    assert tp_s < 100.0 < sl_s


def test_score_signal_uses_own_sl_tp_when_present():
    sig = SimpleNamespace(
        id=7, symbol="ETHUSDT", direction="BUY", confidence=0.7,
        entry_price=100.0, stop_loss=95.0, take_profit=110.0,
        reasoning="vetoed: Kronos forecasts opposing drop",
        status="evaluated", timestamp=datetime.now(timezone.utc),
    )
    bars = [_bar(100, 101, 94, 96)]
    score = score_signal(sig, bars)
    assert score is not None
    assert score.stop_loss == 95.0
    assert score.take_profit == 110.0
    assert score.exit_reason == "sl"
    assert score.gate == "kronos"
    assert score.mfe_r >= 0
    payload = score.__dict__
    assert "mfe_r" in payload and "mae_r" in payload


def test_persist_idempotent_on_signal_id():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)
    score = ShadowScore(
        signal_id=42, symbol="ETHUSDT", direction="BUY", gate="ranging",
        signal_time=now, confidence=0.6, entry_price=100.0, stop_loss=95.0,
        take_profit=110.0, exit_price=95.0, exit_reason="sl",
        pnl_pct=-5.0, pnl_r=-1.0, mfe_r=0.2, mae_r=1.0,
        bars_elapsed=3, scored_at=now,
    )
    assert persist_score(db, score) is True
    assert persist_score(db, score) is False
    assert db.query(ShadowOutcome).count() == 1
    row = db.query(ShadowOutcome).filter(
        cast(ShadowOutcome.signal_id, Integer) == 42
    ).one()
    assert row.mfe_r == 0.2
    assert row.mae_r == 1.0


def test_update_shadows_skips_already_scored_and_taken_trades():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(TradingSignal(
        symbol="ETHUSDT", strategy="combined", direction="BUY",
        confidence=0.7, entry_price=100.0, stop_loss=95.0, take_profit=110.0,
        status="evaluated", reasoning="RANGING regime: blocked early (saves Kronos/LLM cost)",
    ))
    db.add(TradingSignal(
        symbol="ETHUSDT", strategy="combined", direction="BUY",
        confidence=0.8, entry_price=100.0, stop_loss=95.0, take_profit=110.0,
        status="executed", reasoning="entry decision",
    ))
    db.commit()

    bars = [_bar(100, 102, 99, 101) for _ in range(10)]
    stats = update_shadows(db, lambda _sym: bars, lookback_limit=50)
    assert stats["scored"] == 1
    stats2 = update_shadows(db, lambda _sym: bars, lookback_limit=50)
    assert stats2["scored"] == 0
    assert stats2["skipped"] >= 1
    report = gate_report(db)
    assert any(r["gate"] == "ranging" for r in report)


def test_gate_report_costing_vs_saving():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)
    persist_score(db, ShadowScore(
        signal_id=1, symbol="ETHUSDT", direction="BUY", gate="ranging",
        signal_time=now, confidence=0.6, entry_price=100, stop_loss=95,
        take_profit=110, exit_price=110, exit_reason="tp",
        pnl_pct=10.0, pnl_r=2.0, mfe_r=2.0, mae_r=0.1,
        bars_elapsed=4, scored_at=now,
    ))
    persist_score(db, ShadowScore(
        signal_id=2, symbol="ETHUSDT", direction="BUY", gate="kronos",
        signal_time=now, confidence=0.6, entry_price=100, stop_loss=95,
        take_profit=110, exit_price=95, exit_reason="sl",
        pnl_pct=-5.0, pnl_r=-1.0, mfe_r=0.2, mae_r=1.0,
        bars_elapsed=2, scored_at=now,
    ))
    report = {r["gate"]: r for r in gate_report(db)}
    assert report["ranging"]["verdict"] == "COSTING"
    assert report["kronos"]["verdict"] == "SAVING"
