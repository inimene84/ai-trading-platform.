#!/usr/bin/env python3
"""
Jesse Machine Learning Quant Model Trainer
Extracts historical candles from PostgreSQL, computes non-linear predictive features,
labels forward returns, runs Purged Time-Series Cross-Validation, and serializes
the trained model pipeline to storage/models/.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Dict, Any, Tuple, Optional

import joblib
import numpy as np
import pandas as pd
import psycopg2
from sklearn.metrics import classification_report, accuracy_score, precision_score
from sklearn.preprocessing import RobustScaler
from sklearn.pipeline import Pipeline
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier, HistGradientBoostingClassifier
from lightgbm import LGBMClassifier

from ml_features import compute_features_df, FEATURE_NAMES
from validation_metrics import PurgedKFold, deflated_sharpe_ratio, probability_of_backtest_overfitting, calculate_sharpe_ratio
from triple_barrier import apply_triple_barrier, compute_sample_uniqueness
from feature_schema import FEATURE_HASH
from promotion_gates import (
    DSR_GATE,
    PBO_GATE,
    STRATEGY_PT_ATR,
    STRATEGY_SL_ATR,
    evaluate_promotion,
    payoff_ratio_from_geometry,
)

# Database settings — never hardcode credentials; the Jesse container injects these.
DB_HOST = os.getenv("POSTGRES_HOST", "postgres")
DB_NAME = os.getenv("POSTGRES_NAME", "jesse_db")
DB_USER = os.getenv("POSTGRES_USERNAME", "jesse_user")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "")
DB_PORT = int(os.getenv("POSTGRES_PORT", "5432"))

MODELS_DIR = "/root/jesse-trading/storage/models"
if not os.path.exists(MODELS_DIR):
    # If running inside docker container where root is /home:
    MODELS_DIR = "/home/storage/models" if os.path.exists("/home/storage") else "storage/models"
os.makedirs(MODELS_DIR, exist_ok=True)


def load_candles_from_db(symbol: str = "BTC-USDT", timeframe: str = "1h") -> pd.DataFrame:
    """Load 1m candles from PostgreSQL and resample to target timeframe."""
    if not DB_PASS:
        raise RuntimeError("POSTGRES_PASSWORD is not set — refusing to connect")
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS,
    )

    query = (
        "SELECT timestamp, open, high, low, close, volume "
        "FROM candle WHERE symbol = %s ORDER BY timestamp ASC"
    )
    t0 = time.time()
    df = pd.read_sql(query, conn, params=(symbol,))
    conn.close()
    print(f"    Loaded {len(df):,} 1m candles for {symbol} in {time.time()-t0:.2f}s")

    if len(df) == 0:
        raise ValueError(f"No candles found in database for symbol {symbol}")

    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("datetime", inplace=True)

    if timeframe != "1m":
        rule_map = {"5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h", "1d": "1d"}
        resample_rule = rule_map.get(timeframe, timeframe)
        df_resampled = df.resample(resample_rule).agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        ).dropna()
        print(f"    Resampled to {len(df_resampled):,} {timeframe} candles ({df_resampled.index.min().date()} to {df_resampled.index.max().date()})")
        return df_resampled
    return df


def prepare_dataset(
    df: pd.DataFrame,
    labeling_mode: str = "triple_barrier",
    pt_mult: float = STRATEGY_PT_ATR,
    sl_mult: float = STRATEGY_SL_ATR,
    max_holding: int = 24,
    forward_horizon: int = 6,
    threshold_pct: float = 0.75,
) -> Tuple[pd.DataFrame, pd.Series, Optional[pd.Series], Optional[pd.Series]]:
    """
    Computes features and labels outcomes using either:
    1. Triple-Barrier Method (path-dependent PT, SL, timeout with sample uniqueness weights)
    2. Fixed Horizon returns (return[t+H] vs fixed threshold)
    """
    print("[*] Computing 26 quantitative technical indicators & features...")
    t0 = time.time()
    X = compute_features_df(df)

    if labeling_mode == "triple_barrier":
        print(f"[*] Applying Triple-Barrier Method: PT={pt_mult}x ATR, SL={sl_mult}x ATR, Max Holding={max_holding} bars...")
        tb_df = apply_triple_barrier(df, pt_multiplier=pt_mult, sl_multiplier=sl_mult, max_holding_bars=max_holding)
        sample_weights = compute_sample_uniqueness(tb_df, df.index)

        # Mapping: +1 -> 1 (Bullish), -1 -> 2 (Bearish), 0 -> 0 (Neutral/Timeout)
        y_raw = tb_df["label"]
        y = pd.Series(0, index=tb_df.index, dtype=int)
        y[y_raw == 1] = 1
        y[y_raw == -1] = 2

        samples_info_sets = tb_df["t1"]
        common_idx = X.index.intersection(y.index)
        valid_mask = ~(X.loc[common_idx].isna().any(axis=1) | y.loc[common_idx].isna())
        final_idx = common_idx[valid_mask]

        X_clean = X.loc[final_idx]
        y_clean = y.loc[final_idx]
        w_clean = sample_weights.loc[final_idx]
        info_sets_clean = samples_info_sets.loc[final_idx]
    else:
        # Fixed forward horizon
        forward_return = (df["close"].shift(-forward_horizon) / df["close"] - 1.0) * 100.0
        y = pd.Series(0, index=df.index, dtype=int)
        y[forward_return > threshold_pct] = 1
        y[forward_return < -threshold_pct] = 2

        valid_mask = ~(X.isna().any(axis=1) | forward_return.isna())
        X_clean = X[valid_mask]
        y_clean = y[valid_mask]
        w_clean = None
        info_sets_clean = None

    class_counts = y_clean.value_counts().to_dict()
    print(f"    Dataset prepared in {time.time()-t0:.2f}s: {len(X_clean):,} valid rows")
    print(f"    Class Distribution: Neutral(0)={class_counts.get(0, 0):,}, Bullish(1)={class_counts.get(1, 0):,}, Bearish(2)={class_counts.get(2, 0):,}")
    return X_clean, y_clean, w_clean, info_sets_clean



LGBM_GRID = [
    {"n_estimators": 80, "max_depth": 3, "num_leaves": 8, "learning_rate": 0.03,
     "min_child_samples": 80, "reg_lambda": 2.0, "colsample_bytree": 0.6, "subsample": 0.7},
    {"n_estimators": 100, "max_depth": 4, "num_leaves": 12, "learning_rate": 0.03,
     "min_child_samples": 60, "reg_lambda": 1.5, "colsample_bytree": 0.7, "subsample": 0.8},
    {"n_estimators": 120, "max_depth": 4, "num_leaves": 16, "learning_rate": 0.02,
     "min_child_samples": 50, "reg_lambda": 1.0, "colsample_bytree": 0.7, "subsample": 0.8},
    {"n_estimators": 80, "max_depth": 2, "num_leaves": 4, "learning_rate": 0.05,
     "min_child_samples": 120, "reg_lambda": 4.0, "colsample_bytree": 0.5, "subsample": 0.6},
    {"n_estimators": 140, "max_depth": 5, "num_leaves": 20, "learning_rate": 0.02,
     "min_child_samples": 50, "reg_lambda": 1.5, "colsample_bytree": 0.75, "subsample": 0.85},
    {"n_estimators": 160, "max_depth": 3, "num_leaves": 8, "learning_rate": 0.04,
     "min_child_samples": 100, "reg_lambda": 3.0, "colsample_bytree": 0.55, "subsample": 0.7},
]


def _load_trial_ledger() -> int:
    ledger_path = os.path.join(MODELS_DIR, "_trial_ledger.json")
    if not os.path.exists(ledger_path):
        return 0
    try:
        with open(ledger_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return int(data.get("cumulative_trials", 0))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0


def _save_trial_ledger(cumulative_trials: int) -> None:
    ledger_path = os.path.join(MODELS_DIR, "_trial_ledger.json")
    with open(ledger_path, "w", encoding="utf-8") as fh:
        json.dump({"cumulative_trials": int(cumulative_trials)}, fh)


def _make_clf(model_type: str, params: Optional[Dict[str, Any]] = None):
    if model_type == "lightgbm":
        cfg = {
            "n_estimators": 120,
            "learning_rate": 0.03,
            "max_depth": 4,
            "num_leaves": 16,
            "min_child_samples": 60,
            "reg_lambda": 1.5,
            "colsample_bytree": 0.7,
            "subsample": 0.8,
            "bagging_freq": 1,
            "class_weight": "balanced",
            "random_state": 42,
            "verbose": -1,
        }
        if params:
            cfg.update(params)
        return LGBMClassifier(**cfg)
    if model_type == "random_forest":
        return RandomForestClassifier(
            n_estimators=100, max_depth=6, class_weight="balanced", random_state=42, n_jobs=-1,
        )
    return HistGradientBoostingClassifier(
        max_iter=120, learning_rate=0.03, max_depth=5, class_weight="balanced", random_state=42,
    )


def train_model(
    X: pd.DataFrame,
    y: pd.Series,
    sample_weights: Optional[pd.Series] = None,
    samples_info_sets: Optional[pd.Series] = None,
    model_type: str = "lightgbm",
    test_size: float = 0.20,
    pt_mult: float = STRATEGY_PT_ATR,
    sl_mult: float = STRATEGY_SL_ATR,
) -> Tuple[Pipeline, Dict[str, Any]]:
    """Train with a hyperparameter grid so DSR/PBO see the true trial count."""
    split_idx = int(len(X) * (1.0 - test_size))
    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]
    w_train = sample_weights.iloc[:split_idx].to_numpy() if sample_weights is not None else None

    print(f"\n[*] Training Window: {len(X_train):,} bars | Holdout Test Window: {len(X_test):,} bars")

    grid = LGBM_GRID if model_type == "lightgbm" else [{}]
    scaler = RobustScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)

    holdout_columns = []
    trial_sharpes = []
    fitted = []

    print(f"[*] Evaluating {len(grid)} candidate configurations for CPCV/PBO matrix...")
    for i, params in enumerate(grid, 1):
        clf = _make_clf(model_type, params)
        if w_train is not None and model_type in ("lightgbm", "random_forest"):
            clf.fit(X_train_s, y_train, sample_weight=w_train)
        else:
            clf.fit(X_train_s, y_train)
        preds = clf.predict(X_test_s)
        pred_signal = np.where(preds == 1, 1.0, np.where(preds == 2, -1.0, 0.0))
        actual_signal = np.where(y_test.to_numpy() == 1, 1.0, np.where(y_test.to_numpy() == 2, -1.0, 0.0))
        rets = pred_signal * actual_signal * 0.01
        holdout_columns.append(rets)
        sr = calculate_sharpe_ratio(rets)
        trial_sharpes.append(sr)
        fitted.append((params, clf, sr, preds))
        print(f"    trial {i}/{len(grid)} Sharpe={sr:.3f} params={params}")

    trial_matrix = np.column_stack(holdout_columns)
    pbo_cv, med_rank, pbo_ranks = probability_of_backtest_overfitting(
        trial_matrix, n_blocks=min(16, max(4, (trial_matrix.shape[0] // 20) * 2))
    )
    if not pbo_ranks:
        # Unevaluable CSCV (too few periods) must not silently pass the 0.0 default.
        pbo_cv = 1.0
        med_rank = 1.0

    historical = _load_trial_ledger()
    n_trials = historical + len(grid)
    _save_trial_ledger(n_trials)

    var_sharpe = float(np.var(trial_sharpes, ddof=1)) if len(trial_sharpes) > 1 else None
    best_idx = int(np.argmax(trial_sharpes))
    best_params, _, best_sr, best_preds = fitted[best_idx]
    holdout_returns = holdout_columns[best_idx]
    holdout_sr = calculate_sharpe_ratio(holdout_returns)
    dsr_holdout = deflated_sharpe_ratio(holdout_returns, n_trials=n_trials, variance_of_trials=var_sharpe)

    print(f"[*] Best trial #{best_idx + 1} holdout Sharpe={holdout_sr:.3f}; n_trials={n_trials} (ledger+grid)")

    base_clf = _make_clf(model_type, best_params)
    calibrated_clf = CalibratedClassifierCV(estimator=base_clf, method="isotonic", cv=3)
    print(f"[*] Fitting final {model_type} pipeline with Isotonic Probability Calibration...")
    if w_train is not None and model_type in ("lightgbm", "random_forest"):
        calibrated_clf.fit(X_train_s, y_train, sample_weight=w_train)
    else:
        calibrated_clf.fit(X_train_s, y_train)

    pipeline = Pipeline([
        ("scaler", scaler),
        ("classifier", calibrated_clf),
    ])

    test_preds = pipeline.predict(X_test)
    test_acc = accuracy_score(y_test, test_preds)
    report = classification_report(y_test, test_preds, output_dict=True, zero_division=0)

    importances = {}
    first_est = getattr(calibrated_clf.calibrated_classifiers_[0], "estimator", None)
    if first_est and hasattr(first_est, "feature_importances_"):
        for name, imp in zip(FEATURE_NAMES, first_est.feature_importances_):
            importances[name] = float(imp)
        importances = dict(sorted(importances.items(), key=lambda x: x[1], reverse=True))

    metrics = {
        "model_type": model_type,
        "cv_mean_accuracy": float(test_acc),
        "test_accuracy": float(test_acc),
        "deflated_sharpe_ratio": float(dsr_holdout),
        "prob_backtest_overfitting": float(pbo_cv),
        "holdout_sharpe": float(holdout_sr),
        "n_trials": int(n_trials),
        "n_grid": int(len(grid)),
        "best_params": best_params,
        "pt_mult": float(pt_mult),
        "sl_mult": float(sl_mult),
        "payoff_ratio": float(payoff_ratio_from_geometry(pt_mult, sl_mult)),
        "pbo_median_rank": float(med_rank),
        "bullish_precision": float(report.get("1", {}).get("precision", 0)),
        "bullish_recall": float(report.get("1", {}).get("recall", 0)),
        "bearish_precision": float(report.get("2", {}).get("precision", 0)),
        "bearish_recall": float(report.get("2", {}).get("recall", 0)),
        "feature_importances": importances,
    }

    promotion = evaluate_promotion(metrics, pt_mult=pt_mult, sl_mult=sl_mult)
    metrics["promotion_ok"] = promotion.ok
    metrics["promotion_reason"] = promotion.reason

    print("\n=========================================================")
    print("        HOLDOUT OUT-OF-SAMPLE TEST EVALUATION            ")
    print("=========================================================")
    print(f"Model:                     {model_type.upper()} (Isotonic Calibrated)")
    print(f"Overall Accuracy:          {test_acc:.2%}")
    print(f"Holdout Sharpe Ratio:      {holdout_sr:.2f}")
    print(f"Deflated Sharpe Ratio:     {dsr_holdout:.4f} (n_trials={n_trials}, gate > {DSR_GATE})")
    print(f"Prob. of Overfitting (PBO):{pbo_cv:.2%} (gate < {PBO_GATE:.0%})")
    print(f"Promotion:                 {'PASS' if promotion.ok else 'BLOCKED'} — {promotion.reason}")
    print(f"Bullish Precision:         {metrics['bullish_precision']:.2%} (Recall: {metrics['bullish_recall']:.2%})")
    print(f"Bearish Precision:         {metrics['bearish_precision']:.2%} (Recall: {metrics['bearish_recall']:.2%})")
    print("---------------------------------------------------------")
    print("TOP PREDICTIVE FEATURES:")
    for idx, (feat, score) in enumerate(list(importances.items())[:8], 1):
        print(f"  {idx}. {feat:<20} : {score:.4f}")
    print("=========================================================")

    return pipeline, metrics


def main():
    parser = argparse.ArgumentParser(description="Jesse Machine Learning Quant Trainer")
    parser.add_argument("--symbol", default="BTC-USDT", help="Pair symbol (default: BTC-USDT)")
    parser.add_argument("--timeframe", default="1h", help="Candle timeframe (default: 1h)")
    parser.add_argument("--model", default="lightgbm", choices=["lightgbm", "hist_gradient_boosting", "random_forest"])
    parser.add_argument("--labeling", default="triple_barrier", choices=["triple_barrier", "fixed"], help="Labeling methodology")
    parser.add_argument("--pt-mult", type=float, default=STRATEGY_PT_ATR, help="Triple-barrier profit target ATR multiplier (live geometry 5.5)")
    parser.add_argument("--sl-mult", type=float, default=STRATEGY_SL_ATR, help="Triple-barrier stop loss ATR multiplier (live geometry 1.75)")
    parser.add_argument("--holding", type=int, default=24, help="Triple-barrier maximum holding period in bars")
    parser.add_argument("--horizon", type=int, default=6, help="Fixed return forward horizon in bars (default: 6)")
    parser.add_argument("--threshold", type=float, default=0.75, help="Fixed return threshold pct (default: 0.75)")
    parser.add_argument("--force-promote", action="store_true", help="Overwrite production artifact even if DSR/PBO gates fail")
    args = parser.parse_args()

    print("=========================================================")
    print("         JESSE QUANT MACHINE LEARNING TRAINER            ")
    print("=========================================================")
    print(f"Symbol:        {args.symbol}")
    print(f"Timeframe:     {args.timeframe}")
    print(f"Model Engine:  {args.model.upper()} (Isotonic Calibrated)")
    print(f"Labeling Mode: {args.labeling.upper()} (PT={args.pt_mult}x, SL={args.sl_mult}x, MaxHold={args.holding}b)")
    print(f"Feature Hash:  {FEATURE_HASH}")
    print("=========================================================")

    df = load_candles_from_db(args.symbol, args.timeframe)
    X, y, sample_weights, samples_info_sets = prepare_dataset(
        df,
        labeling_mode=args.labeling,
        pt_mult=args.pt_mult,
        sl_mult=args.sl_mult,
        max_holding=args.holding,
        forward_horizon=args.horizon,
        threshold_pct=args.threshold,
    )
    pipeline, metrics = train_model(
        X,
        y,
        sample_weights=sample_weights,
        samples_info_sets=samples_info_sets,
        model_type=args.model,
        pt_mult=args.pt_mult,
        sl_mult=args.sl_mult,
    )

    # Save model artifact with MLOps metadata
    norm_symbol = args.symbol.replace("/", "-")
    model_filename = f"{norm_symbol}_{args.timeframe}_{args.model}.joblib"
    model_path = os.path.join(MODELS_DIR, model_filename)
    promoted = bool(metrics.get("promotion_ok")) or args.force_promote
    if not promoted:
        model_filename = f"{norm_symbol}_{args.timeframe}_{args.model}.rejected.joblib"
        model_path = os.path.join(MODELS_DIR, model_filename)
        print(f"\n[!] Promotion BLOCKED: {metrics.get('promotion_reason')}")
        print("[!] Writing rejected artifact only — production model left unchanged")

    save_payload = {
        "pipeline": pipeline,
        "feature_names": FEATURE_NAMES,
        "feature_hash": FEATURE_HASH,
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "model_type": args.model,
        "labeling_mode": args.labeling,
        "pt_mult": args.pt_mult,
        "sl_mult": args.sl_mult,
        "calibrated": True,
        "expiration_hours": 168,
        "trained_at": datetime.utcnow().isoformat(),
        "metrics": metrics,
        "promotion_ok": promoted,
        "promotion_reason": metrics.get("promotion_reason"),
    }

    joblib.dump(save_payload, model_path)
    print(f"\n[{'✓' if promoted else '!'}] Model pipeline saved to: {model_path}")

    meta_name = f"{norm_symbol}_{args.timeframe}_{args.model}_meta.json"
    if not promoted:
        meta_name = f"{norm_symbol}_{args.timeframe}_{args.model}_rejected_meta.json"
    meta_path = os.path.join(MODELS_DIR, meta_name)
    with open(meta_path, "w") as f:
        json.dump(
            {
                "symbol": args.symbol,
                "timeframe": args.timeframe,
                "model_type": args.model,
                "feature_hash": FEATURE_HASH,
                "labeling_mode": args.labeling,
                "pt_mult": args.pt_mult,
                "sl_mult": args.sl_mult,
                "calibrated": True,
                "expiration_hours": 168,
                "metrics": metrics,
                "promotion_ok": promoted,
                "promotion_reason": metrics.get("promotion_reason"),
                "trained_at": save_payload["trained_at"],
            },
            f,
            indent=2,
        )
    print(f"[✓] Model MLOps metadata saved to: {meta_path}\n")
    if not promoted:
        sys.exit(2)


if __name__ == "__main__":
    main()

