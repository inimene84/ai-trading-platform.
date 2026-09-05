"""
Economic Calendar REST Routes
─────────────────────────────
Endpoints for querying economic calendar events and checking symbol blackout status.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.database.connection import SessionLocal
from backend.services.calendar_service import calendar_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/calendar", tags=["calendar"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class CalendarEventItem(BaseModel):
    time_utc: datetime
    currency: str
    impact: str = Field(..., description="HIGH, MEDIUM, LOW, NONE")
    title: str
    actual: Optional[str] = None
    forecast: Optional[str] = None
    previous: Optional[str] = None
    source: Optional[str] = "forexfactory"


class BatchCalendarIngestPayload(BaseModel):
    events: List[CalendarEventItem]


@router.get("")
@router.get("/")
def get_upcoming_events(
    currency: Optional[str] = Query(default=None, description="Optional currency filter: USD, EUR, etc."),
    hours_ahead: int = Query(default=24, ge=1, le=168),
    min_impact: str = Query(default="HIGH", description="HIGH or MEDIUM"),
    db: Session = Depends(get_db),
):
    """Retrieve upcoming calendar events within the given window."""
    try:
        currencies = [currency.upper().strip()] if currency else None
        events = calendar_service.get_upcoming_events(
            currencies=currencies,
            hours_ahead=hours_ahead,
            min_impact=min_impact.upper(),
            db=db,
        )
        return {
            "status": "ok",
            "count": len(events),
            "events": events,
            "is_stale": calendar_service.is_stale(db=db),
        }
    except Exception as e:
        logger.error(f"Error retrieving calendar events: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/check/{symbol}")
def check_symbol_blackout(
    symbol: str,
    pre_window_min: int = Query(default=30, ge=1, le=120),
    post_window_min: int = Query(default=15, ge=1, le=60),
    db: Session = Depends(get_db),
):
    """Check if the given symbol is currently blocked by a macro event blackout."""
    try:
        is_blocked, active_event, reason = calendar_service.check_blackout(
            symbol=symbol.upper(),
            pre_window_min=pre_window_min,
            post_window_min=post_window_min,
            db=db,
        )
        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "is_blackout": is_blocked,
            "reason": reason,
            "active_event": active_event,
        }
    except Exception as e:
        logger.error(f"Error checking blackout for {symbol}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/batch")
def ingest_batch_events(
    payload: BatchCalendarIngestPayload = Body(...),
    db: Session = Depends(get_db),
):
    """Ingest a list of calendar events (e.g. from n8n or scheduled scraper)."""
    try:
        recorded = []
        for ev in payload.events:
            rec = calendar_service.record_event(
                time_utc=ev.time_utc,
                currency=ev.currency,
                impact=ev.impact,
                title=ev.title,
                actual=ev.actual,
                forecast=ev.forecast,
                previous=ev.previous,
                source=ev.source or "forexfactory",
                db=db,
            )
            recorded.append(rec.id)

        return {
            "status": "ok",
            "count": len(recorded),
            "event_ids": recorded,
        }
    except Exception as e:
        logger.error(f"Error ingesting calendar batch: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
