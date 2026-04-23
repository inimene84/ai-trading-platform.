"""News and market data routes for the News & Data Panel."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse
import asyncio
import time
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/news", tags=["news"])

# ─── Simple in-memory cache ───────────────────────────────────────────────────
_cache: dict = {}
CACHE_TTL = 300  # 5 minutes

def _cached(key: str):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL:
        return entry["data"]
    return None

def _set_cache(key: str, data):
    _cache[key] = {"ts": time.time(), "data": data}

# ─── Sentiment detection ──────────────────────────────────────────────────────
POSITIVE_WORDS = {
    "rally", "surge", "bullish", "gains", "gain", "rises", "rise", "soar",
    "jump", "jumps", "record", "high", "recover", "recovery", "boom",
    "growth", "profit", "profits", "strong", "positive", "upward", "up",
    "breakout", "outperform", "beat", "beats", "upgrade", "buy",
}
NEGATIVE_WORDS = {
    "crash", "drop", "drops", "bearish", "loss", "losses", "fall", "falls",
    "plunge", "plunges", "sink", "sinks", "decline", "declines", "down",
    "fear", "risk", "warn", "warning", "sell", "downgrade", "weak",
    "negative", "low", "slump", "tumble", "tumbles", "panic", "collapse",
}

def _detect_sentiment(text: str) -> str:
    words = set(text.lower().split())
    pos = len(words & POSITIVE_WORDS)
    neg = len(words & NEGATIVE_WORDS)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"

# ─── RSS Feed ─────────────────────────────────────────────────────────────────
RSS_FEEDS = [
    ("CoinDesk",       "https://feeds.feedburner.com/CoinDesk"),
    ("CoinTelegraph",  "https://cointelegraph.com/rss"),
    ("Reddit Crypto",  "https://www.reddit.com/r/CryptoCurrency/new/.rss"),
    ("Yahoo Finance",  "https://feeds.finance.yahoo.com/rss/2.0/headline?s=BTC-USD,ETH-USD,SOL-USD&region=US&lang=en-US"),
]

async def _fetch_feed(source: str, url: str) -> list:
    """Fetch and parse a single RSS feed, return list of news items."""
    try:
        import feedparser
        import concurrent.futures
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            feed = await loop.run_in_executor(pool, feedparser.parse, url)

        items = []
        for entry in feed.entries[:15]:  # cap at 15 per source
            title   = getattr(entry, "title", "") or ""
            summary = getattr(entry, "summary", "") or ""
            # strip HTML tags simply
            import re
            summary = re.sub(r"<[^>]+>", " ", summary).strip()[:300]
            link    = getattr(entry, "link", "") or ""
            pub     = ""
            if hasattr(entry, "published"):
                pub = entry.published
            elif hasattr(entry, "updated"):
                pub = entry.updated

            sentiment = _detect_sentiment(title + " " + summary)
            items.append({
                "title":     title,
                "summary":   summary,
                "url":       link,
                "source":    source,
                "published": pub,
                "sentiment": sentiment,
            })
        return items
    except Exception as e:
        logger.warning(f"Feed {source} failed: {e}")
        return []


@router.get("/feed")
async def get_news_feed():
    """Return mixed crypto+market news from multiple RSS feeds."""
    cached = _cached("news_feed")
    if cached:
        return cached

    tasks = [_fetch_feed(src, url) for src, url in RSS_FEEDS]
    results = await asyncio.gather(*tasks)

    all_items = []
    for r in results:
        all_items.extend(r)

    # sort by published desc (best-effort string sort)
    all_items.sort(key=lambda x: x["published"], reverse=True)

    response = {
        "items":     all_items,
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "count":     len(all_items),
    }
    _set_cache("news_feed", response)
    return response


# ─── Fear & Greed ─────────────────────────────────────────────────────────────
@router.get("/fear-greed")
async def get_fear_greed():
    """Return Fear & Greed index from alternative.me."""
    cached = _cached("fear_greed")
    if cached:
        return cached

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://api.alternative.me/fng/?limit=7")
            data = resp.json()

        latest   = data["data"][0]
        history  = data["data"]
        response = {
            "value":                int(latest["value"]),
            "value_classification": latest["value_classification"],
            "timestamp":            latest["timestamp"],
            "history": [
                {
                    "value":                int(h["value"]),
                    "value_classification": h["value_classification"],
                    "timestamp":            h["timestamp"],
                }
                for h in history
            ],
        }
    except Exception as e:
        logger.warning(f"Fear & Greed fetch failed: {e}")
        response = {
            "value": 50,
            "value_classification": "Neutral",
            "timestamp": str(int(time.time())),
            "history": [],
        }

    _set_cache("fear_greed", response)
    return response


# ─── Economic Calendar ────────────────────────────────────────────────────────
@router.get("/economic-calendar")
async def get_economic_calendar():
    """Return upcoming economic events (curated mock data for reliability)."""
    # Using realistic mock data since free live calendar APIs require registration
    from datetime import date, timedelta
    today = date.today()
    events = [
        {
            "time": f"{today.strftime('%b %d')} 08:30",
            "currency": "USD",
            "event": "Non-Farm Payrolls",
            "impact": "high",
            "forecast": "185K",
            "previous": "177K",
        },
        {
            "time": f"{today.strftime('%b %d')} 10:00",
            "currency": "USD",
            "event": "ISM Manufacturing PMI",
            "impact": "high",
            "forecast": "49.5",
            "previous": "50.3",
        },
        {
            "time": f"{today.strftime('%b %d')} 14:00",
            "currency": "USD",
            "event": "FOMC Meeting Minutes",
            "impact": "high",
            "forecast": "-",
            "previous": "-",
        },
        {
            "time": f"{(today + timedelta(days=1)).strftime('%b %d')} 09:00",
            "currency": "EUR",
            "event": "ECB Interest Rate Decision",
            "impact": "high",
            "forecast": "3.40%",
            "previous": "3.65%",
        },
        {
            "time": f"{(today + timedelta(days=1)).strftime('%b %d')} 08:30",
            "currency": "USD",
            "event": "CPI (YoY)",
            "impact": "high",
            "forecast": "3.1%",
            "previous": "3.2%",
        },
        {
            "time": f"{(today + timedelta(days=1)).strftime('%b %d')} 08:30",
            "currency": "USD",
            "event": "Unemployment Claims",
            "impact": "medium",
            "forecast": "212K",
            "previous": "219K",
        },
        {
            "time": f"{(today + timedelta(days=2)).strftime('%b %d')} 10:00",
            "currency": "USD",
            "event": "Consumer Confidence",
            "impact": "medium",
            "forecast": "104.0",
            "previous": "104.7",
        },
        {
            "time": f"{(today + timedelta(days=2)).strftime('%b %d')} 08:30",
            "currency": "USD",
            "event": "GDP (QoQ)",
            "impact": "high",
            "forecast": "2.4%",
            "previous": "3.2%",
        },
        {
            "time": f"{(today + timedelta(days=3)).strftime('%b %d')} 08:30",
            "currency": "CAD",
            "event": "Employment Change",
            "impact": "medium",
            "forecast": "25.0K",
            "previous": "22.1K",
        },
        {
            "time": f"{(today + timedelta(days=3)).strftime('%b %d')} 03:00",
            "currency": "CNY",
            "event": "Caixin Manufacturing PMI",
            "impact": "medium",
            "forecast": "50.3",
            "previous": "51.2",
        },
        {
            "time": f"{(today + timedelta(days=4)).strftime('%b %d')} 05:30",
            "currency": "GBP",
            "event": "BoE Interest Rate Decision",
            "impact": "high",
            "forecast": "4.50%",
            "previous": "4.75%",
        },
        {
            "time": f"{(today + timedelta(days=4)).strftime('%b %d')} 08:30",
            "currency": "USD",
            "event": "PPI (MoM)",
            "impact": "low",
            "forecast": "0.2%",
            "previous": "0.4%",
        },
    ]
    return {"events": events}


# ─── Market Sentiment ─────────────────────────────────────────────────────────
@router.get("/market-sentiment")
async def get_market_sentiment():
    """Return bull/bear sentiment and top movers from yfinance."""
    cached = _cached("market_sentiment")
    if cached:
        return cached

    SYMBOLS = ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD",
               "DOGE-USD", "ADA-USD", "AVAX-USD", "MATIC-USD", "LINK-USD"]
    top_movers = []
    positive_count = 0
    negative_count = 0
    neutral_count   = 0

    try:
        import yfinance as yf
        import concurrent.futures
        loop = asyncio.get_event_loop()

        def _fetch_tickers():
            tickers = yf.Tickers(" ".join(SYMBOLS))
            results = []
            for sym in SYMBOLS:
                try:
                    t = tickers.tickers[sym]
                    info = t.fast_info
                    price      = float(info.last_price or 0)
                    prev_close = float(info.previous_close or price)
                    if prev_close == 0:
                        continue
                    pct = ((price - prev_close) / prev_close) * 100
                    results.append({
                        "symbol": sym.replace("-USD", ""),
                        "price":  round(price, 4),
                        "change_pct": round(pct, 2),
                    })
                except Exception:
                    pass
            return results

        with concurrent.futures.ThreadPoolExecutor() as pool:
            top_movers = await loop.run_in_executor(pool, _fetch_tickers)

        for m in top_movers:
            if m["change_pct"] > 0:
                positive_count += 1
            elif m["change_pct"] < 0:
                negative_count += 1
            else:
                neutral_count += 1

    except Exception as e:
        logger.warning(f"Market sentiment fetch failed: {e}")
        # Fallback mock data
        top_movers = [
            {"symbol": "BTC", "price": 67234.5, "change_pct": 2.3, "direction": "up"},
            {"symbol": "ETH", "price": 3521.8, "change_pct": 1.7, "direction": "up"},
            {"symbol": "SOL", "price": 142.3, "change_pct": -0.8, "direction": "down"},
            {"symbol": "BNB", "price": 589.4, "change_pct": 0.5, "direction": "up"},
            {"symbol": "XRP", "price": 0.527, "change_pct": -1.2, "direction": "down"},
        ]
        positive_count = 3
        negative_count = 2
        neutral_count = 0

    total = max(positive_count + negative_count + neutral_count, 1)
    response = {
        "bull_pct":    round((positive_count / total) * 100, 1),
        "bear_pct":    round((negative_count / total) * 100, 1),
        "neutral_pct": round((neutral_count  / total) * 100, 1),
        "top_movers":  sorted(top_movers, key=lambda x: abs(x["change_pct"]), reverse=True)[:8],
        "total_tracked": total,
    }
    _set_cache("market_sentiment", response)
    return response
