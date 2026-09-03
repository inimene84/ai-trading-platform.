"""Shadow-track blocked/vetoed signals by walking 1h bars to SL/TP.

CLI/cron only — never called from the live trading cycle.

Conservative same-bar rule: if one bar touches both SL and TP, assume the
stop was hit first (we cannot know intra-bar order from OHLC).
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import Integer, cast, func
from sqlalchemy.orm import Session

from backend.database.models import ShadowOutcome, TradingSignal
from backend.services.decision_engine import atr_from_bars

logger = logging.getLogger(__name__)

SHADOW_MAX_BARS = 48
DEFAULT_SL_ATR_MULT = 1.0
DEFAULT_TP_ATR_MULT = 2.5

# Specific substrings first. These match reasons actually written by
# decision_engine / trading_loop / kronos_gate — not invented labels.
_GATE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ranging", ("ranging regime", "ranging:")),
    ("kronos", (
        "preexecutiongate", "shadow_vetoed", "vetoed:",
        "kronos", "heuristic timing", "vision llm",
    )),
    ("llm_risk", ("risk reviewer",)),
    ("funding", ("funding rate",)),
    ("min_edge", ("min-edge", "min_edge")),
    ("correlation", (
        "direction notional", "exposure cap", "same direction", "correlation",
        "long notional", "short notional",
    )),
    ("expectancy", ("expectancy",)),
    ("max_positions", ("max positions",)),
    ("confidence", ("below threshold",)),
    ("cooldown", ("cooldown",)),
    ("margin", ("insufficient margin", "entries blocked", "kill switch")),
    ("liquidity", ("blacklist", "quote volume", "illiquid")),
    ("duplicate", ("already have", "position already open", "duplicate blocked")),
)


@dataclass
class ShadowScore:
    signal_id: int
    symbol: str
    direction: str
    gate: str
    signal_time: Optional[datetime]
    confidence: float
    entry_price: float
    stop_loss: float
    take_profit: float
    exit_price: float
    exit_reason: str
    pnl_pct: float
    pnl_r: float
    mfe_r: float
    mae_r: float
    bars_elapsed: int
    scored_at: Optional[datetime] = None


def classify_gate(reason: Optional[str], status: Optional[str] = None) -> str:
    """Map a persisted signal reason/status to a gate bucket."""
    blob = f"{status or ''} {reason or ''}".lower()
    for gate, needles in _GATE_RULES:
        if any(n in blob for n in needles):
            return gate
    status_l = (status or "").lower()
    if status_l in ("rejected", "skipped"):
        return "rejected"
    return "other"


def is_blocked_signal(status: Optional[str], reason: Optional[str]) -> bool:
    """True for signals that were not taken because a gate stood aside."""
    status_l = (status or "").lower()
    if status_l in ("executed", "filled", "open"):
        return False
    gate = classify_gate(reason, status)
    if status_l in ("rejected", "skipped"):
        return True
    return gate not in ("other",)


def reconstruct_brackets(
    direction: str,
    entry: float,
    bars: Sequence[Dict[str, Any]],
    sl_atr_mult: float = DEFAULT_SL_ATR_MULT,
    tp_atr_mult: float = DEFAULT_TP_ATR_MULT,
) -> tuple[float, float]:
    """ATR-reconstructed SL/TP when the signal row has none."""
    atr = atr_from_bars(list(bars), entry) if bars else abs(entry) * 0.02
    if atr <= 0:
        atr = abs(entry) * 0.02
    if direction == "BUY":
        return entry - atr * sl_atr_mult, entry + atr * tp_atr_mult
    return entry + atr * sl_atr_mult, entry - atr * tp_atr_mult


def walk_until_exit(
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    bars: Sequence[Dict[str, Any]],
    max_bars: int = SHADOW_MAX_BARS,
) -> Dict[str, Any]:
    """Walk OHLC bars until SL, TP, or timeout.

    Conservative: a bar that trades through both SL and TP counts as a stop.
    """
    risk = abs(entry - sl)
    if risk <= 0:
        risk = max(abs(entry) * 1e-6, 1e-9)
    direction = (direction or "BUY").upper()
    mfe_r = 0.0
    mae_r = 0.0
    last_close = entry
    used = 0

    for i, bar in enumerate(bars[: max(0, int(max_bars))]):
        used = i + 1
        high = float(bar.get("high", bar.get("close", entry)))
        low = float(bar.get("low", bar.get("close", entry)))
        last_close = float(bar.get("close", entry))

        if direction == "BUY":
            mfe_r = max(mfe_r, (high - entry) / risk)
            mae_r = max(mae_r, (entry - low) / risk)
            hit_sl = low <= sl
            hit_tp = high >= tp
        else:
            mfe_r = max(mfe_r, (entry - low) / risk)
            mae_r = max(mae_r, (high - entry) / risk)
            hit_sl = high >= sl
            hit_tp = low <= tp

        if hit_sl and hit_tp:
            return _finish_walk(direction, entry, sl, "sl", used, mfe_r, mae_r, risk)
        if hit_sl:
            return _finish_walk(direction, entry, sl, "sl", used, mfe_r, mae_r, risk)
        if hit_tp:
            return _finish_walk(direction, entry, tp, "tp", used, mfe_r, mae_r, risk)

    return _finish_walk(direction, entry, last_close, "timeout", used, mfe_r, mae_r, risk)


def _finish_walk(
    direction: str,
    entry: float,
    exit_price: float,
    reason: str,
    bars_elapsed: int,
    mfe_r: float,
    mae_r: float,
    risk: float,
) -> Dict[str, Any]:
    if direction == "BUY":
        pnl_pct = ((exit_price - entry) / entry) * 100.0 if entry else 0.0
        pnl_r = (exit_price - entry) / risk
    else:
        pnl_pct = ((entry - exit_price) / entry) * 100.0 if entry else 0.0
        pnl_r = (entry - exit_price) / risk
    return {
        "exit_price": float(exit_price),
        "exit_reason": reason,
        "pnl_pct": float(pnl_pct),
        "pnl_r": float(pnl_r),
        "mfe_r": float(mfe_r),
        "mae_r": float(mae_r),
        "bars_elapsed": int(bars_elapsed),
    }


def bars_after_signal(
    bars: Sequence[Dict[str, Any]],
    signal_time: Optional[datetime],
) -> List[Dict[str, Any]]:
    """Keep bars whose open/date is at or after the signal timestamp."""
    if not signal_time:
        return list(bars)
    if signal_time.tzinfo is None:
        signal_time = signal_time.replace(tzinfo=timezone.utc)
    cutoff = signal_time.timestamp()
    out: List[Dict[str, Any]] = []
    for bar in bars:
        raw = bar.get("date") or bar.get("timestamp") or bar.get("time")
        if raw is None:
            out.append(bar)
            continue
        try:
            if isinstance(raw, datetime):
                dt = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
            elif isinstance(raw, (int, float)):
                ts = float(raw)
                if ts > 1e12:
                    ts /= 1000.0
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            else:
                dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            if dt.timestamp() >= cutoff:
                out.append(bar)
        except Exception:
            out.append(bar)
    return out


def score_signal(
    signal: Any,
    path_bars: Sequence[Dict[str, Any]],
    atr_bars: Optional[Sequence[Dict[str, Any]]] = None,
) -> Optional[ShadowScore]:
    """Score one blocked signal. Returns None when entry/direction are unusable."""
    direction = str(getattr(signal, "direction", "") or "").upper()
    if direction not in ("BUY", "SELL"):
        return None
    entry = getattr(signal, "entry_price", None)
    try:
        entry = float(entry) if entry is not None else 0.0
    except (TypeError, ValueError):
        entry = 0.0
    if entry <= 0 and path_bars:
        try:
            entry = float(path_bars[0].get("open") or path_bars[0].get("close") or 0.0)
        except (TypeError, ValueError, AttributeError):
            entry = 0.0
    if entry <= 0:
        return None

    sl = getattr(signal, "stop_loss", None)
    tp = getattr(signal, "take_profit", None)
    try:
        sl = float(sl) if sl not in (None, 0, 0.0) else None
    except (TypeError, ValueError):
        sl = None
    try:
        tp = float(tp) if tp not in (None, 0, 0.0) else None
    except (TypeError, ValueError):
        tp = None
    if sl is None or tp is None:
        recon_sl, recon_tp = reconstruct_brackets(
            direction, entry, atr_bars or path_bars,
        )
        sl = sl if sl is not None else recon_sl
        tp = tp if tp is not None else recon_tp

    walked = walk_until_exit(direction, entry, sl, tp, path_bars)
    reason = getattr(signal, "reasoning", None) or getattr(signal, "reason", None)
    status = getattr(signal, "status", None)
    ts = getattr(signal, "timestamp", None)
    return ShadowScore(
        signal_id=int(getattr(signal, "id")),
        symbol=str(getattr(signal, "symbol", "") or ""),
        direction=direction,
        gate=classify_gate(reason, status),
        signal_time=ts if isinstance(ts, datetime) else None,
        confidence=float(getattr(signal, "confidence", 0.0) or 0.0),
        entry_price=entry,
        stop_loss=float(sl),
        take_profit=float(tp),
        scored_at=datetime.now(timezone.utc),
        **walked,
    )


def persist_score(db: Session, score: ShadowScore) -> bool:
    """Insert a shadow outcome. Returns False if signal_id already scored."""
    existing = db.query(ShadowOutcome).filter(
        cast(ShadowOutcome.signal_id, Integer) == int(score.signal_id)
    ).first()
    if existing is not None:
        return False
    payload = asdict(score)
    allowed = {c.name for c in ShadowOutcome.__table__.columns}
    allowed.discard("id")
    row_kwargs = {k: v for k, v in payload.items() if k in allowed and v is not None}
    row_kwargs["mfe_r"] = score.mfe_r
    row_kwargs["mae_r"] = score.mae_r
    db.add(ShadowOutcome(**row_kwargs))
    db.commit()
    return True


def gate_report(db: Session) -> List[Dict[str, Any]]:
    """Per-gate avg shadow R: >0 COSTING (blocked winners), <0 SAVING."""
    rows = (
        db.query(
            ShadowOutcome.gate,
            func.count(ShadowOutcome.id),
            func.avg(ShadowOutcome.pnl_r),
        )
        .group_by(ShadowOutcome.gate)
        .all()
    )
    report = []
    for gate, n, avg_r in rows:
        avg = float(avg_r or 0.0)
        if avg > 0:
            verdict = "COSTING"
        elif avg < 0:
            verdict = "SAVING"
        else:
            verdict = "NEUTRAL"
        report.append({
            "gate": gate,
            "n": int(n),
            "avg_shadow_r": avg,
            "verdict": verdict,
        })
    report.sort(key=lambda r: abs(r["avg_shadow_r"]), reverse=True)
    return report


def ensure_table(bind=None) -> None:
    """Create shadow_outcomes if missing (CLI / tests)."""
    from backend.database.connection import engine as default_engine
    from backend.database.models import Base

    Base.metadata.create_all(bind=bind or default_engine, tables=[ShadowOutcome.__table__])


def iter_blocked_signals(db: Session, lookback_limit: int = 2000) -> Iterable[TradingSignal]:
    q = (
        db.query(TradingSignal)
        .order_by(TradingSignal.id.desc())
        .limit(lookback_limit)
    )
    for row in q:
        if is_blocked_signal(row.status, row.reasoning):
            yield row


BarFetcher = Callable[[str], List[Dict[str, Any]]]


def update_shadows(
    db: Session,
    fetch_bars: BarFetcher,
    lookback_limit: int = 2000,
) -> Dict[str, int]:
    """Score new blocked signals. Idempotent on signal_id."""
    ensure_table(db.get_bind())
    scored = 0
    skipped = 0
    failed = 0
    for sig in iter_blocked_signals(db, lookback_limit=lookback_limit):
        existing = db.query(ShadowOutcome).filter(
            cast(ShadowOutcome.signal_id, Integer) == int(sig.id)
        ).first()
        if existing is not None:
            skipped += 1
            continue
        try:
            raw_bars = fetch_bars(sig.symbol) or []
            path = bars_after_signal(raw_bars, getattr(sig, "timestamp", None))
            score = score_signal(sig, path, atr_bars=raw_bars)
            if score is None:
                failed += 1
                continue
            if persist_score(db, score):
                scored += 1
            else:
                skipped += 1
        except Exception as err:
            logger.warning("shadow score failed for signal %s: %s", getattr(sig, "id", "?"), err)
            db.rollback()
            failed += 1
    return {"scored": scored, "skipped": skipped, "failed": failed}


def format_report(rows: Sequence[Dict[str, Any]]) -> str:
    if not rows:
        return "No shadow outcomes yet."
    lines = [
        f"{'gate':<16} {'n':>6} {'avg_R':>10} {'verdict':<8}",
        "-" * 44,
    ]
    for r in rows:
        lines.append(
            f"{r['gate']:<16} {r['n']:>6} {r['avg_shadow_r']:>10.3f} {r['verdict']:<8}"
        )
    return "\n".join(lines)
