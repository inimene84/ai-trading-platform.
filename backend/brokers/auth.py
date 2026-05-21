"""
cTrader Open API OAuth 2.0 helper.

Spotware OAuth endpoints:
  Authorize: https://connect.spotware.com/apps/auth
  Token:     https://connect.spotware.com/apps/token

Full OAuth flow (one-time setup):
  1. Build the authorize URL (see get_auth_url())
  2. Open it in a browser, log in with your cTID
  3. Copy the `code` from the redirect URL
  4. Call exchange_code_for_token(code) → saves tokens to .env / file

Afterwards, use refresh_access_token() to renew silently.
"""

import os
import json
import httpx
from urllib.parse import urlencode
from pathlib import Path
from dotenv import load_dotenv, set_key

load_dotenv()

SPOTWARE_AUTH_URL = "https://connect.spotware.com/apps/auth"
SPOTWARE_TOKEN_URL = "https://connect.spotware.com/apps/token"
ENV_FILE = Path(".env")


def get_auth_url(
    client_id: str | None = None,
    redirect_uri: str = "https://localhost/callback",
    scope: str = "trading",
) -> str:
    """
    Build the OAuth authorization URL.
    Open this URL in a browser to get an authorization code.
    """
    client_id = client_id or os.getenv("CTRADER_CLIENT_ID")
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "response_type": "code",
    }
    return f"{SPOTWARE_AUTH_URL}?{urlencode(params)}"


def exchange_code_for_token(
    code: str,
    redirect_uri: str = "https://localhost/callback",
    client_id: str | None = None,
    client_secret: str | None = None,
    save_to_env: bool = True,
) -> dict:
    """
    Exchange authorization code for access + refresh tokens.
    Optionally writes them back to .env.
    """
    client_id = client_id or os.getenv("CTRADER_CLIENT_ID")
    client_secret = client_secret or os.getenv("CTRADER_CLIENT_SECRET")

    resp = httpx.post(
        SPOTWARE_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    resp.raise_for_status()
    tokens = resp.json()

    if save_to_env and ENV_FILE.exists():
        set_key(str(ENV_FILE), "CTRADER_ACCESS_TOKEN", tokens["access_token"])
        set_key(str(ENV_FILE), "CTRADER_REFRESH_TOKEN", tokens.get("refresh_token", ""))
        print("Tokens saved to .env")

    return tokens


def refresh_access_token(
    refresh_token: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    save_to_env: bool = True,
) -> dict:
    """
    Use refresh token to get a new access token without user interaction.
    Call this on startup if the access token may have expired.
    """
    refresh_token = refresh_token or os.getenv("CTRADER_REFRESH_TOKEN")
    client_id = client_id or os.getenv("CTRADER_CLIENT_ID")
    client_secret = client_secret or os.getenv("CTRADER_CLIENT_SECRET")

    resp = httpx.post(
        SPOTWARE_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    resp.raise_for_status()
    tokens = resp.json()

    if save_to_env and ENV_FILE.exists():
        set_key(str(ENV_FILE), "CTRADER_ACCESS_TOKEN", tokens["access_token"])
        if "refresh_token" in tokens:
            set_key(str(ENV_FILE), "CTRADER_REFRESH_TOKEN", tokens["refresh_token"])

    return tokens


# ── CLI helper ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) == 1:
        print("Auth URL (open in browser):")
        print(get_auth_url())
        print("\nThen run:  python -m src.brokers.auth <code>")
    elif sys.argv[1] == "refresh":
        tokens = refresh_access_token()
        print("Refreshed:", json.dumps(tokens, indent=2))
    else:
        code = sys.argv[1]
        tokens = exchange_code_for_token(code)
        print("Tokens:", json.dumps(tokens, indent=2))
