"""Protective-order safety: pyramid reuse, restore missing SL, full-qty emergency close."""
import os
from unittest.mock import MagicMock, patch

os.environ["MAKER_ENTRY_ENABLED"] = "false"

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


def test_pyramid_reuses_existing_close_position_sl():
    """Pyramid adds must not place duplicate closePosition SL (-4130) or emergency-close."""
    broker = _broker()
    client = MagicMock()
    client.futures_account.return_value = {"availableBalance": "1000"}

    with patch.dict(os.environ, {"MAKER_ENTRY_ENABLED": "false"}), \
         patch.object(broker, "get_positions", return_value=[
        {"symbol": "DOGEUSDT", "side": "SELL", "quantity": 298.0},
    ]), patch.object(broker, "_get_client", return_value=client), \
         patch.object(broker, "_setup_symbol"), \
         patch.object(broker, "_round_qty", side_effect=lambda s, q, **kw: q), \
         patch.object(broker, "_round_price", side_effect=lambda s, p: p), \
         patch.object(broker, "_has_exchange_stop", return_value=True), \
         patch.object(broker, "_has_exchange_take_profit", return_value=True), \
         patch.object(broker, "replace_stop_loss", return_value={"status": "skipped"}) as replace_mock, \
         patch.object(broker, "_safe_create_order", return_value={"orderId": 2, "avgPrice": "0.083"}), \
         patch.object(broker, "_native_trailing_enabled", return_value=False):
        result = broker.place_order(
            symbol="DOGEUSDT", direction="SELL", quantity=298,
            price=0.083, stop_loss=0.085, take_profit=0.081, is_pyramid=True,
        )

    assert result.get("status") == "sent"
    replace_mock.assert_called_once()
    client.futures_create_order.assert_not_called()


def test_pyramid_4130_with_live_stop_no_emergency_close():
    broker = _broker()
    client = MagicMock()
    client.futures_account.return_value = {"availableBalance": "1000"}

    def safe_side_effect(_client, params):
        if params.get("type") == "MARKET":
            return {"orderId": 1, "avgPrice": "0.083"}
        raise Exception("APIError(code=-4130): An open stop or take profit order with GTE and closePosition in the direction is existing.")

    with patch.dict(os.environ, {"MAKER_ENTRY_ENABLED": "false"}), \
         patch.object(broker, "get_positions", return_value=[
        {"symbol": "DOGEUSDT", "side": "SELL", "quantity": 298.0},
    ]), patch.object(broker, "_get_client", return_value=client), \
         patch.object(broker, "_setup_symbol"), \
         patch.object(broker, "_round_qty", side_effect=lambda s, q, **kw: q), \
         patch.object(broker, "_round_price", side_effect=lambda s, p: p), \
         patch.object(broker, "_has_exchange_stop", side_effect=[False, True]), \
         patch.object(broker, "_has_exchange_take_profit", return_value=True), \
         patch.object(broker, "_safe_create_order", side_effect=safe_side_effect), \
         patch.object(broker, "_native_trailing_enabled", return_value=False):
        result = broker.place_order(
            symbol="DOGEUSDT", direction="SELL", quantity=298,
            price=0.083, stop_loss=0.085,
        )

    assert result.get("status") == "sent"
    client.futures_create_order.assert_not_called()


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
         patch.object(broker, "_round_qty", side_effect=lambda s, q, **kw: q), \
         patch.object(broker, "_round_price", side_effect=lambda s, p: p), \
         patch.object(broker, "_live_position_qty", return_value=17.5), \
         patch.object(broker, "_has_exchange_stop", return_value=False), \
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
