"""Break-even / trail must lock at or through entry, not clamp back past it."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.services import ctrader_exit_manager as em
from backend.services.ctrader_service import CTraderService


def _manage(side, entry, current, r, cur_sl=None):
    row = SimpleNamespace(
        entry_price=entry,
        stop_loss=cur_sl,
        broker_metadata={"r_distance": r},
    )
    pos = {
        "symbol": "EURUSD",
        "side": side,
        "position_id": "pos-1",
        "current_price": current,
        "stop_loss": cur_sl,
        "entry_price": entry,
    }
    db = MagicMock()
    amend = MagicMock(return_value={"status": "sent"})
    with patch.object(em, "_find_trade_row", return_value=row), \
         patch.object(em, "_original_r_distance", return_value=r), \
         patch.object(em.ctrader_service, "amend_position_sltp", amend):
        res = em._manage_position(db, pos)
    return res, amend


def test_buy_break_even_locks_at_or_through_entry(monkeypatch):
    monkeypatch.setattr(em, "BE_TRIGGER_R", 1.0)
    monkeypatch.setattr(em, "BE_LOCK_R", 0.1)
    monkeypatch.setattr(em, "TRAIL_START_R", 1.5)
    entry = 1.10000
    min_dist = CTraderService.min_protective_distance("EURUSD", entry)
    r = min_dist
    # 1.05R: BE only. Room for mark-legality to sit at entry (not below it).
    current = entry + r * 1.05
    res, amend = _manage("BUY", entry, current, r, cur_sl=entry - r)
    assert res is not None
    new_sl = amend.call_args.kwargs["stop_loss"]
    assert new_sl >= entry - 1e-9


def test_sell_break_even_locks_at_or_through_entry(monkeypatch):
    monkeypatch.setattr(em, "BE_TRIGGER_R", 1.0)
    monkeypatch.setattr(em, "BE_LOCK_R", 0.1)
    monkeypatch.setattr(em, "TRAIL_START_R", 1.5)
    entry = 1.10000
    min_dist = CTraderService.min_protective_distance("EURUSD", entry)
    r = min_dist
    current = entry - r * 1.05
    res, amend = _manage("SELL", entry, current, r, cur_sl=entry + r)
    assert res is not None
    new_sl = amend.call_args.kwargs["stop_loss"]
    assert new_sl <= entry + 1e-9


def test_buy_trail_may_lock_through_entry(monkeypatch):
    monkeypatch.setattr(em, "BE_TRIGGER_R", 1.0)
    monkeypatch.setattr(em, "BE_LOCK_R", 0.1)
    monkeypatch.setattr(em, "TRAIL_START_R", 1.5)
    monkeypatch.setattr(em, "TRAIL_DIST_R", 0.75)
    entry = 1.10000
    min_dist = CTraderService.min_protective_distance("EURUSD", entry)
    r = min_dist
    current = entry + 2.0 * r
    res, amend = _manage("BUY", entry, current, r, cur_sl=entry - r)
    assert res is not None
    new_sl = amend.call_args.kwargs["stop_loss"]
    assert new_sl > entry
