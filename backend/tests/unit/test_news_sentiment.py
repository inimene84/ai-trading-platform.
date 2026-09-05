"""
Unit tests for Forex & Macro News Sentiment Pipeline
────────────────────────────────────────────────────
Validates:
  - NewsArticle model & deduplication
  - SentimentScore recording & clamping
  - Fast triage keyword/pair detection
  - Recency-weighted decay calculations
  - Sentiment API router endpoints
  - Soft sentiment gating in DecisionEngine
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from backend.database.models import Base, NewsArticle, SentimentScore
from backend.services.news_sentiment_service import NewsSentimentService, _strip_markdown_json
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


def test_markdown_json_stripping():
    raw_markdown = "```json\n{\"pairs\": [{\"pair\": \"EURUSD\", \"score\": 0.5}]}\n```"
    cleaned = _strip_markdown_json(raw_markdown)
    assert cleaned == "{\"pairs\": [{\"pair\": \"EURUSD\", \"score\": 0.5}]}"

    plain = "{\"pairs\": []}"
    assert _strip_markdown_json(plain) == plain


def test_upsert_article_deduplication(db_session):
    svc = NewsSentimentService()
    
    # 1. Insert first article
    art1, created1 = svc.upsert_article(
        source="forexnewsapi",
        external_id="https://example.com/news/1",
        title="ECB signals rate hike as Eurozone inflation accelerates",
        description="European Central Bank indicates tighter monetary policy.",
        db=db_session
    )
    assert created1 is True
    assert art1.id is not None
    assert art1.source == "forexnewsapi"
    assert "Eurozone" in art1.title

    # 2. Insert same source + external_id should deduplicate (created=False)
    art2, created2 = svc.upsert_article(
        source="forexnewsapi",
        external_id="https://example.com/news/1",
        title="Duplicate title",
        db=db_session
    )
    assert created2 is False
    assert art2.id == art1.id

    # 3. Different external_id creates new article
    art3, created3 = svc.upsert_article(
        source="forexnewsapi",
        external_id="https://example.com/news/2",
        title="Dollar rallies on strong payrolls",
        db=db_session
    )
    assert created3 is True
    assert art3.id != art1.id


def test_record_sentiment_clamping(db_session):
    svc = NewsSentimentService()
    art, _ = svc.upsert_article(source="test", external_id="t1", title="Test Headline", db=db_session)

    # Score clamping to [-1.0, 1.0]
    rec_high = svc.record_sentiment(
        article_id=art.id,
        pair="EURUSD",
        sentiment="bullish",
        score=2.5,  # Exceeds max
        confidence=1.5,
        db=db_session
    )
    assert rec_high.score == 1.0
    assert rec_high.confidence == 1.0

    rec_low = svc.record_sentiment(
        article_id=art.id,
        pair="GBPUSD",
        sentiment="bearish",
        score=-3.0,
        confidence=-0.5,
        db=db_session
    )
    assert rec_low.score == -1.0
    assert rec_low.confidence == 0.0


def test_fast_triage_keyword_and_pair_detection():
    svc = NewsSentimentService()

    # 1. Bullish EUR news
    is_act, pairs, score, rat = svc.fast_triage(
        title="Euro surges as ECB delivers surprise hawkish rate hike",
        text="The EUR rallied to new monthly highs against major currencies."
    )
    assert is_act is True
    assert any("EUR" in p for p in pairs)
    assert score > 0.0
    assert "hawkish" in rat or "triage" in rat.lower()

    # 2. Bearish USD / Gold rally
    is_act2, pairs2, score2, rat2 = svc.fast_triage(
        title="Gold rallies to record high as dollar plunges after Fed rate cut",
        text="XAUUSD broke out while USD suffered steep losses."
    )
    assert is_act2 is True
    assert "XAUUSD" in pairs2
    assert score2 != 0.0

    # 3. Neutral routine announcement (no directional cues)
    is_act3, pairs3, score3, rat3 = svc.fast_triage(
        title="Bank of Japan releases schedule of upcoming meetings for next quarter",
        text="The calendar outlines routine administrative sessions."
    )
    assert is_act3 is False
    assert score3 == 0.0


def test_recency_weighted_decay_calculation(db_session):
    svc = NewsSentimentService()
    art, _ = svc.upsert_article(source="test", external_id="decay_1", title="Decay Test", db=db_session)

    now = datetime.now(timezone.utc)
    
    # Insert older bearish score (12 hours ago, score = -0.80)
    score_old = SentimentScore(
        article_id=art.id,
        pair="EURUSD",
        sentiment="bearish",
        score=-0.80,
        confidence=0.90,
        created_at=now - timedelta(hours=12)
    )
    db_session.add(score_old)

    # Insert fresh bullish score (5 minutes ago, score = +0.60)
    score_new = SentimentScore(
        article_id=art.id,
        pair="EURUSD",
        sentiment="bullish",
        score=0.60,
        confidence=0.85,
        created_at=now - timedelta(minutes=5)
    )
    db_session.add(score_new)
    db_session.commit()

    result = svc.get_pair_sentiment("EURUSD", window_hours=24, db=db_session)
    assert result["pair"] == "EURUSD"
    assert result["article_count"] == 2
    
    # Simple average would be (-0.80 + 0.60) / 2 = -0.10 (slightly bearish)
    # Recency weighted should heavily favor the 5-minute-old +0.60 score
    assert result["recency_weighted_score"] > 0.15
    assert result["signal"] == "bullish"
    assert result["confidence"] > 0.0


def test_sentiment_api_endpoints():
    import time
    client = TestClient(app)

    unique_id = f"api_test_article_{int(time.time() * 1000)}"
    # 1. Ingest article with scores
    ingest_payload = {
        "source": "forexnewsapi",
        "external_id": unique_id,
        "title": "US Dollar jumps as nonfarm payrolls beat expectations",
        "description": "Employment data shows persistent labor market strength.",
        "scores": [
            {
                "pair": "EURUSD",
                "sentiment": "bearish",
                "score": -0.65,
                "confidence": 0.85,
                "reasoning": "Strong USD puts downward pressure on EURUSD."
            }
        ]
    }

    res = client.post("/api/sentiment/ingest", json=ingest_payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["created"] is True
    assert data["scores_recorded"] == 1

    # 2. Query pair sentiment
    pair_res = client.get("/api/sentiment/EURUSD")
    assert pair_res.status_code == 200
    p_data = pair_res.json()["sentiment"]
    assert p_data["pair"] == "EURUSD"
    assert p_data["article_count"] >= 1
    assert p_data["recency_weighted_score"] < -0.15
    assert p_data["signal"] == "bearish"

    # 3. Query all sentiment
    all_res = client.get("/api/sentiment")
    assert all_res.status_code == 200
    assert "pairs" in all_res.json()


@pytest.mark.asyncio
async def test_decision_engine_sentiment_gate():
    """Verify decision_engine blocks BUY on strong bearish news sentiment when flag is enabled."""
    from backend.services.decision_engine import DecisionEngine
    from backend.services.risk_config import RiskConfig
    from backend.strategies.base import StrategySignal
    from backend.strategies.market_regime import RegimeResult

    config = RiskConfig()
    engine = DecisionEngine(risk_config=config)
    engine.enable_kronos = False

    bars = [{"time": 1000 + i * 60, "open": 1.1000, "high": 1.1020, "low": 1.0980, "close": 1.1010, "volume": 100} for i in range(50)]

    # Mock regime detector to return TRENDING
    engine.regime_detector.detect = lambda b: RegimeResult(
        regime="TRENDING", confidence=0.85, adx=35.0, bb_width_ratio=0.03, atr_ratio=0.01, price_vs_ema=0.02, reasoning="Strong trend"
    )

    # Mock strategy to always produce a BUY
    engine.strategy.generate_signal = lambda sym, b, **kw: StrategySignal(
        symbol=sym, signal="BUY", confidence=0.80, strategy="TrendFollowing"
    )

    # 1. When flag is disabled (default), BUY goes through
    with patch.dict("os.environ", {"SENTIMENT_FILTER_ENABLED": "false"}):
        with patch("backend.services.decision_engine.opinion_analyze", new=AsyncMock(return_value=None)):
            dec = await engine.evaluate_symbol(
                symbol="EURUSD",
                bars=bars,
                existing_position=None,
                open_count=0,
                pyramid_layers=[],
                cooldown_active=False,
                current_funding_rate=0.0
            )
            assert dec is not None
            assert dec.action == "BUY"

    # 2. When flag is enabled and strong bearish sentiment exists, BUY is vetoed
    mock_sentiment = {
        "pair": "EURUSD",
        "recency_weighted_score": -0.65,
        "confidence": 0.85,
        "signal": "bearish"
    }
    with patch.dict("os.environ", {"SENTIMENT_FILTER_ENABLED": "true"}):
        with patch("backend.services.news_sentiment_service.news_sentiment_service.get_pair_sentiment", return_value=mock_sentiment):
            with patch("backend.services.decision_engine.opinion_analyze", new=AsyncMock(return_value=None)):
                dec = await engine.evaluate_symbol(
                    symbol="EURUSD",
                    bars=bars,
                    existing_position=None,
                    open_count=0,
                    pyramid_layers=[],
                    cooldown_active=False,
                    current_funding_rate=0.0
                )
                assert dec is None  # Vetoed!


