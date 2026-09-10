"""Sidecar timestamp helper: empty/invalid bar dates must not raise NaT."""
import pandas as pd

from main import _build_timestamps


def test_empty_dates_do_not_nat():
    df = pd.DataFrame({"date": [""] * 20, "close": list(range(20))})
    x_ts, y_ts = _build_timestamps(df, lookback=10, pred_len=5)
    assert x_ts.notna().all()
    assert y_ts.notna().all()
    assert len(x_ts) == 10
    assert len(y_ts) == 5


def test_valid_dates_preserved():
    idx = pd.date_range("2026-09-01", periods=12, freq="h", tz="UTC")
    df = pd.DataFrame({"date": idx.astype(str), "close": list(range(12))})
    x_ts, y_ts = _build_timestamps(df, lookback=10, pred_len=5)
    assert x_ts.iloc[-1] == idx[-1]
    assert y_ts.notna().all()
