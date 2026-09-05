"""
Sentiment & News REST Routes
────────────────────────────
Endpoints for querying live recency-weighted sentiment and ingesting
forex/macro/crypto news from n8n workflows, RSS feeds, and external APIs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.database.connection import SessionLocal
from backend.services.news_sentiment_service import news_sentiment_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sentiment", tags=["sentiment"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Request / Response Models ────────────────────────────────────────────────

class IngestScoreItem(BaseModel):
    pair: str
    sentiment: str = Field(..., description="bullish, bearish, neutral")
    score: float = Field(..., ge=-1.0, le=1.0)
    confidence: Optional[float] = Field(default=0.7, ge=0.0, le=1.0)
    reasoning: Optional[str] = None
    model: Optional[str] = "gemini-2.0-flash"


class IngestArticlePayload(BaseModel):
    source: str = Field(..., description="Source name: forexnewsapi, finnhub, etc.")
    external_id: str = Field(..., description="Unique ID / URL of the article")
    title: str = Field(..., min_length=1)
    description: Optional[str] = None
    url: Optional[str] = None
    published_at: Optional[datetime] = None
    raw_payload: Optional[Dict[str, Any]] = None
    scores: Optional[List[IngestScoreItem]] = None
    auto_score: bool = Field(default=True, description="Run hybrid triage/scoring if scores are omitted")


class ScoreTextRequest(BaseModel):
    title: str = Field(..., min_length=1)
    text: Optional[str] = ""
    candidate_pairs: Optional[List[str]] = None


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("")
@router.get("/")
def get_all_sentiment(
    window_hours: int = Query(default=24, ge=1, le=168, description="Rolling window in hours"),
    db: Session = Depends(get_db),
):
    """Retrieve rolling sentiment summary across all active pairs."""
    try:
        data = news_sentiment_service.get_all_latest_sentiment(window_hours=window_hours, db=db)
        return {"status": "ok", "window_hours": window_hours, "pairs": data}
    except Exception as e:
        logger.error(f"Error fetching sentiment list: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{pair}")
def get_pair_sentiment(
    pair: str,
    window_hours: int = Query(default=24, ge=1, le=168, description="Rolling window in hours"),
    db: Session = Depends(get_db),
):
    """Retrieve recency-weighted sentiment score and breakdown for a single pair/symbol."""
    try:
        data = news_sentiment_service.get_pair_sentiment(pair=pair.upper(), window_hours=window_hours, db=db)
        return {"status": "ok", "sentiment": data}
    except Exception as e:
        logger.error(f"Error calculating pair sentiment for {pair}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ingest")
async def ingest_article(
    payload: IngestArticlePayload = Body(...),
    db: Session = Depends(get_db),
):
    """
    Ingest a news article.
    Accepts pre-computed sentiment scores (e.g. from n8n) or automatically
    triggers the hybrid FinBERT/keyword triage and LLM judge.
    """
    try:
        article, created = news_sentiment_service.upsert_article(
            source=payload.source,
            external_id=payload.external_id,
            title=payload.title,
            description=payload.description,
            url=payload.url,
            published_at=payload.published_at or datetime.now(timezone.utc),
            raw_payload=payload.raw_payload,
            db=db,
        )

        recorded_scores = []
        if payload.scores:
            for s in payload.scores:
                rec = news_sentiment_service.record_sentiment(
                    article_id=article.id,
                    pair=s.pair,
                    sentiment=s.sentiment,
                    score=s.score,
                    confidence=s.confidence or 0.7,
                    model=s.model,
                    reasoning=s.reasoning,
                    db=db,
                )
                recorded_scores.append({
                    "pair": rec.pair,
                    "sentiment": rec.sentiment,
                    "score": rec.score,
                    "confidence": rec.confidence,
                })
        elif payload.auto_score and created:
            # Score newly added article
            scores = await news_sentiment_service.process_and_score_article(article, use_llm=True, db=db)
            recorded_scores = [
                {
                    "pair": s.pair,
                    "sentiment": s.sentiment,
                    "score": s.score,
                    "confidence": s.confidence,
                }
                for s in scores
            ]

        return {
            "status": "ok",
            "created": created,
            "article_id": article.id,
            "scores_recorded": len(recorded_scores),
            "scores": recorded_scores,
        }
    except Exception as e:
        logger.error(f"Error ingesting news article: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/score")
async def score_text(payload: ScoreTextRequest = Body(...)):
    """Dry-run test scoring on a raw headline and description."""
    try:
        is_actionable, detected_pairs, kw_score, rationale = news_sentiment_service.fast_triage(
            payload.title, payload.text or ""
        )

        candidates = payload.candidate_pairs or detected_pairs
        llm_results = []
        if candidates and is_actionable:
            llm_results = await news_sentiment_service.score_article_with_llm(
                payload.title, payload.text or "", candidates
            )

        return {
            "status": "ok",
            "is_actionable": is_actionable,
            "triage_detected_pairs": detected_pairs,
            "triage_keyword_score": kw_score,
            "triage_rationale": rationale,
            "llm_scores": llm_results,
        }
    except Exception as e:
        logger.error(f"Error scoring news text: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
