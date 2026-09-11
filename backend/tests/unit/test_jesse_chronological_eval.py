from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.research.jesse_chronological_eval import (
    CostModel,
    Geometry,
    chronological_indices,
    simulate_predictions,
    temperature_scale,
)


def _candles(periods: int = 140) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=periods, freq="1h", tz="UTC")
    close = np.linspace(100.0, 130.0, periods)
    return pd.DataFrame(
        {
            "open": close - 0.1,
            "high": close + 0.6,
            "low": close - 0.6,
            "close": close,
            "volume": np.full(periods, 1000.0),
        },
        index=index,
    )


def test_chronological_indices_purge_each_preceding_segment() -> None:
    index = pd.date_range("2024-01-01", periods=100, freq="1h", tz="UTC")

    train, validation, test = chronological_indices(index, 0.6, 0.2, purge_bars=5)

    assert train.tolist() == list(range(55))
    assert validation.tolist() == list(range(60, 75))
    assert test.tolist() == list(range(80, 95))
    assert train[-1] < validation[0] < test[0]


def test_chronological_indices_reject_invalid_split() -> None:
    index = pd.date_range("2024-01-01", periods=100, freq="1h", tz="UTC")

    with pytest.raises(ValueError, match="leave a test segment"):
        chronological_indices(index, 0.8, 0.2, purge_bars=5)


def test_temperature_scale_preserves_probability_rows() -> None:
    probabilities = np.asarray([[0.7, 0.2, 0.1], [0.1, 0.3, 0.6]])

    scaled = temperature_scale(probabilities, temperature=1.5)

    np.testing.assert_allclose(scaled.sum(axis=1), np.ones(2))
    assert np.argmax(scaled[0]) == 0
    assert np.argmax(scaled[1]) == 2


def test_simulation_charges_round_trip_costs_and_prevents_overlap() -> None:
    frame = _candles()
    sample_index = frame.index[20:120]
    probabilities = np.tile(np.asarray([0.01, 0.98, 0.01]), (len(sample_index), 1))
    geometry = Geometry("test", profit_atr=0.5, stop_atr=4.0, max_holding_bars=12)
    no_costs = CostModel(0.0, 0.0, 0.0)
    realistic_costs = CostModel(0.0006, 0.0003, 0.0001)

    free = simulate_predictions(frame, sample_index, probabilities, 0.5, geometry, no_costs)
    realistic = simulate_predictions(frame, sample_index, probabilities, 0.5, geometry, realistic_costs)

    assert 0 < realistic["trades"] < len(sample_index)
    assert realistic["trades"] == free["trades"]
    assert realistic["total_return_pct"] < free["total_return_pct"]
