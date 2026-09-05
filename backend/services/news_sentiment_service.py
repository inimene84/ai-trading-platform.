"""
Forex & Macro News Sentiment Service
────────────────────────────────────
Implements the hybrid news ingestion, scoring, and recency-weighted aggregation
pipeline for Forex, Commodities (Gold/Silver), and Crypto pairs.

Key functions:
  - upsert_article(): Ingests raw news with strict source + external_id deduplication.
  - score_article_hybrid(): FinBERT/keyword triage -> LLM judge for non-neutral news.
  - record_sentiment(): Persists per-pair sentiment scores.
  - get_pair_sentiment(): Recency-weighted rolling score calculation.
  - get_all_latest_sentiment(): Summary across all active pairs.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import desc
from sqlalchemy.orm import Session

from backend.database.connection import SessionLocal
from backend.database.models import NewsArticle, SentimentScore

logger = logging.getLogger(__name__)

# Standard currency pairs & commodities
COMMON_PAIRS = {
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
    "EURGBP", "EURJPY", "GBPJPY", "AUDJPY", "EURAUD",
    "XAUUSD", "XAGUSD",  # Gold & Silver
    "BTCUSDT", "ETHUSDT", "SOLUSDT"
}

# Currency to pair mapping for headline association
CURRENCY_TO_PRIMARY_PAIRS = {
    "EUR": ["EURUSD", "EURGBP", "EURJPY"],
    "USD": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"],
    "GBP": ["GBPUSD", "EURGBP", "GBPJPY"],
    "JPY": ["USDJPY", "EURJPY", "GBPJPY"],
    "AUD": ["AUDUSD", "AUDJPY", "EURAUD"],
    "CAD": ["USDCAD"],
    "CHF": ["USDCHF"],
    "NZD": ["NZDUSD"],
    "GOLD": ["XAUUSD"],
    "XAU": ["XAUUSD"],
    "SILVER": ["XAGUSD"],
    "XAG": ["XAGUSD"],
    "BTC": ["BTCUSDT"],
    "ETH": ["ETHUSDT"],
    "SOL": ["SOLUSDT"],
}

# High-impact sentiment keywords for fast triage
BULLISH_KEYWORDS = {
    "surge", "rally", "soar", "gain", "bullish", "jump", "record high",
    "rate hike", "hawkish", "outperform", "rebound", "breakout", "upgrade",
    "beat expectations", "accelerates", "climb", "expansion"
}

BEARISH_KEYWORDS = {
    "plunge", "slump", "drop", "sink", "bearish", "crash", "record low",
    "rate cut", "dovish", "underperform", "selloff", "breakdown", "downgrade",
    "miss expectations", "slowdown", "fall", "contraction", "default", "recession"
}


def _strip_markdown_json(text: str) -> str:
    """Safely strip markdown code blocks from LLM responses."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


class NewsSentimentService:
    def __init__(self):
        self.default_model = os.getenv("SENTIMENT_LLM_MODEL", "gemini-2.0-flash")

    def upsert_article(
        self,
        source: str,
        external_id: str,
        title: str,
        description: Optional[str] = None,
        url: Optional[str] = None,
        published_at: Optional[datetime] = None,
        raw_payload: Optional[Dict[str, Any]] = None,
        db: Optional[Session] = None
    ) -> Tuple[NewsArticle, bool]:
        """Upsert a raw news article. Returns (NewsArticle, created_bool)."""
        should_close = False
        if db is None:
            db = SessionLocal()
            should_close = True

        try:
            if not published_at:
                published_at = datetime.now(timezone.utc)
            elif published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)

            existing = db.query(NewsArticle).filter(
                NewsArticle.source == source,
                NewsArticle.external_id == external_id
            ).first()

            if existing:
                return existing, False

            article = NewsArticle(
                source=source,
                external_id=external_id,
                title=title,
                description=description,
                url=url,
                published_at=published_at,
                raw_payload=raw_payload,
            )
            db.add(article)
            db.commit()
            db.refresh(article)
            return article, True
        finally:
            if should_close:
                db.close()

    def record_sentiment(
        self,
        article_id: int,
        pair: str,
        sentiment: str,
        score: float,
        confidence: float = 0.5,
        model: Optional[str] = None,
        reasoning: Optional[str] = None,
        db: Optional[Session] = None
    ) -> SentimentScore:
        """Record sentiment score for an article and pair."""
        should_close = False
        if db is None:
            db = SessionLocal()
            should_close = True

        try:
            clamped_score = max(-1.0, min(1.0, float(score)))
            clamped_conf = max(0.0, min(1.0, float(confidence)))
            norm_sentiment = sentiment.lower()
            if norm_sentiment not in ("bullish", "bearish", "neutral"):
                if clamped_score > 0.1:
                    norm_sentiment = "bullish"
                elif clamped_score < -0.1:
                    norm_sentiment = "bearish"
                else:
                    norm_sentiment = "neutral"

            score_record = SentimentScore(
                article_id=article_id,
                pair=pair.upper(),
                sentiment=norm_sentiment,
                score=round(clamped_score, 4),
                confidence=round(clamped_conf, 4),
                model=model or self.default_model,
                reasoning=reasoning,
            )
            db.add(score_record)
            db.commit()
            db.refresh(score_record)
            return score_record
        finally:
            if should_close:
                db.close()

    def fast_triage(self, title: str, text: str = "") -> Tuple[bool, List[str], float, str]:
        """
        Fast triage step:
        Returns (is_actionable, detected_pairs, keyword_score, rationale).
        If clearly neutral or routine noise, returns is_actionable=False to save LLM cost.
        """
        combined = f"{title} {text}".upper()
        detected_pairs: set[str] = set()

        for pair in COMMON_PAIRS:
            if pair in combined:
                detected_pairs.add(pair)

        for curr, primary_pairs in CURRENCY_TO_PRIMARY_PAIRS.items():
            pattern = rf"\b{curr}\b"
            if re.search(pattern, combined):
                for p in primary_pairs:
                    detected_pairs.add(p)

        lower_combined = f"{title} {text}".lower()
        bull_hits = sum(1 for kw in BULLISH_KEYWORDS if kw in lower_combined)
        bear_hits = sum(1 for kw in BEARISH_KEYWORDS if kw in lower_combined)

        if bull_hits == 0 and bear_hits == 0:
            return False, list(detected_pairs), 0.0, "Neutral / routine information with no directional cues"

        net_diff = bull_hits - bear_hits
        keyword_score = round(max(-1.0, min(1.0, net_diff * 0.35)), 2)
        direction = "bullish" if keyword_score > 0 else "bearish"

        return True, list(detected_pairs), keyword_score, f"Keyword triage: {bull_hits} bullish, {bear_hits} bearish signals ({direction})"

    async def score_article_with_llm(
        self,
        title: str,
        text: str = "",
        pairs_hint: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Call LLM judge to evaluate financial sentiment per currency pair."""
        hint_str = f"Relevant candidate pairs: {', '.join(pairs_hint)}" if pairs_hint else ""
        system_prompt = (
            "You are a professional forex and macro market sentiment analyst. "
            "Given the news headline and description, determine which tradeable currency pairs or commodities "
            "it impacts (standard codes like EURUSD, GBPUSD, USDJPY, XAUUSD, BTCUSDT). "
            "Score sentiment for each pair from -1.0 (strongly bearish for the base currency) "
            "to +1.0 (strongly bullish for the base currency). "
            "Respond ONLY with valid JSON in this exact schema, without markdown formatting:\n"
            "{\"pairs\": [{\"pair\": \"EURUSD\", \"sentiment\": \"bullish\", \"score\": 0.60, \"confidence\": 0.85, \"reasoning\": \"brief rationale\"}]}\n"
            "If no clear impact or neutral, respond with empty pairs list: {\"pairs\": []}."
        )

        user_content = f"{hint_str}\nHeadline: {title}\nDetails: {text}"

        try:
            from backend.llm.client import get_llm_client
            client = get_llm_client()
            response_text = await client.generate(
                prompt=f"{system_prompt}\n\n{user_content}",
                temperature=0.1,
                max_tokens=350,
            )
            cleaned = _strip_markdown_json(response_text)
            parsed = json.loads(cleaned)
            return parsed.get("pairs", [])
        except Exception as e:
            logger.warning(f"LLM sentiment scoring unavailable or failed to parse: {e}")
            return []

    async def process_and_score_article(
        self,
        article: NewsArticle,
        use_llm: bool = True,
        db: Optional[Session] = None
    ) -> List[SentimentScore]:
        """Full hybrid pipeline: fast triage followed by LLM judge if actionable."""
        is_actionable, candidate_pairs, kw_score, rationale = self.fast_triage(
            article.title, article.description or ""
        )

        scored_records: List[SentimentScore] = []

        if not candidate_pairs:
            return scored_records

        if use_llm and is_actionable:
            llm_results = await self.score_article_with_llm(
                article.title, article.description or "", candidate_pairs
            )
            if llm_results:
                for res in llm_results:
                    rec = self.record_sentiment(
                        article_id=article.id,
                        pair=res["pair"],
                        sentiment=res.get("sentiment", "neutral"),
                        score=float(res.get("score", 0.0)),
                        confidence=float(res.get("confidence", 0.7)),
                        model=self.default_model,
                        reasoning=res.get("reasoning", rationale),
                        db=db,
                    )
                    scored_records.append(rec)
                return scored_records

        # Fallback to fast triage keyword score for detected pairs
        for pair in candidate_pairs:
            rec = self.record_sentiment(
                article_id=article.id,
                pair=pair,
                sentiment="bullish" if kw_score > 0.05 else ("bearish" if kw_score < -0.05 else "neutral"),
                score=kw_score,
                confidence=0.5 if is_actionable else 0.2,
                model="fast-triage-keyword",
                reasoning=rationale,
                db=db,
            )
            scored_records.append(rec)

        return scored_records

    def get_pair_sentiment(
        self,
        pair: str,
        window_hours: int = 24,
        db: Optional[Session] = None
    ) -> Dict[str, Any]:
        """
        Calculate rolling recency-weighted sentiment score for a pair:
        Formula: weight = 1 / sqrt(elapsed_seconds + 1)
        Weighted_Score = sum(score * weight) / sum(weight)
        """
        should_close = False
        if db is None:
            db = SessionLocal()
            should_close = True

        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
            scores = db.query(SentimentScore).filter(
                SentimentScore.pair == pair.upper(),
                SentimentScore.created_at >= cutoff
            ).order_by(desc(SentimentScore.created_at)).all()

            if not scores:
                return {
                    "pair": pair.upper(),
                    "article_count": 0,
                    "avg_score": 0.0,
                    "recency_weighted_score": 0.0,
                    "signal": "neutral",
                    "confidence": 0.0,
                    "last_updated": None,
                }

            now_utc = datetime.now(timezone.utc)
            total_weight = 0.0
            weighted_sum = 0.0
            simple_sum = 0.0
            conf_sum = 0.0

            for s in scores:
                created_at = s.created_at
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                elapsed_sec = max(0.0, (now_utc - created_at).total_seconds())
                weight = 1.0 / math.sqrt(elapsed_sec + 1.0)

                total_weight += weight
                weighted_sum += s.score * weight
                simple_sum += s.score
                conf_sum += (s.confidence or 0.5)

            n = len(scores)
            avg_score = round(simple_sum / n, 4)
            recency_weighted_score = round(weighted_sum / total_weight, 4) if total_weight > 0 else 0.0

            # Signal classification: > 0.15 bullish, < -0.15 bearish
            if recency_weighted_score > 0.15:
                signal = "bullish"
            elif recency_weighted_score < -0.15:
                signal = "bearish"
            else:
                signal = "neutral"

            # Volume-damped confidence
            base_conf = conf_sum / n
            volume_multiplier = min(1.0, 1.0 - (1.0 / (1.0 + n * 0.4)))
            confidence = round(base_conf * volume_multiplier, 4)

            latest_created = scores[0].created_at
            last_updated_iso = (
                latest_created.isoformat() if latest_created else None
            )

            return {
                "pair": pair.upper(),
                "article_count": n,
                "avg_score": avg_score,
                "recency_weighted_score": recency_weighted_score,
                "signal": signal,
                "confidence": confidence,
                "last_updated": last_updated_iso,
            }
        finally:
            if should_close:
                db.close()

    def get_all_latest_sentiment(
        self,
        window_hours: int = 24,
        db: Optional[Session] = None
    ) -> List[Dict[str, Any]]:
        """Get summary sentiment for all pairs active in the rolling window."""
        should_close = False
        if db is None:
            db = SessionLocal()
            should_close = True

        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
            distinct_pairs = db.query(SentimentScore.pair).filter(
                SentimentScore.created_at >= cutoff
            ).distinct().all()

            results = []
            for (pair_name,) in distinct_pairs:
                results.append(self.get_pair_sentiment(pair_name, window_hours=window_hours, db=db))

            results.sort(key=lambda x: abs(x["recency_weighted_score"]), reverse=True)
            return results
        finally:
            if should_close:
                db.close()


news_sentiment_service = NewsSentimentService()
