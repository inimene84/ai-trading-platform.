"""Metal vs FX contract-size tests. Isolated from the signal engine import graph."""

import pytest

from backend.services.ctrader_service import CTraderService


def test_units_per_lot_uses_ounces_for_metals_not_fx_units():
    assert CTraderService.units_per_lot("EURUSD") == 100_000
    assert CTraderService.units_per_lot("XAUUSD") == 100
    assert CTraderService.units_per_lot("XAGUSD") == 1_000


def test_ic_markets_silver_volume_is_not_fx_cents_per_lot():
    """0.01 XAGUSD must not be encoded as 100_000 FX cents (that is 1.00 silver lot)."""
    assert CTraderService.lots_to_protocol_volume(0.01, "XAGUSD") == 1_000
    assert CTraderService.protocol_volume_to_lots(100_000, "XAGUSD") == pytest.approx(1.0)
    assert CTraderService.protocol_volume_to_lots(1_000, "XAGUSD") == pytest.approx(0.01)
    assert CTraderService.lots_to_protocol_volume(0.01, "EURUSD") == 100_000


def test_silver_mark_to_market_matches_ic_markets_1000oz_lot():
    """Live desk: 1.00 XAGUSD * 12c ≈ -$120, not COMEX 50 oz ≈ -$6."""
    svc = CTraderService()
    svc._positions = [
        {
            "symbol": "XAGUSD",
            "side": "BUY",
            "quantity": 0.01,
            "volume_cents": 100_000,
            "entry_price": 67.092,
            "unrealized_pnl": 0.0,
            "position_id": "1",
        }
    ]
    svc._last_spots = {"XAGUSD": {"bid": 66.972, "ask": 66.972}}
    pos = svc.get_positions()[0]
    assert pos["quantity"] == pytest.approx(1.0)
    assert pos["unrealized_pnl"] == pytest.approx(-120.0, abs=0.05)


def test_intended_micro_silver_uses_10_ounces():
    svc = CTraderService()
    svc._positions = [
        {
            "symbol": "XAGUSD",
            "side": "BUY",
            "quantity": 0.01,
            "volume_cents": 1_000,
            "entry_price": 67.092,
            "unrealized_pnl": 0.0,
            "position_id": "2",
        }
    ]
    svc._last_spots = {"XAGUSD": {"bid": 66.972, "ask": 66.972}}
    pos = svc.get_positions()[0]
    assert pos["quantity"] == pytest.approx(0.01)
    assert pos["unrealized_pnl"] == pytest.approx(-1.20, abs=0.05)


def test_gold_spec_lot_size_is_100_ounces():
    spec = CTraderService().get_symbol_specification("XAUUSD")
    assert spec["lot_size"] == 100
    assert spec["min_volume"] == 100


def test_fx_spec_lot_size_unchanged():
    spec = CTraderService().get_symbol_specification("EURUSD")
    assert spec["lot_size"] == 100_000


def test_metal_pip_margin_does_not_floor_to_fx_min_units():
    svc = CTraderService()
    gold = svc.calculate_pip_margin("XAUUSD", 0.01, price=2650.0, leverage=100.0)
    silver = svc.calculate_pip_margin("XAGUSD", 0.01, price=67.0, leverage=100.0)
    fx = svc.calculate_pip_margin("EURUSD", 0.01, price=1.085, leverage=100.0)
    assert gold["volume_units"] == 1
    assert silver["volume_units"] == 10
    assert fx["volume_units"] == 1_000
    assert gold["notional_value"] == pytest.approx(2650.0, abs=0.01)
    assert silver["notional_value"] == pytest.approx(670.0, abs=0.01)
