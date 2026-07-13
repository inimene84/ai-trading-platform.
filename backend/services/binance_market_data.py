"""
Binance Native Market Data Service
Fetches OHLCV klines, funding rates, open interest, 24h tickers,
and liquidations directly from Binance Futures REST API.
All public endpoints work WITHOUT API keys.
"""

import asyncio
import logging
import os
import time
from typing import Optional

import atexit

import httpx
from dotenv import load_dotenv
from pathlib import Path

_env_path = Path(__file__).resolve().parents[2] / '.env'
load_dotenv(_env_path, override=True)

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
BINANCE_FAPI_BASE = "https://fapi.binance.com"
CACHE_TTL = 300  # 5 minutes (klines, OI, funding)
TICKER_CACHE_TTL = int(os.getenv("TICKER_CACHE_TTL_SEC", "15"))  # dashboard price polls

# Max concurrent outbound requests to Binance public API.
# Prevents burst overload that triggers IP bans (2400 req/min limit).
_REQUEST_SEMAPHORE = asyncio.Semaphore(10)

DEFAULT_SYMBOLS = [
    'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT',
    'ADAUSDT', 'DOGEUSDT', 'AVAXUSDT', 'DOTUSDT', 'LINKUSDT',
    'POLUSDT', 'LTCUSDT', 'UNIUSDT', 'ATOMUSDT', 'NEARUSDT',
    'OPUSDT', 'ARBUSDT', 'APTUSDT', 'INJUSDT',
]


class BinanceMarketDataService:
    """Async service for fetching Binance Futures public market data."""

    def __init__(self):
        self._cache: dict[str, dict] = {}
        self._api_key = os.getenv('BINANCE_API_KEY', '')
        # Accept both BINANCE_SECRET_KEY (compose) and BINANCE_API_SECRET (legacy)
        self._api_secret = os.getenv('BINANCE_SECRET_KEY', '') or os.getenv('BINANCE_API_SECRET', '')
        # Prefer the symbols the trading loop actually trades; fall back to USDT set
        env_symbols = os.getenv('TRADING_SYMBOLS', '')
        if env_symbols:
            self.symbols = [s.strip().upper() for s in env_symbols.split(',') if s.strip()]
        else:
            self.symbols = DEFAULT_SYMBOLS
        # Shared HTTP client — connection pooling avoids per-request TCP overhead
        # and lets us reuse keep-alive connections.
        self._http_client: Optional[httpx.AsyncClient] = None

    # ── Cache helpers ─────────────────────────────────────────────────────
    def _get_cached(self, key: str, ttl: Optional[int] = None) -> Optional[any]:
        entry = self._cache.get(key)
        limit = CACHE_TTL if ttl is None else ttl
        if entry and (time.time() - entry['ts']) < limit:
            return entry['data']
        return None

    def _set_cache(self, key: str, data: any, ttl: Optional[int] = None):
        self._cache[key] = {'ts': time.time(), 'data': data, 'ttl': ttl}

    # ── HTTP helper ───────────────────────────────────────────────────────
    async def _get_client(self) -> httpx.AsyncClient:
        """Return (and lazily create) a shared httpx.AsyncClient."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=15.0,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._http_client

    async def close(self):
        """Close the shared HTTP client (call on shutdown)."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    async def _request(
        self,
        endpoint: str,
        params: dict = None,
        use_api_key: bool = False,
        timeout: float = 15.0,
    ) -> Optional[any]:
        """Make a GET request to Binance Futures API.

        Uses a shared connection-pooled client and an asyncio.Semaphore
        to cap concurrent requests.  On HTTP 429 (rate-limited), honours
        the Retry-After header and retries once.
        """
        url = f"{BINANCE_FAPI_BASE}{endpoint}"
        headers = {}
        if use_api_key and self._api_key:
            headers['X-MBX-APIKEY'] = self._api_key

        async with _REQUEST_SEMAPHORE:
            client = await self._get_client()
            for attempt in range(2):  # at most 1 retry on 429
                try:
                    resp = await client.get(
                        url, params=params, headers=headers, timeout=timeout,
                    )
                    # ── Handle 429 / rate-limit with back-off ──
                    if resp.status_code == 429 and attempt == 0:
                        retry_after = int(resp.headers.get('Retry-After', '5'))
                        retry_after = min(retry_after, 60)  # cap at 60s
                        logger.warning(
                            f"Binance API 429 on {endpoint} — backing off {retry_after}s"
                        )
                        await asyncio.sleep(retry_after)
                        continue
                    resp.raise_for_status()
                    return resp.json()
                except httpx.TimeoutException:
                    logger.error(f"Binance API timeout: {endpoint}")
                    return None
                except httpx.HTTPStatusError as e:
                    logger.error(
                        f"Binance API HTTP {e.response.status_code}: "
                        f"{endpoint} - {e.response.text[:200]}"
                    )
                    return None
                except Exception as e:
                    logger.error(f"Binance API error: {endpoint} - {e}")
                    return None
        return None  # unreachable, keeps linters happy

    # ══════════════════════════════════════════════════════════════════════
    # 1. OHLCV Klines
    # ══════════════════════════════════════════════════════════════════════
    async def get_klines(
        self,
        symbol: str,
        interval: str = '1h',
        limit: int = 2000,
    ) -> list[dict]:
        """Fetch OHLCV klines from Binance Futures.
        Returns list of bar dicts: [{date, open, high, low, close, volume}, ...]
        """
        cache_key = f"klines:{symbol}:{interval}:{limit}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        data = await self._request('/fapi/v1/klines', {
            'symbol': symbol.upper(),
            'interval': interval,
            'limit': min(limit, 1500),  # Binance max is 1500
        })
        if not data:
            return []

        bars = []
        for k in data:
            # Kline format: [openTime, open, high, low, close, volume, closeTime, ...]
            bars.append({
                'date': self._ms_to_iso(k[0]),
                'open': float(k[1]),
                'high': float(k[2]),
                'low': float(k[3]),
                'close': float(k[4]),
                'volume': float(k[5]),
            })

        self._set_cache(cache_key, bars)
        logger.info(f"Binance klines: {symbol} {interval} -> {len(bars)} bars")
        return bars

    # ══════════════════════════════════════════════════════════════════════
    # 2. Funding Rate
    # ══════════════════════════════════════════════════════════════════════
    async def get_funding_rate(self, symbol: str) -> Optional[dict]:
        """Get latest funding rate for a symbol."""
        cache_key = f"funding:{symbol}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        data = await self._request('/fapi/v1/fundingRate', {
            'symbol': symbol.upper(),
            'limit': 1,
        })
        if not data or len(data) == 0:
            return None

        entry = data[-1]
        result = {
            'symbol': entry.get('symbol', symbol),
            'fundingRate': float(entry.get('fundingRate', 0)),
            'fundingTime': self._ms_to_iso(entry.get('fundingTime', 0)),
        }
        self._set_cache(cache_key, result)
        return result

    async def get_all_funding_rates(self, symbols: list[str] = None) -> list[dict]:
        """Get funding rates for all configured symbols."""
        cache_key = "funding:all"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        target_symbols = symbols or self.symbols
        # Use premiumIndex endpoint for batch funding rates (no auth needed)
        data = await self._request('/fapi/v1/premiumIndex')
        if not data:
            # Fallback: fetch individually
            results = []
            for sym in target_symbols:
                rate = await self.get_funding_rate(sym)
                if rate:
                    results.append(rate)
            return results

        target_set = set(s.upper() for s in target_symbols)
        results = []
        for entry in data:
            sym = entry.get('symbol', '')
            if sym in target_set:
                results.append({
                    'symbol': sym,
                    'fundingRate': float(entry.get('lastFundingRate', 0)),
                    'fundingTime': self._ms_to_iso(entry.get('nextFundingTime', 0)),
                    'markPrice': float(entry.get('markPrice', 0)),
                    'indexPrice': float(entry.get('indexPrice', 0)),
                })

        self._set_cache(cache_key, results)
        return results

    # ══════════════════════════════════════════════════════════════════════
    # 3. Open Interest
    # ══════════════════════════════════════════════════════════════════════
    async def get_open_interest(self, symbol: str) -> Optional[dict]:
        """Get current open interest for a symbol."""
        cache_key = f"oi:{symbol}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        data = await self._request('/fapi/v1/openInterest', {
            'symbol': symbol.upper(),
        })
        if not data:
            return None

        result = {
            'symbol': data.get('symbol', symbol),
            'openInterest': float(data.get('openInterest', 0)),
            'time': self._ms_to_iso(data.get('time', 0)),
        }
        self._set_cache(cache_key, result)
        return result

    async def get_all_open_interest(self, symbols: list[str] = None) -> list[dict]:
        """Get open interest for all configured symbols."""
        cache_key = "oi:all"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        target_symbols = symbols or self.symbols
        results = []
        # OI endpoint only supports single symbol, fetch concurrently
        tasks = [self.get_open_interest(sym) for sym in target_symbols]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in raw_results:
            if isinstance(r, dict):
                results.append(r)

        self._set_cache(cache_key, results)
        return results

    # ══════════════════════════════════════════════════════════════════════
    # 4. 24h Ticker Stats
    # ══════════════════════════════════════════════════════════════════════
    async def get_ticker_24h(self, symbol: str) -> Optional[dict]:
        """Get 24h ticker statistics for a symbol."""
        cache_key = f"ticker24h:{symbol}"
        cached = self._get_cached(cache_key, ttl=TICKER_CACHE_TTL)
        if cached is not None:
            return cached

        data = await self._request('/fapi/v1/ticker/24hr', {
            'symbol': symbol.upper(),
        })
        if not data:
            return None

        result = {
            'symbol': data.get('symbol', symbol),
            'priceChange': float(data.get('priceChange', 0)),
            'priceChangePercent': float(data.get('priceChangePercent', 0)),
            'weightedAvgPrice': float(data.get('weightedAvgPrice', 0)),
            'lastPrice': float(data.get('lastPrice', 0)),
            'volume': float(data.get('volume', 0)),
            'quoteVolume': float(data.get('quoteVolume', 0)),
            'highPrice': float(data.get('highPrice', 0)),
            'lowPrice': float(data.get('lowPrice', 0)),
            'count': int(data.get('count', 0)),
        }
        self._set_cache(cache_key, result, ttl=TICKER_CACHE_TTL)
        try:
            from backend.services.data_hub import DataHub
            DataHub().publish(f"market:quote:{symbol.upper()}", {
                "price": result["lastPrice"],
                "change24h": result["priceChangePercent"],
                "volume": result["volume"],
                "high": result["highPrice"],
                "low": result["lowPrice"],
                "timestamp": self._ms_to_iso(data.get("closeTime", 0)),
            }, ttl_ms=60_000)
        except Exception:
            pass
        return result

    async def get_all_tickers_24h(self, symbols: list[str] = None) -> list[dict]:
        """Get 24h tickers for requested symbols (filters from cached full snapshot)."""
        target_symbols = symbols or self.symbols
        target_set = {s.upper() for s in target_symbols}

        # Cache the FULL Binance response — never cache a symbol-filtered subset.
        # A filtered cache caused stale entries when TRADING_SYMBOLS grew (13/20
        # pairs falsely reported as no-ticker in the symbol-quality gate).
        raw_key = "ticker24h:raw"
        data = self._get_cached(raw_key, ttl=TICKER_CACHE_TTL)
        if data is None:
            data = await self._request('/fapi/v1/ticker/24hr')
            if not data:
                return []
            self._set_cache(raw_key, data, ttl=TICKER_CACHE_TTL)

        results = []
        for entry in data:
            sym = entry.get('symbol', '')
            if sym in target_set:
                results.append({
                    'symbol': sym,
                    'priceChange': float(entry.get('priceChange', 0)),
                    'priceChangePercent': float(entry.get('priceChangePercent', 0)),
                    'weightedAvgPrice': float(entry.get('weightedAvgPrice', 0)),
                    'lastPrice': float(entry.get('lastPrice', 0)),
                    'volume': float(entry.get('volume', 0)),
                    'quoteVolume': float(entry.get('quoteVolume', 0)),
                    'highPrice': float(entry.get('highPrice', 0)),
                    'lowPrice': float(entry.get('lowPrice', 0)),
                    'count': int(entry.get('count', 0)),
                })

        return results

    # ══════════════════════════════════════════════════════════════════════
    # 5. Liquidations (requires API key)
    # ══════════════════════════════════════════════════════════════════════
    async def get_recent_liquidations(
        self,
        symbol: str,
        limit: int = 50,
    ) -> list[dict]:
        """Get recent forced liquidation orders. Requires API key."""
        if not self._api_key:
            return []

        cache_key = f"liquidations:{symbol}:{limit}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        data = await self._request(
            '/fapi/v1/forceOrders',
            params={'symbol': symbol.upper(), 'limit': min(limit, 100)},
            use_api_key=True,
        )
        if not data:
            return []

        results = []
        for entry in data:
            results.append({
                'symbol': entry.get('symbol', symbol),
                'side': entry.get('side', ''),
                'type': entry.get('type', ''),
                'quantity': float(entry.get('origQty', 0)),
                'price': float(entry.get('price', 0)),
                'avgPrice': float(entry.get('averagePrice', 0)),
                'status': entry.get('status', ''),
                'time': self._ms_to_iso(entry.get('time', 0)),
            })

        self._set_cache(cache_key, results)
        return results

    # ══════════════════════════════════════════════════════════════════════
    # Helpers
    # ══════════════════════════════════════════════════════════════════════
    @staticmethod
    def _ms_to_iso(ms_timestamp: int) -> str:
        """Convert millisecond timestamp to ISO 8601 string."""
        if not ms_timestamp:
            return ''
        from datetime import datetime, timezone
        return datetime.fromtimestamp(
            ms_timestamp / 1000, tz=timezone.utc
        ).isoformat()

    def clear_cache(self):
        """Clear all cached data."""
        self._cache.clear()
        logger.info("Binance market data cache cleared")

    def get_price(self, symbol: str) -> Optional[float]:
        """Get current price for a symbol (uses cache or returns None)."""
        cache_key = f"ticker24h:{symbol.upper()}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached.get("lastPrice")
        # Try DataHub
        try:
            from backend.services.data_hub import DataHub
            dh = DataHub().peek(f"market:quote:{symbol.upper()}")
            if dh and isinstance(dh, dict):
                return dh.get("price")
        except Exception:
            pass
        return None


# ── Singleton instance ────────────────────────────────────────────────────────
binance_market_data = BinanceMarketDataService()
