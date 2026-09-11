#!/usr/bin/env python3
"""
label_study.py — read-only diagnostic for triple-barrier label degeneracy.

Motivation
----------
The Jesse LightGBM directional model collapses to a single class. This script
tests whether that collapse is explained by the *labels* rather than the model,
by measuring, on real candles:

  1. the three-way barrier outcome distribution across a (pt_mult, sl_mult) grid
  2. how that distribution moves with the max-holding horizon
  3. the majority-class share (the accuracy a constant predictor achieves)
  4. the ATR scale, i.e. how large `pt_mult * ATR` is in percent of price and
     how often price actually travels that far within the horizon
  5. the meta-label (TP-before-SL | primary signal fired) balance, as the
     Lopez de Prado alternative framing

Nothing here trains a model, touches the database directly, or writes outside
the directory given by --out. Input is a CSV of pre-aggregated OHLCV bars
(see extract_1h.sh, which does the 1m -> 1h roll-up inside Postgres).

The barrier logic is a vectorised re-implementation of the repo's
`triple_barrier.apply_triple_barrier`. Run with --repo-dir pointing at a copy
of the repo to assert bit-for-bit parity with the original loop before any
results are reported.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Live execution geometry (QuantumAIStrategy).
DEPLOYED_PT = 5.5
DEPLOYED_SL = 1.75

GEOMETRY_GRID: List[Tuple[float, float]] = [
    (5.5, 1.75),   # deployed
    (4.0, 2.0),    # previous training geometry
    (1.5, 1.5),
    (2.0, 2.0),
    (3.0, 2.0),
    (2.0, 3.0),
    (3.0, 3.0),
    (4.0, 4.0),
    (5.5, 5.5),
]

HORIZON_GRID: List[int] = [12, 24, 48, 96, 168, 336]


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------
def load_bars(csv_path: str, n_bars: Optional[int]) -> pd.DataFrame:
    """Load the aggregated OHLCV CSV and optionally keep the most recent n_bars."""
    df = pd.read_csv(csv_path)
    df["datetime"] = pd.to_datetime(df["bucket"], unit="ms", utc=True)
    df = df.set_index("datetime").sort_index()
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    if n_bars is not None and len(df) > n_bars:
        df = df.iloc[-n_bars:]
    return df


def get_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Identical to triple_barrier.get_atr: EWM(span=period, adjust=False) of true range."""
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


# --------------------------------------------------------------------------
# vectorised triple barrier
# --------------------------------------------------------------------------
def triple_barrier_fast(
    df: pd.DataFrame,
    pt_multiplier: float,
    sl_multiplier: float,
    max_holding_bars: int,
    event_mask: Optional[np.ndarray] = None,
) -> pd.DataFrame:
    """
    Vectorised equivalent of triple_barrier.apply_triple_barrier.

    Semantics preserved from the original, including the two that matter most:
      * when both barriers fall inside the same bar the stop is assumed first
      * a vertical-barrier expiry is NOT labelled 0 unconditionally; it is
        re-labelled +-1 unless |return| <= 0.2 * sl_multiplier * ATR

    `touch_type` is the honest three-way outcome ('pt' / 'sl' / 'timeout');
    `label` is what the trainer consumes after the timeout re-label.
    """
    n = len(df)
    if n <= max_holding_bars:
        raise ValueError("not enough bars for the requested horizon")

    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    vol = get_atr(df, period=14).to_numpy(dtype=float)

    ev = np.arange(0, n - max_holding_bars, dtype=int)
    if event_mask is not None:
        ev = ev[event_mask[: len(ev)]]
    if ev.size == 0:
        raise ValueError("no events selected")

    entry = close[ev]
    v = vol[ev].copy()
    bad = ~np.isfinite(v) | (v <= 0)
    v[bad] = entry[bad] * 0.01

    upper = entry + pt_multiplier * v
    lower = entry - sl_multiplier * v

    active = np.ones(ev.size, dtype=bool)
    label = np.zeros(ev.size, dtype=int)
    touch_step = np.full(ev.size, max_holding_bars, dtype=int)
    touch_type = np.full(ev.size, "timeout", dtype=object)
    ret_pct = np.zeros(ev.size, dtype=float)

    for step in range(1, max_holding_bars + 1):
        if not active.any():
            break
        j = ev + step
        tp_hit = high[j] >= upper
        sl_hit = low[j] <= lower
        hit = active & (tp_hit | sl_hit)
        if not hit.any():
            continue
        sl_first = hit & sl_hit           # ties resolve to the stop
        tp_first = hit & tp_hit & ~sl_hit
        label[sl_first] = -1
        touch_type[sl_first] = "sl"
        ret_pct[sl_first] = (lower[sl_first] / entry[sl_first] - 1.0) * 100.0
        label[tp_first] = 1
        touch_type[tp_first] = "pt"
        ret_pct[tp_first] = (upper[tp_first] / entry[tp_first] - 1.0) * 100.0
        touch_step[hit] = step
        active &= ~hit

    if active.any():
        final = close[ev[active] + max_holding_bars]
        r = (final / entry[active] - 1.0) * 100.0
        thr = 0.2 * sl_multiplier * v[active] / entry[active] * 100.0
        lab = np.where(r > thr, 1, np.where(r < -thr, -1, 0))
        ret_pct[active] = r
        label[active] = lab

    return pd.DataFrame(
        {
            "entry_price": entry,
            "trgt": v,
            "ret": ret_pct,
            "label": label,
            "touch_type": touch_type,
            "bars_to_touch": touch_step,
        },
        index=df.index[ev],
    )


def parity_check(df: pd.DataFrame, repo_dir: str, pt: float, sl: float, horizon: int, n_bars: int) -> Dict:
    """Assert the vectorised implementation matches the repo loop exactly."""
    sys.path.insert(0, repo_dir)
    from triple_barrier import apply_triple_barrier  # type: ignore

    sub = df.iloc[:n_bars]
    ref = apply_triple_barrier(
        sub, pt_multiplier=pt, sl_multiplier=sl, max_holding_bars=horizon
    )
    mine = triple_barrier_fast(sub, pt, sl, horizon)
    mine = mine.loc[ref.index]
    return {
        "repo_dir": repo_dir,
        "geometry": f"{pt}/{sl}",
        "horizon": horizon,
        "n_events": int(len(ref)),
        "label_mismatches": int((ref["label"].to_numpy() != mine["label"].to_numpy()).sum()),
        "touch_type_mismatches": int(
            (ref["touch_type"].to_numpy() != mine["touch_type"].to_numpy()).sum()
        ),
        "max_abs_ret_diff": float(np.max(np.abs(ref["ret"].to_numpy() - mine["ret"].to_numpy()))),
        "max_abs_trgt_diff": float(np.max(np.abs(ref["trgt"].to_numpy() - mine["trgt"].to_numpy()))),
    }


# --------------------------------------------------------------------------
# summaries
# --------------------------------------------------------------------------
def summarise(tb: pd.DataFrame, pt: float, sl: float, horizon: int) -> Dict:
    n = len(tb)
    tt = tb["touch_type"].value_counts()
    lab = tb["label"].value_counts()

    # what the trainer actually sees: +1 -> class 1, -1 -> class 2, 0 -> class 0
    trainer_counts = {
        "class1_bullish": int(lab.get(1, 0)),
        "class2_bearish": int(lab.get(-1, 0)),
        "class0_neutral": int(lab.get(0, 0)),
    }
    majority = max(trainer_counts.values()) / n

    up = tb.loc[tb["touch_type"] == "pt", "bars_to_touch"]
    dn = tb.loc[tb["touch_type"] == "sl", "bars_to_touch"]

    n_touched = len(up) + len(dn)
    return {
        "pt_mult": pt,
        "sl_mult": sl,
        "horizon_bars": horizon,
        "payoff_ratio": pt / sl,
        # For a driftless walk the up barrier is hit first with probability
        # sl/(pt+sl), which is also the break-even win rate at this payoff.
        # Empirical up-first rates converging on this value mean the labels
        # carry no unconditional information beyond the geometry itself.
        "breakeven_winrate": sl / (pt + sl),
        "martingale_up_first": sl / (pt + sl),
        "n_events": n,
        "frac_up_first": float(tt.get("pt", 0) / n),
        "frac_down_first": float(tt.get("sl", 0) / n),
        "frac_timeout": float(tt.get("timeout", 0) / n),
        "frac_up_first_given_touched": float(len(up) / n_touched) if n_touched else None,
        "trainer_label_counts": trainer_counts,
        "trainer_label_fracs": {k: v / n for k, v in trainer_counts.items()},
        "majority_class_share": float(majority),
        "up_bars_to_touch_median": float(up.median()) if len(up) else None,
        "up_bars_to_touch_p90": float(up.quantile(0.90)) if len(up) else None,
        "down_bars_to_touch_median": float(dn.median()) if len(dn) else None,
    }


def excursion_stats(df: pd.DataFrame, horizon: int, pt: float, sl: float) -> Dict:
    """
    How far does price actually travel, in ATR units, within `horizon` bars?
    This is barrier-independent and answers 'is the target even reachable'.
    """
    atr = get_atr(df, 14)
    high, low, close = df["high"], df["low"], df["close"]
    fwd_max = high.rolling(horizon).max().shift(-horizon)
    fwd_min = low.rolling(horizon).min().shift(-horizon)

    mfe_atr = ((fwd_max - close) / atr).iloc[: len(df) - horizon].dropna()
    mae_atr = ((close - fwd_min) / atr).iloc[: len(df) - horizon].dropna()

    qs = [0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99]
    return {
        "horizon_bars": horizon,
        "n": int(len(mfe_atr)),
        "mfe_atr_quantiles": {f"p{int(q*100)}": float(mfe_atr.quantile(q)) for q in qs},
        "mae_atr_quantiles": {f"p{int(q*100)}": float(mae_atr.quantile(q)) for q in qs},
        "frac_mfe_reaches_pt": float((mfe_atr >= pt).mean()),
        "frac_mae_reaches_sl": float((mae_atr >= sl).mean()),
        "frac_both_reachable": float(((mfe_atr >= pt) & (mae_atr >= sl)).mean()),
        "frac_neither_reachable": float(((mfe_atr < pt) & (mae_atr < sl)).mean()),
    }


def atr_scale(df: pd.DataFrame, pt: float) -> Dict:
    atr = get_atr(df, 14)
    frac = (atr / df["close"]).dropna()
    qs = [0.05, 0.25, 0.50, 0.75, 0.95]
    return {
        "atr_over_price_pct": {f"p{int(q*100)}": float(frac.quantile(q) * 100) for q in qs},
        "atr_over_price_mean_pct": float(frac.mean() * 100),
        "pt_move_pct": {f"p{int(q*100)}": float(frac.quantile(q) * pt * 100) for q in qs},
        "pt_move_mean_pct": float(frac.mean() * pt * 100),
    }


def conditional_rates(df: pd.DataFrame, pt: float, sl: float, horizon: int, n_buckets: int = 5) -> Dict:
    """
    Descriptive only — no model is fitted. Splits events into quantile buckets of
    a few cheap indicators and reports the up-first rate per bucket. Spread across
    buckets is evidence that the label has conditional structure worth learning;
    a flat profile means even a perfect classifier has nothing to find.
    """
    tb = triple_barrier_fast(df, pt, sl, horizon)
    close = df["close"]
    delta = close.diff()
    avg_gain = delta.clip(lower=0.0).ewm(span=14, adjust=False).mean()
    avg_loss = (-delta).clip(lower=0.0).ewm(span=14, adjust=False).mean()
    feats = {
        "rsi_14": 100.0 - (100.0 / (1.0 + avg_gain / (avg_loss + 1e-12))),
        "close_over_ema50": close / close.ewm(span=50, adjust=False).mean(),
        "atr_over_price": get_atr(df, 14) / close,
        "ret_24": close.pct_change(24),
    }
    is_up = (tb["touch_type"] == "pt").astype(float)
    out: Dict = {"horizon_bars": horizon, "base_up_first_rate": float(is_up.mean()), "features": {}}
    for name, series in feats.items():
        s = series.reindex(tb.index)
        try:
            buckets = pd.qcut(s, n_buckets, labels=False, duplicates="drop")
        except ValueError:
            continue
        grp = is_up.groupby(buckets).agg(["mean", "size"])
        rates = [float(x) for x in grp["mean"].tolist()]
        out["features"][name] = {
            "up_first_rate_by_quintile": rates,
            "counts": [int(x) for x in grp["size"].tolist()],
            "spread_pp": float((max(rates) - min(rates)) * 100) if rates else None,
        }
    return out


# --------------------------------------------------------------------------
# meta-labelling
# --------------------------------------------------------------------------
def quantum_ai_event_mask(df: pd.DataFrame) -> np.ndarray:
    """Bars where QuantumAIStrategy's long filter fires (copied from train_ml.py)."""
    close = df["close"]
    ema_fast = close.ewm(span=20, adjust=False).mean()
    ema_mid = close.ewm(span=50, adjust=False).mean()
    ema_slow = close.ewm(span=200, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(span=14, adjust=False).mean()
    avg_loss = loss.ewm(span=14, adjust=False).mean()
    rsi = 100.0 - (100.0 / (1.0 + avg_gain / (avg_loss + 1e-12)))
    mask = (
        (close > ema_slow)
        & (ema_mid > ema_slow)
        & (close <= ema_fast * 1.005)
        & (close >= ema_mid * 0.99)
        & (rsi >= 38.0)
        & (rsi <= 54.0)
        & (close > df["open"])
    )
    return mask.to_numpy()


def meta_label_study(df: pd.DataFrame, pt: float, sl: float, horizons: List[int]) -> Dict:
    """
    Meta-label = 1 iff the long primary signal reaches TP before SL.
    Also reports the realised ATR-normalised expectancy of the raw primary rule,
    which is what a meta-model would have to improve on.
    """
    mask = quantum_ai_event_mask(df)
    out: Dict = {"n_primary_events_total": int(mask.sum()), "by_horizon": []}
    for h in horizons:
        try:
            tb = triple_barrier_fast(df, pt, sl, h, event_mask=mask)
        except ValueError:
            continue
        is_pt = (tb["touch_type"] == "pt").to_numpy()
        is_sl = (tb["touch_type"] == "sl").to_numpy()
        is_to = (tb["touch_type"] == "timeout").to_numpy()
        # realised outcome in ATR units
        r_atr = (tb["ret"].to_numpy() / 100.0) * tb["entry_price"].to_numpy() / tb["trgt"].to_numpy()
        out["by_horizon"].append(
            {
                "horizon_bars": h,
                "n_events": int(len(tb)),
                "meta_label_pos_rate": float(is_pt.mean()),
                "frac_sl": float(is_sl.mean()),
                "frac_timeout": float(is_to.mean()),
                "breakeven_winrate": sl / (pt + sl),
                "expectancy_atr_per_trade": float(np.mean(r_atr)),
                "expectancy_pct_per_trade": float(np.mean(tb["ret"].to_numpy())),
            }
        )
    return out


# --------------------------------------------------------------------------
# exact reconstruction of the rejected model's confusion matrix
# --------------------------------------------------------------------------
def reconstruct_confusion(metrics: Dict[str, float], max_n: int = 40000) -> Optional[Dict]:
    """
    The rejected-model metadata records precision/recall/accuracy as float64
    ratios of small integers. Recover the underlying confusion matrix by
    searching integer counts that reproduce every reported float exactly.
    """
    def find_ratio(target: float, max_den: int) -> List[Tuple[int, int]]:
        hits = []
        for den in range(1, max_den + 1):
            num = round(target * den)
            if 0 <= num <= den and float(num) / den == target:
                hits.append((num, den))
        return hits

    br = find_ratio(metrics["bearish_recall"], 20000)
    bur = find_ratio(metrics["bullish_recall"], 20000)
    if not br or not bur:
        return None
    tp2, n2 = br[0]
    tp1, n1 = bur[0]

    pred1 = round(tp1 / metrics["bullish_precision"]) if metrics["bullish_precision"] > 0 else 0
    pred2 = round(tp2 / metrics["bearish_precision"]) if metrics["bearish_precision"] > 0 else 0

    acc = metrics["test_accuracy"]
    for n_total in range(max(n1 + n2, pred1 + pred2), max_n):
        correct = round(acc * n_total)
        if float(correct) / n_total != acc:
            continue
        if correct < tp1 + tp2:
            continue
        n0 = n_total - n1 - n2
        pred0 = n_total - pred1 - pred2
        if n0 < 0 or pred0 < 0:
            continue
        tp0 = correct - tp1 - tp2
        if tp0 > min(n0, pred0):
            continue
        return {
            "n_holdout": n_total,
            "true_class1_bullish": n1,
            "true_class2_bearish": n2,
            "true_class0_neutral": n0,
            "pred_class1": pred1,
            "pred_class2": pred2,
            "pred_class0": pred0,
            "correct": correct,
            "tp_class1": tp1,
            "tp_class2": tp2,
            "tp_class0": tp0,
            "accuracy": correct / n_total,
            "majority_class_share": max(n1, n2, n0) / n_total,
            "accuracy_minus_majority": correct / n_total - max(n1, n2, n0) / n_total,
        }
    return None


def holdout_slice_check(
    df: pd.DataFrame,
    pt: float,
    sl: float,
    horizon: int,
    holdout_n: int,
    use_event_mask: bool,
) -> Dict:
    """
    Rebuild the exact label vector a recorded model was scored on and compare it
    with the counts recovered from its metadata. The trainer maps label == +1 to
    the positive class, so a vertical-barrier expiry that merely drifted up by
    more than 0.2 * sl * ATR is recorded as a win it never took.
    """
    mask = quantum_ai_event_mask(df) if use_event_mask else None
    tb = triple_barrier_fast(df, pt, sl, horizon, event_mask=mask)
    tail = tb.iloc[-holdout_n:]
    pos = tail["label"] == 1
    lab = tail["label"].value_counts()
    # the earlier 3-class trainer kept +1 / -1 / 0 apart; the current 2-class one
    # folds -1 and 0 together, so report both majority shares
    counts3 = {"class1": int(lab.get(1, 0)), "class2": int(lab.get(-1, 0)), "class0": int(lab.get(0, 0))}
    return {
        "geometry": f"{pt}/{sl}",
        "horizon_bars": horizon,
        "event_sampled": use_event_mask,
        "n_labelled_events_total": int(len(tb)),
        "holdout_n": int(len(tail)),
        "holdout_positive": int(pos.sum()),
        "holdout_negative": int((~pos).sum()),
        "holdout_positive_rate": float(pos.mean()),
        "holdout_majority_share_2class": float(max(pos.mean(), 1 - pos.mean())),
        "holdout_counts_3class": counts3,
        "holdout_majority_share_3class": float(max(counts3.values()) / len(tail)),
        "positives_that_touched_pt": int((pos & (tail["touch_type"] == "pt")).sum()),
        "positives_that_only_timed_out": int((pos & (tail["touch_type"] == "timeout")).sum()),
        "mean_ret_pct_pt_positives": float(tb.loc[(tb["label"] == 1) & (tb["touch_type"] == "pt"), "ret"].mean()),
        "mean_ret_pct_timeout_positives": float(
            tb.loc[(tb["label"] == 1) & (tb["touch_type"] == "timeout"), "ret"].mean()
        ),
    }


def synthetic_sharpe_from_hits(n_correct: int, n_total: int, annualization: int = 365) -> float:
    """
    Reproduce train_ml.py's `holdout_sharpe`, which is computed on a synthetic
    series paying +-0.01 per bar according to sign agreement. Purely a rescaled
    hit rate; it contains no PnL information.
    """
    r = np.concatenate([np.full(n_correct, 0.01), np.full(n_total - n_correct, -0.01)])
    return float(np.mean(r) / np.std(r, ddof=1) * np.sqrt(annualization))


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def run_symbol(symbol: str, csv: str, n_bars: Optional[int], repo_dir: Optional[str]) -> Dict:
    df = load_bars(csv, n_bars)
    res: Dict = {
        "symbol": symbol,
        "csv": os.path.basename(csv),
        "n_bars_used": int(len(df)),
        "window_start": str(df.index[0]),
        "window_end": str(df.index[-1]),
    }

    if repo_dir:
        res["parity_check"] = parity_check(df, repo_dir, DEPLOYED_PT, DEPLOYED_SL, 24, min(2000, len(df)))

    res["atr_scale_deployed_pt"] = atr_scale(df, DEPLOYED_PT)

    res["geometry_grid_h24"] = [
        summarise(triple_barrier_fast(df, pt, sl, 24), pt, sl, 24) for pt, sl in GEOMETRY_GRID
    ]

    res["horizon_sweep_deployed"] = []
    res["excursion_by_horizon"] = []
    for h in HORIZON_GRID:
        if len(df) <= h + 50:
            continue
        res["horizon_sweep_deployed"].append(
            summarise(triple_barrier_fast(df, DEPLOYED_PT, DEPLOYED_SL, h), DEPLOYED_PT, DEPLOYED_SL, h)
        )
        res["excursion_by_horizon"].append(excursion_stats(df, h, DEPLOYED_PT, DEPLOYED_SL))

    # full distribution of bars-to-touch for the up barrier at a long horizon
    long_h = max(h for h in HORIZON_GRID if len(df) > h + 50)
    tb_long = triple_barrier_fast(df, DEPLOYED_PT, DEPLOYED_SL, long_h)
    up = tb_long.loc[tb_long["touch_type"] == "pt", "bars_to_touch"]
    res["up_bars_to_touch_deployed"] = {
        "horizon_bars": long_h,
        "n_up_first": int(len(up)),
        "quantiles": {f"p{int(q*100)}": float(up.quantile(q)) for q in (0.1, 0.25, 0.5, 0.75, 0.9)}
        if len(up)
        else {},
        "frac_of_all_events": float(len(up) / len(tb_long)),
    }

    res["conditional_rates"] = {
        f"h{h}": conditional_rates(df, DEPLOYED_PT, DEPLOYED_SL, h)
        for h in (24, 96) if len(df) > h + 300
    }

    res["meta_label_deployed"] = meta_label_study(df, DEPLOYED_PT, DEPLOYED_SL, HORIZON_GRID)
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", action="append", required=True, metavar="SYMBOL=PATH")
    ap.add_argument("--bars", type=int, default=8000, help="most recent N 1h bars to use (0 = all)")
    ap.add_argument("--repo-dir", default=None, help="dir containing triple_barrier.py for the parity check")
    ap.add_argument("--out", default="/tmp/labelstudy", help="output directory (only writes here)")
    ap.add_argument("--rejected-meta", default=None, help="path to *_rejected_meta.json for confusion reconstruction")
    ap.add_argument(
        "--verify-holdout",
        action="append",
        default=None,
        metavar="SYMBOL:N:EVENTS",
        help="rebuild the last N labels for SYMBOL over FULL history and report the class split; "
        "EVENTS is 1 to event-sample on the QuantumAI long filter, 0 for every bar "
        "(e.g. BTC-USDT:372:1)",
    )
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    n_bars = args.bars if args.bars > 0 else None

    report: Dict = {
        "deployed_geometry": {"pt_mult": DEPLOYED_PT, "sl_mult": DEPLOYED_SL,
                              "payoff_ratio": DEPLOYED_PT / DEPLOYED_SL,
                              "breakeven_winrate": DEPLOYED_SL / (DEPLOYED_PT + DEPLOYED_SL)},
        "bars_requested": args.bars,
        "symbols": [],
    }

    paths = dict(spec.split("=", 1) for spec in args.csv)
    for symbol, path in paths.items():
        print(f"[*] {symbol}: {path}", file=sys.stderr)
        report["symbols"].append(run_symbol(symbol, path, n_bars, args.repo_dir))

    if args.verify_holdout:
        report["holdout_verification"] = []
        for spec in args.verify_holdout:
            symbol, n_hold, ev = spec.split(":")
            df_full = load_bars(paths[symbol], None)
            chk = holdout_slice_check(
                df_full, DEPLOYED_PT, DEPLOYED_SL, 24, int(n_hold), bool(int(ev))
            )
            chk["symbol"] = symbol
            chk["n_bars_full_history"] = int(len(df_full))
            report["holdout_verification"].append(chk)

    if args.rejected_meta:
        with open(args.rejected_meta) as fh:
            meta = json.load(fh)
        m = meta.get("metrics", meta)
        rec = reconstruct_confusion(m)
        if rec:
            rec["reported_holdout_sharpe"] = m.get("holdout_sharpe")
            # the grid trial that produced holdout_sharpe binarises 1-vs-rest
            n = rec["n_holdout"]
            rec["binary_hits_implied_by_holdout_sharpe"] = next(
                (k for k in range(n + 1)
                 if abs(synthetic_sharpe_from_hits(k, n) - float(m["holdout_sharpe"])) < 1e-9),
                None,
            )
            rec["sharpe_of_final_calibrated_model"] = synthetic_sharpe_from_hits(rec["correct"], n)
            rec["sharpe_of_always_majority"] = synthetic_sharpe_from_hits(
                max(rec["true_class1_bullish"], rec["true_class2_bearish"], rec["true_class0_neutral"]), n
            )
        report["rejected_model_reconstruction"] = rec

    out_path = os.path.join(args.out, "label_study.json")
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    print(f"[+] wrote {out_path}", file=sys.stderr)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
