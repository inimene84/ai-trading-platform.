"""cTrader OAuth endpoint resolution (production vs sandbox apps)."""

from __future__ import annotations

import os

# Spotware portal "Sandbox" apps use sandbox-connect; demo/live trading apps use openapi/connect.
SANDBOX_AUTH_URL = "https://sandbox-connect.spotware.com/apps/auth"
SANDBOX_TOKEN_URL = "https://sandbox-connect.spotware.com/apps/token"
PROD_AUTH_URL = "https://connect.spotware.com/apps/auth"
PROD_TOKEN_URL = "https://openapi.ctrader.com/apps/token"
PROD_TOKEN_URL_FALLBACK = "https://connect.spotware.com/apps/token"


def ctrader_env() -> str:
    return os.getenv("CTRADER_ENV", "demo").strip().lower()


def is_sandbox_app() -> bool:
    return ctrader_env() in {"sandbox", "playground"}


def auth_url() -> str:
    return SANDBOX_AUTH_URL if is_sandbox_app() else PROD_AUTH_URL


def token_urls() -> tuple[str, ...]:
    if is_sandbox_app():
        return (SANDBOX_TOKEN_URL,)
    return (PROD_TOKEN_URL, PROD_TOKEN_URL_FALLBACK)


def protobuf_host(is_live: bool = False) -> str:
    """TCP/protobuf host — sandbox and demo both use demo.ctraderapi.com."""
    if is_live:
        return "live.ctraderapi.com"
    return "demo.ctraderapi.com"
