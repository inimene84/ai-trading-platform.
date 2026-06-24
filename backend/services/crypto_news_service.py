"""
Dedicated Crypto News Service
Aggregates news from free crypto APIs: CoinGecko, CryptoCompare, Alternative.me
No API keys required for any endpoint.
"""

import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
CACHE_TTL = 300  # 5 minutes

RSS_FEEDS = [
    ("CoinDesk",       "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("CoinTelegraph",  "https://cointelegraph.com/rss"),
    ("Reddit Crypto",  "https://www.reddit.com/r/CryptoCurrency/new/.rss"),
    ("Yahoo Finance",  "https://feeds.finance.yahoo.com/rss/2.0/headline?s=BTC-USD,ETH-USD,SOL-USD&region=US&lang=en-US"),
    ("Bitcoinist",     "https://bitcoinist.com/feed/"),
    ("NewsBTC",        "https://www.newsbtc.com/feed/"),
    ("CryptoPotato",   "https://cryptopotato.com/feed/"),
    ("99Bitcoins",     "https://99bitcoins.com/feed/"),
    ("CryptoBriefing", "https://cryptobriefing.com/feed/"),
    ("Crypto.news",    "https://crypto.news/feed/"),
    ("BitcoinMagazine","https://bitcoinmagazine.com/.rss/full/"),
]

POSITIVE_WORDS = {
    'rally', 'surge', 'bullish', 'gains', 'gain', 'rises', 'rise', 'soar',
    'jump', 'jumps', 'record', 'high', 'recover', 'recovery', 'boom',
    'growth', 'profit', 'profits', 'strong', 'positive', 'upward', 'up',
    'breakout', 'outperform', 'beat', 'beats', 'upgrade', 'buy', 'moon',
    'pump', 'adoption', 'partnership', 'launch', 'approval', 'etf',
}

NEGATIVE_WORDS = {
    'crash', 'drop', 'drops', 'bearish', 'loss', 'losses', 'fall', 'falls',
    'plunge', 'plunges', 'sink', 'sinks', 'decline', 'declines', 'down',
    'fear', 'risk', 'warn', 'warning', 'sell', 'downgrade', 'weak',
    'negative', 'low', 'slump', 'tumble', 'tumbles', 'panic', 'collapse',
    'hack', 'exploit', 'scam', 'fraud', 'ban', 'regulation', 'dump',
    'liquidation', 'bankruptcy', 'sec', 'lawsuit',
}

# Symbol to keyword mapping for news filtering
SYMBOL_KEYWORDS = {
    'BTCUSDT':  ['bitcoin', 'btc'],
    'ETHUSDT':  ['ethereum', 'ether', 'eth'],
    'BNBUSDT':  ['binance', 'bnb', 'binance coin'],
    'SOLUSDT':  ['solana', 'sol'],
    'XRPUSDT':  ['ripple', 'xrp'],
    'ADAUSDT':  ['cardano', 'ada'],
    'DOTUSDT':  ['polkadot', 'dot'],
    'DOGEUSDT': ['dogecoin', 'doge'],
    'LTCUSDT':  ['litecoin', 'ltc'],
    'LINKUSDT': ['chainlink', 'link'],
    'AVAXUSDT': ['avalanche', 'avax'],
    'MATICUSDT':['polygon', 'matic'],
    'ICPUSDT':  ['internet computer', 'icp', 'dfinity'],
    'ATOMUSDT': ['cosmos', 'atom'],
    'NEARUSDT': ['near protocol', 'near'],
    'UNIUSDT':  ['uniswap', 'uni'],
    'ARBUSDT':  ['arbitrum', 'arb'],
    'OPUSDT':   ['optimism', 'op'],
    'SUIUSDT':  ['sui'],
    'APTUSDT':  ['aptos', 'apt'],
    'INJUSDT':  ['injective', 'inj'],
    'FILUSDT':  ['filecoin', 'fil'],
    'TRXUSDT':  ['tron', 'trx'],
    'XLMUSDT':  ['stellar', 'xlm'],
    'HBARUSDT': ['hedera', 'hbar'],
    'PEPEUSDT': ['pepe'],
    'SHIBUSDT': ['shiba', 'shib'],
    'WIFUSDT':  ['dogwifhat', 'wif'],
    'TONUSDT':  ['toncoin', 'ton'],
}


class CryptoNewsService:
    """Aggregates crypto news from free APIs."""

    def __init__(self):
        self._cache: dict[str, dict] = {}

    def _get_cached(self, key: str) -> Optional[any]:
        entry = self._cache.get(key)
        if entry and (time.time() - entry['ts']) < CACHE_TTL:
            return entry['data']
        return None

    def _set_cache(self, key: str, data: any):
        self._cache[key] = {'ts': time.time(), 'data': data}

    def _classify_sentiment(self, text: str) -> str:
        """Simple keyword-based sentiment classification."""
        text_lower = text.lower()
        words = set(text_lower.split())
        pos_count = len(words & POSITIVE_WORDS)
        neg_count = len(words & NEGATIVE_WORDS)
        if pos_count > neg_count:
            return 'positive'
        elif neg_count > pos_count:
            return 'negative'
        return 'neutral'

    # ══════════════════════════════════════════════════════════════════════
    # 1. CoinGecko Trending
    # ══════════════════════════════════════════════════════════════════════
    async def get_trending_coins(self) -> list[dict]:
        """Get trending coins from CoinGecko."""
        cache_key = 'trending'
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get('https://api.coingecko.com/api/v3/search/trending')
                resp.raise_for_status()
                data = resp.json()

            coins = []
            for item in data.get('coins', []):
                coin = item.get('item', {})
                coins.append({
                    'name': coin.get('name', ''),
                    'symbol': coin.get('symbol', ''),
                    'market_cap_rank': coin.get('market_cap_rank'),
                    'price_btc': coin.get('price_btc', 0),
                    'score': coin.get('score', 0),
                    'slug': coin.get('slug', ''),
                })

            self._set_cache(cache_key, coins)
            return coins
        except Exception as e:
            logger.error(f"CoinGecko trending error: {e}")
            return []

    # ══════════════════════════════════════════════════════════════════════
    # 2. CryptoCompare News + RSS Fallback
    # ══════════════════════════════════════════════════════════════════════
    async def _fetch_rss_feed(self, source: str, url: str) -> list:
        """Fetch and parse a single RSS feed, return list of news items."""
        try:
            import feedparser
            import concurrent.futures
            import asyncio
            loop = asyncio.get_event_loop()
            with concurrent.futures.ThreadPoolExecutor() as pool:
                feed = await loop.run_in_executor(pool, feedparser.parse, url)

            items = []
            for entry in feed.entries[:15]:
                title = getattr(entry, "title", "") or ""
                summary = getattr(entry, "summary", "") or ""
                import re
                summary = re.sub(r"<[^>]+>", " ", summary).strip()[:300]
                link = getattr(entry, "link", "") or ""
                pub = ""
                if hasattr(entry, "published"):
                    pub = entry.published
                elif hasattr(entry, "updated"):
                    pub = entry.updated

                sentiment = self._classify_sentiment(title + " " + summary)
                items.append({
                    "title": title,
                    "body": summary,
                    "url": link,
                    "source": source,
                    "published_at": pub,
                    "categories": "",
                    "sentiment": sentiment,
                })
            return items
        except Exception as e:
            logger.warning(f"Feed {source} failed: {e}")
            return []

    async def get_rss_news(self) -> list[dict]:
        """Fetch news from all configured RSS feeds."""
        import asyncio
        tasks = [self._fetch_rss_feed(src, url) for src, url in RSS_FEEDS]
        results = await asyncio.gather(*tasks)
        all_items = []
        for r in results:
            all_items.extend(r)
        
        all_items.sort(key=lambda x: str(x.get("published_at", "")), reverse=True)
        return all_items

    async def get_crypto_news(self, symbols: list[str] = None) -> list[dict]:
        """Get crypto news from CryptoCompare with sentiment classification. Falls back to RSS."""
        import os
        cache_key = f"news:{','.join(symbols) if symbols else 'all'}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        articles = []
        try:
            api_key = os.getenv("CRYPTOCOMPARE_API_KEY", "")
            headers = {"authorization": f"Apikey {api_key}"} if api_key else {}
            
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    'https://min-api.cryptocompare.com/data/v2/news/',
                    params={'lang': 'EN', 'sortOrder': 'latest'},
                    headers=headers
                )
                resp.raise_for_status()
                data = resp.json()

            for item in data.get('Data', []):
                title = item.get('title', '')
                body = item.get('body', '')
                sentiment = self._classify_sentiment(f"{title} {body}")

                article = {
                    'title': title,
                    'body': body[:300],
                    'source': item.get('source', ''),
                    'url': item.get('url', ''),
                    'published_at': item.get('published_on', 0),
                    'categories': item.get('categories', ''),
                    'sentiment': sentiment,
                }
                articles.append(article)
                
        except Exception as e:
            logger.warning(f"CryptoCompare news error: {e}. Falling back to RSS feeds.")
            
        # Fallback to RSS if CryptoCompare fails or returns no data
        if not articles:
            articles = await self.get_rss_news()
            
        # Filter by symbols if provided
        filtered_articles = []
        for article in articles:
            if symbols:
                title = article.get('title', '')
                body = article.get('body', '')
                text_lower = f"{title} {body}".lower()
                matched = False
                for sym in symbols:
                    lookup = sym.upper()
                    if lookup.endswith('USDC'):
                        lookup = lookup[:-4] + 'USDT'
                    base = lookup.lower().replace('usdt', '')
                    keywords = SYMBOL_KEYWORDS.get(lookup, [base])
                    if any(kw in text_lower for kw in keywords):
                        matched = True
                        article['related_symbol'] = sym
                        break
                if matched:
                    filtered_articles.append(article)
            else:
                filtered_articles.append(article)

        self._set_cache(cache_key, filtered_articles)
        return filtered_articles

    # ══════════════════════════════════════════════════════════════════════
    # 3. Fear & Greed Index
    # ══════════════════════════════════════════════════════════════════════
    async def get_fear_greed(self) -> dict:
        """Get current Fear & Greed index from Alternative.me."""
        cache_key = 'fear_greed'
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get('https://api.alternative.me/fng/?limit=7')
                resp.raise_for_status()
                data = resp.json()

            entries = data.get('data', [])
            if not entries:
                return {}

            current = entries[0]
            result = {
                'value': int(current.get('value', 0)),
                'classification': current.get('value_classification', ''),
                'timestamp': current.get('timestamp', ''),
                'history': [
                    {
                        'value': int(e.get('value', 0)),
                        'classification': e.get('value_classification', ''),
                        'timestamp': e.get('timestamp', ''),
                    }
                    for e in entries
                ],
            }

            self._set_cache(cache_key, result)
            return result
        except Exception as e:
            logger.error(f"Fear & Greed index error: {e}")
            return {}

    # ══════════════════════════════════════════════════════════════════════
    # Combined summary for trading loop
    # ══════════════════════════════════════════════════════════════════════
    async def get_market_summary(self, symbols: list[str] = None) -> dict:
        """Get a combined market summary for the trading loop."""
        import asyncio
        news, trending, fng = await asyncio.gather(
            self.get_crypto_news(symbols),
            self.get_trending_coins(),
            self.get_fear_greed(),
            return_exceptions=True,
        )
        return {
            'news': news if isinstance(news, list) else [],
            'trending': trending if isinstance(trending, list) else [],
            'fear_greed': fng if isinstance(fng, dict) else {},
            'timestamp': time.time(),
        }

    def clear_cache(self):
        """Clear all cached data."""
        self._cache.clear()
        logger.info("Crypto news cache cleared")


# ── Singleton instance ────────────────────────────────────────────────────────
crypto_news_service = CryptoNewsService()
