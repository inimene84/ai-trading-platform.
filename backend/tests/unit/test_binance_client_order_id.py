"""Entry clientOrderId is a per-minute intent key, not a per-second rotator."""
from backend.services.binance_futures_service import entry_client_order_id


def test_same_intent_same_minute_is_stable():
    minute_start = (1_700_000_000 // 60) * 60
    a = entry_client_order_id("BTCUSDT", "BUY", False, 0.01, now=minute_start + 5)
    b = entry_client_order_id("BTCUSDT", "BUY", False, 0.01, now=minute_start + 55)
    assert a == b
    assert a.startswith("xBTCUSDB")
    assert len(a) <= 36


def test_minute_bucket_rotates():
    minute_start = (1_700_000_000 // 60) * 60
    a = entry_client_order_id("BTCUSDT", "BUY", False, 0.01, now=minute_start + 59)
    b = entry_client_order_id("BTCUSDT", "BUY", False, 0.01, now=minute_start + 60)
    assert a != b


def test_pyramid_and_qty_stay_distinct_in_same_minute():
    minute_start = (1_700_000_000 // 60) * 60
    entry = entry_client_order_id("BTCUSDT", "BUY", False, 0.01, now=minute_start + 10)
    pyramid = entry_client_order_id("BTCUSDT", "BUY", True, 0.01, now=minute_start + 10)
    other_qty = entry_client_order_id("BTCUSDT", "BUY", True, 0.02, now=minute_start + 10)
    assert entry != pyramid
    assert pyramid != other_qty
    assert "e" in entry
    assert "p" in pyramid
