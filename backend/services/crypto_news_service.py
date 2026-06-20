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
    'BTCUSDT': ['bitcoin', 'btc'],
    'ETHUSDT': ['ethereum', 'eth'],
    'SOLUSDT': ['solana', 'sol'],
    'BNBUSDT': ['bnb', 'binance coin'],
    'XRPUSDT': ['xrp', 'ripple'],
    'ADAUSDT': ['cardano', 'ada'],
    'DOGEUSDT': ['dogecoin', 'doge'],
    'AVAXUSDT': ['avalanche', 'avax'],
    'DOTUSDT': ['polkadot', 'dot'],
    'LINKUSDT': ['chainlink', 'link'],
    'POLUSDT': ['polygon', 'matic'],
    'LTCUSDT': ['litecoin', 'ltc'],
    'UNIUSDT': ['uniswap', 'uni'],
    'ATOMUSDT': ['cosmos', 'atom'],
    'NEARUSDT': ['near protocol', 'near'],
    'OPUSDT': ['optimism', 'op'],
    'ARBUSDT': ['arbitrum', 'arb'],
    'APTUSDT': ['aptos', 'apt'],
    'INJUSDT': ['injective', 'inj'],
    'SUIUSDT': ['sui'],
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
    # 2. RSS-based Crypto News (replaces CryptoCompare which now requires key)
    # ══════════════════════════════════════════════════════════════════════
    _RSS_FEEDS = [
        ("Cointelegraph",   "https://cointelegraph.com/rss"),
        ("CoinDesk",        "https://www.coindesk.com/arc/outboundfeeds/rss/"),
        ("Decrypt",         "https://decrypt.co/feed"),
        ("BeInCrypto",      "https://beincrypto.com/feed/"),
        ("CryptoSlate",     "https://cryptoslate.com/feed/"),
    ]

    async def _fetch_rss_articles(self, client: httpx.AsyncClient, name: str, url: str) -> list[dict]:
        """Fetch and parse one RSS feed, return list of article dicts."""
        import re, html as html_mod, time as time_mod
        try:
            resp = await client.get(url, timeout=12.0, follow_redirects=True)
            resp.raise_for_status()
            content = resp.text
            # Extract <item> blocks
            item_blocks = re.findall(r'<item>(.*?)</item>', content, re.DOTALL)
            articles = []
            for block in item_blocks[:20]:
                title_m = re.search(r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', block, re.DOTALL)
                desc_m  = re.search(r'<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>', block, re.DOTALL)
                link_m  = re.search(r'<link>(.*?)</link>', block, re.DOTALL)
                title = html_mod.unescape((title_m.group(1) if title_m else '').strip())
                body  = html_mod.unescape((desc_m.group(1)  if desc_m  else '').strip())
                # Strip HTML tags from body
                body = re.sub(r'<[^>]+>', ' ', body)[:400]
                link = (link_m.group(1) if link_m else '').strip()
                if not title:
                    continue
                articles.append({
                    'title': title,
                    'body': body,
                    'source': name,
                    'url': link,
                    'published_at': int(time_mod.time()),
                    'categories': 'crypto',
                    'sentiment': self._classify_sentiment(f"{title} {body}"),
                })
            return articles
        except Exception as e:
            logger.warning(f"RSS feed {name} error: {e}")
            return []

    async def get_crypto_news(self, symbols: list[str] = None) -> list[dict]:
        """Get crypto news from free RSS feeds with sentiment classification."""
        cache_key = f"rss_news:{','.join(symbols) if symbols else 'all'}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            import asyncio
            async with httpx.AsyncClient(
                timeout=15.0,
                headers={"User-Agent": "Mozilla/5.0 (compatible; crypto-bot/1.0)"},
            ) as client:
                tasks = [self._fetch_rss_articles(client, name, url)
                         for name, url in self._RSS_FEEDS]
                results = await asyncio.gather(*tasks, return_exceptions=True)

            all_articles: list[dict] = []
            for r in results:
                if isinstance(r, list):
                    all_articles.extend(r)

            # De-duplicate by title prefix
            seen: set[str] = set()
            unique: list[dict] = []
            for a in all_articles:
                key = a['title'][:60].lower()
                if key not in seen:
                    seen.add(key)
                    unique.append(a)

            # Filter by symbol keywords if requested
            if symbols:
                filtered: list[dict] = []
                for article in unique:
                    text_lower = f"{article['title']} {article['body']}".lower()
                    for sym in symbols:
                        lookup = sym.upper()
                        if lookup.endswith('USDC'):
                            lookup = lookup[:-4] + 'USDT'
                        base = lookup.lower().replace('usdt', '')
                        keywords = SYMBOL_KEYWORDS.get(lookup, [base])
                        if any(kw in text_lower for kw in keywords):
                            article['related_symbol'] = sym
                            filtered.append(article)
                            break
                articles = filtered
            else:
                articles = unique

            logger.info(f"RSS news: fetched {len(all_articles)} raw → {len(articles)} matched")
            self._set_cache(cache_key, articles)
            return articles
        except Exception as e:
            logger.error(f"RSS news aggregation error: {e}")
            return []

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
