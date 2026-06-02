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
    # 2. CryptoCompare News
    # ══════════════════════════════════════════════════════════════════════
    async def get_crypto_news(self, symbols: list[str] = None) -> list[dict]:
        """Get crypto news from CryptoCompare with sentiment classification."""
        cache_key = f"news:{','.join(symbols) if symbols else 'all'}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    'https://min-api.cryptocompare.com/data/v2/news/',
                    params={'lang': 'EN', 'sortOrder': 'latest'},
                )
                resp.raise_for_status()
                data = resp.json()

            articles = []
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

                # Filter by symbols if provided
                if symbols:
                    text_lower = f"{title} {body}".lower()
                    matched = False
                    for sym in symbols:
                        keywords = SYMBOL_KEYWORDS.get(sym, [sym.lower().replace('usdt', '')])
                        if any(kw in text_lower for kw in keywords):
                            matched = True
                            article['related_symbol'] = sym
                            break
                    if not matched:
                        continue

                articles.append(article)

            self._set_cache(cache_key, articles)
            return articles
        except Exception as e:
            logger.error(f"CryptoCompare news error: {e}")
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
