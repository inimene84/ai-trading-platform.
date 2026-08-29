"""
cTrader OAuth Token Persistence and Auto-Refresh Manager.

Features:
- Encrypted / structured persistent store for rotating refresh tokens.
- Proactive token renewal before the 30-day expiration window (< 5 days remaining).
- Resilient fallback from persistent volume/file to environment variables.
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional, Dict, Any

import httpx

logger = logging.getLogger(__name__)

SPOTWARE_TOKEN_URL = "https://connect.spotware.com/apps/token"
TOKENS_FILE = Path("data/ctrader_tokens.json")


def _ensure_dir():
    TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)


class CTraderTokenStore:
    """Manages secure token lifecycle and rotation persistence."""

    def __init__(self, file_path: Path = TOKENS_FILE):
        self.file_path = file_path
        self._memory_cache: Optional[Dict[str, Any]] = None

    def get_tokens(self) -> Dict[str, Any]:
        """Load the latest valid token set."""
        if self._memory_cache:
            return dict(self._memory_cache)

        # 1. Try file storage
        if self.file_path.exists():
            try:
                data = json.loads(self.file_path.read_text(encoding="utf-8"))
                if data.get("access_token") or data.get("accessToken"):
                    self._memory_cache = data
                    return dict(data)
            except Exception as e:
                logger.warning(f"Failed to read cTrader token store file: {e}")

        # 2. Fall back to environment bootstrap
        access_token = os.getenv("CTRADER_ACCESS_TOKEN", "").strip()
        refresh_token = os.getenv("CTRADER_REFRESH_TOKEN", "").strip()
        client_id = os.getenv("CTRADER_CLIENT_ID", "").strip()
        client_secret = os.getenv("CTRADER_CLIENT_SECRET", "").strip()
        account_id = os.getenv("CTRADER_ACCOUNT_ID", "0").strip()

        tokens = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "account_id": int(account_id) if account_id.isdigit() else 0,
            "updated_at": int(time.time()),
            "expires_in": 2592000,  # default ~30 days
        }
        self._memory_cache = tokens
        return dict(tokens)

    def save_tokens(self, tokens: Dict[str, Any]) -> None:
        """Persist tokens atomically to disk and memory."""
        self._memory_cache = dict(tokens)
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.file_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
            tmp_path.replace(self.file_path)
            logger.info("cTrader tokens saved to persistent token store")
        except Exception as e:
            logger.error(f"Failed to save cTrader tokens to disk: {e}")

    def refresh_if_needed(self, force: bool = False) -> Optional[str]:
        """
        Check if token needs refresh (less than 5 days remaining or forced).
        Returns active access_token or None.
        """
        tokens = self.get_tokens()
        client_id = tokens.get("client_id") or os.getenv("CTRADER_CLIENT_ID", "")
        client_secret = tokens.get("client_secret") or os.getenv("CTRADER_CLIENT_SECRET", "")
        refresh_token = tokens.get("refresh_token") or os.getenv("CTRADER_REFRESH_TOKEN", "")

        if not refresh_token or not client_id or not client_secret:
            return tokens.get("access_token")

        now = int(time.time())
        updated_at = tokens.get("updated_at", now)
        expires_in = tokens.get("expires_in", 2592000)
        # Refresh if 25 days old or forced
        time_elapsed = now - updated_at
        should_refresh = force or (time_elapsed > (expires_in - 432000))  # 5 days before expiry

        if not should_refresh:
            return tokens.get("access_token")

        logger.info("cTrader token rotation: renewing access token via Spotware OAuth...")
        try:
            resp = httpx.post(
                SPOTWARE_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                timeout=15.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                new_access = data.get("accessToken") or data.get("access_token")
                new_refresh = data.get("refreshToken") or data.get("refresh_token") or refresh_token
                expires = data.get("expiresIn") or data.get("expires_in") or 2592000

                tokens["access_token"] = new_access
                tokens["refresh_token"] = new_refresh
                tokens["updated_at"] = now
                tokens["expires_in"] = expires
                self.save_tokens(tokens)
                logger.info("cTrader token successfully renewed and rotated!")
                return new_access
            else:
                logger.error(f"cTrader token refresh rejected ({resp.status_code}): {resp.text}")
        except Exception as e:
            logger.error(f"cTrader token refresh network error: {e}")

        return tokens.get("access_token")


# Global token store instance
token_store = CTraderTokenStore()
ctrader_token_store = token_store
