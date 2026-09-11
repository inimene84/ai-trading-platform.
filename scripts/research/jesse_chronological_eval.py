#!/usr/bin/env python3
"""Leakage-aware, artifact-free evaluation for the VPS Jesse candle store.

The script reads candles from PostgreSQL, trains models in memory, and emits a
JSON report. It never writes or promotes a model. Model selection uses only a
chronological validation segment; the final test segment is evaluated once.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd
import psycopg2

try:
    from lightgbm import LGBMClassifier
except ImportError:  # pragma: no cover - exercised on the Jesse image
    LGBMClassifier = None  # type: ignore[assignment,misc]


@dataclass(frozen=True)
class Geometry:
    name: str
    profit_atr: float
    stop_atr: float
    max_holding_bars: int


@dataclass(frozen=True)
class CostModel:
    fee_per_side: float
    slippage_per_side: float
    funding_per_8h: float


FEATURE_COLUMNS = [
    "ret_1",
    "ret_3",
    "ret_6",
    "ret_12",
    "ema_9_21",
    "ema_21_50",
    "ema_50_200",
    "rsi_14",
    "atr_pct",
    "volatility_24",
    "volume_zscore_24",
    "body_ratio",
    "upper_wick_ratio",
    "lower_wick_ratio",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTC-USDT")
    parser.add_argument("--timeframe", default="1h", choices=["15m", "1h", "4h"])
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--train-fraction", type=float, default=0.60)
    parser.add_argument("--validation-fraction", type=float, default=0.20)
    parser.add_argument("--fee-per-side", type=float, default=0.0006)
    parser.add_argument("--slippage-per-side", type=float, default=0.0003)
    parser.add_argument("--funding-per-8h", type=float, default=0.0001)
    parser.add_argument("--output", default="-")
    return parser.parse_args()


def load_candles(symbol: str, timeframe: str, start: str, end: str | None) -> pd.DataFrame:
    required = ["POSTGRES_HOST", "POSTGRES_NAME", "POSTGRES_USERNAME", "POSTGRES_PASSWORD"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Missing database environment variables: {', '.join(missing)}")

    query = """
        SELECT timestamp, open, high, low, close, volume
        FROM candle
        WHERE symbol = %s
          AND timestamp >= %s
          AND (%s IS NULL OR timestamp < %s)
        ORDER BY timestamp ASC
    """
    start_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(end, tz="UTC").timestamp() * 1000) if end else None
    connection = psycopg2.connect(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.environ["POSTGRES_NAME"],
        user=os.environ["POSTGRES_USERNAME"],
        password=os.environ["POSTGRES_PASSWORD"],
    )
    try:
        frame = pd.read_sql_query(query, connection, params=(symbol, start_ms, end_ms, end_ms))
    finally:
        connection.close()

    if frame.empty:
        raise RuntimeError(f"No candles found for {symbol}")

    frame.index = pd.to_datetime(frame.pop("timestamp"), unit="ms", utc=True)
    rules = {"15m": "15min", "1h": "1h", "4h": "4h"}
    return (
        frame.resample(rules[timeframe])
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
    )


def average_true_range(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    previous_close = frame["close"].shift(1)
    ranges = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1).ewm(span=period, adjust=False).mean()


def relative_strength_index(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return 100 - (100 / (1 + gain / (loss + 1e-12)))


def compute_features(frame: pd.DataFrame) -> pd.DataFrame:
    close = frame["close"]
    ema_9 = close.ewm(span=9, adjust=False).mean()
    ema_21 = close.ewm(span=21, adjust=False).mean()
    ema_50 = close.ewm(span=50, adjust=False).mean()
    ema_200 = close.ewm(span=200, adjust=False).mean()
    atr = average_true_range(frame)
    candle_range = (frame["high"] - frame["low"]).clip(lower=1e-12)
    volume_mean = frame["volume"].rolling(24, min_periods=24).mean()
    volume_std = frame["volume"].rolling(24, min_periods=24).std()
    features = pd.DataFrame(index=frame.index)
    for horizon in (1, 3, 6, 12):
        features[f"ret_{horizon}"] = close.pct_change(horizon)
    features["ema_9_21"] = ema_9 / ema_21 - 1
    features["ema_21_50"] = ema_21 / ema_50 - 1
    features["ema_50_200"] = ema_50 / ema_200 - 1
    features["rsi_14"] = relative_strength_index(close) / 100
    features["atr_pct"] = atr / close
    features["volatility_24"] = close.pct_change().rolling(24, min_periods=24).std()
    features["volume_zscore_24"] = (frame["volume"] - volume_mean) / (volume_std + 1e-12)
    features["body_ratio"] = (frame["close"] - frame["open"]).abs() / candle_range
    features["upper_wick_ratio"] = (
        frame["high"] - pd.concat([frame["open"], frame["close"]], axis=1).max(axis=1)
    ) / candle_range
    features["lower_wick_ratio"] = (
        pd.concat([frame["open"], frame["close"]], axis=1).min(axis=1) - frame["low"]
    ) / candle_range
    return features[FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan).dropna()


def triple_barrier_labels(frame: pd.DataFrame, index: pd.Index, geometry: Geometry) -> pd.Series:
    atr = average_true_range(frame)
    labels = pd.Series(index=index, dtype="int8")
    for timestamp in index:
        location = frame.index.get_loc(timestamp)
        if location + geometry.max_holding_bars >= len(frame):
            labels.loc[timestamp] = 0
            continue
        entry = float(frame["close"].iloc[location])
        volatility = float(atr.iloc[location])
        upper = entry + geometry.profit_atr * volatility
        lower = entry - geometry.stop_atr * volatility
        label = 0
        future = frame.iloc[location + 1 : location + 1 + geometry.max_holding_bars]
        for row in future.itertuples():
            profit_hit = row.high >= upper
            stop_hit = row.low <= lower
            if profit_hit and stop_hit:
                label = -1  # conservative ordering when intrabar order is unknown
                break
            if profit_hit:
                label = 1
                break
            if stop_hit:
                label = -1
                break
        labels.loc[timestamp] = label
    return labels


def chronological_indices(
    index: pd.Index,
    train_fraction: float,
    validation_fraction: float,
    purge_bars: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if not 0 < train_fraction < 1 or not 0 < validation_fraction < 1:
        raise ValueError("Split fractions must be between zero and one")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("Train and validation fractions must leave a test segment")
    count = len(index)
    train_end = int(count * train_fraction)
    validation_end = int(count * (train_fraction + validation_fraction))
    train = np.arange(0, max(0, train_end - purge_bars))
    validation = np.arange(train_end, max(train_end, validation_end - purge_bars))
    test = np.arange(validation_end, count - purge_bars)
    if min(len(train), len(validation), len(test)) == 0:
        raise ValueError("Not enough observations for purged chronological splits")
    return train, validation, test


def temperature_scale(probabilities: np.ndarray, temperature: float) -> np.ndarray:
    clipped = np.clip(probabilities, 1e-9, 1)
    logits = np.log(clipped) / temperature
    logits -= logits.max(axis=1, keepdims=True)
    scaled = np.exp(logits)
    return scaled / scaled.sum(axis=1, keepdims=True)


def select_temperature(probabilities: np.ndarray, labels: np.ndarray) -> float:
    candidates = np.geomspace(0.35, 3.0, 40)
    losses = []
    for temperature in candidates:
        scaled = temperature_scale(probabilities, float(temperature))
        losses.append(-float(np.log(scaled[np.arange(len(labels)), labels] + 1e-12).mean()))
    return float(candidates[int(np.argmin(losses))])


def _bar_hours(frame: pd.DataFrame) -> float:
    return float(frame.index.to_series().diff().dropna().median().total_seconds() / 3600)


def simulate_predictions(
    frame: pd.DataFrame,
    sample_index: pd.Index,
    probabilities: np.ndarray,
    threshold: float,
    geometry: Geometry,
    costs: CostModel,
) -> dict[str, float | int]:
    atr = average_true_range(frame)
    by_timestamp = {timestamp: probabilities[pos] for pos, timestamp in enumerate(sample_index)}
    timestamps = list(sample_index)
    bar_hours = _bar_hours(frame)
    trade_returns: list[float] = []
    exit_times: list[pd.Timestamp] = []
    cursor = 0

    while cursor < len(timestamps):
        timestamp = timestamps[cursor]
        probs = by_timestamp[timestamp]
        predicted_class = int(np.argmax(probs))
        if predicted_class == 0 or float(probs[predicted_class]) < threshold:
            cursor += 1
            continue
        side = 1 if predicted_class == 1 else -1
        location = frame.index.get_loc(timestamp)
        entry = float(frame["close"].iloc[location])
        volatility = float(atr.iloc[location])
        take_profit = entry + side * geometry.profit_atr * volatility
        stop_loss = entry - side * geometry.stop_atr * volatility
        future = frame.iloc[location + 1 : location + 1 + geometry.max_holding_bars]
        exit_price = float(future["close"].iloc[-1])
        exit_timestamp = future.index[-1]
        bars_held = len(future)

        for bars_held, row in enumerate(future.itertuples(), start=1):
            if side == 1:
                profit_hit = row.high >= take_profit
                stop_hit = row.low <= stop_loss
            else:
                profit_hit = row.low <= take_profit
                stop_hit = row.high >= stop_loss
            if profit_hit and stop_hit:
                exit_price = stop_loss
                exit_timestamp = row.Index
                break
            if profit_hit:
                exit_price = take_profit
                exit_timestamp = row.Index
                break
            if stop_hit:
                exit_price = stop_loss
                exit_timestamp = row.Index
                break

        gross_return = side * (exit_price / entry - 1)
        funding_intervals = math.floor((bars_held * bar_hours) / 8)
        net_return = (
            gross_return
            - 2 * (costs.fee_per_side + costs.slippage_per_side)
            - funding_intervals * costs.funding_per_8h
        )
        trade_returns.append(float(net_return))
        exit_times.append(exit_timestamp)
        cursor = int(sample_index.searchsorted(exit_timestamp, side="right"))

    if not trade_returns:
        return {
            "total_return_pct": 0.0,
            "annualized_sharpe": 0.0,
            "sortino": 0.0,
            "max_drawdown_pct": 0.0,
            "trades": 0,
            "win_rate_pct": 0.0,
        }

    returns = np.asarray(trade_returns)
    equity = np.cumprod(1 + returns)
    running_peak = np.maximum.accumulate(np.concatenate([[1.0], equity]))
    drawdowns = np.concatenate([[1.0], equity]) / running_peak - 1
    daily_index = pd.date_range(sample_index[0].floor("D"), sample_index[-1].ceil("D"), freq="1D", tz="UTC")
    daily = pd.Series(0.0, index=daily_index)
    for timestamp, value in zip(exit_times, trade_returns):
        day = timestamp.floor("D")
        if day in daily.index:
            daily.loc[day] = (1 + daily.loc[day]) * (1 + value) - 1
    daily_std = float(daily.std(ddof=1))
    downside_std = float(daily[daily < 0].std(ddof=1))
    sharpe = float(daily.mean() / daily_std * math.sqrt(365)) if daily_std > 1e-12 else 0.0
    sortino = float(daily.mean() / downside_std * math.sqrt(365)) if downside_std > 1e-12 else 0.0
    return {
        "total_return_pct": float((equity[-1] - 1) * 100),
        "annualized_sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown_pct": float(drawdowns.min() * 100),
        "trades": int(len(returns)),
        "win_rate_pct": float((returns > 0).mean() * 100),
    }


def select_threshold(
    frame: pd.DataFrame,
    sample_index: pd.Index,
    probabilities: np.ndarray,
    geometry: Geometry,
    costs: CostModel,
) -> tuple[float, dict[str, float | int]]:
    candidates = np.arange(0.40, 0.71, 0.05)
    scored: list[tuple[float, dict[str, float | int]]] = []
    for threshold in candidates:
        metrics = simulate_predictions(frame, sample_index, probabilities, float(threshold), geometry, costs)
        scored.append((float(threshold), metrics))
    eligible = [item for item in scored if int(item[1]["trades"]) >= 20]
    pool = eligible or scored
    return max(pool, key=lambda item: (float(item[1]["annualized_sharpe"]), float(item[1]["total_return_pct"])))


def buy_and_hold(frame: pd.DataFrame, sample_index: pd.Index, costs: CostModel) -> dict[str, float | int]:
    start = float(frame.loc[sample_index[0], "close"])
    finish = float(frame.loc[sample_index[-1], "close"])
    net_return = finish / start - 1 - 2 * (costs.fee_per_side + costs.slippage_per_side)
    subset = frame.loc[sample_index[0] : sample_index[-1], "close"].resample("1D").last().dropna()
    daily = subset.pct_change().dropna()
    sharpe = float(daily.mean() / daily.std(ddof=1) * math.sqrt(365)) if daily.std(ddof=1) > 0 else 0.0
    equity = subset / subset.iloc[0]
    drawdown = equity / equity.cummax() - 1
    return {
        "total_return_pct": float(net_return * 100),
        "annualized_sharpe": sharpe,
        "sortino": 0.0,
        "max_drawdown_pct": float(drawdown.min() * 100),
        "trades": 1,
        "win_rate_pct": float(net_return > 0) * 100,
    }


def evaluate_geometry(
    frame: pd.DataFrame,
    features: pd.DataFrame,
    geometry: Geometry,
    costs: CostModel,
    train_positions: np.ndarray,
    validation_positions: np.ndarray,
    test_positions: np.ndarray,
) -> dict[str, Any]:
    if LGBMClassifier is None:
        raise RuntimeError("lightgbm is required in the Jesse evaluation environment")
    labels = triple_barrier_labels(frame, features.index, geometry)
    encoded = labels.map({0: 0, 1: 1, -1: 2}).astype(int)
    model = LGBMClassifier(
        n_estimators=160,
        learning_rate=0.03,
        max_depth=5,
        num_leaves=24,
        class_weight="balanced",
        random_state=42,
        deterministic=True,
        verbosity=-1,
    )
    model.fit(features.iloc[train_positions], encoded.iloc[train_positions])
    validation_raw = model.predict_proba(features.iloc[validation_positions])
    temperature = select_temperature(validation_raw, encoded.iloc[validation_positions].to_numpy())
    validation_probabilities = temperature_scale(validation_raw, temperature)
    threshold, validation_metrics = select_threshold(
        frame,
        features.index[validation_positions],
        validation_probabilities,
        geometry,
        costs,
    )
    test_probabilities = temperature_scale(model.predict_proba(features.iloc[test_positions]), temperature)
    test_metrics = simulate_predictions(
        frame,
        features.index[test_positions],
        test_probabilities,
        threshold,
        geometry,
        costs,
    )
    return {
        "geometry": asdict(geometry),
        "temperature_selected_on_validation": temperature,
        "confidence_threshold_selected_on_validation": threshold,
        "validation_metrics": validation_metrics,
        "test_metrics": test_metrics,
    }


def period(index: pd.Index, positions: Sequence[int]) -> dict[str, Any]:
    return {
        "start": index[positions[0]].isoformat(),
        "end": index[positions[-1]].isoformat(),
        "observations": len(positions),
    }


def main() -> None:
    args = parse_args()
    costs = CostModel(args.fee_per_side, args.slippage_per_side, args.funding_per_8h)
    frame = load_candles(args.symbol, args.timeframe, args.start, args.end)
    features = compute_features(frame)
    geometries = [
        Geometry("documented_existing_4.0_2.0_24", 4.0, 2.0, 24),
        Geometry("strategy_aligned_5.5_1.75_48", 5.5, 1.75, 48),
    ]
    purge_bars = max(item.max_holding_bars for item in geometries)
    train, validation, test = chronological_indices(
        features.index,
        args.train_fraction,
        args.validation_fraction,
        purge_bars,
    )
    results = [
        evaluate_geometry(frame, features, geometry, costs, train, validation, test)
        for geometry in geometries
    ]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "data_period": {"start": frame.index[0].isoformat(), "end": frame.index[-1].isoformat(), "bars": len(frame)},
        "splits": {
            "train": period(features.index, train),
            "validation": period(features.index, validation),
            "test": period(features.index, test),
            "purge_bars": purge_bars,
        },
        "cost_model": asdict(costs),
        "experiments": results,
        "spot_buy_and_hold_test_baseline": buy_and_hold(frame, features.index[test], costs),
        "notes": [
            "Models are trained in memory and are not saved or promoted.",
            "Temperature and confidence threshold are selected on validation only.",
            "Test is evaluated once after model and threshold selection.",
            "Signals do not overlap; same-bar TP/SL collisions are resolved as stop-loss first.",
            "Funding is charged conservatively every completed 8-hour holding interval.",
        ],
    }
    serialized = json.dumps(report, indent=2, sort_keys=True)
    if args.output == "-":
        print(serialized)
    else:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(serialized + "\n", encoding="utf-8")
        print(f"Wrote {output}")


if __name__ == "__main__":
    main()
