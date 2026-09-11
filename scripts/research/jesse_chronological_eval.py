#!/usr/bin/env python3
"""Reproducible, artifact-free evaluation against the Jesse candle store.

Features are observed at a candle close and can only trade at the next candle
open. Models, calibration, and threshold selection are kept chronological.
The script reads PostgreSQL and emits JSON; it never saves or promotes models.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd
import psycopg2

try:
    from lightgbm import LGBMClassifier
except ImportError:  # pragma: no cover - only required by real evaluations
    LGBMClassifier = None  # type: ignore[assignment,misc]


@dataclass(frozen=True)
class ExitPolicy:
    name: str
    stop_atr: float
    target_atr: float
    max_holding_bars: int
    partial_atr: float | None = None
    partial_fraction: float = 0.0
    trailing_activation_atr: float | None = None
    trailing_distance_atr: float | None = None


@dataclass(frozen=True)
class CostScenario:
    fee_per_side: float
    slippage_per_side: float
    assumed_funding_rate: float
    funding_hours_utc: tuple[int, ...] = (0, 8, 16)


@dataclass
class TradePath:
    side: int
    signal_time: pd.Timestamp
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_fill: float
    net_return: float
    equity: pd.Series
    partial_exits: int
    funding_events: int


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
    parser.add_argument("--exchange", default="Binance Perpetual Futures")
    parser.add_argument("--source-timeframe", default="1m")
    parser.add_argument("--timeframe", default="1h", choices=["15m", "1h", "4h"])
    parser.add_argument("--start", default="2024-01-01T00:00:00Z")
    parser.add_argument("--end", default="2026-09-09T00:00:00Z")
    parser.add_argument("--train-fraction", type=float, default=0.60)
    parser.add_argument("--validation-fraction", type=float, default=0.20)
    parser.add_argument("--fee-per-side", type=float, default=0.0004)
    parser.add_argument("--slippage-per-side", type=float, default=0.0002)
    parser.add_argument("--assumed-funding-rate", type=float, default=0.0001)
    parser.add_argument("--code-commit", required=True)
    parser.add_argument("--output", default="-")
    return parser.parse_args()


def _utc_timestamp(value: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")


def _frame_hash(frame: pd.DataFrame) -> str:
    hashed = pd.util.hash_pandas_object(frame.reset_index(), index=False).values
    return hashlib.sha256(hashed.tobytes()).hexdigest()


def validate_candles(frame: pd.DataFrame, expected_interval: pd.Timedelta) -> dict[str, Any]:
    if frame.empty:
        raise ValueError("Candle dataset is empty")
    if not frame.index.is_monotonic_increasing:
        raise ValueError("Candle timestamps are not monotonic increasing")
    if not frame.index.is_unique:
        duplicates = int(frame.index.duplicated().sum())
        raise ValueError(f"Candle timestamps are not unique ({duplicates} duplicates)")
    deltas = frame.index.to_series().diff().dropna()
    gaps = deltas[deltas != expected_interval]
    backward = deltas[deltas <= pd.Timedelta(0)]
    if not backward.empty:
        raise ValueError("Candle timestamps contain non-positive intervals")
    return {
        "timestamp_unique": True,
        "timestamp_monotonic": True,
        "expected_interval_seconds": int(expected_interval.total_seconds()),
        "unexpected_interval_count": int(len(gaps)),
        "missing_interval_estimate": int(
            sum(max(0, round(delta / expected_interval) - 1) for delta in gaps)
        ),
        "largest_interval_seconds": int(deltas.max().total_seconds()) if not deltas.empty else 0,
    }


def load_candles(
    symbol: str,
    exchange: str,
    source_timeframe: str,
    timeframe: str,
    start: str,
    end: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    required = ["POSTGRES_HOST", "POSTGRES_NAME", "POSTGRES_USERNAME", "POSTGRES_PASSWORD"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Missing database environment variables: {', '.join(missing)}")
    start_timestamp = _utc_timestamp(start)
    end_timestamp = _utc_timestamp(end)
    if end_timestamp <= start_timestamp:
        raise ValueError("End cutoff must be after start cutoff")
    query = """
        SELECT timestamp, open, high, low, close, volume
        FROM candle
        WHERE symbol = %s
          AND exchange = %s
          AND timeframe = %s
          AND timestamp >= %s
          AND timestamp < %s
        ORDER BY timestamp ASC
    """
    connection = psycopg2.connect(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.environ["POSTGRES_NAME"],
        user=os.environ["POSTGRES_USERNAME"],
        password=os.environ["POSTGRES_PASSWORD"],
    )
    try:
        raw = pd.read_sql_query(
            query,
            connection,
            params=(
                symbol,
                exchange,
                source_timeframe,
                int(start_timestamp.timestamp() * 1000),
                int(end_timestamp.timestamp() * 1000),
            ),
        )
    finally:
        connection.close()
    if raw.empty:
        raise RuntimeError(
            f"No candles for symbol={symbol}, exchange={exchange}, timeframe={source_timeframe}"
        )
    raw.index = pd.to_datetime(raw.pop("timestamp"), unit="ms", utc=True)
    source_intervals = {"1m": "1min", "5m": "5min", "15m": "15min", "1h": "1h"}
    if source_timeframe not in source_intervals:
        raise ValueError(f"Unsupported source timeframe: {source_timeframe}")
    raw_quality = validate_candles(raw, pd.Timedelta(source_intervals[source_timeframe]))
    source_hash = _frame_hash(raw)

    rules = {"15m": "15min", "1h": "1h", "4h": "4h"}
    frame = (
        raw.resample(rules[timeframe])
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
    )
    resampled_quality = validate_candles(frame, pd.Timedelta(rules[timeframe]))
    provenance = {
        "database_table": "candle",
        "symbol": symbol,
        "exchange": exchange,
        "source_timeframe": source_timeframe,
        "requested_start_inclusive": start_timestamp.isoformat(),
        "requested_end_exclusive": end_timestamp.isoformat(),
        "raw_rows": len(raw),
        "raw_start": raw.index[0].isoformat(),
        "raw_end": raw.index[-1].isoformat(),
        "raw_sha256": source_hash,
        "raw_quality": raw_quality,
        "resampled_quality": resampled_quality,
        "funding_provenance": "Unavailable: the Jesse PostgreSQL schema has no funding-rate table.",
    }
    return frame, provenance


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
    return 100 - 100 / (1 + gain / (loss + 1e-12))


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


def _adverse_fill(price: float, side: int, slippage: float, opening: bool) -> float:
    direction = side if opening else -side
    return price * (1 + direction * slippage)


def _is_funding_timestamp(timestamp: pd.Timestamp, hours: tuple[int, ...]) -> bool:
    return timestamp.minute == 0 and timestamp.second == 0 and timestamp.hour in hours


def simulate_trade(
    frame: pd.DataFrame,
    signal_time: pd.Timestamp,
    side: int,
    policy: ExitPolicy,
    costs: CostScenario,
) -> TradePath:
    if side not in (-1, 1):
        raise ValueError("Trade side must be -1 or 1")
    signal_location = frame.index.get_loc(signal_time)
    entry_location = signal_location + 1
    final_location = min(entry_location + policy.max_holding_bars - 1, len(frame) - 1)
    if entry_location >= len(frame):
        raise ValueError("No next bar is available for entry")

    atr = average_true_range(frame)
    entry_time = frame.index[entry_location]
    raw_entry = float(frame["open"].iloc[entry_location])
    entry_fill = _adverse_fill(raw_entry, side, costs.slippage_per_side, opening=True)
    initial_atr = float(atr.iloc[signal_location])
    stop = raw_entry - side * policy.stop_atr * initial_atr
    target = raw_entry + side * policy.target_atr * initial_atr
    remaining = 1.0
    realized_pnl = 0.0
    exit_fees = 0.0
    funding_cost = 0.0
    funding_events = 0
    partial_exits = 0
    partial_done = False
    favorable_extreme = raw_entry
    path_values: dict[pd.Timestamp, float] = {}
    exit_time = frame.index[final_location]

    for location in range(entry_location, final_location + 1):
        timestamp = frame.index[location]
        row = frame.iloc[location]
        if timestamp > entry_time and _is_funding_timestamp(timestamp, costs.funding_hours_utc):
            funding_cost += remaining * side * costs.assumed_funding_rate
            funding_events += 1

        if side == 1:
            stop_hit = float(row["low"]) <= stop
            target_hit = float(row["high"]) >= target
        else:
            stop_hit = float(row["high"]) >= stop
            target_hit = float(row["low"]) <= target

        forced_price: float | None = None
        if stop_hit and target_hit:
            forced_price = stop
        elif stop_hit:
            forced_price = stop
        elif target_hit:
            forced_price = target

        if forced_price is not None:
            exit_fill = _adverse_fill(forced_price, side, costs.slippage_per_side, opening=False)
            realized_pnl += remaining * side * (exit_fill / entry_fill - 1)
            exit_fees += remaining * costs.fee_per_side * (exit_fill / entry_fill)
            remaining = 0.0
            exit_time = timestamp
        else:
            close = float(row["close"])
            current_atr = float(atr.iloc[location])
            favorable_move = side * (close - raw_entry)
            if (
                not partial_done
                and policy.partial_atr is not None
                and policy.partial_fraction > 0
                and favorable_move >= policy.partial_atr * current_atr
            ):
                fraction = min(remaining, policy.partial_fraction)
                partial_fill = _adverse_fill(close, side, costs.slippage_per_side, opening=False)
                realized_pnl += fraction * side * (partial_fill / entry_fill - 1)
                exit_fees += fraction * costs.fee_per_side * (partial_fill / entry_fill)
                remaining -= fraction
                partial_done = True
                partial_exits += 1

            if policy.trailing_activation_atr is not None and policy.trailing_distance_atr is not None:
                favorable_extreme = max(favorable_extreme, close) if side == 1 else min(favorable_extreme, close)
                activation = side * (favorable_extreme - raw_entry)
                if activation >= policy.trailing_activation_atr * current_atr:
                    candidate = favorable_extreme - side * policy.trailing_distance_atr * current_atr
                    if side == 1 and candidate < close:
                        stop = max(stop, candidate)
                    elif side == -1 and candidate > close:
                        stop = min(stop, candidate)

            if location == final_location and remaining > 0:
                exit_fill = _adverse_fill(close, side, costs.slippage_per_side, opening=False)
                realized_pnl += remaining * side * (exit_fill / entry_fill - 1)
                exit_fees += remaining * costs.fee_per_side * (exit_fill / entry_fill)
                remaining = 0.0
                exit_time = timestamp

        mark_component = remaining * side * (float(row["close"]) / entry_fill - 1)
        path_values[timestamp] = (
            1
            - costs.fee_per_side
            + realized_pnl
            - exit_fees
            - funding_cost
            + mark_component
        )
        if remaining <= 0:
            break

    equity = pd.Series(path_values, dtype=float)
    return TradePath(
        side=side,
        signal_time=signal_time,
        entry_time=entry_time,
        exit_time=exit_time,
        entry_fill=entry_fill,
        net_return=float(equity.iloc[-1] - 1),
        equity=equity,
        partial_exits=partial_exits,
        funding_events=funding_events,
    )


def payoff_direction_labels(
    frame: pd.DataFrame,
    index: pd.Index,
    policy: ExitPolicy,
    costs: CostScenario,
) -> pd.Series:
    """Choose the better positive side under the exact simulated payoff policy."""
    labels = pd.Series(0, index=index, dtype="int8")
    for timestamp in index:
        long_return = simulate_trade(frame, timestamp, 1, policy, costs).net_return
        short_return = simulate_trade(frame, timestamp, -1, policy, costs).net_return
        best = max(long_return, short_return)
        if best <= 0 or math.isclose(long_return, short_return, abs_tol=1e-12):
            labels.loc[timestamp] = 0
        else:
            labels.loc[timestamp] = 1 if long_return > short_return else -1
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
    test = np.arange(validation_end, count)
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
    losses = [
        -float(
            np.log(temperature_scale(probabilities, float(value))[np.arange(len(labels)), labels] + 1e-12).mean()
        )
        for value in candidates
    ]
    return float(candidates[int(np.argmin(losses))])


def equity_metrics(equity: pd.Series) -> dict[str, float | None]:
    daily_equity = equity.resample("1D").last().dropna()
    daily_returns = daily_equity.pct_change()
    daily_returns.iloc[0] = daily_equity.iloc[0] - 1.0
    if daily_returns.empty:
        sharpe = sortino = None
    else:
        standard_deviation = float(daily_returns.std(ddof=1))
        downside_deviation = float(np.sqrt(np.mean(np.minimum(daily_returns.to_numpy(), 0.0) ** 2)))
        sharpe = (
            float(daily_returns.mean() / standard_deviation * math.sqrt(365))
            if standard_deviation > 1e-12
            else None
        )
        sortino = (
            float(daily_returns.mean() / downside_deviation * math.sqrt(365))
            if downside_deviation > 1e-12
            else None
        )
    values_with_initial_capital = np.concatenate([[1.0], equity.to_numpy()])
    drawdown = values_with_initial_capital / np.maximum.accumulate(values_with_initial_capital) - 1
    return {
        "total_return_pct": float((equity.iloc[-1] - 1) * 100),
        "annualized_sharpe": sharpe,
        "annualized_sortino": sortino,
        "max_drawdown_pct": float(drawdown.min() * 100),
    }


def simulate_predictions(
    frame: pd.DataFrame,
    sample_index: pd.Index,
    probabilities: np.ndarray,
    threshold: float,
    policy: ExitPolicy,
    costs: CostScenario,
    horizon_start: pd.Timestamp,
    horizon_end: pd.Timestamp,
) -> dict[str, Any]:
    probability_by_time = {
        timestamp: probabilities[position] for position, timestamp in enumerate(sample_index)
    }
    horizon = frame.loc[horizon_start:horizon_end].index
    equity = pd.Series(1.0, index=horizon)
    account_equity = 1.0
    trade_returns: list[float] = []
    funding_events = 0
    partial_exits = 0
    cursor = 0
    timestamps = list(sample_index)

    while cursor < len(timestamps):
        signal_time = timestamps[cursor]
        predicted_class = int(np.argmax(probability_by_time[signal_time]))
        if predicted_class == 0 or float(probability_by_time[signal_time][predicted_class]) < threshold:
            cursor += 1
            continue
        side = 1 if predicted_class == 1 else -1
        trade = simulate_trade(frame, signal_time, side, policy, costs)
        trade_equity = trade.equity.loc[:horizon_end]
        equity.loc[trade.entry_time : trade.exit_time] = account_equity * trade_equity
        account_equity *= 1 + trade.net_return
        equity.loc[trade.exit_time:] = account_equity
        trade_returns.append(trade.net_return)
        funding_events += trade.funding_events
        partial_exits += trade.partial_exits
        cursor = int(sample_index.searchsorted(trade.exit_time, side="left"))
        if cursor < len(sample_index) and sample_index[cursor] < trade.exit_time:
            cursor += 1

    metrics = equity_metrics(equity)
    metrics.update(
        {
            "trades": len(trade_returns),
            "win_rate_pct": float(np.mean(np.asarray(trade_returns) > 0) * 100) if trade_returns else 0.0,
            "funding_events": funding_events,
            "partial_exits": partial_exits,
            "equity_start": horizon_start.isoformat(),
            "equity_end": horizon_end.isoformat(),
        }
    )
    return metrics


def buy_and_hold(
    frame: pd.DataFrame,
    horizon_start: pd.Timestamp,
    horizon_end: pd.Timestamp,
    costs: CostScenario,
) -> dict[str, Any]:
    subset = frame.loc[horizon_start:horizon_end]
    if len(subset) < 2:
        raise ValueError("Buy-and-hold horizon requires a next bar for entry")
    entry_fill = _adverse_fill(float(subset["open"].iloc[1]), 1, costs.slippage_per_side, opening=True)
    equity = pd.Series(1.0, index=subset.index)
    equity.iloc[1:] = 1 - costs.fee_per_side + subset["close"].iloc[1:] / entry_fill - 1
    exit_fill = _adverse_fill(float(subset["close"].iloc[-1]), 1, costs.slippage_per_side, opening=False)
    equity.iloc[-1] = (
        1
        - costs.fee_per_side
        + exit_fill / entry_fill
        - 1
        - costs.fee_per_side * (exit_fill / entry_fill)
    )
    metrics = equity_metrics(equity)
    metrics.update(
        {
            "equity_start": horizon_start.isoformat(),
            "equity_end": horizon_end.isoformat(),
            "funding_included": False,
            "description": "Unlevered spot buy-and-hold with identical fee/slippage fill model.",
        }
    )
    return metrics


def select_threshold(
    frame: pd.DataFrame,
    sample_index: pd.Index,
    probabilities: np.ndarray,
    policy: ExitPolicy,
    costs: CostScenario,
    horizon_start: pd.Timestamp,
    horizon_end: pd.Timestamp,
) -> tuple[float, dict[str, Any]]:
    candidates = np.arange(0.40, 0.71, 0.05)
    scored = [
        (
            float(threshold),
            simulate_predictions(
                frame,
                sample_index,
                probabilities,
                float(threshold),
                policy,
                costs,
                horizon_start,
                horizon_end,
            ),
        )
        for threshold in candidates
    ]
    eligible = [item for item in scored if item[1]["trades"] >= 20]
    pool = eligible or scored
    return max(
        pool,
        key=lambda item: (
            item[1]["annualized_sharpe"] if item[1]["annualized_sharpe"] is not None else -math.inf,
            item[1]["total_return_pct"],
        ),
    )


def _model() -> Any:
    if LGBMClassifier is None:
        raise RuntimeError("lightgbm is required in the Jesse evaluation environment")
    return LGBMClassifier(
        n_estimators=160,
        learning_rate=0.03,
        max_depth=5,
        num_leaves=24,
        class_weight="balanced",
        random_state=42,
        deterministic=True,
        force_col_wise=True,
        n_jobs=1,
        verbosity=-1,
    )


def evaluate_policy(
    frame: pd.DataFrame,
    features: pd.DataFrame,
    labels: pd.Series,
    policy: ExitPolicy,
    costs: CostScenario,
    train_positions: np.ndarray,
    validation_positions: np.ndarray,
    test_positions: np.ndarray,
    horizon_start: pd.Timestamp,
    horizon_end: pd.Timestamp,
) -> dict[str, Any]:
    encoded = labels.map({0: 0, 1: 1, -1: 2}).astype(int)
    model = _model()
    model.fit(features.iloc[train_positions], encoded.iloc[train_positions])
    validation_raw = model.predict_proba(features.iloc[validation_positions])
    temperature = select_temperature(validation_raw, encoded.iloc[validation_positions].to_numpy())
    validation_probabilities = temperature_scale(validation_raw, temperature)
    validation_start = features.index[validation_positions[0]]
    validation_end = frame.index[
        frame.index.get_loc(features.index[validation_positions[-1]]) + policy.max_holding_bars
    ]
    threshold, validation_metrics = select_threshold(
        frame,
        features.index[validation_positions],
        validation_probabilities,
        policy,
        costs,
        validation_start,
        validation_end,
    )
    test_probabilities = temperature_scale(model.predict_proba(features.iloc[test_positions]), temperature)
    test_metrics = simulate_predictions(
        frame,
        features.index[test_positions],
        test_probabilities,
        threshold,
        policy,
        costs,
        horizon_start,
        horizon_end,
    )
    return {
        "scope": (
            "Research proxy for RiskConfig exit defaults only; it does not reproduce the "
            "CombinedStrategy, regime/Kronos/LLM gates, exchange tick ordering, or live sizing."
        ),
        "exit_policy": asdict(policy),
        "temperature_selected_on_validation": temperature,
        "confidence_threshold_selected_on_validation": threshold,
        "validation_metrics": validation_metrics,
        "test_metrics": test_metrics,
    }


def period(index: pd.Index, positions: Sequence[int]) -> dict[str, Any]:
    return {
        "signal_start": index[positions[0]].isoformat(),
        "signal_end": index[positions[-1]].isoformat(),
        "observations": len(positions),
    }


def _versions() -> dict[str, str]:
    packages = ["lightgbm", "numpy", "pandas", "psycopg2-binary", "scikit-learn"]
    versions = {"python": platform.python_version()}
    for package in packages:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def main() -> None:
    args = parse_args()
    costs = CostScenario(
        args.fee_per_side,
        args.slippage_per_side,
        args.assumed_funding_rate,
    )
    frame, provenance = load_candles(
        args.symbol,
        args.exchange,
        args.source_timeframe,
        args.timeframe,
        args.start,
        args.end,
    )
    features = compute_features(frame)
    policies = [
        ExitPolicy("symmetric_fixed_barrier_reference", 1.0, 2.5, 72),
        ExitPolicy(
            "production_defaults_exit_proxy",
            1.0,
            2.5,
            72,
            partial_atr=1.0,
            partial_fraction=0.5,
            trailing_activation_atr=2.0,
            trailing_distance_atr=0.8,
        ),
    ]
    max_holding = max(policy.max_holding_bars for policy in policies)
    eligible_features = features.iloc[: -(max_holding + 1)]
    train, validation, test = chronological_indices(
        eligible_features.index,
        args.train_fraction,
        args.validation_fraction,
        max_holding,
    )
    horizon_start = eligible_features.index[test[0]]
    horizon_end = frame.index[
        frame.index.get_loc(eligible_features.index[test[-1]]) + max_holding
    ]
    experiments = []
    for policy in policies:
        labels = payoff_direction_labels(frame, eligible_features.index, policy, costs)
        experiments.append(
            evaluate_policy(
                frame,
                eligible_features,
                labels,
                policy,
                costs,
                train,
                validation,
                test,
                horizon_start,
                horizon_end,
            )
        )
    command = (
        "python3 scripts/research/jesse_chronological_eval.py "
        f"--symbol {args.symbol!r} --exchange {args.exchange!r} "
        f"--source-timeframe {args.source_timeframe!r} --timeframe {args.timeframe!r} "
        f"--start {args.start!r} --end {args.end!r} "
        f"--fee-per-side {args.fee_per_side} --slippage-per-side {args.slippage_per_side} "
        f"--assumed-funding-rate {args.assumed_funding_rate} --code-commit {args.code_commit}"
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "code_commit": args.code_commit,
        "command": command,
        "library_versions": _versions(),
        "random_seed": 42,
        "data_provenance": provenance,
        "resampled_data": {
            "timeframe": args.timeframe,
            "bars": len(frame),
            "start": frame.index[0].isoformat(),
            "end": frame.index[-1].isoformat(),
        },
        "splits": {
            "train": period(eligible_features.index, train),
            "validation": period(eligible_features.index, validation),
            "test": period(eligible_features.index, test),
            "purge_bars_between_train_validation_and_validation_test": max_holding,
            "common_equity_horizon": {
                "start": horizon_start.isoformat(),
                "end": horizon_end.isoformat(),
            },
        },
        "cost_scenario": {
            **asdict(costs),
            "funding_interpretation": (
                "Scenario assumption, not historical funding: positive fixed rate means longs pay "
                "and shorts receive at 00:00/08:00/16:00 UTC while open."
            ),
            "fill_accounting": (
                "Fees are charged on exact entry/exit fill notionals; adverse slippage is applied "
                "to each fill price."
            ),
        },
        "experiments": experiments,
        "spot_buy_and_hold_test_baseline": buy_and_hold(frame, horizon_start, horizon_end, costs),
        "notes": [
            "Features observed at candle close execute no earlier than the next candle open.",
            "Labels use the same next-open fills, exit policy, costs, and funding scenario as simulation.",
            "Strategy and benchmark use the identical equity start/end horizon and daily MTM metrics.",
            "Models are trained in memory and are not saved or promoted.",
            "Test is evaluated only after validation-only calibration and threshold selection.",
            "Same-bar stop/target ambiguity is resolved conservatively as stop first.",
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
