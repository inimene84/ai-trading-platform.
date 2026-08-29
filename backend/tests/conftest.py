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
