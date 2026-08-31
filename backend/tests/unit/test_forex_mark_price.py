"""Regression tests for forex mark price routing on the dashboard."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.routes.trading import (
    _coalesce_mark_price,
    _is_ctrader_symbol,
    _is_ctrader_trade,
    get_positions,
)


def test_is_ctrader_symbol_detects_fx_pairs():
    assert _is_ctrader_symbol("EURUSD") is True
    assert _is_ctrader_symbol("BTCUSDT") is False


def test_is_ctrader_trade_without_broker_field():
    trade = SimpleNamespace(symbol="USDJPY", broker=None, exchange=None, broker_position_id=None)
    assert _is_ctrader_trade(trade) is True


def test_coalesce_mark_price_never_returns_zero_when_entry_exists():
    assert _coalesce_mark_price(0.0, 1.1615) == pytest.approx(1.1615)
    assert _coalesce_mark_price(None, 159.78) == pytest.approx(159.78)


@pytest.mark.asyncio
async def test_dashboard_positions_use_ctrader_mark_not_binance():
    trade = SimpleNamespace(
        id=1,
        symbol="EURUSD",
        direction="BUY",
        quantity=0.01,
        entry_price=1.1615,
        stop_loss=1.1605,
        take_profit=1.1625,
        strategy="MOMENTUM_TREND_PULSE",
        broker=None,
        broker_position_id=None,
        timestamp=None,
        exchange=None,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [trade]

    with patch("backend.routes.trading.SessionLocal", return_value=db), patch(
        "backend.routes.trading.ctrader_broker"
    ) as broker, patch(
        "backend.routes.trading.upsert_ctrader_live_trades", return_value=0
    ), patch(
        "backend.routes.trading._fetch_mark_prices_for_symbols", return_value={}
    ) as fetch_marks:
        broker.get_positions.return_value = []
        broker.get_mark_price.return_value = 1.1618
        broker.quote_to_usd_rate.return_value = 1.0
        broker.CONTRACT_UNITS_PER_LOT = 100_000
        broker.ensure_spot_quotes.return_value = None
        result = await get_positions()

    fetch_marks.assert_called_once_with(set())
    pos = result["positions"][0]
    assert pos["broker"] == "ctrader"
    assert pos["current_price"] == pytest.approx(1.1618)
    assert pos["unrealized_pnl_pct"] != pytest.approx(-100.0)


@pytest.mark.asyncio
async def test_price_endpoint_routes_forex_to_ctrader():
    from backend.routes.trading import get_price

    with patch("backend.routes.trading.ctrader_broker") as broker:
        broker.get_mark_price.return_value = 1.1617
        broker.ensure_spot_quotes.return_value = None
        out = await get_price("EURUSD")

    assert out["source"] == "ctrader"
    assert out["price"] == pytest.approx(1.1617)
