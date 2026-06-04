import hmac
import os
from typing import Iterable

from fastapi import HTTPException, Request, status


AUTH_ENV_VARS = ("ADMIN_API_KEY", "API_AUTH_TOKEN", "BACKEND_API_KEY")
PUBLIC_PATHS = {
    "/",
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
}
SENSITIVE_PREFIXES = (
    "/api-keys",
    "/api/api-keys",
    "/storage",
    "/api/storage",
    "/ollama",
    "/api/ollama",
)
TRADING_PREFIXES = ("/trading", "/api/trading")
PUBLIC_TRADING_PATHS = {
    "/trading/strategies",
    "/api/trading/strategies",
}


def _tokens_from_env(env_names: Iterable[str] = AUTH_ENV_VARS) -> list[str]:
    return [token for name in env_names if (token := os.getenv(name, "").strip())]


def is_sensitive_request(request: Request) -> bool:
    """Return True for endpoints that can mutate state, expose secrets, or control processes."""
    path = request.url.path.rstrip("/") or "/"
    method = request.method.upper()

    if method == "OPTIONS" or path in PUBLIC_PATHS:
        return False

    if path.startswith(SENSITIVE_PREFIXES):
        return True

    if path.startswith(TRADING_PREFIXES):
        return path not in PUBLIC_TRADING_PATHS

    return method not in {"GET", "HEAD"}


def validate_admin_request(request: Request) -> None:
    configured_tokens = _tokens_from_env()
    if not configured_tokens:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sensitive API operations require ADMIN_API_KEY, API_AUTH_TOKEN, or BACKEND_API_KEY to be configured.",
        )

    supplied_token = request.headers.get("x-api-key", "").strip()
    auth_header = request.headers.get("authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        supplied_token = auth_header[7:].strip()

    if not supplied_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing admin API token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not any(hmac.compare_digest(supplied_token, token) for token in configured_tokens):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid admin API token.")
