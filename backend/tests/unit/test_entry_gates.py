"""New-bar gate and per-symbol expectancy gate for the trading loop."""
from unittest.mock import MagicMock, patch

from backend.services.trading_loop import TradingLoopService, negative_expectancy_symbols


def _bars(ts: str):
    return [{"date": "2026-07-10T16:00:00", "close": 100.0},
            {"date": ts, "close": 101.0}]


# ── New-bar gate ────────────────────────────────────────────────────────────

def test_new_bar_gate_evaluates_first_sighting():
    loop = TradingLoopService()
    assert loop._should_evaluate_bar("ETHUSDT", _bars("2026-07-10T17:00:00")) is True


def test_new_bar_gate_blocks_same_bar_reevaluation():
    """15-min cycles on 1h bars: cycles 2-4 within the hour must not re-decide."""
    loop = TradingLoopService()
    bars = _bars("2026-07-10T17:00:00")
    assert loop._should_evaluate_bar("ETHUSDT", bars) is True
    assert loop._should_evaluate_bar("ETHUSDT", bars) is False
    assert loop._should_evaluate_bar("ETHUSDT", bars) is False


def test_new_bar_gate_reopens_on_new_bar():
    loop = TradingLoopService()
    assert loop._should_evaluate_bar("ETHUSDT", _bars("2026-07-10T17:00:00")) is True
    assert loop._should_evaluate_bar("ETHUSDT", _bars("2026-07-10T17:00:00")) is False
    assert loop._should_evaluate_bar("ETHUSDT", _bars("2026-07-10T18:00:00")) is True


def test_new_bar_gate_tracks_symbols_independently():
    loop = TradingLoopService()
    bars = _bars("2026-07-10T17:00:00")
    assert loop._should_evaluate_bar("ETHUSDT", bars) is True
    assert loop._should_evaluate_bar("SOLUSDT", bars) is True
    assert loop._should_evaluate_bar("ETHUSDT", bars) is False


def test_new_bar_gate_fails_open_without_timestamps():
    loop = TradingLoopService()
    bars = [{"close": 100.0}]
    assert loop._should_evaluate_bar("ETHUSDT", bars) is True
    assert loop._should_evaluate_bar("ETHUSDT", bars) is True


def test_new_bar_gate_disabled_via_config():
    loop = TradingLoopService()
    loop.risk_config = MagicMock(eval_on_new_bar_only=False)
    bars = _bars("2026-07-10T17:00:00")
    assert loop._should_evaluate_bar("ETHUSDT", bars) is True
    assert loop._should_evaluate_bar("ETHUSDT", bars) is True


# ── Expectancy gate ─────────────────────────────────────────────────────────

def test_negative_expectancy_symbols_blocks_proven_losers():
    rows = [
        ("AVAXUSDT", 59, -8.08),   # loser, enough sample -> blocked
        ("SOLUSDT", 65, -8.04),    # loser, enough sample -> blocked
        ("ADAUSDT", 78, 12.66),    # winner -> kept
        ("LINKUSDT", 16, -4.92),   # loser but small sample -> kept
        ("ATOMUSDT", 25, 0.0),     # breakeven -> kept
    ]
    blocked = negative_expectancy_symbols(rows, min_trades=20)
    assert blocked == {"AVAXUSDT", "SOLUSDT"}


def test_negative_expectancy_symbols_handles_null_aggregates():
    rows = [("XUSDT", None, None), ("YUSDT", 30, None)]
    assert negative_expectancy_symbols(rows, min_trades=20) == set()


def test_expectancy_gate_never_blocks_open_positions():
    """A symbol with an open position must stay managed even if it bleeds."""
    loop = TradingLoopService()
    loop.risk_config = MagicMock(
        symbol_expectancy_gate_enabled=True,
        symbol_expectancy_lookback_days=30,
        symbol_expectancy_min_trades=20,
    )
    db = MagicMock()
    # Aggregate rows: both AVAX and SOL are proven losers
    db.query.return_value.filter.return_value.group_by.return_value.all.return_value = [
        ("AVAXUSDT", 59, -8.08),
        ("SOLUSDT", 65, -8.04),
    ]
    # AVAX has an open position -> exempt from blocking
    db.query.return_value.filter.return_value.distinct.return_value.all.return_value = [
        ("AVAXUSDT",),
    ]
    with patch("backend.services.trading_loop.SessionLocal", return_value=db):
        kept = loop._apply_expectancy_gate(["AVAXUSDT", "SOLUSDT", "ADAUSDT"])
    assert kept == ["AVAXUSDT", "ADAUSDT"]


def test_expectancy_gate_disabled_keeps_all():
    loop = TradingLoopService()
    loop.risk_config = MagicMock(symbol_expectancy_gate_enabled=False)
    assert loop._apply_expectancy_gate(["AVAXUSDT", "SOLUSDT"]) == ["AVAXUSDT", "SOLUSDT"]


def test_expectancy_gate_fails_open_on_db_error():
    loop = TradingLoopService()
    loop.risk_config = MagicMock(
        symbol_expectancy_gate_enabled=True,
        symbol_expectancy_lookback_days=30,
        symbol_expectancy_min_trades=20,
    )
    with patch("backend.services.trading_loop.SessionLocal", side_effect=RuntimeError("db down")):
        kept = loop._apply_expectancy_gate(["AVAXUSDT", "SOLUSDT"])
    assert kept == ["AVAXUSDT", "SOLUSDT"]
