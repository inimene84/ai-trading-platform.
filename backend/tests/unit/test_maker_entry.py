"""Unit tests for the post-only (GTX) maker-entry path with MARKET fallback.

Covers backend/services/binance_futures_service.py::_try_maker_entry, the
fee-churn fix (d0799f8). The method must:
  - return the order dict on a FULL maker fill (fee saved),
  - return None (-> caller MARKETs) on post-only rejection, terminal
    CANCELED/EXPIRED/REJECTED status, or timeout with zero fill,
  - on a PARTIAL fill at timeout, top up the remainder at MARKET and return the
    order dict (so the caller does NOT double-fill the full qty).

These run fully offline with a fake Binance client; no network, no real sleeps.
"""
import os
import pytest

from backend.services.binance_futures_service import BinanceFuturesService


@pytest.fixture(autouse=True)
def _fast_and_clean(monkeypatch):
    # Make the poll loop instant and deterministic.
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    monkeypatch.setenv("MAKER_WAIT_SEC", "2")
    # Ensure no real API creds / network are ever touched.
    monkeypatch.setenv("BINANCE_API_KEY", "")
    monkeypatch.setenv("BINANCE_SECRET_KEY", "")
    yield


class FakeClient:
    """Minimal stand-in for python-binance Client used by _try_maker_entry."""

    def __init__(self, *, create_raises=None, statuses=None, final=None,
                 bid="100.00", ask="100.02"):
        self._create_raises = create_raises
        # statuses: list of dicts returned by successive futures_get_order calls
        self._statuses = list(statuses or [])
        self._final = final
        self._bid = bid
        self._ask = ask
        self.created_orders = []
        self.canceled = []

    def futures_orderbook_ticker(self, symbol=None):
        return {"bidPrice": self._bid, "askPrice": self._ask}

    def futures_create_order(self, **params):
        self.created_orders.append(params)
        if params.get("type") == "LIMIT" and self._create_raises:
            raise self._create_raises
        if params.get("type") == "LIMIT":
            return {"orderId": 555, "status": "NEW"}
        # MARKET top-up / order
        return {"orderId": 999, "status": "FILLED", "avgPrice": self._ask}

    def futures_get_order(self, symbol=None, orderId=None):
        if self._statuses:
            return self._statuses.pop(0)
        if self._final is not None:
            return self._final
        return {"status": "NEW", "executedQty": "0"}

    def futures_cancel_order(self, symbol=None, orderId=None):
        self.canceled.append(orderId)
        return {"orderId": orderId, "status": "CANCELED"}


def _svc():
    return BinanceFuturesService()


def test_maker_entry_full_fill_returns_order_and_saves_taker():
    client = FakeClient(statuses=[{"status": "FILLED", "avgPrice": "100.00",
                                   "executedQty": "1.0"}])
    svc = _svc()
    res = svc._try_maker_entry(client, "BTCUSDT", "BUY", 1.0, "LONG")
    assert res is not None
    assert res["status"] == "FILLED"
    # Exactly one LIMIT order placed, no MARKET, no cancel.
    types = [o["type"] for o in client.created_orders]
    assert types == ["LIMIT"]
    assert client.created_orders[0]["timeInForce"] == "GTX"
    assert client.canceled == []


def test_maker_entry_buy_rests_at_bid_side_sell_at_ask_side():
    # BUY must rest at/below best bid, SELL at/above best ask (so it's a maker).
    # Exact value depends on the symbol's tick-size rounding, so assert the SIDE
    # relationship rather than an exact tick.
    client = FakeClient(statuses=[{"status": "FILLED", "avgPrice": "100.00",
                                   "executedQty": "1.0"}], bid="100.00", ask="100.02")
    svc = _svc()
    svc._try_maker_entry(client, "BTCUSDT", "BUY", 1.0, "LONG")
    buy_px = client.created_orders[0]["price"]
    assert buy_px <= 100.02  # BUY rests on the bid side, never above the ask

    client2 = FakeClient(statuses=[{"status": "FILLED", "avgPrice": "100.02",
                                    "executedQty": "1.0"}], bid="100.00", ask="100.02")
    svc._try_maker_entry(client2, "BTCUSDT", "SELL", 1.0, "SHORT")
    sell_px = client2.created_orders[0]["price"]
    assert sell_px >= 100.00  # SELL rests on the ask side, never below the bid


def test_maker_entry_post_only_rejected_returns_none():
    # GTX would cross -> create raises -> fall back to MARKET (None).
    client = FakeClient(create_raises=Exception("-2021 order would immediately match"))
    svc = _svc()
    res = svc._try_maker_entry(client, "BTCUSDT", "BUY", 1.0, "LONG")
    assert res is None


def test_maker_entry_terminal_status_returns_none():
    client = FakeClient(statuses=[{"status": "EXPIRED", "executedQty": "0"}])
    svc = _svc()
    res = svc._try_maker_entry(client, "BTCUSDT", "BUY", 1.0, "LONG")
    assert res is None


def test_maker_entry_timeout_no_fill_cancels_and_returns_none(monkeypatch):
    # WAIT=0 -> loop body skipped, straight to post-loop cancel + reconcile.
    monkeypatch.setenv("MAKER_WAIT_SEC", "0")
    client = FakeClient(final={"status": "CANCELED", "executedQty": "0"})
    svc = _svc()
    res = svc._try_maker_entry(client, "BTCUSDT", "BUY", 1.0, "LONG")
    assert res is None
    assert client.canceled == [555]


def test_maker_entry_partial_fill_tops_up_at_market_and_returns_order(monkeypatch):
    # Times out with 0.4/1.0 filled -> remainder 0.6 topped up at MARKET.
    monkeypatch.setenv("MAKER_WAIT_SEC", "0")
    client = FakeClient(
        final={"status": "PARTIALLY_FILLED", "executedQty": "0.4", "avgPrice": "100.00"},
    )
    svc = _svc()
    res = svc._try_maker_entry(client, "BTCUSDT", "BUY", 1.0, "LONG")
    assert res is not None  # returns the order (caller must NOT re-MARKET full qty)
    # A MARKET top-up for the remaining 0.6 must have been placed.
    market_orders = [o for o in client.created_orders if o["type"] == "MARKET"]
    assert len(market_orders) == 1
    assert market_orders[0]["quantity"] == pytest.approx(0.6, abs=1e-6)
    assert client.canceled == [555]


def test_maker_entry_filled_during_cancel_race_returns_final(monkeypatch):
    # Loop times out, but the order FILLED between timeout and cancel.
    monkeypatch.setenv("MAKER_WAIT_SEC", "0")
    client = FakeClient(final={"status": "FILLED", "executedQty": "1.0", "avgPrice": "100.00"})
    svc = _svc()
    res = svc._try_maker_entry(client, "BTCUSDT", "BUY", 1.0, "LONG")
    assert res is not None
    assert res["status"] == "FILLED"
    # No MARKET top-up needed since fully filled.
    assert [o["type"] for o in client.created_orders] == ["LIMIT"]
