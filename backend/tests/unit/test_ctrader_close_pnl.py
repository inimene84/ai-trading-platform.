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
    broker.quote_to_usd_rate.return_value = 1 / 159.819  # JPY -> USD

    with patch("backend.routes.trading.SessionLocal", return_value=db), patch(
        "backend.services.ctrader_service.ctrader_broker", broker
    ), patch(
        "backend.services.trading_mode.get_trading_mode", return_value=TradingMode.LIVE
    ):
        result = await close_position(1)

    # SELL 0.1 lot = 10,000 units; price moved 0.100 against the position.
    # -1000 JPY converted at 159.819 is about -6.26 USD.
    assert trade.exit_price == pytest.approx(159.819)
    assert result["pnl"] == pytest.approx(-1000.0 / 159.819, rel=1e-3)
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
    # 0.1 lot = 10,000 units; 0.099 JPY per unit = 990 JPY = ~6.20 USD.
    assert pos["current_price"] == pytest.approx(159.62)
    assert pos["unrealized_pnl"] > 0
    assert pos["pnl_currency"] == "USD"
    assert pos["unrealized_pnl"] == pytest.approx(990.0 / 159.62, rel=1e-3)


def test_quote_currency_pnl_is_normalised_to_usd():
    """A yen P&L shown beside a dollar P&L made USDJPY look ~150x worse."""
    svc = CTraderService()
    svc._last_spots = {
        "USDJPY": {"symbol": "USDJPY", "symbol_id": 4, "bid": 159.76, "ask": 159.78},
        "EURUSD": {"symbol": "EURUSD", "symbol_id": 1, "bid": 1.1617, "ask": 1.1618},
        "USDCAD": {"symbol": "USDCAD", "symbol_id": 8, "bid": 1.3855, "ask": 1.3856},
    }
    # USD-quoted pair needs no conversion.
    assert svc.quote_to_usd_rate("EURUSD", 1.1617) == pytest.approx(1.0)
    # USD-base pairs convert by the pair rate itself.
    assert svc.quote_to_usd_rate("USDJPY", 159.77) == pytest.approx(1 / 159.77)
    assert svc.quote_to_usd_rate("USDCAD", 1.3855) == pytest.approx(1 / 1.3855)
    # Cross pair resolves through a streamed leg.
    assert svc.quote_to_usd_rate("EURJPY", 185.0) == pytest.approx(1 / 159.77, rel=1e-3)
    # Nothing to convert with -> caller must not claim USD.
    assert svc.quote_to_usd_rate("EURGBP", 0.85) is None


def test_unconvertible_pnl_is_labelled_with_its_own_currency():
    svc = CTraderService()
    svc._positions = [
        {
            "symbol": "EURGBP",
            "side": "BUY",
            "quantity": 0.1,
            "entry_price": 0.8500,
            "position_id": "9",
        }
    ]
    svc._last_spots = {
        "EURGBP": {"symbol": "EURGBP", "symbol_id": 30, "bid": 0.8510, "ask": 0.8511}
    }
    pos = svc.get_positions()[0]
    assert pos["pnl_currency"] == "GBP", "must not imply USD when no rate exists"


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
