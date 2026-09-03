"""Forex entries must carry a stop the broker will actually accept.

Live evidence: six of seven open positions had a take-profit but no stop
loss. The engine sized stops from M5 ATR, producing 1.7-7.7 pip distances,
and IC Markets discards protection inside its stop level *silently* — the
order fills and the stop is simply absent.
"""

import sys
import types
from unittest.mock import patch

import pytest

from backend.services.ctrader_service import CTraderService


class _FakeNewOrderReq:
    """The Spotware SDK is only installed in the runtime image."""

    def __init__(self):
        self.ctidTraderAccountId = 0
        self.symbolId = 0
        self.orderType = 0
        self.tradeSide = 0
        self.volume = 0


@pytest.fixture
def fake_ctrader_sdk(monkeypatch):
    messages = types.ModuleType("ctrader_open_api.messages.OpenApiMessages_pb2")
    messages.ProtoOANewOrderReq = _FakeNewOrderReq
    package = types.ModuleType("ctrader_open_api.messages")
    package.OpenApiMessages_pb2 = messages
    root = types.ModuleType("ctrader_open_api")
    root.messages = package
    for name, mod in (
        ("ctrader_open_api", root),
        ("ctrader_open_api.messages", package),
        ("ctrader_open_api.messages.OpenApiMessages_pb2", messages),
    ):
        monkeypatch.setitem(sys.modules, name, mod)

    internet = types.ModuleType("twisted.internet")
    internet.reactor = types.SimpleNamespace(callFromThread=lambda fn: fn())
    twisted = types.ModuleType("twisted")
    twisted.internet = internet
    monkeypatch.setitem(sys.modules, "twisted", twisted)
    monkeypatch.setitem(sys.modules, "twisted.internet", internet)
    return messages


PIP = 0.0001


def _pips(symbol, entry, level):
    pip = float(CTraderService.pip_size_for(symbol))
    return abs(level - entry) / pip


def test_tight_stops_are_widened_to_the_broker_minimum():
    """The exact distances seen live, each one silently dropped by the broker."""
    observed = [
        ("EURUSD", "BUY", 1.16195, 1.16165),   # 3.0 pips
        ("GBPUSD", "BUY", 1.35548, 1.35471),   # 7.7 pips
        ("AUDUSD", "SELL", 0.71650, 0.71667),  # 1.7 pips
        ("NZDUSD", "SELL", 0.59147, 0.59124),  # 2.3 pips
        ("USDCAD", "SELL", 1.38535, 1.38571),  # 3.6 pips
    ]
    for symbol, direction, entry, sl in observed:
        assert _pips(symbol, entry, sl) < CTraderService.MIN_FX_STOP_PIPS
        clamped_sl, _ = CTraderService.clamp_protective_prices(
            symbol, entry, sl, None, direction=direction
        )
        assert _pips(symbol, entry, clamped_sl) == pytest.approx(
            CTraderService.MIN_FX_STOP_PIPS, rel=1e-6
        )


def test_jpy_minimum_uses_jpy_pip_size():
    entry, sl = 159.720, 159.740  # 2.0 pips, dropped live
    clamped_sl, _ = CTraderService.clamp_protective_prices(
        "USDJPY", entry, sl, None, direction="SELL"
    )
    assert _pips("USDJPY", entry, clamped_sl) == pytest.approx(
        CTraderService.MIN_JPY_STOP_PIPS, rel=1e-6
    )
    assert clamped_sl > entry, "a short's stop belongs above entry"


def test_jpy_cross_minimum_is_wider_than_major_fx():
    """Live NZDJPY/CHFJPY: 10 JPY pips (0.10) were sent and the broker dropped them."""
    sl, _ = CTraderService.clamp_protective_prices(
        "NZDJPY", 91.640, 91.681, None, direction="SELL"
    )
    assert sl - 91.640 == pytest.approx(0.30, abs=1e-6)
    sl, _ = CTraderService.clamp_protective_prices(
        "CHFJPY", 192.863, 192.960, None, direction="SELL"
    )
    assert sl - 192.863 == pytest.approx(0.30, abs=1e-6)


def test_missing_stop_price_sits_beyond_mark_for_losing_short():
    sl = CTraderService.missing_stop_price("CHFJPY", "SELL", 192.863, 193.400)
    assert sl > 193.400
    assert sl - 193.400 == pytest.approx(0.30, abs=1e-6)


def test_protection_is_forced_onto_the_correct_side():
    """A BUY whose take-profit sat below entry was silently flipped by abs()."""
    entry = 1.35548
    sl, tp = CTraderService.clamp_protective_prices(
        "GBPUSD", entry, 1.35471, 1.35529, direction="BUY"
    )
    assert sl < entry, "long stop must sit below entry"
    assert tp > entry, "long target must sit above entry"

    sl, tp = CTraderService.clamp_protective_prices(
        "USDCAD", 1.38535, 1.38571, 1.38483, direction="SELL"
    )
    assert sl > 1.38535
    assert tp < 1.38535


def test_wide_levels_are_still_capped():
    """The original NZDJPY 142.020 regression must keep working."""
    sl, tp = CTraderService.clamp_protective_prices("NZDJPY", 94.540, 70.800, 142.020)
    max_dist = CTraderService.max_protective_distance("NZDJPY", 94.540)
    assert abs(tp - 94.540) <= max_dist + 1e-9
    assert abs(sl - 94.540) <= max_dist + 1e-9


def test_generous_stops_are_left_alone():
    entry, sl, tp = 1.16195, 1.16195 - 30 * PIP, 1.16195 + 60 * PIP
    out_sl, out_tp = CTraderService.clamp_protective_prices(
        "EURUSD", entry, sl, tp, direction="BUY"
    )
    assert out_sl == pytest.approx(sl, abs=1e-9)
    assert out_tp == pytest.approx(tp, abs=1e-9)


def test_overlay_reports_broker_held_protection():
    """The dashboard must show attached protection, not the requested level."""
    from backend.services.ctrader_trade_sync import overlay_live_mark

    payload = {
        "broker": "ctrader",
        "symbol": "EURUSD",
        "broker_position_id": "1",
        "entry_price": 1.16172,
        "quantity": 0.1,
        "stop_loss": 1.16164,   # what the engine asked for
        "take_profit": None,
    }
    out = overlay_live_mark(
        payload,
        {"1": {"entry_price": 1.16172, "quantity": 0.1, "unrealized_pnl": 0.0,
               "stop_loss": 1.16072, "take_profit": 1.16372}},
        {},
    )
    assert out["stop_loss"] == pytest.approx(1.16072), "broker value must win"
    assert out["take_profit"] == pytest.approx(1.16372)


def test_live_entry_is_refused_when_stop_cannot_be_encoded(fake_ctrader_sdk):
    """Opening without the requested stop is what left positions unprotected."""
    svc = CTraderService()
    svc._dry_run = False
    svc._connected = True
    svc._authenticated = True
    svc._protocol = object()
    svc._positions = []

    # A stop that collapses to zero distance must abort the entry, not fill naked.
    with patch.object(svc, "relative_stop_units", return_value=0), patch.object(
        CTraderService, "clamp_protective_prices",
        classmethod(lambda cls, s, e, sl, tp, direction=None: (sl, tp)),
    ):
        res = svc.place_order(
            symbol="EURUSD", direction="BUY", quantity=0.1,
            price=1.16195, stop_loss=1.161949, take_profit=1.16250,
        )

    assert res["status"] == "error"
    assert "stop loss" in res["error"]
