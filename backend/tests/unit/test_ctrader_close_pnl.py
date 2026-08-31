"""A live cTrader close must never record a fabricated exit price or zero P&L.

The live close is an async protocol send that returns no fill price. Falling
back to entry_price made every forex trade look like a flat 0.00 outcome, which
hid real losses from the daily-loss guard and from per-symbol expectancy.
"""

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.routes.trading import close_position
from backend.services.ctrader_service import CTraderProtocol, CTraderService
from backend.services.trading_mode import TradingMode


def _ctrader_trade(direction="SELL", entry=159.719, qty=0.1):
    return SimpleNamespace(
        id=1,
        symbol="USDJPY",
        direction=direction,
        quantity=qty,
        entry_price=entry,
        status="open",
        exit_price=None,
        pnl=0.0,
        closed_at=None,
        notes="",
        broker="ctrader",
        exchange="ctrader",
        broker_position_id="667118033",
        broker_order_id=None,
    )


def _db_with(trade):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = trade
    return db


@pytest.mark.asyncio
async def test_close_without_fill_price_records_unknown_not_zero():
    """No streamed spot and no broker price -> exit/pnl must be None, not 0.0."""
    trade = _ctrader_trade()
    db = _db_with(trade)
    broker = MagicMock()
    broker.close_position.return_value = {"status": "sent", "position_id": "667118033"}
    broker.get_mark_price.return_value = None
    broker.CONTRACT_UNITS_PER_LOT = 100_000

    with patch("backend.routes.trading.SessionLocal", return_value=db), patch(
        "backend.services.ctrader_service.ctrader_broker", broker
    ), patch(
        "backend.services.trading_mode.get_trading_mode", return_value=TradingMode.LIVE
    ):
        result = await close_position(1)

    assert trade.status == "closed"
    assert trade.exit_price is None
    assert trade.pnl is None, "fabricated 0.0 P&L hides real forex losses"
    assert result["pnl"] is None


@pytest.mark.asyncio
async def test_close_uses_streamed_mark_price_and_contract_units():
    """A SELL closed above entry is a loss, scaled by contract units not lots."""
    trade = _ctrader_trade(direction="SELL", entry=159.719, qty=0.1)
    db = _db_with(trade)
    broker = MagicMock()
    broker.close_position.return_value = {"status": "sent"}
    broker.get_mark_price.return_value = 159.819
    broker.CONTRACT_UNITS_PER_LOT = 100_000

    with patch("backend.routes.trading.SessionLocal", return_value=db), patch(
        "backend.services.ctrader_service.ctrader_broker", broker
    ), patch(
        "backend.services.trading_mode.get_trading_mode", return_value=TradingMode.LIVE
    ):
        result = await close_position(1)

    # SELL 0.1 lot = 10,000 units; price moved 0.100 against the position.
    assert trade.exit_price == pytest.approx(159.819)
    assert result["pnl"] == pytest.approx(-1000.0)
    assert trade.pnl < 0


@pytest.mark.asyncio
async def test_close_prefers_broker_reported_pnl():
    trade = _ctrader_trade()
    db = _db_with(trade)
    broker = MagicMock()
    broker.close_position.return_value = {"status": "closed", "price": 159.5, "pnl": 21.9}
    broker.CONTRACT_UNITS_PER_LOT = 100_000

    with patch("backend.routes.trading.SessionLocal", return_value=db), patch(
        "backend.services.ctrader_service.ctrader_broker", broker
    ), patch(
        "backend.services.trading_mode.get_trading_mode", return_value=TradingMode.LIVE
    ):
        result = await close_position(1)

    assert trade.exit_price == pytest.approx(159.5)
    assert result["pnl"] == pytest.approx(21.9)


def test_get_mark_price_uses_opposite_side_of_book():
    svc = CTraderService()
    svc._last_spots = {
        "EURUSD": {"symbol": "EURUSD", "symbol_id": 1, "bid": 1.16170, "ask": 1.16180}
    }
    # A long exits on the bid, a short exits on the ask.
    assert svc.get_mark_price("EURUSD", "BUY") == pytest.approx(1.16170)
    assert svc.get_mark_price("EURUSD", "SELL") == pytest.approx(1.16180)
    assert svc.get_mark_price("EURUSD") == pytest.approx(1.16175)
    assert svc.get_mark_price("GBPUSD") is None


def test_open_positions_are_marked_to_market():
    svc = CTraderService()
    svc._positions = [
        {
            "symbol": "USDJPY",
            "side": "SELL",
            "quantity": 0.1,
            "entry_price": 159.719,
            "unrealized_pnl": 0.0,
            "position_id": "667118033",
            "broker": "ctrader",
        }
    ]
    svc._last_spots = {
        "USDJPY": {"symbol": "USDJPY", "symbol_id": 4, "bid": 159.60, "ask": 159.62}
    }
    pos = svc.get_positions()[0]
    # Short filled at 159.719, buys back at the 159.62 ask -> profit.
    # 0.1 lot = 10,000 units; 0.099 JPY per unit = 990 JPY.
    assert pos["current_price"] == pytest.approx(159.62)
    assert pos["unrealized_pnl"] > 0
    assert pos["unrealized_pnl"] == pytest.approx(990.0)


class _FakeSubscribeSpotsReq:
    """Stand-in for the protobuf message; the SDK is only installed in the image."""

    def __init__(self):
        self.ctidTraderAccountId = 0
        self.symbolId = []


@pytest.fixture
def fake_ctrader_messages(monkeypatch):
    messages = types.ModuleType("ctrader_open_api.messages.OpenApiMessages_pb2")
    messages.ProtoOASubscribeSpotsReq = _FakeSubscribeSpotsReq
    package = types.ModuleType("ctrader_open_api.messages")
    package.OpenApiMessages_pb2 = messages
    root = types.ModuleType("ctrader_open_api")
    root.messages = package
    monkeypatch.setitem(sys.modules, "ctrader_open_api", root)
    monkeypatch.setitem(sys.modules, "ctrader_open_api.messages", package)
    monkeypatch.setitem(
        sys.modules, "ctrader_open_api.messages.OpenApiMessages_pb2", messages
    )
    return messages


def test_spot_subscription_is_requested_once_per_symbol(fake_ctrader_messages):
    """Without ProtoOASubscribeSpotsReq the broker streams no quotes at all."""
    proto = CTraderProtocol.__new__(CTraderProtocol)
    proto._creds = {"account_id": 46756268}
    proto._subscribed_spot_ids = set()
    sent = []
    proto._send = lambda msg, ptype: sent.append((msg, ptype))

    proto._subscribe_spots({1, 2})
    assert len(sent) == 1
    msg, ptype = sent[0]
    assert ptype == 2127, "spot subscription must use ProtoOASubscribeSpotsReq"
    assert msg.ctidTraderAccountId == 46756268
    assert sorted(msg.symbolId) == [1, 2]

    # Already-subscribed symbols must not re-request on every reconcile.
    proto._subscribe_spots({1, 2})
    assert len(sent) == 1

    proto._subscribe_spots({2, 4})
    assert len(sent) == 2
    assert sorted(sent[1][0].symbolId) == [4]


def test_positions_without_spot_keep_zero_and_do_not_crash():
    svc = CTraderService()
    svc._positions = [
        {
            "symbol": "NZDUSD",
            "side": "BUY",
            "quantity": 0.1,
            "entry_price": 0.59126,
            "unrealized_pnl": 0.0,
            "position_id": "1",
        }
    ]
    svc._last_spots = {}
    pos = svc.get_positions()[0]
    assert pos["unrealized_pnl"] == 0.0
    assert "current_price" not in pos
