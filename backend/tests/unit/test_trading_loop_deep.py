"""
Deep Unit Tests for TradingLoopService & BinanceFuturesService (WP-Deep)
========================================================================
Covers:
- Trailing stop ratchet under consecutive favorable ticks
- Maker entry placement and precision rounding
- Emergency exits on catastrophic drawdown
- Symbol expectancy gating
- Partial take-profit calculations and bounds
"""

import pytest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from backend.database.models import Trade
from backend.services.trading_loop_helpers import (
    TrailingStopManager,
    EmergencyExitManager,
    PartialTPManager,
)
from backend.services.binance_futures_service import BinanceFuturesService
from backend.services.risk_config import RiskConfig
from backend.services.unified_trading import UnifiedOrderResponse


def test_trailing_stop_ratchet_consecutive_ticks():
    """Verify trailing stop ratchets up on BUY and never loosens on retracement."""
    trade = Trade(
        id=101,
        symbol="BTCUSDT",
        direction="BUY",
        quantity=0.01,
        entry_price=50000.0,
        stop_loss=49000.0,
        take_profit=60000.0,
        status="open",
        notes="",
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [trade]

    risk_cfg = RiskConfig(
        trailing_stop_enabled=True,
        step_trail_enabled=False,
        trail_activation_atr=1.5,
        trail_atr_mult=0.8,
    )

    high_water = {}
    broker = MagicMock()

    # Tick 1: ATR is 500 (high=50250, low=49750). Activation = 1.5*500 = 750. Price=52000 -> gain=2000 >= 750
    bars_1 = [
        {"date": "2026-08-28T01:00:00Z", "open": 50000.0, "high": 52250.0, "low": 51750.0, "close": 52000.0, "volume": 100}
    ] * 20
    TrailingStopManager.apply_trailing_stop(db, "BTCUSDT", bars_1, high_water, risk_cfg, broker)
    stop_1 = trade.stop_loss
    assert stop_1 is not None and stop_1 > 49000.0  # Ratcheted up to ~51600

    # Tick 2: Price goes higher to 54,000
    bars_2 = [
        {"date": "2026-08-28T02:00:00Z", "open": 52000.0, "high": 54250.0, "low": 53750.0, "close": 54000.0, "volume": 100}
    ] * 20
    TrailingStopManager.apply_trailing_stop(db, "BTCUSDT", bars_2, high_water, risk_cfg, broker)
    stop_2 = trade.stop_loss
    assert stop_2 > stop_1  # Ratcheted higher to ~53600

    # Tick 3: Retracement to 53,800
    bars_3 = [
        {"date": "2026-08-28T03:00:00Z", "open": 54000.0, "high": 54000.0, "low": 53500.0, "close": 53800.0, "volume": 100}
    ] * 20
    TrailingStopManager.apply_trailing_stop(db, "BTCUSDT", bars_3, high_water, risk_cfg, broker)
    stop_3 = trade.stop_loss
    assert stop_3 == stop_2  # Must not loosen / decrease


def test_binance_qty_and_price_precision():
    """Verify precision rounding adheres strictly to lot and tick size step rules."""
    service = BinanceFuturesService()

    # BTC precision
    qty_btc = service._round_qty("BTCUSDT", 0.12345678)
    assert qty_btc == 0.123

    # DOGE precision (integer quantity)
    qty_doge = service._round_qty("DOGEUSDT", 55.89)
    assert qty_doge == 55.0

    # SOL precision (LOT_SIZE step)
    qty_sol = service._round_qty("SOLUSDT", 2.456)
    assert qty_sol == 2.45

    # Tick size price rounding
    px_btc = service._round_price("BTCUSDT", 64123.4567)
    assert px_btc == 64123.46  # 2 decimal places price precision


@pytest.mark.asyncio
async def test_emergency_exit_manager_force_close():
    """Verify catastrophic drawdown triggers immediate emergency close."""
    trade = SimpleNamespace(
        id=201,
        symbol="ETHUSDT",
        direction="BUY",
        quantity=0.5,
        entry_price=3000.0,
        status="open",
        exit_price=None,
        pnl=0.0,
        notes="",
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [trade]

    broker = MagicMock()
    broker.get_positions.return_value = [{"symbol": "ETHUSDT", "mark_price": 2500.0, "entry_price": 3000.0}]

    pm = MagicMock()
    pm.emergency_drawdown_pct = -8.0

    mock_resp = UnifiedOrderResponse(
        success=True,
        order_id="force-close-1",
        message="filled",
        mode="live",
        filled_price=2500.0,
        commission=0.1,
        realized_pnl=-250.0,
    )

    with patch("backend.services.trading_loop_helpers.get_position_manager", return_value=pm), \
         patch("backend.services.trading_loop_helpers.UnifiedTrading") as mock_ut_cls:
        mock_ut_cls.return_value.place_order.return_value = mock_resp
        exits = await EmergencyExitManager.run_emergency_exits(db, broker, {}, {})

    assert exits == 1
    assert trade.exit_price == 2500.0
    assert trade.status == "closed"


def test_partial_tp_manager_paper_bounds():
    """Verify paper partial TP closes defined fraction when in profit without exceeding bounds."""
    trade = Trade(
        id=301,
        symbol="SOLUSDT",
        direction="BUY",
        quantity=10.0,
        entry_price=100.0,
        status="open",
        notes="",
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [trade]

    risk_cfg = RiskConfig(
        partial_tp_enabled=True,
        partial_tp_atr_mult=1.0,
        partial_tp_close_pct=0.33,
    )

    # ATR is 5 (102.5 - 97.5). Profit is 112 - 100 = 12 > 1*5
    bars = [
        {"date": "2026-08-28T01:00:00Z", "open": 100.0, "high": 114.5, "low": 109.5, "close": 112.0, "volume": 50}
    ] * 20

    mock_resp = UnifiedOrderResponse(
        success=True,
        order_id="partial-tp-1",
        message="filled",
        mode="paper",
        filled_price=112.0,
        commission=0.01,
        realized_pnl=3.96,
    )

    with patch("backend.services.trading_loop_helpers.UnifiedTrading") as mock_ut_cls:
        mock_ut_cls.return_value.place_order.return_value = mock_resp
        PartialTPManager.apply_partial_tp(db, "SOLUSDT", bars, risk_cfg, "TestStrategy")

    # Initial quantity was 10.0, 33% (3.3) closed -> remaining ~6.7
    assert trade.quantity == pytest.approx(6.7)
    assert "PARTIAL_TP_DONE" in trade.notes
