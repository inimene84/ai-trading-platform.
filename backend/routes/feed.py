"""Unified multi-asset feed routes (crypto + equities + metals + forex)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from backend.services.feed_scheduler import feed_scheduler
from backend.services.multi_asset_bars import AssetClass
from backend.services.unified_feed import unified_feed

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feed", tags=["feed"])

_VALID_ASSET_CLASSES = {"crypto", "forex", "metal", "oil", "stock", "index"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get("/overview")
async def get_feed_overview():
    """Quotes for the full default universe, grouped by asset class."""
    try:
        universe = unified_feed.default_universe()
        crypto, equities, metals = await asyncio.gather(
            unified_feed.get_quotes(universe["crypto"]),
            unified_feed.get_quotes(universe["equities"]),
            unified_feed.get_quotes(universe["metals"]),
        )
        return JSONResponse(content={
            "as_of": _utc_now_iso(),
            "crypto": crypto,
            "equities": equities,
            "metals": metals,
        })
    except Exception as e:
        logger.error("Feed overview error: %s", e)
        return JSONResponse(content={
            "as_of": _utc_now_iso(),
            "crypto": [], "equities": [], "metals": [], "error": str(e),
        })


@router.get("/quotes")
async def get_feed_quotes(
    symbols: Optional[str] = Query(default=None, description="Comma-separated symbols"),
    asset_class: Optional[str] = Query(default=None, description="crypto|forex|metal|oil|stock|index"),
):
    """Quotes for an explicit symbol list and/or a single asset class."""
    symbol_list = None
    if symbols:
        symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    ac = asset_class.strip().lower() if asset_class else None
    if ac is not None and ac not in _VALID_ASSET_CLASSES:
        return JSONResponse(status_code=400, content={
            "as_of": _utc_now_iso(), "quotes": [],
            "error": f"invalid asset_class '{asset_class}'",
        })
    typed_ac: Optional[AssetClass] = ac  # type: ignore[assignment]
    try:
        quotes = await unified_feed.get_quotes(symbols=symbol_list, asset_class=typed_ac)
        return JSONResponse(content={"as_of": _utc_now_iso(), "quotes": quotes})
    except Exception as e:
        logger.error("Feed quotes error: %s", e)
        return JSONResponse(content={"as_of": _utc_now_iso(), "quotes": [], "error": str(e)})


@router.get("/bars")
async def get_feed_bars(symbol: str, timeframe: str = "1h", limit: int = 100):
    """OHLCV bars passthrough from the unified feed (DataHub-cached 60s)."""
    try:
        payload = await unified_feed.get_bars(symbol=symbol, timeframe=timeframe, limit=limit)
        return JSONResponse(content=payload)
    except Exception as e:
        logger.error("Feed bars error for %s: %s", symbol, e)
        return JSONResponse(content={
            "as_of": _utc_now_iso(), "symbol": symbol.upper(), "data": [], "error": str(e),
        })


@router.get("/scheduler/status")
async def get_feed_scheduler_status():
    """Cron scheduler status (jobs, cron expressions, last/next run)."""
    try:
        status = feed_scheduler.status()
        status["as_of"] = _utc_now_iso()
        return JSONResponse(content=status)
    except Exception as e:
        logger.error("Feed scheduler status error: %s", e)
        return JSONResponse(content={
            "as_of": _utc_now_iso(), "enabled": False, "jobs": [], "error": str(e),
        })
