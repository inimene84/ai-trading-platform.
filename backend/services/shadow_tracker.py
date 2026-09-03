"""
Shadow PnL Tracker — score the trades the gates refused
========================================================
Every cycle the decision pipeline persists a `TradingSignal` row for EVERY
evaluation — including the ones that were vetoed or blocked (Kronos veto,
RANGING block, funding gate, min-edge gate, expectancy gate, LLM risk
reviewer, correlation caps, ...). Today those rows are write-only: nobody
knows whether a gate is *saving* money (blocking losers) or *costing* money
(blocking winners).

This tracker closes that loop. For each non-executed directional signal it
reconstructs the hypothetical trade (entry = first bar close at/after the
signal; SL/TP from the signal row when present, else ATR defaults) and walks
forward 1h bars until SL/TP is touched or a horizon (default 48 bars) is
reached. The outcome is stored in `shadow_outcomes` and aggregated per gate:

    avg_shadow_r > 0  → the gate blocked net winners  → COSTING money
    avg_shadow_r < 0  → the gate blocked net losers   → SAVING money

Design:
  • Pure scoring function (`score_hypothetical`) — unit-testable, no I/O.
  • Bars are supplied by an injected fetcher so the module never imports
    broker clients directly (testable offline, reusable for cTrader/Binance).
  • Idempotent: a signal is scored at most once (unique signal_id).
  • Never raises into callers — the trading loop must not break because of
    analytics.

CLI: scripts/run_shadow_tracker.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Tunables ────────────────────────────────────────────────────────────────
HORIZON_BARS = 48          # score at most 48 x 1h bars forward (~2 days)
SIGNAL_LOOKBACK_HOURS = 96 # only score signals from the last 4 days
SL_ATR_MULT = 1.0          # reconstruction defaults == RiskConfig defaults
TP_ATR_MULT = 2.5
ATR_PERIOD = 14

# Signal statuses that mean "the gate said no" (executed = real trade, skip)
BLOCKED_STATUSES = {"rejected", "skipped", "evaluated"}


# ── Gate classification ─────────────────────────────────────────────────────
def classify_gate(reasoning: str, status: str) -> str:
    """Map a signal's reasoning text to the gate that blocked it."""
    r = (reasoning or "").lower()
    if "kronos" in r and ("veto" in r or "flip" in r):
        return "kronos_veto"
    if "vetoed by risk reviewer" in r or "risk reviewer" in r:
        return "llm_risk_reviewer"
    if "ranging" in r:
        return "ranging_block"
    if "min-edge" in r or "min edge" in r:
        return "min_edge_gate"
    if "funding" in r:
        return "funding_gate"
    if "expectancy" in r:
        return "expectancy_gate"
    if "same-direction" in r or "correlated" in r:
        return "correlation_cap"
    if "exposure cap" in r or "notional cap" in r:
        return "exposure_cap"
    if "max positions" in r:
        return "max_positions"
    if "blacklist" in r or "illiquid" in r:
        return "symbol_quality_gate"
    if "confidence below threshold" in r or "below threshold" in r:
        return "confidence_gate"
    if "ai opinion too weak" in r:
        return "ai_opinion_gate"
    if "cooldown" in r:
        return "cooldown"
    if status == "skipped":
        return "order_failed"
    return "other"


# ── ATR (matches decision_engine.atr_from_bars semantics) ───────────────────
def atr_from_bars(bars: List[Dict[str, Any]], fallback_price: float, periods: int = ATR_PERIOD) -> float:
    if not bars:
        return fallback_price * 0.02
    window = bars[-(periods + 1):] if len(bars) >= periods + 1 else bars
    if len(window) < 2:
        return fallback_price * 0.02
    trs = []
    for i in range(1, len(window)):
        h, l_val, prev_c = window[i]["high"], window[i]["low"], window[i - 1]["close"]
        trs.append(max(h - l_val, abs(h - prev_c), abs(l_val - prev_c)))
    return sum(trs) / len(trs) if trs else fallback_price * 0.02


# ── Bar timestamp parsing (tolerant: iso date / ms epoch / s epoch) ─────────
def bar_ts(bar: Dict[str, Any]) -> Optional[float]:
    v = bar.get("date") or bar.get("time") or bar.get("open_time")
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v / 1000.0 if v > 1e12 else float(v)
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


# ── Pure scoring ────────────────────────────────────────────────────────────
@dataclass
class ShadowScore:
    exit_price: float
    exit_reason: str          # "sl" | "tp" | "timeout"
    pnl_pct: float            # signed % move captured, direction-aware
    pnl_r: float              # PnL in R multiples (risk = entry→SL distance)
    mfe_r: float              # max favorable excursion (R)
    mae_r: float              # max adverse excursion (R, negative)
    bars_elapsed: int


def score_hypothetical(
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    bars_after: List[Dict[str, Any]],
    horizon: int = HORIZON_BARS,
) -> Optional[ShadowScore]:
    """Walk bars forward and resolve the hypothetical trade.

    Conservative same-bar rule: if a bar touches BOTH SL and TP we assume the
    stop was hit first (never credit the optimistic path).
    """
    if not bars_after or entry <= 0 or sl <= 0 or tp <= 0 or sl == entry:
        return None
    risk = abs(entry - sl)
    sign = 1.0 if direction == "BUY" else -1.0

    mfe = 0.0
    mae = 0.0
    for i, bar in enumerate(bars_after[:horizon], start=1):
        high, low, close = bar["high"], bar["low"], bar["close"]
        mfe = max(mfe, sign * (high - entry) / risk)
        mae = min(mae, sign * (low - entry) / risk)

        sl_hit = low <= sl if direction == "BUY" else high >= sl
        tp_hit = high >= tp if direction == "BUY" else low <= tp
        if sl_hit:  # checked first on purpose (conservative)
            return ShadowScore(sl, "sl", sign * (sl - entry) / entry * 100,
                               sign * (sl - entry) / risk, mfe, mae, i)
        if tp_hit:
            return ShadowScore(tp, "tp", sign * (tp - entry) / entry * 100,
                               sign * (tp - entry) / risk, mfe, mae, i)

    last = bars_after[min(horizon, len(bars_after)) - 1]["close"]
    n = min(horizon, len(bars_after))
    return ShadowScore(last, "timeout", sign * (last - entry) / entry * 100,
                       sign * (last - entry) / risk, mfe, mae, n)


def reconstruct_levels(
    direction: str, entry: float, bars_before: List[Dict[str, Any]],
) -> tuple[float, float]:
    """ATR-based SL/TP for signals that were blocked before levels existed."""
    atr = atr_from_bars(bars_before, entry)
    if direction == "BUY":
        return entry - atr * SL_ATR_MULT, entry + atr * TP_ATR_MULT
    return entry + atr * SL_ATR_MULT, entry - atr * TP_ATR_MULT


# ── DB row (created lazily to avoid importing models at module import time) ─
def ensure_shadow_table():
    """Create the shadow_outcomes table if it doesn't exist."""
    from backend.database.connection import engine, Base
    from backend.database.models import ShadowOutcome  # noqa: F401
    Base.metadata.create_all(bind=engine, tables=[ShadowOutcome.__table__])


# ── Main update pass ────────────────────────────────────────────────────────
async def run_shadow_update(
    bars_fetcher: Callable[[str], Any],
    lookback_hours: int = SIGNAL_LOOKBACK_HOURS,
    horizon: int = HORIZON_BARS,
) -> Dict[str, int]:
    """Score all unscored blocked signals within the lookback window.

    `bars_fetcher(symbol)` must return a list of 1h bars (oldest→newest),
    each with high/low/close and a timestamp usable by `bar_ts`.
    Returns counters for logging.
    """
    from backend.database.connection import SessionLocal
    from backend.database.models import TradingSignal, ShadowOutcome

    stats = {"candidates": 0, "scored": 0, "unresolved": 0, "errors": 0}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    db = SessionLocal()
    try:
        rows = (
            db.query(TradingSignal)
            .filter(
                TradingSignal.timestamp >= cutoff,
                TradingSignal.direction.in_(["BUY", "SELL"]),
                TradingSignal.status.in_(list(BLOCKED_STATUSES)),
            )
            .order_by(TradingSignal.timestamp.asc())
            .all()
        )
        already = {
            sid for (sid,) in db.query(ShadowOutcome.signal_id).all()
        }
        rows = [r for r in rows if r.id not in already]
        stats["candidates"] = len(rows)
    finally:
        db.close()

    bars_cache: Dict[str, List[Dict[str, Any]]] = {}
    for sig in rows:
        try:
            sym = sig.symbol
            if sym not in bars_cache:
                bars_cache[sym] = await bars_fetcher(sym) or []
            bars = bars_cache[sym]
            if len(bars) < 5:
                stats["errors"] += 1
                continue

            sig_ts = sig.timestamp
            if sig_ts.tzinfo is None:
                sig_ts = sig_ts.replace(tzinfo=timezone.utc)
            sig_epoch = sig_ts.timestamp()

            idx = next(
                (i for i, b in enumerate(bars) if (bar_ts(b) or 0) >= sig_epoch),
                None,
            )
            if idx is None:
                stats["unresolved"] += 1  # signal newer than our newest bar
                continue

            bars_before = bars[: idx + 1]
            bars_after = bars[idx + 1:]
            entry = float(sig.entry_price or bars[idx]["close"])
            if sig.stop_loss and sig.take_profit:
                sl, tp = float(sig.stop_loss), float(sig.take_profit)
            else:
                sl, tp = reconstruct_levels(sig.direction, entry, bars_before)

            if len(bars_after) < 1:
                stats["unresolved"] += 1
                continue
            resolved = any(
                (b["low"] <= sl if sig.direction == "BUY" else b["high"] >= sl) or
                (b["high"] >= tp if sig.direction == "BUY" else b["low"] <= tp)
                for b in bars_after[:horizon]
            )
            if not resolved and len(bars_after) < horizon:
                stats["unresolved"] += 1  # window still open — score next run
                continue

            score = score_hypothetical(sig.direction, entry, sl, tp, bars_after, horizon)
            if not score:
                stats["errors"] += 1
                continue

            db = SessionLocal()
            try:
                db.add(ShadowOutcome(
                    signal_id=sig.id,
                    symbol=sym,
                    direction=sig.direction,
                    gate=classify_gate(sig.reasoning, sig.status),
                    signal_time=sig_ts,
                    confidence=float(sig.confidence or 0.0),
                    entry_price=entry,
                    stop_loss=sl,
                    take_profit=tp,
                    exit_price=score.exit_price,
                    exit_reason=score.exit_reason,
                    pnl_pct=round(score.pnl_pct, 4),
                    pnl_r=round(score.pnl_r, 4),
                    mfe_r=round(score.mfe_r, 4),
                    mae_r=round(score.mae_r, 4),
                    bars_elapsed=score.bars_elapsed,
                ))
                db.commit()
                stats["scored"] += 1
            except Exception as e:
                db.rollback()
                logger.warning(f"shadow persist failed for signal {sig.id}: {e}")
                stats["errors"] += 1
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"shadow scoring failed for signal {getattr(sig, 'id', '?')}: {e}")
            stats["errors"] += 1

    return stats


# ── Aggregation report ──────────────────────────────────────────────────────
def shadow_report(days: int = 30) -> List[Dict[str, Any]]:
    """Per-gate aggregate: is each gate saving or costing money?

    avg_shadow_r > 0 → blocked trades would have WON  → gate is COSTING
    avg_shadow_r < 0 → blocked trades would have LOST → gate is SAVING
    """
    from sqlalchemy import func, cast, Integer
    from backend.database.connection import SessionLocal
    from backend.database.models import ShadowOutcome

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    db = SessionLocal()
    try:
        rows = (
            db.query(
                ShadowOutcome.gate,
                func.count(ShadowOutcome.id),
                func.avg(ShadowOutcome.pnl_r),
                func.avg(ShadowOutcome.pnl_pct),
                func.avg(ShadowOutcome.mfe_r),
                func.sum(cast(ShadowOutcome.pnl_r > 0, Integer)),
            )
            .filter(ShadowOutcome.signal_time >= cutoff)
            .group_by(ShadowOutcome.gate)
            .all()
        )
    finally:
        db.close()

    report = []
    for gate, n, avg_r, avg_pct, avg_mfe, wins in rows:
        win_rate = (wins or 0) / n if n else 0.0
        verdict = "COSTING" if (avg_r or 0) > 0.05 else "SAVING" if (avg_r or 0) < -0.05 else "NEUTRAL"
        report.append({
            "gate": gate,
            "blocked_signals": n,
            "win_rate_if_taken": round(win_rate, 3),
            "avg_shadow_r": round(float(avg_r or 0), 3),
            "avg_shadow_pnl_pct": round(float(avg_pct or 0), 3),
            "avg_mfe_r": round(float(avg_mfe or 0), 3),
            "verdict": verdict,
        })
    return sorted(report, key=lambda r: r["avg_shadow_r"], reverse=True)
