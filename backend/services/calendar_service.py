"""
Economic Calendar Service
─────────────────────────
Handles ingestion and querying of macroeconomic calendar events (NFP, FOMC, CPI,
interest rate decisions, etc.) to power the Event-Risk Filter.

Features:
  - Multi-source ingestion (ForexFactory / JBlanked) with deduplication.
  - Currency-to-instrument resolution (e.g. EURUSD maps to EUR and USD; XAUUSD maps to USD).
  - Pre/post event blackout detection (e.g. ±30 min around HIGH impact releases).
  - Fail-closed staleness tracking.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import desc
from sqlalchemy.orm import Session

from backend.database.connection import SessionLocal
from backend.database.models import CalendarEvent

logger = logging.getLogger(__name__)

# Standard currency mappings for instruments
SYMBOL_CURRENCIES = {
    "EURUSD": ["EUR", "USD"],
    "GBPUSD": ["GBP", "USD"],
    "USDJPY": ["USD", "JPY"],
    "USDCHF": ["USD", "CHF"],
    "AUDUSD": ["AUD", "USD"],
    "USDCAD": ["USD", "CAD"],
    "NZDUSD": ["NZD", "USD"],
    "EURGBP": ["EUR", "GBP"],
    "EURJPY": ["EUR", "JPY"],
    "GBPJPY": ["GBP", "JPY"],
    "AUDJPY": ["AUD", "JPY"],
    "EURAUD": ["EUR", "AUD"],
    "XAUUSD": ["USD"],  # Gold priced in USD
    "XAGUSD": ["USD"],  # Silver priced in USD
    "BTCUSDT": ["USD"],
    "ETHUSDT": ["USD"],
    "SOLUSDT": ["USD"],
}

# High-impact keywords that warrant full trading blackout
CRITICAL_EVENT_KEYWORDS = (
    "nonfarm", "payroll", "cpi", "fomc", "fed interest rate", "interest rate",
    "rate decision", "central bank", "ecb", "boe", "boj", "gdp", "employment"
)


class CalendarService:
    def __init__(self):
        self._last_sync_time: Optional[datetime] = None

    @staticmethod
    def get_currencies_for_symbol(symbol: str) -> List[str]:
        """Map symbol to its underlying macro currencies."""
        sym = symbol.upper().strip()
        if sym in SYMBOL_CURRENCIES:
            return SYMBOL_CURRENCIES[sym]
        
        # Heuristic for 6-letter FX pairs: AAA/BBB
        if len(sym) == 6 and sym.isalpha():
            return [sym[:3], sym[3:]]
        
        # Crypto USDT pairs map to USD macro
        if sym.endswith("USDT") or sym.endswith("USD"):
            return ["USD"]

        return []

    def record_event(
        self,
        time_utc: datetime,
        currency: str,
        impact: str,
        title: str,
        actual: Optional[str] = None,
        forecast: Optional[str] = None,
        previous: Optional[str] = None,
        source: str = "forexfactory",
        db: Optional[Session] = None
    ) -> CalendarEvent:
        """Record or update an individual calendar event."""
        should_close = False
        if db is None:
            db = SessionLocal()
            should_close = True

        try:
            if time_utc.tzinfo is None:
                time_utc = time_utc.replace(tzinfo=timezone.utc)

            curr = currency.upper().strip()
            norm_impact = impact.upper().strip()
            if norm_impact not in ("HIGH", "MEDIUM", "LOW", "NONE"):
                norm_impact = "HIGH" if "high" in impact.lower() else "MEDIUM"

            # Check if event already exists
            existing = db.query(CalendarEvent).filter(
                CalendarEvent.currency == curr,
                CalendarEvent.time_utc == time_utc,
                CalendarEvent.title == title.strip()
            ).first()

            if existing:
                existing.actual = actual or existing.actual
                existing.forecast = forecast or existing.forecast
                existing.previous = previous or existing.previous
                existing.impact = norm_impact
                db.commit()
                db.refresh(existing)
                return existing

            new_event = CalendarEvent(
                time_utc=time_utc,
                currency=curr,
                impact=norm_impact,
                title=title.strip(),
                actual=actual,
                forecast=forecast,
                previous=previous,
                source=source,
            )
            db.add(new_event)
            db.commit()
            db.refresh(new_event)
            return new_event
        finally:
            if should_close:
                db.close()

    def get_upcoming_events(
        self,
        currencies: Optional[List[str]] = None,
        hours_ahead: int = 24,
        hours_behind: int = 2,
        min_impact: str = "HIGH",
        db: Optional[Session] = None
    ) -> List[Dict[str, Any]]:
        """Query calendar events in a window around now."""
        should_close = False
        if db is None:
            db = SessionLocal()
            should_close = True

        try:
            now_utc = datetime.now(timezone.utc)
            start_time = now_utc - timedelta(hours=hours_behind)
            end_time = now_utc + timedelta(hours=hours_ahead)

            query = db.query(CalendarEvent).filter(
                CalendarEvent.time_utc >= start_time,
                CalendarEvent.time_utc <= end_time
            )

            if currencies:
                curr_upper = [c.upper() for c in currencies]
                query = query.filter(CalendarEvent.currency.in_(curr_upper))

            if min_impact == "HIGH":
                query = query.filter(CalendarEvent.impact == "HIGH")
            elif min_impact == "MEDIUM":
                query = query.filter(CalendarEvent.impact.in_(["HIGH", "MEDIUM"]))

            events = query.order_by(CalendarEvent.time_utc.asc()).all()

            results = []
            for ev in events:
                results.append({
                    "id": ev.id,
                    "time_utc": ev.time_utc.isoformat() if ev.time_utc else None,
                    "currency": ev.currency,
                    "impact": ev.impact,
                    "title": ev.title,
                    "actual": ev.actual,
                    "forecast": ev.forecast,
                    "previous": ev.previous,
                    "source": ev.source,
                })
            return results
        finally:
            if should_close:
                db.close()

    def check_blackout(
        self,
        symbol: str,
        pre_window_min: int = 30,
        post_window_min: int = 15,
        db: Optional[Session] = None
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Check whether an active high-impact economic release places the symbol in blackout.
        Returns: (is_blocked: bool, active_event: dict | None, reason: str)
        """
        currencies = self.get_currencies_for_symbol(symbol)
        if not currencies:
            return False, None, "No macro currencies tracked for symbol"

        should_close = False
        if db is None:
            db = SessionLocal()
            should_close = True

        try:
            now_utc = datetime.now(timezone.utc)
            window_start = now_utc - timedelta(minutes=post_window_min)
            window_end = now_utc + timedelta(minutes=pre_window_min)

            active_event = db.query(CalendarEvent).filter(
                CalendarEvent.currency.in_(currencies),
                CalendarEvent.impact == "HIGH",
                CalendarEvent.time_utc >= window_start,
                CalendarEvent.time_utc <= window_end
            ).order_by(CalendarEvent.time_utc.asc()).first()

            if active_event:
                ev_time = active_event.time_utc
                if ev_time.tzinfo is None:
                    ev_time = ev_time.replace(tzinfo=timezone.utc)
                diff_sec = (ev_time - now_utc).total_seconds()
                rel_min = round(diff_sec / 60.0, 1)

                status_desc = f"in {rel_min}m" if rel_min > 0 else f"{abs(rel_min)}m ago"
                reason = (
                    f"Active high-impact event [{active_event.currency}] '{active_event.title}' "
                    f"({status_desc}, within -{post_window_min}m/+{pre_window_min}m window)"
                )
                ev_dict = {
                    "id": active_event.id,
                    "title": active_event.title,
                    "currency": active_event.currency,
                    "time_utc": active_event.time_utc.isoformat(),
                    "impact": active_event.impact,
                }
                return True, ev_dict, reason

            return False, None, "No active high-impact events in window"
        finally:
            if should_close:
                db.close()

    def is_stale(self, max_age_hours: int = 24, db: Optional[Session] = None) -> bool:
        """Check if calendar data has not been updated within max_age_hours."""
        should_close = False
        if db is None:
            db = SessionLocal()
            should_close = True

        try:
            latest = db.query(CalendarEvent).order_by(desc(CalendarEvent.created_at)).first()
            if not latest or not latest.created_at:
                return True
            
            created = latest.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            
            age = (datetime.now(timezone.utc) - created).total_seconds() / 3600.0
            return age > max_age_hours
        finally:
            if should_close:
                db.close()


calendar_service = CalendarService()
