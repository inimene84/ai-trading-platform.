"""Tests for Binance open-order merge and comprehensive cancellation."""
from unittest.mock import MagicMock, patch

import pytest

from backend.services.binance_futures_service import BinanceFuturesService


@pytest.fixture
def broker():
    svc = BinanceFuturesService.__new__(BinanceFuturesService)
    svc.leverage = 5
    svc.margin_type = "ISOLATED"
    svc.symbols = ["BTCUSDT"]
    return svc


def test_get_open_orders_merges_regular_and_algo(broker):
    client = MagicMock()
    client.futures_get_open_orders.return_value = [{
        "orderId": 1, "symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT",
        "origQty": "0.001", "price": "50000", "status": "NEW",
    }]
    client.futures_get_open_algo_orders.return_value = [{
        "algoId": 99, "symbol": "BTCUSDT", "side": "SELL", "orderType": "STOP_MARKET",
        "quantity": "0.001", "triggerPrice": "48000", "algoStatus": "NEW",
    }]

    with patch.object(broker, "_get_client", return_value=client):
        orders = broker.get_open_orders()

    assert len(orders) == 2
    regular = next(o for o in orders if o["order_id"] == "1")
    algo = next(o for o in orders if o["algo_id"] == 99)
    assert regular["type"] == "LIMIT"
    assert algo["type"] == "STOP_MARKET"


def test_cancel_non_protective_orders_preserves_sl_tp(broker):
    client = MagicMock()
    client.futures_get_open_orders.return_value = [{
        "orderId": 1, "symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT",
        "origQty": "0.001", "price": "50000", "status": "NEW",
    }]
    client.futures_get_open_algo_orders.return_value = [{
        "algoId": 99, "symbol": "BTCUSDT", "side": "SELL", "orderType": "STOP_MARKET",
        "quantity": "0.001", "triggerPrice": "48000", "algoStatus": "NEW",
    }]

    with patch.object(broker, "_get_client", return_value=client), \
         patch.object(broker, "_to_futures_symbol", return_value="BTCUSDT"), \
         patch.object(broker, "get_open_orders", return_value=[
        {"order_id": "1", "symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT",
         "quantity": 0.001, "price": 50000, "algo_id": None},
        {"order_id": "99", "symbol": "BTCUSDT", "side": "SELL", "type": "STOP_MARKET",
         "quantity": 0.001, "price": 48000, "algo_id": 99},
    ]):
        cancelled = broker.cancel_non_protective_orders("BTCUSDT")

    assert cancelled == 1
    client.futures_cancel_order.assert_called_once_with(symbol="BTCUSDT", orderId=1)
    client.futures_cancel_algo_order.assert_not_called()


def test_cancel_all_orders_cancels_regular_and_algo(broker):
    client = MagicMock()
    client.futures_get_open_algo_orders.return_value = [
        {"algoId": 10, "symbol": "BTCUSDT"},
        {"algoId": 11, "symbol": "ETHUSDT"},  # different symbol — skip
    ]

    with patch.object(broker, "_get_client", return_value=client), \
         patch.object(broker, "_to_futures_symbol", return_value="BTCUSDT"):
        broker.cancel_all_orders("BTCUSDT")

    client.futures_cancel_all_open_orders.assert_called_once_with(symbol="BTCUSDT")
    client.futures_cancel_algo_order.assert_called_once_with(algoId=10)


def test_cancel_all_orders_handles_algo_list_wrapper(broker):
    """Some Binance SDK versions wrap algo orders in {'orders': [...]}."""
    client = MagicMock()
    client.futures_get_open_algo_orders.return_value = {
        "orders": [{"algoId": 42, "symbol": "BTCUSDT"}],
    }

    with patch.object(broker, "_get_client", return_value=client), \
         patch.object(broker, "_to_futures_symbol", return_value="BTCUSDT"):
        broker.cancel_all_orders("BTCUSDT")

    client.futures_cancel_algo_order.assert_called_once_with(algoId=42)
