"""
Signal Candidates & Timing Control REST Routes.
Exposes endpoints for market scanning, news correlation, timing queues, and n8n execution orchestration.
"""

from fastapi import APIRouter, HTTPException, Query, Body
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import time
import logging

from backend.services.signal_candidate_engine import (
    signal_candidate_engine,
    TimingMode,
    CandidateStatus,
)

logger = logging.getLogger(__name__)

# Dual-mounted at /signals and /api/signals so nginx /api/backend rewrite
# (dashboard) and n8n/direct /api/signals callers both resolve.
router = APIRouter(prefix="/signals", tags=["signals"])


# ── Request / Response Models ────────────────────────────────────────────────
class MarketScanRequest(BaseModel):
    universe: Optional[List[str]] = Field(
        default=["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "BTCUSDT", "ETHUSDT", "SOLUSDT"],
        description="List of symbols to scan",
    )
    timeframe: str = Field(default="M5", description="Candlestick timeframe (M1, M5, M15, H1)")


class NewsScanRequest(BaseModel):
    lookahead_minutes: int = Field(default=60, ge=5, le=1440)


class ExecuteCandidateRequest(BaseModel):
    candidate_id: str = Field(..., description="Unique ID of the candidate signal")
    force: bool = Field(default=False, description="Override timing window check")


class TimingConfigPayload(BaseModel):
    pre_event_window_min: Optional[int] = Field(default=15, ge=1, le=120)
    at_release_window_sec: Optional[int] = Field(default=45, ge=5, le=300)
    post_reaction_delay_min: Optional[int] = Field(default=2, ge=0, le=60)
    post_reaction_window_min: Optional[int] = Field(default=10, ge=1, le=120)
    max_spread_pips: Optional[float] = Field(default=2.0, ge=0.1, le=20.0)
    max_slippage_pips: Optional[float] = Field(default=1.5, ge=0.1, le=20.0)
    default_risk_pct: Optional[float] = Field(default=0.5, ge=0.1, le=5.0)
    account_equity_override: Optional[float] = Field(default=10000.0, ge=100.0)
    strategies_enabled: Optional[Dict[str, bool]] = None


# ── Endpoints ────────────────────────────────────────────────────────────────
@router.post("/scan-markets")
async def scan_markets(payload: MarketScanRequest = Body(...)):
    """Run technical strategy rules against market data and generate structured candidates."""
    try:
        candidates = await signal_candidate_engine.scan_markets(
            universe=payload.universe,
            timeframe=payload.timeframe,
        )
        return {
            "status": "ok",
            "timeframe": payload.timeframe,
            "candidates_count": len(candidates),
            "candidates": candidates,
            "timestamp": int(time.time()),
        }
    except Exception as e:
        logger.error(f"Market scan error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scan-news")
async def scan_news(payload: NewsScanRequest = Body(...)):
    """Correlate economic calendar and news sentiment to generate event-triggered candidates."""
    try:
        candidates = await signal_candidate_engine.scan_news_and_events(
            lookahead_minutes=payload.lookahead_minutes
        )
        return {
            "status": "ok",
            "lookahead_minutes": payload.lookahead_minutes,
            "candidates_count": len(candidates),
            "candidates": candidates,
            "timestamp": int(time.time()),
        }
    except Exception as e:
        logger.error(f"News scan error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/candidates")
async def get_all_candidates(
    status: Optional[str] = Query(None, description="Filter by status: PENDING, READY, EXECUTED, EXPIRED"),
    broker: Optional[str] = Query(None, description="Filter by broker: ctrader, binance_futures"),
):
    """Retrieve all generated signal candidates in memory."""
    all_cands = list(signal_candidate_engine.candidates.values())

    if status:
        all_cands = [c for c in all_cands if c.get("status") == status.upper()]
    if broker:
        all_cands = [c for c in all_cands if c.get("broker") == broker.lower()]

    # Sort newest first
    all_cands.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    return {
        "count": len(all_cands),
        "candidates": all_cands,
        "timestamp": int(time.time()),
    }


@router.get("/ready-for-execution")
async def get_ready_signals():
    """Polled by n8n or execution workers to get signals currently inside their active timing window."""
    now_ts = int(time.time())
    ready = signal_candidate_engine.get_ready_signals(now_ts)
    return {
        "ready_count": len(ready),
        "signals": ready,
        "server_time": now_ts,
    }


@router.post("/execute-candidate")
async def execute_candidate(payload: ExecuteCandidateRequest = Body(...)):
    """Execute a validated trade candidate through the smart multi-broker router."""
    res = await signal_candidate_engine.execute_candidate(
        candidate_id=payload.candidate_id,
        force=payload.force,
    )
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Execution failed"))
    return res


@router.get("/timing-config")
async def get_timing_config():
    """Get current strategy enabling and execution timing window parameters."""
    return {
        "config": signal_candidate_engine.timing_config,
        "timing_modes": [
            TimingMode.PRE_EVENT,
            TimingMode.AT_RELEASE,
            TimingMode.POST_REACTION,
            TimingMode.BAR_CLOSE,
        ],
    }


@router.post("/timing-config")
async def update_timing_config(payload: TimingConfigPayload = Body(...)):
    """Update execution timing windows, spread gates, and risk parameters."""
    cfg = signal_candidate_engine.timing_config
    if payload.pre_event_window_min is not None:
        cfg["pre_event_window_min"] = payload.pre_event_window_min
    if payload.at_release_window_sec is not None:
        cfg["at_release_window_sec"] = payload.at_release_window_sec
    if payload.post_reaction_delay_min is not None:
        cfg["post_reaction_delay_min"] = payload.post_reaction_delay_min
    if payload.post_reaction_window_min is not None:
        cfg["post_reaction_window_min"] = payload.post_reaction_window_min
    if payload.max_spread_pips is not None:
        cfg["max_spread_pips"] = payload.max_spread_pips
    if payload.max_slippage_pips is not None:
        cfg["max_slippage_pips"] = payload.max_slippage_pips
    if payload.default_risk_pct is not None:
        cfg["default_risk_pct"] = payload.default_risk_pct
    if payload.account_equity_override is not None:
        cfg["account_equity_override"] = payload.account_equity_override
    if payload.strategies_enabled is not None:
        cfg["strategies_enabled"].update(payload.strategies_enabled)

    return {
        "status": "ok",
        "message": "Timing configuration updated successfully",
        "config": cfg,
    }
