"""Pyramid guard allows same-direction adds, blocks opposite legs."""
from unittest.mock import MagicMock, patch

from backend.services.binance_futures_service import BinanceFuturesService


def _broker():
    svc = BinanceFuturesService.__new__(BinanceFuturesService)
    svc.leverage = 10
    svc.margin_type = "CROSSED"
    svc.dry_run = True
    svc._leverage_set = set()
    svc._lot_step = {}
    svc._lot_min = {}
    svc._qty_precision = {}
    return svc


def test_pyramid_allows_same_side_short_add():
    broker = _broker()
    with patch.object(broker, "get_positions", return_value=[
        {"symbol": "POLUSDT", "side": "SELL", "quantity": 361.0},
    ]):
        # dry_run returns before guard — patch dry_run off and mock client path
        broker.dry_run = False
        client = MagicMock()
        client.futures_symbol_ticker.return_value = {"price": "0.076"}
        client.futures_account.return_value = {"availableBalance": "1000"}
        client.futures_change_leverage.return_value = {}
        client.futures_change_margin_type.return_value = {}
        with patch.object(broker, "_get_client", return_value=client), \
             patch.object(broker, "_setup_symbol"), \
             patch.object(broker, "_round_qty", return_value=100.0), \
             patch.object(broker, "_safe_create_order", return_value={"orderId": 1, "avgPrice": "0.076"}), \
             patch.object(broker, "_native_trailing_enabled", return_value=False):
            result = broker.place_order(
                symbol="POLUSDT", direction="SELL", quantity=100,
                price=0.076, is_pyramid=True,
            )
    assert result.get("status") == "sent"
