"""Manual live routes must mutate Binance first and keep SQL reconciled."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.routes.trading import (
    ConfigUpdateRequest,
    LiveOrderRequest,
    ModifyPositionRequest,
    close_position,
    modify_position,
    place_live_order,
    update_config,
)
from backend.services.unified_trading import UnifiedOrderResponse


def _db_with_trade(trade):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = trade
    return db


@pytest.mark.asyncio
async def test_manual_close_sends_reduce_only_exchange_order_before_db_close():
    trade = SimpleNamespace(
        id=1, symbol="ETHUSDT", direction="BUY", quantity=0.1,
        entry_price=100.0, status="open", exit_price=None, pnl=0.0,
        closed_at=None, notes="",
    )
    db = _db_with_trade(trade)
    router = MagicMock()
    router.place_order.return_value = UnifiedOrderResponse(
        True, "close-1", "filled", "live", filled_price=105.0,
        filled_qty=0.1, commission=0.02, realized_pnl=0.6,
    )

    with patch("backend.routes.trading.SessionLocal", return_value=db), \
         patch("backend.routes.trading.UnifiedTrading", return_value=router):
        result = await close_position(1)

    order = router.place_order.call_args.args[0]
    assert order.reduce_only is True
    assert order.side.value == "sell"
    assert trade.status == "closed"
    assert trade.exit_price == 105.0
    assert trade.pnl == pytest.approx(0.58)
    db.commit.assert_called_once()
    assert result["success"] is True


@pytest.mark.asyncio
async def test_manual_close_leaves_db_open_when_exchange_close_fails():
    trade = SimpleNamespace(
        id=1, symbol="ETHUSDT", direction="BUY", quantity=0.1,
        entry_price=100.0, status="open", exit_price=None, pnl=0.0,
        closed_at=None, notes="",
    )
    db = _db_with_trade(trade)
    router = MagicMock()
    router.place_order.return_value = UnifiedOrderResponse(
        False, "", "exchange rejected", "live",
    )

    with patch("backend.routes.trading.SessionLocal", return_value=db), \
         patch("backend.routes.trading.UnifiedTrading", return_value=router):
        with pytest.raises(HTTPException) as exc:
            await close_position(1)

    assert exc.value.status_code == 502
    assert trade.status == "open"
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_modify_position_persists_only_after_exchange_updates():
    trade = SimpleNamespace(
        id=1, symbol="ETHUSDT", direction="BUY", status="open",
        stop_loss=90.0, take_profit=120.0,
    )
    db = _db_with_trade(trade)
    broker = MagicMock()
    broker.replace_stop_loss.return_value = {"status": "replaced"}
    broker.replace_take_profit.return_value = {"status": "replaced"}

    with patch("backend.routes.trading.SessionLocal", return_value=db), \
         patch(
             "backend.services.binance_futures_service.binance_futures_broker",
             broker,
         ):
        result = await modify_position(
            1, ModifyPositionRequest(stop_loss=95.0, take_profit=125.0),
        )

    assert trade.stop_loss == 95.0
    assert trade.take_profit == 125.0
    assert result["success"] is True
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_manual_live_order_records_exchange_fill():
    router = MagicMock()
    router.place_order.return_value = UnifiedOrderResponse(
        True, "entry-1", "filled", "live", filled_price=100.0,
        filled_qty=0.2,
    )
    db = MagicMock()
    db.refresh.side_effect = lambda trade: setattr(trade, "id", 77)

    with patch("backend.routes.trading.SessionLocal", return_value=db), \
         patch("backend.routes.trading.UnifiedTrading", return_value=router):
        result = await place_live_order(LiveOrderRequest(
            symbol="ETHUSDT",
            side="buy",
            quantity=0.2,
            price=100.0,
            stop_loss=90.0,
            take_profit=120.0,
        ))

    trade = db.add.call_args.args[0]
    assert trade.symbol == "ETHUSDT"
    assert trade.status == "filled"
    assert trade.entry_price == 100.0
    assert trade.binance_order_id == "entry-1"
    assert result["trade_id"] == 77
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_config_update_persists_to_env_file(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "USE_RISK_REVIEWER_LLM=true\nENABLE_PERSONAS=false\nOTHER=value\n"
    )
    monkeypatch.setenv("ENV_FILE_PATH", str(env_file))

    result = await update_config(ConfigUpdateRequest(
        use_risk_reviewer_llm=False,
        enable_personas=True,
    ))

    content = env_file.read_text()
    assert "USE_RISK_REVIEWER_LLM=false" in content
    assert "ENABLE_PERSONAS=true" in content
    assert "OTHER=value" in content
    assert result["config"]["use_risk_reviewer_llm"] is False
    assert result["config"]["enable_personas"] is True

