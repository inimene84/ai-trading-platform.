"""Metal vs FX contract-size tests. Isolated from the signal engine import graph."""

import pytest

from backend.services.ctrader_service import CTraderService


def test_units_per_lot_uses_ounces_for_metals_not_fx_units():
    assert CTraderService.units_per_lot("EURUSD") == 100_000
    assert CTraderService.units_per_lot("XAUUSD") == 100
    assert CTraderService.units_per_lot("XAGUSD") == 5_000


def test_silver_mark_to_market_is_not_fx_notional():
    svc = CTraderService()
    svc._positions = [
        {
            "symbol": "XAGUSD",
            "side": "BUY",
            "quantity": 0.01,
            "entry_price": 67.092,
            "unrealized_pnl": 0.0,
            "position_id": "1",
        }
    ]
    svc._last_spots = {"XAGUSD": {"bid": 66.905, "ask": 66.905}}
    pos = svc.get_positions()[0]
    assert pos["unrealized_pnl"] == pytest.approx(-9.35, abs=0.05)
    assert pos["unrealized_pnl"] > -20


def test_gold_spec_lot_size_is_100_ounces():
    spec = CTraderService().get_symbol_specification("XAUUSD")
    assert spec["lot_size"] == 100


def test_fx_spec_lot_size_unchanged():
    spec = CTraderService().get_symbol_specification("EURUSD")
    assert spec["lot_size"] == 100_000


def test_metal_pip_margin_does_not_floor_to_fx_min_units():
    svc = CTraderService()
    gold = svc.calculate_pip_margin("XAUUSD", 0.01, price=2650.0, leverage=100.0)
    silver = svc.calculate_pip_margin("XAGUSD", 0.01, price=67.0, leverage=100.0)
    fx = svc.calculate_pip_margin("EURUSD", 0.01, price=1.085, leverage=100.0)
    assert gold["volume_units"] == 1
    assert silver["volume_units"] == 50
    assert fx["volume_units"] == 1_000
    assert gold["notional_value"] == pytest.approx(2650.0, abs=0.01)
    assert silver["notional_value"] == pytest.approx(3350.0, abs=0.01)
