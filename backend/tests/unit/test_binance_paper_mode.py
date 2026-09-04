"""Local Binance paper mode: simulated fills, no live futures orders, no $0 kill."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.services.binance_futures_service import BinanceFuturesService
from backend.services.trading_loop import TradingLoopService
from backend.services.trading_mode import (
    TradingMode,
    get_trading_mode,
    live_binance_orders_allowed,
    paper_starting_balance,
)
from backend.services.unified_trading import (
    OrderSide,
    OrderType,
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
    svc._lot_step = {"BTCUSDT": 0.001}
    svc._lot_min = {"BTCUSDT": 0.001}
    svc._qty_precision = {"BTCUSDT": 3}
    return svc


def test_paper_starting_balance_from_env(monkeypatch):
    monkeypatch.setenv("PAPER_BALANCE", "25000")
    assert paper_starting_balance() == 25000.0


def test_paper_starting_balance_rejects_non_positive(monkeypatch):
    monkeypatch.setenv("PAPER_BALANCE", "0")
    assert paper_starting_balance() == 100_000.0


def test_explicit_paper_mode_disables_live_binance_orders(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    assert get_trading_mode() == TradingMode.PAPER
    assert live_binance_orders_allowed() is False


def test_explicit_live_mode_allows_binance_orders(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.setenv("PAPER_TRADING", "false")
    monkeypatch.setenv("DRY_RUN_ALL", "false")
    assert get_trading_mode() == TradingMode.LIVE
    assert live_binance_orders_allowed() is True


def test_kill_switch_skips_halt_in_paper_with_live_zero(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    loop = _loop()
    live_zero = {"equity": 0.0, "balance": 0.0, "broker": "binance_futures"}
    assert loop._kill_switch_action(0.0, live_zero) == "ok"


def test_kill_switch_halts_live_zero_equity(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.setenv("PAPER_TRADING", "false")
    monkeypatch.setenv("DRY_RUN_ALL", "false")
    loop = _loop(kill_floor=50.0)
    assert loop._kill_switch_action(0.0, {"equity": 0.0, "balance": 0.0}) == "halt"
    assert loop._kill_switch_action(49.99, {"equity": 49.99}) == "halt"
    assert loop._kill_switch_action(65.0, {"equity": 65.0}) == "ok"


def test_kill_switch_blocks_entries_when_live_equity_missing(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.setenv("PAPER_TRADING", "false")
    monkeypatch.setenv("DRY_RUN_ALL", "false")
    loop = _loop()
    assert loop._kill_switch_action(0.0, {"broker": "binance_futures"}) == "block_entries"


def test_effective_balance_uses_paper_book_not_live_zero(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("PAPER_BALANCE", "77777")
    loop = _loop()
    live = MagicMock()
    live.get_balance.return_value = {"equity": 0.0, "balance": 0.0, "available": 0.0}
    with patch(
        "backend.services.unified_trading.UnifiedTrading.get_paper_portfolio",
        return_value={"cash": 1234.0, "equity": 1500.0, "margin_used": 266.0},
    ), patch(
        "backend.services.trading_loop.get_active_broker",
        return_value=live,
    ):
        bal = loop._get_effective_balance()
    assert bal["broker"] == "paper_trading"
    assert bal["equity"] == 1500.0
    assert bal["balance"] == 1234.0
    live.get_balance.assert_not_called()


def test_effective_balance_falls_back_to_paper_config_when_book_wiped(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("PAPER_BALANCE", "88888")
    loop = _loop()
    with patch(
        "backend.services.unified_trading.UnifiedTrading.get_paper_portfolio",
        return_value={"cash": 0.0, "equity": 0.0, "margin_used": 0.0},
    ), patch.object(loop, "_last_paper_book_from_db", return_value=None):
        bal = loop._get_effective_balance()
    assert bal["broker"] == "paper_trading"
    assert bal["equity"] == 88888.0
    assert bal["balance"] == 88888.0


def test_paper_place_order_does_not_call_binance(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    UnifiedTrading._instance = None
    broker = MagicMock()
    router = UnifiedTrading()
    router.register_broker("binance_futures", broker)
    router.init_session("binance_futures", mode="paper", paper_balance=10_000.0, session_id="paper-binance")

    resp = router.place_order(UnifiedOrder(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=0.01,
        price=50_000.0,
    ))

    assert resp.success is True
    assert resp.mode == "paper"
    assert str(resp.order_id).startswith("paper_")
    broker.place_order.assert_not_called()
    UnifiedTrading._instance = None


def test_binance_service_refuses_live_order_in_explicit_paper(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    svc = _binance_svc()
    client = MagicMock()
    with patch.object(svc, "_get_client", return_value=client):
        result = svc.place_order(
            symbol="BTCUSDT",
            direction="BUY",
            action="open",
            quantity=0.01,
            price=50_000.0,
        )
    assert result["status"] == "error"
    assert "TRADING_MODE=live" in result["reason"]
    client.futures_create_order.assert_not_called()
    svc._get_client.assert_not_called()


def test_binance_service_allows_mocked_live_order_when_mode_live(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.setenv("PAPER_TRADING", "false")
    monkeypatch.setenv("DRY_RUN_ALL", "false")
    svc = _binance_svc()
    client = MagicMock()
    client.futures_account.return_value = {"availableBalance": "10000"}
    with patch.object(svc, "_get_client", return_value=client), \
         patch.object(svc, "_setup_symbol"), \
         patch.object(svc, "_round_price", side_effect=lambda s, p: p), \
         patch.object(svc, "get_positions", return_value=[]), \
         patch.object(svc, "_native_trailing_enabled", return_value=False), \
         patch.object(svc, "_safe_create_order", return_value={"orderId": 99, "avgPrice": "50000"}):
        result = svc.place_order(
            symbol="BTCUSDT",
            direction="BUY",
            action="open",
            quantity=0.01,
            price=50_000.0,
        )
    assert result.get("reason") != "live Binance orders disabled unless TRADING_MODE=live"
    svc._get_client.assert_called()


def test_sync_and_emergency_skipped_when_not_live_binance(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("ACTIVE_BROKER", "binance_futures")
    loop = _loop()
    assert loop._is_live_binance() is False


def test_paper_cycle_positions_shape(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    loop = _loop()
    pos = SimpleNamespace(
        symbol="ETHUSDT", side="long", quantity=0.5,
        avg_price=2000.0, unrealized_pnl=10.0, current_price=2020.0,
    )
    with patch(
        "backend.services.unified_trading.UnifiedTrading.get_paper_positions",
        return_value=[pos],
    ):
        cached = loop._paper_cycle_positions()
    assert cached == [{
        "symbol": "ETHUSDT",
        "side": "BUY",
        "quantity": 0.5,
        "entry_price": 2000.0,
        "unrealized_pnl": 10.0,
        "mark_price": 2020.0,
    }]
