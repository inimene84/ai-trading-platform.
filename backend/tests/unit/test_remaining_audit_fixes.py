"""Regression tests for the second-pass trading-safety audit."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from backend.database.models import Base, Trade
from backend.routes.sentry import _validate_sentry_token
from backend.security import AUTH_ENV_VARS
from backend.services.binance_futures_service import BinanceFuturesService
from backend.services.ctrader_service import CTraderService
from backend.services.ctrader_trade_sync import reconcile_ctrader_positions
from backend.services.trading_loop_helpers import EmergencyExitManager
from backend.services.trading_mode import (
    live_ctrader_orders_allowed,
    live_exchange_orders_allowed,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _binance_svc():
    svc = BinanceFuturesService.__new__(BinanceFuturesService)
    svc.dry_run = False
    svc.leverage = 10
    svc.margin_type = "ISOLATED"
    svc._leverage_set = set()
    svc._lot_step = {"BTCUSDT": 0.001}
    svc._lot_min = {"BTCUSDT": 0.001}
    svc._qty_precision = {"BTCUSDT": 3}
    return svc


def _req(method: str, path: str) -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": []})


def test_paper_mode_blocks_ctrader_and_binance_live_orders(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("PAPER_TRADING", "true")
    monkeypatch.setenv("DRY_RUN_ALL", "false")
    assert live_exchange_orders_allowed() is False
    assert live_ctrader_orders_allowed() is False


def test_ctrader_place_order_simulates_when_not_live(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    svc = CTraderService()
    svc._dry_run = False
    svc._connected = True
    svc._authenticated = True
    svc._protocol = object()
    res = svc.place_order(
        symbol="EURUSD", direction="BUY", quantity=0.1, price=1.10,
    )
    assert res["status"] == "simulated"
    assert res["broker"] == "ctrader:paper"


def test_ctrader_amend_and_close_simulate_when_not_live(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    svc = CTraderService()
    svc._dry_run = False
    svc._connected = True
    svc._authenticated = True
    svc._protocol = object()
    svc._positions = [{
        "position_id": "99", "symbol": "EURUSD", "quantity": 0.1, "entry_price": 1.10,
    }]
    amend = svc.amend_position_sltp("99", stop_loss=1.09, take_profit=1.12)
    assert amend["status"] == "simulated"
    close = svc.close_position(position_id="99", symbol="EURUSD", volume=0.1)
    assert close["broker"] == "ctrader:paper"
    assert close["status"] == "closed"


def test_cadjpy_db_stop_is_implausible_live_stop_is_not():
    assert CTraderService.is_plausible_stop("CADJPY", 112.937, 95.926, "SELL") is False
    assert CTraderService.is_plausible_stop("CADJPY", 112.937, 113.237, "SELL") is True
    assert CTraderService.is_plausible_stop("EURUSD", 1.1000, 1.0950, "BUY") is True
    assert CTraderService.is_plausible_stop("EURUSD", 1.1000, 1.2000, "BUY") is False


def test_amend_refuses_implausible_stop_on_live_connection(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "live")
    svc = CTraderService()
    svc._dry_run = False
    svc._connected = True
    svc._authenticated = True
    svc._protocol = object()
    svc._positions = [{
        "position_id": "cad-1",
        "symbol": "CADJPY",
        "side": "SELL",
        "entry_price": 112.937,
        "quantity": 0.1,
    }]
    res = svc.amend_position_sltp("cad-1", stop_loss=95.926, take_profit=95.326)
    assert res["status"] == "error"
    assert "implausible" in res["error"]


def test_reconcile_copies_live_sl_tp_onto_db_row():
    db = _session()
    trade = Trade(
        symbol="CADJPY",
        direction="SELL",
        quantity=0.1,
        entry_price=112.937,
        stop_loss=95.926,
        take_profit=95.326,
        status="open",
        broker="ctrader",
        broker_position_id="cad-1",
    )
    db.add(trade)
    db.commit()

    live = [{
        "position_id": "cad-1",
        "symbol": "CADJPY",
        "quantity": 0.1,
        "entry_price": 112.937,
        "side": "SELL",
        "stop_loss": 113.237,
        "take_profit": 112.637,
    }]
    res = reconcile_ctrader_positions(db, live_positions=live, broker=MagicMock())
    assert res["updated"] == 1
    assert res["closed"] == 0
    row = db.query(Trade).one()
    assert row.status == "open"
    assert row.stop_loss == pytest.approx(113.237)
    assert row.take_profit == pytest.approx(112.637)


def test_empty_ctrader_snapshot_without_deal_refuses_bulk_close():
    db = _session()
    db.add(Trade(
        symbol="EURUSD", direction="BUY", quantity=0.1, entry_price=1.10,
        status="open", broker="ctrader", broker_position_id="ghost-1",
    ))
    db.commit()
    broker = MagicMock()
    broker.get_recent_deal.return_value = None
    res = reconcile_ctrader_positions(db, live_positions=[], broker=broker)
    assert res["closed"] == 0
    assert db.query(Trade).one().status == "open"


def test_empty_ctrader_snapshot_with_deal_closes_confirmed_row():
    db = _session()
    db.add(Trade(
        symbol="EURUSD", direction="BUY", quantity=0.1, entry_price=1.10,
        status="open", broker="ctrader", broker_position_id="sl-1",
    ))
    db.commit()
    broker = MagicMock()
    broker.get_recent_deal.return_value = {
        "position_id": "sl-1",
        "symbol": "EURUSD",
        "execution_price": 1.095,
        "gross_profit": -50.0,
    }
    broker.get_exit_price.return_value = 1.095
    broker.quote_to_usd_rate.return_value = 1.0
    broker.CONTRACT_UNITS_PER_LOT = 100_000
    res = reconcile_ctrader_positions(db, live_positions=[], broker=broker)
    assert res["closed"] == 1
    assert db.query(Trade).one().status == "closed"


@pytest.mark.asyncio
async def test_emergency_exit_already_flat_leaves_open_when_live_qty_remains():
    trade = SimpleNamespace(
        symbol="BTCUSDT",
        direction="BUY",
        quantity=1.0,
        entry_price=100000.0,
        broker="binance_futures",
        notes="",
        status="open",
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [trade]
    broker = MagicMock()
    broker.get_positions.return_value = [{
        "symbol": "BTCUSDT",
        "mark_price": 50000.0,
        "entry_price": 100000.0,
        "quantity": 1.0,
    }]
    ut = MagicMock()
    ut.place_order.return_value = SimpleNamespace(
        success=False,
        filled_price=None,
        message="ReduceOnly Order is rejected. -2022",
        realized_pnl=None,
        commission=0,
    )
    with patch(
        "backend.services.trading_loop_helpers.UnifiedTrading", return_value=ut
    ), patch(
        "backend.services.trading_loop_helpers.get_position_manager"
    ) as pm_fn:
        pm_fn.return_value.emergency_drawdown_pct = -1.0
        closed = await EmergencyExitManager.run_emergency_exits(
            db, broker, pyramid_layers={}, sl_cooldown={},
        )
    assert closed == 0
    assert trade.status == "open"


@pytest.mark.asyncio
async def test_emergency_exit_already_flat_closes_when_live_qty_is_zero():
    trade = SimpleNamespace(
        symbol="BTCUSDT",
        direction="BUY",
        quantity=1.0,
        entry_price=100000.0,
        broker="binance_futures",
        notes="",
        status="open",
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [trade]
    broker = MagicMock()
    broker.get_positions.return_value = [{
        "symbol": "BTCUSDT",
        "mark_price": 50000.0,
        "entry_price": 100000.0,
        "quantity": 0.0,
    }]
    ut = MagicMock()
    ut.place_order.return_value = SimpleNamespace(
        success=False,
        filled_price=None,
        message="already flat",
        realized_pnl=None,
        commission=0,
    )
    with patch(
        "backend.services.trading_loop_helpers.UnifiedTrading", return_value=ut
    ), patch(
        "backend.services.trading_loop_helpers.get_position_manager"
    ) as pm_fn:
        pm_fn.return_value.emergency_drawdown_pct = -1.0
        closed = await EmergencyExitManager.run_emergency_exits(
            db, broker, pyramid_layers={}, sl_cooldown={},
        )
    assert closed == 1
    assert trade.status == "closed"


def test_sentry_token_missing_returns_503(monkeypatch):
    for name in ("SENTRY_WATCHDOG_TOKEN", "ADMIN_API_KEY", "API_AUTH_TOKEN", "BACKEND_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(HTTPException) as exc:
        _validate_sentry_token(_req("POST", "/sentry/emergency-halt"))
    assert exc.value.status_code == 503


def test_trading_mutation_without_admin_token_is_rejected(monkeypatch):
    """Paper startup may omit a token; trading POSTs must still fail closed."""
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("PAPER_TRADING", "true")
    for name in AUTH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)
    res = client.post("/api/trading/loop/start")
    assert res.status_code == 401
    assert "not configured" in res.json()["detail"]


def test_orphan_flatten_does_not_count_rejected_close(monkeypatch):
    from backend.services import sentry_emergency

    monkeypatch.setenv("ACTIVE_BROKER", "binance_futures")
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.setenv("BINANCE_DRY_RUN", "false")

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    broker = MagicMock()
    broker.place_order.return_value = {"status": "error", "message": "margin"}
    broker.get_positions.return_value = [
        {"symbol": "ETHUSDT", "quantity": 0.2, "side": "BUY"},
    ]
    monkeypatch.setattr(
        "backend.database.connection.SessionLocal", lambda: db, raising=False
    )
    monkeypatch.setattr(
        "backend.services.binance_futures_service.binance_futures_broker", broker
    )
    result = sentry_emergency._close_all_positions_sync()
    assert result["closed_orphans"] == 0
    assert any("ETHUSDT" in e for e in result["errors"])


def test_sl_fail_unverified_does_not_emergency_close():
    broker = _binance_svc()
    client = MagicMock()
    client.futures_account.return_value = {"availableBalance": "1000"}

    def safe_side_effect(_client, params):
        if params.get("type") == "MARKET":
            return {"orderId": 1, "avgPrice": "1.15"}
        raise Exception("SL rejected")

    def has_stop(_sym, _side, raise_on_error=False):
        if raise_on_error:
            raise Exception("order list timeout")
        return False

    with patch.dict("os.environ", {"MAKER_ENTRY_ENABLED": "false"}), \
         patch.object(broker, "get_positions", return_value=[]), \
         patch.object(broker, "_get_client", return_value=client), \
         patch.object(broker, "_setup_symbol"), \
         patch.object(broker, "_round_qty", side_effect=lambda s, q, **kw: q), \
         patch.object(broker, "_round_price", side_effect=lambda s, p: p), \
         patch.object(broker, "_has_exchange_stop", side_effect=has_stop), \
         patch.object(broker, "_safe_create_order", side_effect=safe_side_effect), \
         patch.object(broker, "_native_trailing_enabled", return_value=False):
        result = broker.place_order(
            symbol="XRPUSDT", direction="SELL", quantity=5,
            price=1.15, stop_loss=1.18,
        )

    assert result["status"] == "error"
    assert "no emergency close" in result["message"]
    client.futures_create_order.assert_not_called()


def test_ensure_protective_orders_refuses_implausible_db_stop():
    broker = _binance_svc()
    client = MagicMock()
    client.futures_symbol_ticker.return_value = {"price": "1.15"}
    with patch.object(broker, "_to_futures_symbol", return_value="XRPUSDT"), \
         patch.object(broker, "_live_position_qty", return_value=10.0), \
         patch.object(broker, "get_positions", return_value=[
             {"symbol": "XRPUSDT", "quantity": 10.0, "entry_price": 1.15},
         ]), \
         patch.object(broker, "_has_exchange_stop", return_value=False), \
         patch.object(broker, "_has_exchange_take_profit", return_value=True), \
         patch.object(broker, "_get_client", return_value=client), \
         patch.object(broker, "_safe_create_order") as safe:
        res = broker.ensure_protective_orders("XRPUSDT", "SELL", stop_loss=0.50)

    assert res["status"] == "skipped"
    safe.assert_not_called()


def test_maker_partial_topup_uses_safe_create_order(monkeypatch):
    monkeypatch.setenv("MAKER_WAIT_SEC", "0")
    broker = _binance_svc()
    client = MagicMock()
    client.futures_orderbook_ticker.return_value = {
        "bidPrice": "100", "askPrice": "100.1",
    }
    client.futures_create_order.return_value = {"orderId": 11}
    client.futures_get_order.return_value = {
        "status": "PARTIALLY_FILLED",
        "executedQty": "0.4",
    }
    with patch.object(broker, "_round_price", side_effect=lambda s, p: p), \
         patch.object(broker, "_round_qty", side_effect=lambda s, q, **kw: q), \
         patch.object(broker, "_safe_create_order") as safe:
        result = broker._try_maker_entry(client, "BTCUSDT", "BUY", 1.0, "LONG")

    assert result["executedQty"] == "0.4"
    safe.assert_called_once()
    params = safe.call_args.args[1]
    assert params["type"] == "MARKET"
    assert params["quantity"] == pytest.approx(0.6)
    assert params["newClientOrderId"].startswith("xBTCUSD")
    assert client.futures_create_order.call_count == 1
