from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from scripts.research.jesse_chronological_eval import (
    CostScenario,
    ExitPolicy,
    buy_and_hold,
    chronological_indices,
    equity_metrics,
    payoff_direction_labels,
    simulate_predictions,
    simulate_trade,
    validate_candles,
)


def _candles(
    closes: list[float],
    *,
    opens: list[float] | None = None,
    high_offset: float = 0.1,
    low_offset: float = 0.1,
    start: str = "2024-01-01T00:00:00Z",
) -> pd.DataFrame:
    close = np.asarray(closes, dtype=float)
    open_values = np.asarray(opens if opens is not None else closes, dtype=float)
    index = pd.date_range(start, periods=len(close), freq="1h")
    return pd.DataFrame(
        {
            "open": open_values,
            "high": np.maximum(open_values, close) + high_offset,
            "low": np.minimum(open_values, close) - low_offset,
            "close": close,
            "volume": np.full(len(close), 1000.0),
        },
        index=index,
    )


ZERO_COSTS = CostScenario(0.0, 0.0, 0.0)
FIXED_POLICY = ExitPolicy("fixed", stop_atr=1.0, target_atr=1.0, max_holding_bars=2)


def test_payoff_labels_are_directionally_symmetric() -> None:
    rising = _candles([100, 100, 103, 104], high_offset=0.2, low_offset=0.2)
    falling = _candles([100, 100, 97, 96], high_offset=0.2, low_offset=0.2)

    rising_label = payoff_direction_labels(rising, rising.index[:1], FIXED_POLICY, ZERO_COSTS)
    falling_label = payoff_direction_labels(falling, falling.index[:1], FIXED_POLICY, ZERO_COSTS)

    assert rising_label.iloc[0] == 1
    assert falling_label.iloc[0] == -1


def test_signal_close_executes_at_next_bar_open() -> None:
    frame = _candles(
        [100, 111, 112, 113],
        opens=[100, 110, 111, 112],
        high_offset=0.1,
        low_offset=0.1,
    )
    policy = ExitPolicy("hold", stop_atr=100, target_atr=100, max_holding_bars=2)

    trade = simulate_trade(frame, frame.index[0], 1, policy, ZERO_COSTS)

    assert trade.entry_time == frame.index[1]
    assert trade.entry_fill == pytest.approx(110.0)
    assert trade.net_return == pytest.approx(112 / 110 - 1)


def test_exit_scan_includes_entry_bar_after_next_open() -> None:
    frame = _candles([100, 100, 100], opens=[100, 100, 100], high_offset=5, low_offset=0.1)
    policy = ExitPolicy("target", stop_atr=10, target_atr=0.5, max_holding_bars=2)

    trade = simulate_trade(frame, frame.index[0], 1, policy, ZERO_COSTS)

    assert trade.exit_time == frame.index[1]
    assert trade.net_return > 0


def test_missing_historical_funding_uses_conservative_fixed_timestamp_debits() -> None:
    frame = _candles(
        [100] * 12,
        high_offset=0.01,
        low_offset=0.01,
        start="2024-01-01T00:00:00Z",
    )
    policy = ExitPolicy("hold", stop_atr=100, target_atr=100, max_holding_bars=9)
    funding = CostScenario(0.0, 0.0, 0.0001)

    long_trade = simulate_trade(frame, frame.index[0], 1, policy, funding)
    short_trade = simulate_trade(frame, frame.index[0], -1, policy, funding)

    assert long_trade.funding_events == 1
    assert short_trade.funding_events == 1
    assert long_trade.net_return == pytest.approx(-0.0001)
    assert short_trade.net_return == pytest.approx(-0.0001)


def test_fees_and_slippage_are_applied_through_fill_notionals() -> None:
    frame = _candles([100, 100, 100], high_offset=0.01, low_offset=0.01)
    policy = ExitPolicy("hold", stop_atr=100, target_atr=100, max_holding_bars=1)
    costs = CostScenario(0.001, 0.002, 0.0)

    trade = simulate_trade(frame, frame.index[0], 1, policy, costs)

    entry_fill = 100 * 1.002
    exit_fill = 100 * 0.998
    expected = -0.001 + (exit_fill / entry_fill - 1) - 0.001 * (exit_fill / entry_fill)
    assert trade.net_return == pytest.approx(expected)


def test_equity_metrics_use_mtm_drawdown_and_valid_downside_deviation() -> None:
    index = pd.date_range("2024-01-01", periods=3, freq="1D", tz="UTC")
    equity = pd.Series([1.0, 0.8, 1.0], index=index)

    metrics = equity_metrics(equity)

    daily_returns = np.asarray([0.0, -0.2, 0.25])
    expected_downside = math.sqrt(np.mean(np.minimum(daily_returns, 0) ** 2))
    expected_sortino = daily_returns.mean() / expected_downside * math.sqrt(365)
    assert metrics["max_drawdown_pct"] == pytest.approx(-20.0)
    assert metrics["annualized_sortino"] == pytest.approx(expected_sortino)


def test_strategy_mtm_records_open_trade_drawdown() -> None:
    frame = _candles(
        [100, 90, 80, 100, 105],
        opens=[100, 100, 90, 80, 100],
        high_offset=0.1,
        low_offset=0.1,
    )
    probabilities = np.tile(np.asarray([0.0, 1.0, 0.0]), (1, 1))
    policy = ExitPolicy("hold", stop_atr=100, target_atr=100, max_holding_bars=4)

    metrics = simulate_predictions(
        frame,
        frame.index[:1],
        probabilities,
        0.5,
        policy,
        ZERO_COSTS,
        frame.index[0],
        frame.index[-1],
    )

    assert metrics["max_drawdown_pct"] == pytest.approx(-20.0)


def test_benchmark_and_strategy_share_exact_horizon() -> None:
    frame = _candles([100, 101, 102, 103, 104])
    probabilities = np.tile(np.asarray([1.0, 0.0, 0.0]), (2, 1))
    policy = ExitPolicy("hold", stop_atr=100, target_atr=100, max_holding_bars=2)
    start, end = frame.index[0], frame.index[-1]

    strategy = simulate_predictions(
        frame,
        frame.index[:2],
        probabilities,
        0.5,
        policy,
        ZERO_COSTS,
        start,
        end,
    )
    benchmark = buy_and_hold(frame, start, end, ZERO_COSTS)

    assert strategy["equity_start"] == benchmark["equity_start"] == start.isoformat()
    assert strategy["equity_end"] == benchmark["equity_end"] == end.isoformat()


def test_chronological_indices_purge_only_preceding_segments() -> None:
    index = pd.date_range("2024-01-01", periods=100, freq="1h", tz="UTC")

    train, validation, test = chronological_indices(index, 0.6, 0.2, purge_bars=5)

    assert train.tolist() == list(range(55))
    assert validation.tolist() == list(range(60, 75))
    assert test.tolist() == list(range(80, 100))


def test_data_validation_reports_gaps_and_rejects_duplicates() -> None:
    frame = _candles([100, 101, 102])
    gapped = frame.drop(frame.index[1])

    report = validate_candles(gapped, pd.Timedelta("1h"))

    assert report["unexpected_interval_count"] == 1
    assert report["missing_interval_estimate"] == 1
    duplicated = pd.concat([frame, frame.iloc[-1:]]).sort_index()
    with pytest.raises(ValueError, match="not unique"):
        validate_candles(duplicated, pd.Timedelta("1h"))
