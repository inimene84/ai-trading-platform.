"""Protective-order safety: place-new-first, restore missing SL, full-qty emergency close."""
from unittest.mock import MagicMock, patch

import pytest

from backend.services.binance_futures_service import BinanceFuturesService


def _broker():
    svc = BinanceFuturesService.__new__(BinanceFuturesService)
    svc.leverage = 10
    svc.margin_type = "CROSSED"
    svc.dry_run = False
    svc._leverage_set = set()
    svc._lot_step = {}
    svc._lot_min = {}
    svc._qty_precision = {}
    return svc


def test_pyramid_does_not_cancel_before_new_sl():
    """Pyramid adds must snapshot old orders and cancel only after new SL is placed."""
    broker = _broker()
    old_sl = {
        "order_id": "99", "symbol": "XRPUSDT", "side": "BUY",
        "type": "STOP_MARKET", "quantity": 0.0, "price": 1.18, "algo_id": None,
    }
    client = MagicMock()
    client.futures_symbol_ticker.return_value = {"price": "1.15"}
    client.futures_account.return_value = {"availableBalance": "1000"}

    with patch.object(broker, "get_positions", return_value=[
        {"symbol": "XRPUSDT", "side": "SELL", "quantity": 10.0},
    ]), patch.object(broker, "_get_client", return_value=client), \
         patch.object(broker, "_setup_symbol"), \
         patch.object(broker, "_round_qty", side_effect=lambda s, q: q), \
         patch.object(broker, "_round_price", side_effect=lambda s, p: p), \
         patch.object(broker, "_collect_protective_orders", return_value=[old_sl]), \
         patch.object(broker, "_safe_create_order", return_value={"orderId": 2, "avgPrice": "1.15"}), \
         patch.object(broker, "_native_trailing_enabled", return_value=False), \
         patch.object(broker, "_cancel_listed_orders", return_value=1) as cancel_mock:
        broker.place_order(
            symbol="XRPUSDT", direction="SELL", quantity=5,
            price=1.15, stop_loss=1.18, take_profit=1.08, is_pyramid=True,
        )

    client.futures_cancel_all_open_orders.assert_not_called()
    cancel_mock.assert_called_once()
    assert cancel_mock.call_args[0][2] == [old_sl]


def test_sl_fail_emergency_close_uses_full_position_qty():
    broker = _broker()
    client = MagicMock()
    client.futures_symbol_ticker.return_value = {"price": "1.15"}
    client.futures_account.return_value = {"availableBalance": "1000"}

    def safe_side_effect(_client, params):
        if params.get("type") == "MARKET":
            return {"orderId": 1, "avgPrice": "1.15"}
        raise Exception("SL rejected")

    with patch.object(broker, "get_positions", return_value=[
        {"symbol": "XRPUSDT", "side": "SELL", "quantity": 17.5},
    ]), patch.object(broker, "_get_client", return_value=client), \
         patch.object(broker, "_setup_symbol"), \
         patch.object(broker, "_round_qty", side_effect=lambda s, q: q), \
         patch.object(broker, "_round_price", side_effect=lambda s, p: p), \
         patch.object(broker, "_live_position_qty", return_value=17.5), \
         patch.object(broker, "_safe_create_order", side_effect=safe_side_effect), \
         patch.object(broker, "_native_trailing_enabled", return_value=False):
        result = broker.place_order(
            symbol="XRPUSDT", direction="SELL", quantity=5,
            price=1.15, stop_loss=1.18,
        )

    assert result.get("status") == "error"
    emg = client.futures_create_order.call_args.kwargs
    assert emg["quantity"] == 17.5


def test_ensure_protective_orders_restores_missing_sl():
    broker = _broker()
    client = MagicMock()
    client.futures_symbol_ticker.return_value = {"price": "1.15"}

    with patch.object(broker, "_to_futures_symbol", return_value="XRPUSDT"), \
         patch.object(broker, "_live_position_qty", return_value=10.0), \
         patch.object(broker, "_has_exchange_stop", return_value=False), \
         patch.object(broker, "_has_exchange_take_profit", return_value=True), \
         patch.object(broker, "_get_client", return_value=client), \
         patch.object(broker, "_round_price", side_effect=lambda s, p: p), \
         patch.object(broker, "_safe_create_order", return_value={"algoId": 555}) as safe_mock:
        res = broker.ensure_protective_orders("XRPUSDT", "SELL", stop_loss=1.1846)

    assert res["status"] == "restored"
    assert "SL@" in res["restored"][0]
    safe_mock.assert_called_once()


def test_ensure_protective_orders_skips_when_sl_present():
    broker = _broker()
    with patch.object(broker, "_to_futures_symbol", return_value="XRPUSDT"), \
         patch.object(broker, "_live_position_qty", return_value=10.0), \
         patch.object(broker, "_has_exchange_stop", return_value=True):
        res = broker.ensure_protective_orders("XRPUSDT", "SELL", stop_loss=1.18)
    assert res["status"] == "ok"
