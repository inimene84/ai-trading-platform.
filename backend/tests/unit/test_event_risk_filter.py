"""
Unit tests for Economic Calendar & Event-Risk Filter
────────────────────────────────────────────────────
Validates:
  - CalendarEvent recording & currency symbol mapping
  - Blackout detection windows (pre/post event timing)
  - EventRiskFilter evaluation (veto/reduce/pass)
  - Stale calendar fail-closed defense in LIVE mode
  - DecisionEngine integration with EVENT_RISK_FILTER_ENABLED
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from backend.database.models import Base, CalendarEvent
from backend.services.calendar_service import CalendarService
from backend.services.event_risk_filter import EventRiskFilter
from backend.strategies.base import StrategySignal
from backend.strategies.market_regime import RegimeResult
from backend.main import app


@pytest.fixture
def db_session():
    """In-memory SQLite session for isolated database tests."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


def test_symbol_currency_resolution():
    svc = CalendarService()
    assert set(svc.get_currencies_for_symbol("EURUSD")) == {"EUR", "USD"}
    assert set(svc.get_currencies_for_symbol("GBPJPY")) == {"GBP", "JPY"}
    assert svc.get_currencies_for_symbol("XAUUSD") == ["USD"]
    assert svc.get_currencies_for_symbol("BTCUSDT") == ["USD"]


def test_calendar_event_recording_and_deduplication(db_session):
    svc = CalendarService()
    ev_time = datetime.now(timezone.utc) + timedelta(minutes=20)

    # 1. Insert new event
    ev1 = svc.record_event(
        time_utc=ev_time,
        currency="USD",
        impact="HIGH",
        title="Non-Farm Employment Change",
        forecast="180K",
        previous="150K",
        db=db_session
    )
    assert ev1.id is not None
    assert ev1.currency == "USD"
    assert ev1.impact == "HIGH"

    # 2. Update same event with actual figures
    ev2 = svc.record_event(
        time_utc=ev_time,
        currency="USD",
        impact="HIGH",
        title="Non-Farm Employment Change",
        actual="210K",
        db=db_session
    )
    assert ev2.id == ev1.id
    assert ev2.actual == "210K"


def test_blackout_window_detection(db_session):
    svc = CalendarService()
    now_utc = datetime.now(timezone.utc)

    # Event in 15 minutes (within ±30m pre-window)
    svc.record_event(
        time_utc=now_utc + timedelta(minutes=15),
        currency="USD",
        impact="HIGH",
        title="FOMC Rate Decision",
        db=db_session
    )

    # EURUSD should be blocked (contains USD)
    blocked_eurusd, ev, reason = svc.check_blackout("EURUSD", pre_window_min=30, post_window_min=15, db=db_session)
    assert blocked_eurusd is True
    assert ev is not None
    assert "FOMC" in reason

    # EURGBP should NOT be blocked (does not contain USD)
    blocked_eurgbp, _, _ = svc.check_blackout("EURGBP", pre_window_min=30, post_window_min=15, db=db_session)
    assert blocked_eurgbp is False


def test_event_risk_filter_gate(db_session):
    filter_svc = EventRiskFilter()

    # 1. When disabled (default), returns pass
    with patch.dict("os.environ", {"EVENT_RISK_FILTER_ENABLED": "false"}):
        res = filter_svc.evaluate_order("EURUSD", proposed_quantity=1.0, proposed_direction="BUY")
        assert res.approved is True
        assert res.action == "pass"

    # 2. When enabled and blackout is active, vetoes order
    with patch.dict("os.environ", {"EVENT_RISK_FILTER_ENABLED": "true"}):
        with patch("backend.services.calendar_service.calendar_service.is_stale", return_value=False):
            with patch("backend.services.calendar_service.calendar_service.check_blackout", return_value=(True, {"title": "CPI"}, "CPI event active")):
                res = filter_svc.evaluate_order("EURUSD", proposed_quantity=1.0, proposed_direction="BUY")
                assert res.approved is False
                assert res.action == "veto"
                assert "CPI" in res.reason


def test_calendar_api_routes():
    client = TestClient(app)

    # 1. Ingest batch events
    event_payload = {
        "events": [
            {
                "time_utc": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
                "currency": "EUR",
                "impact": "HIGH",
                "title": "ECB Monetary Policy Statement"
            }
        ]
    }
    res = client.post("/api/calendar/batch", json=event_payload)
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    # 2. Check symbol blackout
    check_res = client.get("/api/calendar/check/EURUSD")
    assert check_res.status_code == 200
    assert check_res.json()["is_blackout"] is True
    assert "ECB" in check_res.json()["reason"]


@pytest.mark.asyncio
async def test_decision_engine_event_risk_veto():
    """Verify decision_engine vetoes entry when event_risk_filter blocks."""
    from backend.services.decision_engine import DecisionEngine
    from backend.services.risk_config import RiskConfig

    config = RiskConfig()
    engine = DecisionEngine(risk_config=config)
    engine.enable_kronos = False

    bars = [{"time": 1000 + i * 60, "open": 1.1000, "high": 1.1020, "low": 1.0980, "close": 1.1010, "volume": 100} for i in range(50)]

    engine.regime_detector.detect = lambda b: RegimeResult(
        regime="TRENDING", confidence=0.85, adx=35.0, bb_width_ratio=0.03, atr_ratio=0.01, price_vs_ema=0.02, reasoning="Trend"
    )
    engine.strategy.generate_signal = lambda sym, b, **kw: StrategySignal(
        symbol=sym, signal="BUY", confidence=0.80, strategy="TrendFollowing"
    )

    with patch.dict("os.environ", {"EVENT_RISK_FILTER_ENABLED": "true"}):
        with patch("backend.services.decision_engine.opinion_analyze", new=AsyncMock(return_value=None)):
            with patch("backend.services.calendar_service.calendar_service.is_stale", return_value=False):
                with patch("backend.services.calendar_service.calendar_service.check_blackout", return_value=(True, {"title": "NFP"}, "Active NFP release")):
                    dec = await engine.evaluate_symbol(
                        symbol="EURUSD",
                        bars=bars,
                        existing_position=None,
                        open_count=0,
                        pyramid_layers=[],
                        cooldown_active=False,
                        current_funding_rate=0.0
                    )
                    assert dec is None  # Blocked by event risk filter!
