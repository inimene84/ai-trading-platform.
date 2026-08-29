"""
Unit tests for cTrader Open API Service & Token Lifecycle Manager.
Verifies BrokerService protocol adherence, symbol mapping, lot conversions, and paper simulation.
"""
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from backend.brokers.base import BrokerService
from backend.services.ctrader_service import CTraderService, ctrader_broker
from backend.services.ctrader_tokens import CTraderTokenStore


def test_ctrader_implements_broker_service_protocol():
    """Verify CTraderService strictly satisfies the BrokerService protocol."""
    svc = CTraderService()
    assert isinstance(svc, BrokerService)
    assert hasattr(svc, "connect")
    assert hasattr(svc, "disconnect")
    assert hasattr(svc, "get_balance")
    assert hasattr(svc, "get_positions")
    assert hasattr(svc, "place_order")
    assert hasattr(svc, "cancel_order")
    assert hasattr(svc, "close_position")
    assert hasattr(svc, "status")


def test_symbol_normalization():
    """Test mapping of generic/yfinance tickers to canonical cTrader pairs."""
    svc = CTraderService()
    assert svc._normalize_symbol("EURUSD=X") == "EURUSD"
    assert svc._normalize_symbol("GBPUSD=X") == "GBPUSD"
    assert svc._normalize_symbol("BTC-USD") == "BTCUSD"
    assert svc._normalize_symbol("ETH-USD") == "ETHUSD"
    assert svc._normalize_symbol("USDJPY=X") == "USDJPY"
    assert svc._normalize_symbol("EURUSD") == "EURUSD"


def test_paper_order_execution():
    """Verify order placement in default paper/dry-run mode."""
    svc = CTraderService()
    assert svc.dry_run is True

    result = svc.place_order(
        symbol="EURUSD=X",
        direction="BUY",
        volume=0.5,
        price=1.0850,
        stop_loss=1.0800,
        take_profit=1.0950,
    )
    assert result["status"] == "simulated"
    assert result["broker"] == "ctrader:paper"
    assert result["symbol"] == "EURUSD"
    assert result["direction"] == "BUY"
    assert result["quantity"] == 0.5
    assert "order_id" in result


def test_get_balance_structure():
    """Verify balance and equity dictionary structure."""
    svc = CTraderService()
    svc.balance = 50000.0
    svc.equity = 50250.0
    svc.margin = 1200.0

    balance = svc.get_balance()
    assert balance["balance"] == 50000.0
    assert balance["equity"] == 50250.0
    assert balance["margin_used"] == 1200.0
    assert balance["available"] == 48800.0
    assert balance["broker"] == "ctrader"


def test_positions_tracking_and_close():
    """Verify position reporting and closing."""
    svc = CTraderService()
    svc._positions = [
        {
            "symbol": "EURUSD",
            "side": "BUY",
            "quantity": 1.0,
            "entry_price": 1.0850,
            "unrealized_pnl": 150.0,
            "position_id": "pos_123",
            "broker": "ctrader",
        }
    ]

    positions = svc.get_positions()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "EURUSD"
    assert positions[0]["position_id"] == "pos_123"

    close_res = svc.close_position("pos_123")
    assert close_res["status"] == "closed"
    assert len(svc.get_positions()) == 0


def test_cancel_order_paper():
    """Verify order cancellation in paper mode."""
    svc = CTraderService()
    res = svc.cancel_order("ord_456")
    assert res["success"] is True
    assert res["order_id"] == "ord_456"


def test_status_report():
    """Verify status snapshot contains all required keys."""
    svc = CTraderService()
    st = svc.status()
    assert "connected" in st
    assert "authenticated" in st
    assert "env" in st
    assert "dry_run" in st
    assert "symbols_supported" in st
    assert "balance" in st
    assert "equity" in st


def test_token_store_save_and_load(tmp_path):
    """Verify token store reads and writes persistent token data."""
    store_file = tmp_path / "test_tokens.json"
    store = CTraderTokenStore(file_path=store_file)

    sample_tokens = {
        "access_token": "acc_123",
        "refresh_token": "ref_456",
        "client_id": "cid_789",
        "client_secret": "sec_000",
        "account_id": 46756268,
        "updated_at": 1700000000,
        "expires_in": 2592000,
    }
    store.save_tokens(sample_tokens)

    loaded = store.get_tokens()
    assert loaded["access_token"] == "acc_123"
    assert loaded["refresh_token"] == "ref_456"
    assert loaded["account_id"] == 46756268
