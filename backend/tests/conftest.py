import os

import pytest

@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def auth_headers():
    """Admin token from env so POSTs pass when the VPS/container has auth enabled."""
    key = (
        os.getenv("ADMIN_API_KEY")
        or os.getenv("API_AUTH_TOKEN")
        or os.getenv("BACKEND_API_KEY")
        or ""
    ).strip()
    return {"X-API-Key": key} if key else {}


@pytest.fixture(autouse=True)
def clean_trading_mode_for_tests(monkeypatch):
    """Ensure TRADING_MODE from .env does not block unit tests of live order methods.

    Tests that specifically test paper mode (e.g. test_binance_paper_mode.py)
    will monkeypatch TRADING_MODE to 'paper'.
    """
    if os.getenv("TRADING_MODE") == "paper":
        monkeypatch.delenv("TRADING_MODE", raising=False)
