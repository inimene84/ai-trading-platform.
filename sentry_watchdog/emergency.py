"""Standalone emergency helpers for the sentry_watchdog container."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


def _state_path() -> Path:
    base = Path(os.getenv("SENTRY_STATE_DIR", "/app/data/sentry"))
    base.mkdir(parents=True, exist_ok=True)
    return base / "trading_status.json"


def write_halt_file(*, reason: str, source: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "status": "HALTED_BY_SENTRY",
        "reason": reason,
        "halted_at": now,
        "halted_by": source,
        "resumed_at": None,
        "updated_at": now,
    }
    path = _state_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)
    return payload


def cancel_binance_orders() -> dict[str, Any]:
    broker = os.getenv("ACTIVE_BROKER", "ctrader")
    if broker != "binance_futures":
        return {"skipped": True, "reason": f"broker={broker}"}
    if os.getenv("PAPER_TRADING", "false").lower() == "true":
        return {"skipped": True, "reason": "paper_trading"}
    if os.getenv("BINANCE_DRY_RUN", "false").lower() == "true":
        return {"skipped": True, "reason": "dry_run"}

    api_key = os.getenv("BINANCE_API_KEY", "").strip()
    secret = os.getenv("BINANCE_SECRET_KEY", "").strip()
    if not api_key or not secret:
        return {"skipped": True, "reason": "missing_binance_keys"}

    from binance.client import Client

    testnet = os.getenv("BINANCE_TESTNET", "false").lower() == "true"
    client = Client(api_key, secret, testnet=testnet)

    symbols: set[str] = set()
    try:
        for order in client.futures_get_open_orders():
            sym = order.get("symbol")
            if sym:
                symbols.add(sym)
        algo = client.futures_get_open_algo_orders()
        algo_list = algo if isinstance(algo, list) else algo.get("orders", [])
        for order in algo_list:
            sym = order.get("symbol")
            if sym:
                symbols.add(sym)
    except Exception as exc:
        return {"error": str(exc), "symbols_cancelled": 0}

    cancelled = 0
    errors: list[str] = []
    for sym in sorted(symbols):
        try:
            client.futures_cancel_all_open_orders(symbol=sym)
            algo = client.futures_get_open_algo_orders()
            algo_list = algo if isinstance(algo, list) else algo.get("orders", [])
            for order in algo_list:
                if order.get("symbol") == sym and order.get("algoId"):
                    try:
                        client.futures_cancel_algo_order(algoId=order["algoId"])
                    except Exception:
                        pass
            cancelled += 1
        except Exception as exc:
            errors.append(f"{sym}: {exc}")

    return {"symbols_seen": len(symbols), "symbols_cancelled": cancelled, "errors": errors}


async def send_telegram(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
            return resp.is_success
    except Exception:
        return False


async def call_backend_emergency_halt(base_url: str, token: str, reason: str, source: str) -> bool:
    url = f"{base_url.rstrip('/')}/sentry/emergency-halt"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Sentry-Token"] = token
    payload = {"reason": reason, "source": source, "manual": False}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            return resp.is_success
    except Exception:
        return False


async def fetch_sentry_status(base_url: str) -> dict[str, Any] | None:
    url = f"{base_url.rstrip('/')}/sentry/status"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            if resp.is_success:
                data = resp.json()
                return data if isinstance(data, dict) else None
    except Exception:
        pass
    return None


async def call_backend_auto_resume(base_url: str, token: str, source: str) -> dict[str, Any] | None:
    url = f"{base_url.rstrip('/')}/sentry/auto-resume"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Sentry-Token"] = token
    payload = {"source": source}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.is_success:
                data = resp.json()
                return data if isinstance(data, dict) else {"ok": True}
            return {"ok": False, "status_code": resp.status_code, "body": resp.text[:200]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def read_local_halt_status() -> str | None:
    path = _state_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("status")
    except Exception:
        return "HALTED_BY_SENTRY"


def seconds_since_local_halt() -> float | None:
    path = _state_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        halted_at = data.get("halted_at")
        if not halted_at:
            return None
        parsed = datetime.fromisoformat(halted_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - parsed).total_seconds()
    except Exception:
        return None
