"""Tests for mirroring cTrader live positions into the dashboard Trade table."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import MagicMock, patch

import pytest

from backend.database.models import Base, Trade
from backend.services.ctrader_trade_sync import (
    overlay_live_mark,
    persist_ctrader_execution,
    upsert_ctrader_live_trades,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_upsert_creates_open_trade_for_live_ctrader_position():
    db = _session()
    live = [
        {
            "symbol": "USDJPY",
            "side": "SELL",
            "quantity": 0.1,
            "entry_price": 159.719,
            "position_id": "667118033",
            "broker": "ctrader",
        }
    ]
    created = upsert_ctrader_live_trades(db, live)
    assert created == 1
    row = db.query(Trade).one()
    assert row.symbol == "USDJPY"
    assert row.direction == "SELL"
    assert row.quantity == pytest.approx(0.1)
    assert row.broker == "ctrader"
    assert row.broker_position_id == "667118033"
    assert row.status == "open"

    created_again = upsert_ctrader_live_trades(db, live)
    assert created_again == 0
    assert db.query(Trade).count() == 1


def test_upsert_attaches_position_id_to_pending_execution_row():
    db = _session()
    db.add(
        Trade(
            symbol="GBPUSD",
            direction="SELL",
            quantity=0.1,
            entry_price=1.35,
            status="open",
            broker="ctrader",
            exchange="ctrader",
            strategy="signal_candidate",
        )
    )
    db.commit()
    created = upsert_ctrader_live_trades(
        db,
        [
            {
                "symbol": "GBPUSD",
                "side": "SELL",
                "quantity": 0.1,
                "entry_price": 1.35484,
                "position_id": "667118037",
            }
        ],
    )
    assert created == 0
    row = db.query(Trade).one()
    assert row.broker_position_id == "667118037"
    assert row.entry_price == pytest.approx(1.35484)


def test_upsert_ignores_empty_live_book():
    db = _session()
    db.add(
        Trade(
            symbol="EURUSD",
            direction="BUY",
            quantity=0.1,
            entry_price=1.08,
            status="open",
            broker="ctrader",
        )
    )
    db.commit()
    assert upsert_ctrader_live_trades(db, []) == 0
    assert db.query(Trade).filter(Trade.status == "open").count() == 1


def test_overlay_live_mark_prefers_broker_pnl():
    payload = {
        "broker": "ctrader",
        "symbol": "USDJPY",
        "broker_position_id": "1",
        "entry_price": 159.0,
        "quantity": 0.1,
        "unrealized_pnl": 0.0,
        "unrealized_pnl_pct": 0.0,
    }
    out = overlay_live_mark(
        payload,
        {
            "1": {
                "entry_price": 159.719,
                "quantity": 0.1,
                "unrealized_pnl": -1.25,
                "current_price": 159.83,
            }
        },
        {},
    )
    assert out["entry_price"] == pytest.approx(159.719)
    assert out["unrealized_pnl"] == pytest.approx(-1.25)
    assert out["unrealized_pnl_pct"] != 0
    # yfinance cannot price a bare FX pair; the streamed spot must win.
    assert out["current_price"] == pytest.approx(159.83)


def test_persist_ctrader_execution_writes_open_row():
    db = _session()
    fake_session = MagicMock()
    fake_session.add.side_effect = db.add
    fake_session.commit.side_effect = db.commit
    fake_session.refresh.side_effect = lambda t: None
    fake_session.rollback.side_effect = db.rollback
    fake_session.close.side_effect = lambda: None

    with patch("backend.services.ctrader_trade_sync.SessionLocal", return_value=fake_session):
        persist_ctrader_execution(
            symbol="AUDUSD",
            direction="BUY",
            quantity=0.1,
            entry_price=0.7168,
            strategy="MOMENTUM_TREND_PULSE",
            order_id="oid-1",
        )

    row = db.query(Trade).one()
    assert row.symbol == "AUDUSD"
    assert row.broker == "ctrader"
    assert row.status == "open"
    assert row.strategy == "MOMENTUM_TREND_PULSE"


@pytest.mark.asyncio
async def test_dashboard_positions_include_ctrader_broker_field():
    from types import SimpleNamespace
    from backend.routes.trading import get_positions

    trade = SimpleNamespace(
        id=99,
        symbol="USDJPY",
        direction="SELL",
        quantity=0.1,
        entry_price=159.719,
        stop_loss=None,
        take_profit=None,
        strategy="ctrader_live",
        broker="ctrader",
        broker_position_id="667118033",
        timestamp=None,
        exchange="ctrader",
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [trade]
    live = [
        {
            "symbol": "USDJPY",
            "position_id": "667118033",
            "entry_price": 159.719,
            "quantity": 0.1,
            "unrealized_pnl": -0.5,
        }
    ]
    with patch("backend.routes.trading.SessionLocal", return_value=db), patch(
        "backend.routes.trading.ctrader_broker"
    ) as broker, patch(
        "backend.routes.trading.upsert_ctrader_live_trades", return_value=1
    ), patch(
        "backend.routes.trading._fetch_mark_prices_for_symbols", return_value={}
    ):
        broker.get_positions.return_value = live
        result = await get_positions()

    assert result["count"] == 1
    pos = result["positions"][0]
    assert pos["symbol"] == "USDJPY"
    assert pos["broker"] == "ctrader"
    assert pos["unrealized_pnl"] == pytest.approx(-0.5)
