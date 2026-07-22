"""
Unit tests for Binance WebSocket Candle Cache (WP1)
"""

import pytest
from backend.services.binance_websocket import BinanceWebSocketService
from backend.services.data_hub import DataHub


def test_ws_candle_ring_buffer():
    ws = BinanceWebSocketService()
    symbol = "btcusdt"

    # Initially empty
    assert ws.get_candle_history(symbol) == []
    assert ws.candle_count(symbol) == 0

    # Simulate kline WS closed messages
    kline_event_1 = {
        "e": "kline",
        "k": {
            "s": "BTCUSDT",
            "t": 1700000000000,
            "o": "60000.0",
            "h": "60500.0",
            "l": "59900.0",
            "c": "60400.0",
            "v": "150.0",
            "x": True,
        }
    }
    ws._handle_kline(kline_event_1)

    assert ws.candle_count(symbol) == 1
    candles = ws.get_candle_history(symbol)
    assert len(candles) == 1
    assert candles[0]["open"] == 60000.0
    assert candles[0]["close"] == 60400.0

    # Test DataHub cache topic publication
    dh = DataHub()
    cached = dh.peek("ohlcv:BTCUSDT:1h")
    assert cached is not None
    assert cached["close"] == 60400.0


def test_ws_candle_deduplication():
    ws = BinanceWebSocketService()
    symbol = "ethusdt"

    kline_event = {
        "e": "kline",
        "k": {
            "s": "ETHUSDT",
            "t": 1700003600000,
            "o": "3000.0",
            "h": "3050.0",
            "l": "2990.0",
            "c": "3020.0",
            "v": "500.0",
            "x": True,
        }
    }

    # Send duplicate closed event with same timestamp
    ws._handle_kline(kline_event)
    ws._handle_kline(kline_event)

    # Should only store 1 candle
    assert ws.candle_count(symbol) == 1
