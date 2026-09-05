"""Regression tests for the trading-safety audit fixes."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.services.binance_futures_service import BinanceFuturesService
from backend.services.trading_loop import TradingLoopService
from backend.services.trading_mode import (
    TradingMode,
    get_trading_mode,
    live_binance_orders_allowed,
    live_ctrader_orders_allowed,
    live_exchange_orders_allowed,
)
from backend.services.unified_trading import (
    OrderSide,
    OrderType,
    PaperTradingEngine,
    UnifiedOrder,
    UnifiedTrading,
)


def _loop(kill_floor=50.0):
    loop = TradingLoopService()
    loop.risk_config = SimpleNamespace(kill_floor_usdt=kill_floor)
    return loop


def _binance_svc():
    svc = BinanceFuturesService.__new__(BinanceFuturesService)
    svc.dry_run = False
    svc.leverage = 10
    svc.margin_type = "ISOLATED"
    svc._leverage_set = set()
    svc._lot_step = {"BTCUSDT": 0.001, "UNIUSDT": 1.0}
    svc._lot_min = {"BTCUSDT": 0.001, "UNIUSDT": 1.0}
    svc._qty_precision = {"BTCUSDT": 3, "UNIUSDT": 0}
    return svc


def test_unset_trading_mode_defaults_to_paper_and_blocks_live_orders(monkeypatch):
    monkeypatch.delenv("TRADING_MODE", raising=False)
    monkeypatch.setenv("PAPER_TRADING", "true")
    monkeypatch.setenv("DRY_RUN_ALL", "true")
    assert get_trading_mode() == TradingMode.PAPER
    assert live_exchange_orders_allowed() is False
    assert live_binance_orders_allowed() is False
    assert live_ctrader_orders_allowed() is False


def test_kill_switch_blocks_entries_on_balance_error(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.setenv("PAPER_TRADING", "false")
    monkeypatch.setenv("DRY_RUN_ALL", "false")
    loop = _loop()
    assert loop._kill_switch_action(
        0.0, {"equity": 0.0, "balance": 0.0, "error": "timeout"}
    ) == "block_entries"


def test_handle_api_exception_ignores_price_and_self_ban_text():
    BinanceFuturesService._banned_until = None
    BinanceFuturesService._handle_api_exception(
        Exception("Limit price can't be higher than 64185.2")
    )
    assert BinanceFuturesService._banned_until is None
    BinanceFuturesService._handle_api_exception(
        Exception("Binance IP ban active. Cooldown remaining: 418.3s")
    )
    assert BinanceFuturesService._banned_until is None


def test_handle_api_exception_matches_status_code():
    BinanceFuturesService._banned_until = None
    exc = Exception("rate limited")
    exc.status_code = 429
    exc.code = None
    BinanceFuturesService._handle_api_exception(exc)
    assert BinanceFuturesService._banned_until is not None
    BinanceFuturesService._banned_until = None


def test_round_qty_no_min_bump_on_reduce_only_remainder():
    broker = _binance_svc()
    assert broker._round_qty("UNIUSDT", 0.4, bump_to_min=False) == 0.0
    assert broker._round_qty("BTCUSDT", 0.0003, bump_to_min=False) == 0.0


def test_round_price_uses_tick_size_not_precision():
    broker = _binance_svc()
    # BTCUSDT tick is 0.1; pricePrecision-only rounding would keep 0.05
    assert broker._round_price("BTCUSDT", 64123.45) == 64123.4


def test_close_position_uses_live_qty(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.setenv("PAPER_TRADING", "false")
    monkeypatch.setenv("DRY_RUN_ALL", "false")
    svc = _binance_svc()
    with patch.object(svc, "_live_position_qty", return_value=0.42) as live_qty, \
         patch.object(svc, "place_order", return_value={"status": "sent"}) as place:
        svc.close_position(symbol="BTCUSDT", direction="BUY")
    live_qty.assert_called()
    assert place.call_args.kwargs["quantity"] == 0.42


def test_already_flat_requires_verified_zero_qty(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.setenv("PAPER_TRADING", "false")
    monkeypatch.setenv("DRY_RUN_ALL", "false")
    svc = _binance_svc()
    client = MagicMock()
    client.futures_account.return_value = {"availableBalance": "1000"}
    with patch.object(svc, "_get_client", return_value=client), \
         patch.object(svc, "_setup_symbol"), \
         patch.object(svc, "_safe_create_order", side_effect=Exception("ReduceOnly Order is rejected. -2022")), \
         patch.object(svc, "_live_position_qty", return_value=1.5):
        result = svc.place_order(
            symbol="BTCUSDT", direction="BUY", action="close", quantity=2.0, price=50000,
        )
    assert result["status"] == "error"
    assert "live qty" in result["message"]


def test_paper_close_requires_price_and_records_pnl():
    UnifiedTrading._instance = None
    engine = PaperTradingEngine()
    pid = engine.create_portfolio("reuse-test", 10_000.0, leverage=1.0)
    open_res = engine.place_order(pid, UnifiedOrder(
        symbol="ETHUSDT", side=OrderSide.BUY, order_type=OrderType.MARKET,
        quantity=1.0, price=2000.0,
    ))
    assert open_res.success
    rejected = engine.place_order(pid, UnifiedOrder(
        symbol="ETHUSDT", side=OrderSide.SELL, order_type=OrderType.MARKET,
        quantity=1.0, reduce_only=True,
    ))
    assert rejected.success is False
    closed = engine.place_order(pid, UnifiedOrder(
        symbol="ETHUSDT", side=OrderSide.SELL, order_type=OrderType.MARKET,
        quantity=1.0, price=2100.0, reduce_only=True,
    ))
    assert closed.success is True
    assert closed.filled_price == 2100.0
    trade = engine._portfolios[pid]["trades"][-1]
    assert trade.pnl == pytest.approx(100.0)
    assert engine.get_portfolio(pid)["positions"]["ETHUSDT"]["long"] == 0
    UnifiedTrading._instance = None


def test_init_session_reuses_stable_paper_book(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    UnifiedTrading._instance = None
    ut = UnifiedTrading()
    first = ut.init_session("binance_futures", mode="paper", paper_balance=12_345.0)
    second = ut.init_session("binance_futures", mode="paper", paper_balance=99_999.0)
    assert first.paper_portfolio_id == second.paper_portfolio_id
    UnifiedTrading._instance = None


def test_risk_guard_skips_zero_value_daily_baseline():
    from datetime import datetime, timezone
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from backend.database.models import Base, PortfolioSnapshot
    from backend.services.risk_config import RiskConfig
    from backend.services.risk_guard import enforce_risk_limits

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    today = datetime.now(timezone.utc).replace(hour=1, minute=0, second=0, microsecond=0)
    db.add(PortfolioSnapshot(total_value=0.0, cash=0.0, timestamp=today))
    db.add(PortfolioSnapshot(total_value=1000.0, cash=1000.0, timestamp=today.replace(hour=2)))
    db.commit()
    current = PortfolioSnapshot(total_value=990.0, cash=990.0, timestamp=today.replace(hour=3))
    cfg = RiskConfig(
        max_daily_loss_pct=5.0,
        max_portfolio_drawdown_pct=99.0,
        max_positions=99,
        max_open_positions=99,
        max_directional_exposure_usdt=0,
    )
    # 990 vs the $0 row would look like a 100% day; the >0 filter must use $1000.
    enforce_risk_limits(db, cfg, open_trades=[], latest_snapshot=current)
    db.close()
