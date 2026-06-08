"""Telemetry proxy routes — keep secrets server-side where possible."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse
import logging
import os

import httpx

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/telemetry", tags=["telemetry"])


@router.post("/influx")
async def write_influx(payload: dict):
    """
    Proxy a line-protocol write to InfluxDB.

    Prefer server env vars when the client omits credentials; fall back to the
    request body for dashboard-local dev setups.
    """
    url = payload.get("url") or os.getenv("INFLUXDB_URL", "")
    token = payload.get("token") or os.getenv("INFLUXDB_TOKEN", "")
    org = payload.get("org") or os.getenv("INFLUXDB_ORG", "-")
    bucket = payload.get("bucket") or os.getenv("INFLUXDB_BUCKET", "trading-memory")
    data = payload.get("data") or {}

    if not url or not token:
        return JSONResponse(status_code=400, content={"error": "InfluxDB url/token not configured"})

    # Build a minimal trade line from the monitoring payload
    symbol = data.get("symbol", "unknown")
    side = str(data.get("side", "")).lower()
    price = float(data.get("price", 0))
    qty = float(data.get("quantity", 0))
    success = "true" if data.get("success") else "false"
    broker = data.get("broker", "unknown")
    line = (
        f"frontend_trade,symbol={symbol},side={side},broker={broker},success={success} "
        f"price={price},quantity={qty}"
    )
    write_url = f"{url.rstrip('/')}/api/v2/write?org={org}&bucket={bucket}&precision=ms"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                write_url,
                content=line,
                headers={"Authorization": f"Token {token}", "Content-Type": "text/plain"},
            )
        if resp.status_code not in (200, 204):
            return JSONResponse(status_code=resp.status_code, content={"error": resp.text})
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Influx telemetry proxy error: {e}")
        return JSONResponse(status_code=502, content={"error": str(e)})


@router.post("/telegram")
async def send_telegram(payload: dict):
    """Send a trade alert via Telegram Bot API."""
    token = payload.get("token") or os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = payload.get("chatId") or os.getenv("TELEGRAM_CHAT_ID", "")
    text = payload.get("text", "")

    if not token or not chat_id:
        return JSONResponse(status_code=400, content={"error": "Telegram token/chatId not configured"})
    if not text:
        return JSONResponse(status_code=400, content={"error": "text is required"})

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(api_url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
            })
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"raw": resp.text}
        if not resp.is_success:
            return JSONResponse(status_code=resp.status_code, content=body)
        return {"status": "ok", "result": body.get("result")}
    except Exception as e:
        logger.error(f"Telegram telemetry proxy error: {e}")
        return JSONResponse(status_code=502, content={"error": str(e)})


@router.post("/n8n")
async def trigger_n8n(payload: dict):
    """Forward an event to an n8n webhook URL."""
    webhook_url = payload.get("webhookUrl") or os.getenv("N8N_WEBHOOK_URL", "")
    event = payload.get("event", "event")
    body = payload.get("payload") or {}

    if not webhook_url:
        return JSONResponse(status_code=400, content={"error": "webhookUrl not configured"})

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(webhook_url, json={"event": event, **body})
        if not resp.is_success:
            return JSONResponse(status_code=resp.status_code, content={"error": resp.text})
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"n8n telemetry proxy error: {e}")
        return JSONResponse(status_code=502, content={"error": str(e)})
