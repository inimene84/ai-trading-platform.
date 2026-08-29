"""
Multi-Provider OHLCV Data Service (WP2)
======================================
Provides multi-venue public market data fallback for crypto futures.
Hierarchy:
  1. Binance WS Cache (zero REST requests)
  2. Binance Futures REST
  3. Bybit Futures REST (httpx)
  4. OKX Futures REST (httpx)
  5. KuCoin Spot/Futures REST (httpx)
  6. yfinance (equities fallback ONLY — skipped for crypto)

All providers return uniform bar dict list:
  [{'date': str, 'open': float, 'high': float, 'low': float, 'close': float, 'volume': float}, ...]
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
import httpx

from backend.services.data_hub import DataHub

logger = logging.getLogger(__name__)

# Timeout per provider attempt
PROVIDER_TIMEOUT_SEC = 5.0
CACHE_TTL_MS = 60_000  # 1 minute cache in DataHub


def _ms_to_iso(ms_timestamp: int) -> str:
    if not ms_timestamp:
        return ''
    return datetime.fromtimestamp(ms_timestamp / 1000, tz=timezone.utc).isoformat()


class MultiProviderDataFetcher:
    """
    Async multi-exchange fallback fetcher for OHLCV candlestick data.
    Prevents Binance API weight limit / 429 exhaustion.
    """

    def __init__(self):
        self.data_hub = DataHub()

    async def get_ohlcv(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 200,
        preferred_provider: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch OHLCV candles with automatic multi-provider failover.
        """
        clean_symbol = symbol.upper().replace("/", "")
        topic = f"ohlcv:{clean_symbol}:{interval}"

        # 1. Check DataHub cache
        cached = self.data_hub.peek(topic)
        if cached is not None and isinstance(cached, list) and len(cached) >= min(limit, 20):
            return cached[-limit:]

        providers = ["bybit", "okx", "kucoin"]
        if preferred_provider and preferred_provider in providers:
            providers.remove(preferred_provider)
            providers.insert(0, preferred_provider)

        for provider in providers:
            try:
                bars = None
                if provider == "bybit":
                    bars = await self._fetch_bybit(clean_symbol, interval, limit)
                elif provider == "okx":
                    bars = await self._fetch_okx(clean_symbol, interval, limit)
                elif provider == "kucoin":
                    bars = await self._fetch_kucoin(clean_symbol, interval, limit)

                if bars and len(bars) >= 20:
                    logger.info(f"MultiProviderData: successfully fetched {len(bars)} bars for {clean_symbol} via {provider.upper()}")
                    self.data_hub.publish(topic, bars, ttl_ms=CACHE_TTL_MS)
                    return bars[-limit:]

            except Exception as e:
                logger.warning(f"MultiProviderData: {provider.upper()} failed for {clean_symbol}: {e}")
                continue

        logger.error(f"MultiProviderData: all alternative providers failed for {clean_symbol}")
        return []

    async def _fetch_bybit(self, symbol: str, interval: str, limit: int) -> Optional[List[Dict[str, Any]]]:
        """Fetch OHLCV from Bybit V5 Linear Futures public API."""
        # Interval mapping
        tf_map = {"15m": "15", "1h": "60", "4h": "240", "1d": "D"}
        bybit_tf = tf_map.get(interval, "60")
        url = "https://api.bybit.com/v5/market/kline"
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": bybit_tf,
            "limit": min(limit, 200)
        }

        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT_SEC) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        list_data = data.get("result", {}).get("list", [])
        if not list_data:
            return None

        # Bybit returns list newest first: [startTime, open, high, low, close, volume, turnover]
        bars = []
        for k in reversed(list_data):
            bars.append({
                "date": _ms_to_iso(int(k[0])),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })
        return bars

    async def _fetch_okx(self, symbol: str, interval: str, limit: int) -> Optional[List[Dict[str, Any]]]:
        """Fetch OHLCV from OKX V5 Swap Futures public API."""
        # Convert BTCUSDT -> BTC-USDT-SWAP
        base = symbol.replace("USDT", "").replace("USDC", "")
        quote = "USDT" if "USDT" in symbol else "USDC"
        inst_id = f"{base}-{quote}-SWAP"

        tf_map = {"15m": "15m", "1h": "1H", "4h": "4H", "1d": "1Dutc"}
        okx_tf = tf_map.get(interval, "1H")

        url = "https://www.okx.com/api/v5/market/candles"
        params = {"instId": inst_id, "bar": okx_tf, "limit": min(limit, 100)}

        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT_SEC) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        list_data = data.get("data", [])
        if not list_data:
            return None

        # OKX returns newest first: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
        bars = []
        for k in reversed(list_data):
            bars.append({
                "date": _ms_to_iso(int(k[0])),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })
        return bars

    async def _fetch_kucoin(self, symbol: str, interval: str, limit: int) -> Optional[List[Dict[str, Any]]]:
        """Fetch OHLCV from KuCoin Spot/Futures public API."""
        # Convert BTCUSDT -> BTC-USDT
        base = symbol.replace("USDT", "").replace("USDC", "")
        quote = "USDT" if "USDT" in symbol else "USDC"
        kc_symbol = f"{base}-{quote}"

        tf_map = {"15m": "15min", "1h": "1hour", "4h": "4hour", "1d": "1day"}
        kc_tf = tf_map.get(interval, "1hour")

        url = "https://api.kucoin.com/api/v1/market/candles"
        params = {"symbol": kc_symbol, "type": kc_tf}

        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT_SEC) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        list_data = data.get("data", [])
        if not list_data:
            return None

        # KuCoin returns newest first: [time, open, close, high, low, volume, turnover]
        bars = []
        for k in reversed(list_data):
            bars.append({
                "date": _ms_to_iso(int(k[0]) * 1000),
                "open": float(k[1]),
                "high": float(k[3]),
                "low": float(k[4]),
                "close": float(k[2]),
                "volume": float(k[5]),
            })
        return bars[:limit]


# Global singleton
multi_provider_data = MultiProviderDataFetcher()
