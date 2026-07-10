"""Live close routing: reduce-only orders must target the existing hedge leg."""
import os
from unittest.mock import MagicMock, patch

from backend.services.binance_futures_service import BinanceFuturesService
from backend.services.unified_trading import (
    OrderSide,
    OrderType,
    UnifiedOrder,
    UnifiedTrading,
)


def _fresh_router(broker):
    UnifiedTrading._instance = None
    router = UnifiedTrading()
    router.register_broker("binance_futures", broker)
    router.init_session("binance_futures", mode="live", session_id="test-live")
    return router


def test_unified_sell_close_passes_original_long_direction_and_close_action():
    broker = MagicMock()
    broker.place_order.return_value = {
        "status": "sent",
        "order_id": "1",
        "quantity": 0.1,
        "filled_price": 100.0,
    }
    router = _fresh_router(broker)

    with patch.dict(os.environ, {
        "TRADING_MODE": "live",
        "PAPER_TRADING": "false",
        "DRY_RUN_ALL": "false",
    }):
        response = router.place_order(UnifiedOrder(
            symbol="ETHUSDT",
            side=OrderSide.SELL,  # actual close order side
            order_type=OrderType.MARKET,
            quantity=0.1,
            reduce_only=True,
        ))

    assert response.success is True
    kwargs = broker.place_order.call_args.kwargs
    assert kwargs["direction"] == "BUY"  # original position direction
    assert kwargs["action"] == "close"
    assert kwargs["reduce_only"] is True


def test_unified_buy_close_passes_original_short_direction():
    broker = MagicMock()
    broker.place_order.return_value = {"status": "sent", "order_id": "2"}
    router = _fresh_router(broker)

    with patch.dict(os.environ, {
        "TRADING_MODE": "live",
        "PAPER_TRADING": "false",
        "DRY_RUN_ALL": "false",
    }):
        router.place_order(UnifiedOrder(
            symbol="SOLUSDT",
            side=OrderSide.BUY,
            quantity=1.0,
            reduce_only=True,
        ))

    kwargs = broker.place_order.call_args.kwargs
    assert kwargs["direction"] == "SELL"
    assert kwargs["action"] == "close"


def test_already_flat_is_idempotent_success():
    broker = MagicMock()
    broker.place_order.return_value = {
        "status": "already_flat",
        "already_closed": True,
        "message": "position already closed on exchange",
    }
    router = _fresh_router(broker)

    with patch.dict(os.environ, {
        "TRADING_MODE": "live",
        "PAPER_TRADING": "false",
        "DRY_RUN_ALL": "false",
    }):
        response = router.place_order(UnifiedOrder(
            symbol="ETHUSDT",
            side=OrderSide.SELL,
            quantity=0.1,
            reduce_only=True,
        ))

    assert response.success is True


def _broker_service():
    svc = BinanceFuturesService.__new__(BinanceFuturesService)
    svc.leverage = 10
    svc.margin_type = "CROSSED"
    svc.dry_run = False
    svc._leverage_set = set()
    svc._lot_step = {"ETHUSDT": 0.001}
    svc._lot_min = {"ETHUSDT": 0.001}
    svc._qty_precision = {"ETHUSDT": 3}
    return svc


def test_reduce_only_long_close_sends_sell_on_long_and_ignores_zero_margin():
    svc = _broker_service()
    client = MagicMock()
    client.futures_account.return_value = {"availableBalance": "0"}
    client.futures_create_order.return_value = {
        "orderId": 123,
        "avgPrice": "100",
    }

    with patch.object(svc, "_get_client", return_value=client), \
         patch.object(svc, "_setup_symbol"), \
         patch.object(svc, "_round_price", side_effect=lambda s, p: p):
        result = svc.place_order(
            symbol="ETHUSDT",
            direction="BUY",  # original LONG position
            action="close",
            quantity=0.1,
            price=100.0,
            reduce_only=True,
        )

    assert result["status"] == "sent"
    params = client.futures_create_order.call_args.kwargs
    assert params["side"] == "SELL"
    assert params["positionSide"] == "LONG"


def test_partial_close_keeps_exchange_protective_orders():
    svc = _broker_service()
    client = MagicMock()
    client.futures_account.return_value = {"availableBalance": "0"}
    client.futures_create_order.return_value = {
        "orderId": 123,
        "avgPrice": "100",
    }

    with patch.object(svc, "_get_client", return_value=client), \
         patch.object(svc, "_setup_symbol"), \
         patch.object(svc, "_round_price", side_effect=lambda s, p: p), \
         patch.object(svc, "_live_position_qty", return_value=0.5):
        result = svc.place_order(
            symbol="ETHUSDT",
            direction="BUY",
            action="close",
            quantity=0.1,
            price=100.0,
            reduce_only=True,
        )

    assert result["status"] == "sent"
    client.futures_cancel_all_open_orders.assert_not_called()
    client.futures_cancel_algo_order.assert_not_called()

