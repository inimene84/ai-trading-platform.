"""Research proxy for Grok bots and n8n — forwards to the Scrapling sidecar."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, Field

from backend.security import validate_admin_request
from scrapling_sidecar.url_guard import is_public_http_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/research", tags=["research"])

SCRAPLING_RESEARCH_URL = os.getenv("SCRAPLING_RESEARCH_URL", "http://ai-trading-scrapling:8080").rstrip("/")
ADMIN_API_KEY = (os.getenv("ADMIN_API_KEY") or "").strip()


class ResearchFetchRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=2000)
    mode: str = Field(default="http", description="http or stealth")
    extraction_type: str = Field(default="markdown", description="markdown, html, or text")
    css_selector: Optional[str] = None


def _sidecar_headers() -> Dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if ADMIN_API_KEY:
        headers["Authorization"] = f"Bearer {ADMIN_API_KEY}"
        headers["X-API-Key"] = ADMIN_API_KEY
    return headers


@router.get("/health")
async def research_health() -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(f"{SCRAPLING_RESEARCH_URL}/health")
        return {"status": "ok" if response.is_success else "down"}
    except Exception:
        logger.exception("scrapling health check failed")
        return {"status": "down"}


@router.post("/fetch")
async def research_fetch(request: Request, payload: ResearchFetchRequest = Body(...)) -> Dict[str, Any]:
    """Fetch a public page through Scrapling and return extracted content."""
    validate_admin_request(request)
    if not is_public_http_url(payload.url):
        raise HTTPException(status_code=400, detail="url must be a public http(s) host")
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(
                f"{SCRAPLING_RESEARCH_URL}/fetch",
                json=payload.model_dump(),
                headers=_sidecar_headers(),
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Scrapling sidecar unreachable: {exc}") from exc

    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="fetch failed")
    return response.json()
