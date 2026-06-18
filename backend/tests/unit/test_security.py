import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.security import admin_auth_enabled, is_sensitive_request, validate_admin_request


def _req(method: str, path: str) -> Request:
    scope = {"type": "http", "method": method, "path": path, "headers": []}
    return Request(scope)


def test_admin_auth_disabled_without_env(monkeypatch):
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    assert admin_auth_enabled() is False
    validate_admin_request(_req("POST", "/trading/loop/start"))


def test_sensitive_trading_mutations(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "secret")
    assert is_sensitive_request(_req("POST", "/trading/loop/start")) is True
    assert is_sensitive_request(_req("GET", "/trading/status")) is False
    assert is_sensitive_request(_req("GET", "/trading/strategies")) is False


def test_validate_admin_request_rejects_missing_token(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "secret")
    with pytest.raises(HTTPException) as exc:
        validate_admin_request(_req("POST", "/trading/config/update"))
    assert exc.value.status_code == 401


def test_validate_admin_request_accepts_header(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "secret")
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/trading/loop/stop",
        "headers": [(b"x-api-key", b"secret")],
    }
    validate_admin_request(Request(scope))
