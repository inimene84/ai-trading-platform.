"""Expanded safety coverage for BinanceFuturesService.

Covers protective-order classification (so SL/TP are never cancelled),
-4130 error classification, symbol conversion, price rounding, protective
order collection by position side, live position qty extraction, and exit
price fallback (recent fill -> stale -> mark price).
"""
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.services.binance_futures_service import BinanceFuturesService


def _broker():
    """Build a BinanceFuturesService without running __init__ (no network)."""
    svc = BinanceFuturesService.__new__(BinanceFuturesService)
    svc.dry_run = False
    svc._lot_step = {}
    svc._lot_min = {}
    svc._qty_precision = {}
    return svc


# --------------------------------------------------------------------------- #
# _is_protective_order_type — SL/TP/trailing must never be cancelled
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("order_type", [
    "STOP_MARKET", "TAKE_PROFIT_MARKET", "TRAILING_STOP_MARKET", "STOP", "TAKE_PROFIT",
])
def test_is_protective_order_type_true(order_type):
    assert _broker()._is_protective_order_type(order_type) is True


@pytest.mark.parametrize("order_type", ["LIMIT", "MARKET", "", None])
def test_is_protective_order_type_false(order_type):
    assert _broker()._is_protective_order_type(order_type) is False


# --------------------------------------------------------------------------- #
# _is_existing_close_position_error — only -4130 is a closePosition conflict
# --------------------------------------------------------------------------- #
def test_is_existing_close_position_error_4130():
    assert BinanceFuturesService._is_existing_close_position_error(
        Exception("Client error: code=-4130 closePosition already exists")
    ) is True


def test_is_existing_close_position_error_other():
    assert BinanceFuturesService._is_existing_close_position_error(
        Exception("code=-1111 Precision over limit")
    ) is False
    assert BinanceFuturesService._is_existing_close_position_error(Exception("")) is False


# --------------------------------------------------------------------------- #
# _to_futures_symbol — internal symbol -> Binance Futures format
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("symbol,expected", [
    ("ETH-USD", "ETHUSDT"),
    ("BTC/USDT", "BTCUSDT"),
    ("BTC/USDC", "BTCUSDC"),       # explicit USDC preserved
    ("BTC-USD", "BTCUSDT"),        # legacy yfinance form -> USDT
    ("FOO", "FOOUSDT"),           # unknown -> appends USDT
    ("EURUSD=X", None),           # unsupported -> None
    ("EURUSD", None),             # bare FX must not become EURUSDUSDT
    ("XAUUSD", None),             # metals are cTrader, not Binance
])
def test_to_futures_symbol(symbol, expected):
    assert _broker()._to_futures_symbol(symbol) == expected


# --------------------------------------------------------------------------- #
# _round_price — tick-size rounding (PRICE_PRECISION table is empty in tests)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("symbol,price,expected", [
    ("BTCUSDT", 43215.67, 43215.7),    # tick 0.1
    ("ETHUSDT", 1789.234, 1789.23),    # tick 0.01
    ("XRPUSDT", 0.51234, 0.5123),      # tick 0.0001
    ("ABCXYZ", 1.23456, 1.235),        # default tick 0.001
])
def test_round_price(symbol, price, expected):
    with patch.dict("backend.services.binance_futures_service.PRICE_PRECISION", {}, clear=True):
        assert _broker()._round_price(symbol, price) == expected


# --------------------------------------------------------------------------- #
# _collect_protective_orders — filters by symbol + protective type + side
# --------------------------------------------------------------------------- #
def test_collect_protective_orders_long_filters_sell_side():
    """LONG positions are protected by SELL-side SL/TP. A BUY-side LIMIT entry
    must NOT be collected as protective (so it can be cancelled freely)."""
    broker = _broker()
    open_orders = [
        {"symbol": "BTCUSDT", "type": "STOP_MARKET", "side": "SELL", "order_id": "1"},
        {"symbol": "BTCUSDT", "type": "TAKE_PROFIT_MARKET", "side": "SELL", "order_id": "2"},
        {"symbol": "BTCUSDT", "type": "LIMIT", "side": "BUY", "order_id": "3"},  # entry, not protective
        {"symbol": "ETHUSDT", "type": "STOP_MARKET", "side": "SELL", "order_id": "4"},  # other symbol
    ]
    with patch.object(broker, "get_open_orders", return_value=open_orders):
        result = broker._collect_protective_orders("BTCUSDT", "LONG")

    ids = [o["order_id"] for o in result]
    assert ids == ["1", "2"]


def test_collect_protective_orders_short_filters_buy_side():
    """SHORT positions are protected by BUY-side SL/TP."""
    broker = _broker()
    open_orders = [
        {"symbol": "BTCUSDT", "type": "STOP_MARKET", "side": "BUY", "order_id": "1"},
        {"symbol": "BTCUSDT", "type": "STOP_MARKET", "side": "SELL", "order_id": "2"},  # wrong side for short
    ]
    with patch.object(broker, "get_open_orders", return_value=open_orders):
        result = broker._collect_protective_orders("BTCUSDT", "SHORT")

    ids = [o["order_id"] for o in result]
    assert ids == ["1"]


# --------------------------------------------------------------------------- #
# _has_exchange_stop / _has_exchange_take_profit
# --------------------------------------------------------------------------- #
def test_has_exchange_stop_true_when_stop_present():
    broker = _broker()
    with patch.object(broker, "_collect_protective_orders", return_value=[
        {"type": "STOP_MARKET", "side": "SELL"},
        {"type": "TAKE_PROFIT_MARKET", "side": "SELL"},
    ]):
        assert broker._has_exchange_stop("BTCUSDT", "LONG") is True
        assert broker._has_exchange_take_profit("BTCUSDT", "LONG") is True


def test_has_exchange_stop_false_when_only_tp_present():
    broker = _broker()
    with patch.object(broker, "_collect_protective_orders", return_value=[
        {"type": "TAKE_PROFIT_MARKET", "side": "SELL"},
    ]):
        assert broker._has_exchange_stop("BTCUSDT", "LONG") is False
        assert broker._has_exchange_take_profit("BTCUSDT", "LONG") is True


def test_has_exchange_stop_false_when_no_protection():
    broker = _broker()
    with patch.object(broker, "_collect_protective_orders", return_value=[]):
        assert broker._has_exchange_stop("BTCUSDT", "LONG") is False
        assert broker._has_exchange_take_profit("BTCUSDT", "LONG") is False


# --------------------------------------------------------------------------- #
# _live_position_qty — exchange position size by side
# --------------------------------------------------------------------------- #
def test_live_position_qty_long_returns_matching_qty():
    broker = _broker()
    with patch.object(broker, "get_positions", return_value=[
        {"symbol": "BTCUSDT", "side": "LONG", "quantity": 0.5},
        {"symbol": "ETHUSDT", "side": "SHORT", "quantity": 2.0},
    ]):
        assert broker._live_position_qty("BTCUSDT", "LONG") == pytest.approx(0.5)


def test_live_position_qty_short_matches_sell_side():
    broker = _broker()
    with patch.object(broker, "get_positions", return_value=[
        {"symbol": "BTCUSDT", "side": "SELL", "quantity": 0.5},
    ]):
        assert broker._live_position_qty("BTCUSDT", "SHORT") == pytest.approx(0.5)


def test_live_position_qty_zero_when_no_match():
    broker = _broker()
    with patch.object(broker, "get_positions", return_value=[]):
        assert broker._live_position_qty("BTCUSDT", "LONG") == 0.0


# --------------------------------------------------------------------------- #
# get_exit_price — recent fill preferred, stale -> mark price, else None
# --------------------------------------------------------------------------- #
def test_get_exit_price_prefers_recent_fill():
    broker = _broker()
    client = MagicMock()
    now_ms = int(time.time() * 1000)
    client.futures_account_trades.return_value = [
        {"price": "94250.5", "time": now_ms - 3_600_000},  # 1h ago, recent
    ]

    with patch.object(broker, "_to_futures_symbol", return_value="BTCUSDT"), \
         patch.object(broker, "_get_client", return_value=client):
        assert broker.get_exit_price("BTCUSDT") == pytest.approx(94250.5)

    client.futures_mark_price.assert_not_called()  # fill used, no mark fallback


def test_get_exit_price_falls_back_to_mark_when_fill_stale():
    broker = _broker()
    client = MagicMock()
    now_ms = int(time.time() * 1000)
    # 48h-old fill -> stale, must fall through to the live mark price.
    client.futures_account_trades.return_value = [
        {"price": "90000.0", "time": now_ms - 48 * 3_600_000},
    ]
    client.futures_mark_price.return_value = {"markPrice": "94310.2"}

    with patch.object(broker, "_to_futures_symbol", return_value="BTCUSDT"), \
         patch.object(broker, "_get_client", return_value=client):
        assert broker.get_exit_price("BTCUSDT") == pytest.approx(94310.2)


def test_get_exit_price_none_when_both_fail():
    broker = _broker()
    client = MagicMock()
    client.futures_account_trades.side_effect = RuntimeError("api error")
    client.futures_mark_price.side_effect = RuntimeError("api error")

    with patch.object(broker, "_to_futures_symbol", return_value="BTCUSDT"), \
         patch.object(broker, "_get_client", return_value=client):
        assert broker.get_exit_price("BTCUSDT") is None


def test_get_exit_price_none_for_unsupported_symbol():
    broker = _broker()
    with patch.object(broker, "_to_futures_symbol", return_value=None):
        assert broker.get_exit_price("EURUSD=X") is None
