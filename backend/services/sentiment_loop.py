"""
Native Sentiment Loop
─────────────────────
Repo-side replacement for the fragile n8n crypto-news → InfluxDB sentiment
pipeline. Runs entirely inside the backend process, so per-coin sentiment in
the `news-sentiment` bucket no longer depends on n8n, Cloudflare, or webhook
health being up.

For each trading symbol on a fixed interval it:
  1. Pulls recent crypto news (CryptoCompare via CryptoNewsService).
  2. Scores per-coin sentiment.
     - Fast path (default): keyword classifier already in CryptoNewsService.
     - LLM path (optional, SENTIMENT_LOOP_USE_LLM=true): richer score via the
       existing deep-analysis LLM, falling back to keywords on any error.
  3. Writes a `news_sentiment,symbol=<COIN>` point via the existing
     influxdb_writer.write_news_sentiment(), which the opinion_layer /
     influxdb_sentiment_reader already consume.

Design rules:
  - Never raises out of the loop: any per-symbol error is logged and skipped.
  - Zero new hard dependencies — reuses crypto_news_service + influx singletons.
  - Always emits a row per symbol (NEUTRAL when no news) so the reader's
    BTC/CRYPTO fallback chain is never triggered by missing per-coin data.

Env:
  SENTIMENT_LOOP_ENABLED        default "true"
  SENTIMENT_LOOP_INTERVAL_MIN   default "30"
  SENTIMENT_LOOP_USE_LLM        default "false"
  TRADING_SYMBOLS               shared with trading_loop (comma list)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from backend.services.crypto_news_service import crypto_news_service
from backend.services.influxdb_writer import influx
from backend.services.qdrant_client import qdrant
from backend.utils.embeddings import generate_text_embedding

logger = logging.getLogger(__name__)

# Map the keyword classifier's labels to a numeric score + direction tag.
_LABEL_TO_SCORE = {"positive": 0.6, "negative": -0.6, "neutral": 0.0}
_LABEL_TO_DIR = {"positive": "BULLISH", "negative": "BEARISH", "neutral": "NEUTRAL"}

_DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "POLUSDT", "LTCUSDT", "UNIUSDT", "ATOMUSDT", "NEARUSDT",
    "OPUSDT", "ARBUSDT", "APTUSDT", "INJUSDT", "SUIUSDT",
]


def _load_symbols() -> list[str]:
    env_syms = os.getenv("TRADING_SYMBOLS", "")
    if env_syms:
        syms = [s.strip().upper() for s in env_syms.split(",") if s.strip()]
        if syms:
            return syms
    return list(_DEFAULT_SYMBOLS)


def _aggregate_keyword_sentiment(articles: list[dict]) -> tuple[float, float, str, int]:
    """Aggregate the per-article keyword labels into one score for a coin.

    Returns (sentiment_score, impact_score, direction, headline_count).
    """
    if not articles:
        return 0.0, 0.0, "NEUTRAL", 0, 0.0

    scores = []
    for a in articles:
        label = (a.get("sentiment") or "neutral").lower()
        scores.append(_LABEL_TO_SCORE.get(label, 0.0))

    n = len(scores)
    avg = sum(scores) / n if n else 0.0
    # Clamp to [-1, 1]
    avg = max(-1.0, min(1.0, avg))

    if avg > 0.05:
        direction = "BULLISH"
    elif avg < -0.05:
        direction = "BEARISH"
    else:
        direction = "NEUTRAL"

    # Impact scales with how much coverage this coin got (saturates at ~10 items).
    impact = max(0.0, min(1.0, n / 10.0))
    # Confidence is 0.1 minimum so coins with neutral sentiment but some coverage aren't dropped
    confidence = max(impact, 0.1) if n > 0 else 0.0
    return round(avg, 4), round(impact, 4), direction, n, round(confidence, 4)


class SentimentLoopService:
    """Background task that produces per-coin sentiment into InfluxDB."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._state = "stopped"  # stopped, running, error
        self._error: Optional[str] = None
        self._interval_minutes = int(os.getenv("SENTIMENT_LOOP_INTERVAL_MIN", "30") or "30")
        self._use_llm = os.getenv("SENTIMENT_LOOP_USE_LLM", "false").lower() == "true"
        self._symbols = _load_symbols()
        self._last_cycle: Optional[str] = None
        self._next_cycle: Optional[str] = None
        self._cycle_count = 0
        self._last_written: dict[str, dict] = {}  # symbol -> last point summary
        self._archived_news_urls = set()

    # ── status ──────────────────────────────────────────────────────────
    def status(self) -> dict:
        return {
            "state": self._state,
            "running": self._running,
            "error": self._error,
            "interval_minutes": self._interval_minutes,
            "use_llm": self._use_llm,
            "symbols": self._symbols,
            "symbol_count": len(self._symbols),
            "cycle_count": self._cycle_count,
            "last_cycle": self._last_cycle,
            "next_cycle": self._next_cycle,
            "last_written": self._last_written,
        }

    # ── lifecycle ───────────────────────────────────────────────────────
    async def start(self) -> None:
        if self._running:
            logger.info("SentimentLoopService already running")
            return
        self._running = True
        self._state = "running"
        self._error = None
        self._task = asyncio.create_task(self._loop())
        logger.info(
            f"✓ Sentiment loop started (interval={self._interval_minutes}m, "
            f"symbols={len(self._symbols)}, use_llm={self._use_llm})"
        )

    async def stop(self) -> None:
        self._running = False
        self._state = "stopped"
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        logger.info("Sentiment loop stopped")

    # ── core loop ───────────────────────────────────────────────────────
    async def _loop(self) -> None:
        last_macro_fetch = 0.0

        while self._running:
            try:
                await self.run_once()
            except Exception as e:  # never let the loop die
                self._state = "error"
                self._error = str(e)
                logger.error(f"Sentiment loop cycle error: {e}")

            if os.getenv("MACRO_LOOP_ENABLED", "true").lower() == "true":
                macro_interval = int(os.getenv("MACRO_LOOP_INTERVAL_MIN", "60")) * 60
                now = time.time()
                if now - last_macro_fetch >= macro_interval:
                    try:
                        await self.run_macro_tick()
                        last_macro_fetch = now
                    except Exception as e:
                        logger.error(f"Macro loop cycle error: {e}")
            # Sleep until next cycle
            self._next_cycle = datetime.now(timezone.utc).isoformat()
            for _ in range(self._interval_minutes * 60):
                if not self._running:
                    return
                await asyncio.sleep(1)

    async def run_once(self, dry_run: bool = False) -> dict:
        """Run a single full pass over all symbols. Returns a summary.

        dry_run=True computes sentiment but does NOT write to InfluxDB — used
        by the manual-trigger route and local tests.
        """
        start = time.time()
        written = 0
        skipped = 0
        per_symbol = {}

        for sym in self._symbols:
            try:
                articles = await crypto_news_service.get_crypto_news([sym])
            except Exception as e:
                logger.warning(f"[sentiment] news fetch failed for {sym}: {e}")
                articles = []

            score, impact, direction, n, confidence = _aggregate_keyword_sentiment(articles)

            # Optional LLM refinement (best-effort; falls back to keyword score).
            if self._use_llm and articles:
                try:
                    score, impact, direction = await self._llm_refine(sym, articles, score, impact, direction)
                except Exception as e:
                    logger.debug(f"[sentiment] LLM refine failed for {sym}, using keyword: {e}")

            topics = ",".join(
                sorted({(a.get("categories") or "").split("|")[0] for a in articles if a.get("categories")})
            )[:200]

            point = {
                "symbol": sym,
                "sentiment_score": score,
                "impact_score": impact,
                "direction": direction,
                "headline_count": n,
            }

            per_symbol[sym] = point

            # Archive impactful news into Qdrant
            for article in articles:
                url = article.get("url") or article.get("title")
                if not url or url in self._archived_news_urls:
                    continue
                
                # Only archive high-impact or strong confidence AI-filtered articles to save space
                if confidence > 0.5 or impact > 0.6:
                    try:
                        emb = await self._generate_embedding(article.get("title", "") + " " + article.get("body", ""))
                        if emb:
                            await qdrant.store_news_article(
                                title=article.get("title", ""),
                                content=article.get("body", ""),
                                source=article.get("source", "native-sentiment"),
                                url=article.get("url", ""),
                                published_at=article.get("published_on"),
                                sentiment=score,
                                symbols=[sym],
                                embedding=emb,
                            )
                            self._archived_news_urls.add(url)
                            # Keep set size reasonable
                            if len(self._archived_news_urls) > 5000:
                                self._archived_news_urls.clear()
                    except Exception as e:
                        logger.warning(f"Failed to archive news {url}: {e}")

            if not dry_run:
                try:
                    base = sym.replace("USDT", "").replace("USDC", "").replace("PERP", "").upper()
                    await influx.write_news_sentiment(
                        symbol=base,                       # reader keys on the base coin tag
                        sentiment_score=score,
                        impact_score=impact,
                        source="native-sentiment-loop",
                        time_horizon="short",
                        topics=topics,
                        confidence=confidence,             # properly uses the computed confidence
                        direction=direction,
                    )
                    written += 1
                    self._last_written[base] = point
                except Exception as e:
                    skipped += 1
                    logger.warning(f"[sentiment] influx write failed for {sym}: {e}")

        if per_symbol:
            crypto_score = sum(v["sentiment_score"] for v in per_symbol.values()) / len(per_symbol)
            crypto_impact = sum(v["impact_score"] for v in per_symbol.values()) / len(per_symbol)
            crypto_n = sum(v["headline_count"] for v in per_symbol.values())
            
            if crypto_score > 0.05:
                crypto_dir = "BULLISH"
            elif crypto_score < -0.05:
                crypto_dir = "BEARISH"
            else:
                crypto_dir = "NEUTRAL"
                
            crypto_point = {
                "sentiment_score": round(crypto_score, 4),
                "impact_score": round(crypto_impact, 4),
                "direction": crypto_dir,
                "headline_count": crypto_n
            }

            if not dry_run:
                try:
                    await influx.write_news_sentiment(
                        symbol="CRYPTO",
                        sentiment_score=crypto_score,
                        impact_score=crypto_impact,
                        source="native-sentiment-loop-aggregate",
                        time_horizon="short",
                        topics="aggregate",
                        confidence=crypto_impact,
                        direction=crypto_dir,
                    )
                    written += 1
                    self._last_written["CRYPTO"] = crypto_point
                except Exception as e:
                    logger.warning(f"[sentiment] influx write failed for CRYPTO: {e}")
            else:
                per_symbol["CRYPTO"] = crypto_point

        self._cycle_count += 1
        self._last_cycle = datetime.now(timezone.utc).isoformat()
        elapsed = round(time.time() - start, 2)
        summary = {
            "cycle": self._cycle_count,
            "symbols": len(self._symbols),
            "written": written,
            "skipped": skipped,
            "elapsed_sec": elapsed,
            "dry_run": dry_run,
        }
        if dry_run:
            summary["per_symbol"] = per_symbol
        logger.info(f"[sentiment] cycle complete: {summary}")
        return summary

    # ── optional LLM scoring ────────────────────────────────────────────
    async def _llm_refine(self, symbol: str, articles: list[dict], score: float,
                          impact: float, direction: str) -> tuple[float, float, str]:
        """Use the existing deep-analysis LLM to refine the score.

        Imported lazily so the loop has no hard LLM dependency. Any failure
        bubbles up to the caller, which keeps the keyword result.
        """
        import json
        from backend.llm.router import call_llm_resilient

        headlines = "\n".join(f"- {a.get('title', '')}" for a in articles[:15] if a.get("title"))
        system_prompt = (
            "You are a crypto market sentiment analyst. Given recent headlines for one coin, "
            "return ONLY a compact JSON object: "
            '{"sentiment_score": <-1..1>, "impact_score": <0..1>, "direction": "BULLISH|BEARISH|NEUTRAL"}.'
        )
        user_prompt = f"Coin: {symbol}\nHeadlines:\n{headlines}\n\nReturn the JSON only."

        res_str = await call_llm_resilient(
            task_type="deep_analysis",
            prompt=user_prompt,
            system=system_prompt,
            temperature=0.2,
            max_tokens=120,
            response_json=True
        )
        
        p = json.loads(res_str)
        new_score = max(-1.0, min(1.0, float(p.get("sentiment_score", score))))
        new_impact = max(0.0, min(1.0, float(p.get("impact_score", impact))))
        new_dir = str(p.get("direction", direction)).upper()
        if new_dir not in {"BULLISH", "BEARISH", "NEUTRAL"}:
            new_dir = direction
        return round(new_score, 4), round(new_impact, 4), new_dir

    async def _generate_embedding(self, text: str) -> Optional[list[float]]:
        """Best-effort embedding for news articles (OpenRouter → LiteLLM)."""
        return await generate_text_embedding(text, normalize=False)

    async def run_macro_tick(self, dry_run: bool = False) -> None:
        """Fetch and write global macro sentiment (Fear & Greed)."""
        try:
            fng = await crypto_news_service.get_fear_greed()
            value = fng.get('value')
            if value is not None:
                if not dry_run:
                    await influx.write_global_sentiment(
                        index_value=value,
                        source="alternative.me"
                    )
                logger.info(f"[macro] Fetched Fear & Greed: {value}")
        except Exception as e:
            logger.warning(f"[macro] fetch failed: {e}")



# Module-level singleton (mirrors trading_loop / influx pattern)
sentiment_loop = SentimentLoopService()
