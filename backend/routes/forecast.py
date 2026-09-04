"""Forecast routes: Kronos per-symbol forecasts + latest batch results.

GET /api/forecast/{symbol}      — on-demand Kronos forecast via the unified feed.
GET /api/forecast/batch/latest  — last scheduler-produced batch results.

Fail-graceful convention: provider/sidecar outages return HTTP 200 with a
NEUTRAL payload and an `error` field — never a 500 stack.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from backend.services import kronos_service
from backend.services.feed_scheduler import feed_scheduler
from backend.services.unified_feed import unified_feed

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/forecast", tags=["forecast"])


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Fallback store for tests / alternate writers via `set_batch_latest`.
# The feed scheduler's `kronos_batch` job is the primary source.
BATCH_LATEST: Dict[str, Any] = {"as_of": None, "results": []}


def set_batch_latest(results: List[Dict[str, Any]], as_of: Optional[str] = None) -> None:
    """Store the latest batch forecast results (called by tests / alternate writers)."""
    BATCH_LATEST["as_of"] = as_of or _utc_now_iso()
    BATCH_LATEST["results"] = list(results or [])


def get_batch_latest() -> Dict[str, Any]:
    """Return a copy of the latest batch snapshot: {"as_of", "results"}."""
    try:
        snap = feed_scheduler.get_latest_batch()
        if snap.get("results"):
            return snap
    except Exception:
        logger.debug("Forecast: scheduler batch store unavailable", exc_info=True)
    return {
        "as_of": BATCH_LATEST.get("as_of"),
        "results": list(BATCH_LATEST.get("results") or []),
    }


def _neutral_forecast(symbol: str, interval: str, error: Optional[str]) -> Dict[str, Any]:
    return {
        "symbol": symbol.upper(),
        "interval": interval,
        "as_of": _utc_now_iso(),
        "signal": "NEUTRAL",
        "confidence": 0.0,
        "predicted_close": None,
        "predicted_change_pct": 0.0,
        "cum_change_5_pct": 0.0,
        "cum_change_10_pct": 0.0,
        "reversal_risk": False,
        "model_backend": None,
        "forecast_path": None,
        "error": error,
    }


# NOTE: /batch/latest is registered BEFORE /{symbol} so "batch" is not
# captured as a symbol path parameter.
@router.get("/batch/latest")
async def get_batch_latest_endpoint():
    """Latest batch forecast results produced by the scheduler."""
    try:
        return JSONResponse(content=get_batch_latest())
    except Exception as e:
        logger.error("Batch forecast read error: %s", e)
        return JSONResponse(content={"as_of": None, "results": [], "error": str(e)})


@router.get("/{symbol}")
async def get_forecast(
    symbol: str,
    interval: str = Query(default="1h"),
    pred_len: int = Query(default=10, ge=1, le=20),
    include_path: bool = Query(default=False),
):
    """On-demand Kronos forecast for a symbol using unified-feed bars."""
    try:
        bars_payload = await unified_feed.get_bars(symbol, interval, limit=400)
        bars = (bars_payload or {}).get("data") or []
        result = await kronos_service.predict(
            bars, symbol, interval=interval, include_path=include_path, pred_len=pred_len
        )
        return JSONResponse(content={
            "symbol": symbol.upper(),
            "interval": interval,
            "as_of": _utc_now_iso(),
            "signal": result.get("signal", "NEUTRAL"),
            "confidence": result.get("confidence", 0.0),
            "predicted_close": result.get("predicted_close"),
            "predicted_change_pct": result.get("predicted_change_pct", 0.0),
            "cum_change_5_pct": result.get("cum_change_5_pct", 0.0),
            "cum_change_10_pct": result.get("cum_change_10_pct", 0.0),
            "reversal_risk": result.get("reversal_risk", False),
            "model_backend": result.get("model_backend"),
            "forecast_path": result.get("forecast_path"),
            "error": result.get("error"),
        })
    except Exception as e:
        logger.error("Forecast error for %s: %s", symbol, e)
        return JSONResponse(content=_neutral_forecast(symbol, interval, str(e)))
