"""Unified multi-asset market-data feed.

Single entry point for live quotes across asset classes:
  crypto  -> Binance Futures 24h tickers (binance_market_data)
  stock/index/oil -> yfinance (per-symbol 60s TTL cache)
  metal/forex -> cTrader streamed mark price when available, else yfinance
                 futures/FX proxies (GC=F, EURUSD=X, ...)

Never raises to callers: provider failures yield a Quote with
price=None, stale=True and an error message. Every successful quote is
published to the DataHub topic ``market:quote:{SYMBOL}`` (ttl 60s).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TypedDict

import yfinance as yf
from dotenv import load_dotenv

from backend.services.binance_market_data import binance_market_data
from backend.services.ctrader_service import ctrader_service
from backend.services.data_hub import DataHub
from backend.services.multi_asset_bars import AssetClass, classify_symbol, fetch_bars

load_dotenv()
logger = logging.getLogger(__name__)

QUOTE_CACHE_TTL_SEC = 60  # per-symbol TTL cache for yfinance quotes / bars


class Quote(TypedDict):
    symbol: str
    asset_class: str
    price: Optional[float]
    change_abs: Optional[float]
    change_pct: Optional[float]
    high: Optional[float]
    low: Optional[float]
    volume: Optional[float]
    source: str
    as_of: str  # ISO-8601 UTC
    stale: bool
    error: Optional[str]


# yfinance symbol maps (extends the map in multi_asset_bars)
_YF_GENERAL_MAP = {
    "SPX": "^GSPC", "NDX": "^NDX", "VIX": "^VIX",
    "USOIL": "CL=F", "UKOIL": "BZ=F", "WTI": "CL=F", "BRENT": "BZ=F",
    "GOLD": "GC=F", "SILVER": "SI=F",
}
_YF_METALS_MAP = {
    "XAUUSD": "GC=F", "XAGUSD": "SI=F", "XPTUSD": "PL=F", "XPDUSD": "PA=F",
}

_DEFAULT_CRYPTO = "BTCUSDC,ETHUSDC,SOLUSDC,BNBUSDC,XRPUSDC"
_DEFAULT_EQUITIES = "AAPL,MSFT,NVDA,SPX"
_DEFAULT_METALS = "XAUUSD,XAGUSD,XPTUSD,XPDUSD"
# Forex majors tracked by the dashboard multi-asset panel.
_DEFAULT_FOREX = "EURUSD,GBPUSD,USDJPY,EURJPY,AUDUSD,USDCAD,USDCHF,NZDUSD"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_list(key: str, default: str) -> List[str]:
    raw = os.getenv(key, "").strip()
    if not raw:
        raw = default
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def _yf_symbol_for(symbol: str, asset_class: str) -> str:
    if asset_class in ("metal", "forex"):
        if symbol in _YF_METALS_MAP:
            return _YF_METALS_MAP[symbol]
        if len(symbol) == 6 and symbol.isalpha():
            return f"{symbol}=X"
        return symbol
    return _YF_GENERAL_MAP.get(symbol, symbol)


def _fi_get(fast_info: Any, *keys: str) -> Any:
    """Read yfinance FastInfo as either a mapping or an attribute bag."""
    for key in keys:
        if isinstance(fast_info, dict):
            value = fast_info.get(key)
        else:
            try:
                value = fast_info[key]
            except Exception:
                value = getattr(fast_info, key, None)
        if value is not None:
            return value
    return None


class UnifiedFeedService:
    """Async facade over Binance / yfinance / cTrader quote providers."""

    def __init__(self) -> None:
        self._yf_cache: Dict[str, tuple[float, Quote]] = {}

    def default_universe(self) -> Dict[str, List[str]]:
        crypto_default = os.getenv("TRADING_SYMBOLS", "").strip() or _DEFAULT_CRYPTO
        return {
            "crypto": _env_list("FEED_SYMBOLS_CRYPTO", crypto_default),
            "equities": _env_list("FEED_SYMBOLS_EQUITIES", _DEFAULT_EQUITIES),
            "metals": _env_list("FEED_SYMBOLS_METALS", _DEFAULT_METALS),
        }

    @staticmethod
    def _error_quote(symbol: str, asset_class: str, source: str, error: str) -> Quote:
        return Quote(
            symbol=symbol, asset_class=asset_class, price=None,
            change_abs=None, change_pct=None, high=None, low=None,
            volume=None, source=source, as_of=_utc_now_iso(),
            stale=True, error=error,
        )

    @staticmethod
    def _publish(quote: Quote) -> None:
        if quote["price"] is None:
            return
        try:
            DataHub().publish(
                f"market:quote:{quote['symbol']}", dict(quote), ttl_ms=60_000
            )
        except Exception:
            logger.debug("UnifiedFeed: DataHub publish skipped for %s", quote["symbol"], exc_info=True)

    async def _crypto_quotes(self, symbols: List[str]) -> List[Quote]:
        try:
            tickers = await binance_market_data.get_all_tickers_24h(symbols)
        except Exception as e:
            logger.error("UnifiedFeed: Binance ticker fetch failed: %s", e)
            tickers = []
        by_symbol = {t.get("symbol", "").upper(): t for t in tickers or []}
        quotes: List[Quote] = []
        for sym in symbols:
            t = by_symbol.get(sym.upper())
            if not t:
                quotes.append(self._error_quote(sym.upper(), "crypto", "binance", "no ticker data"))
                continue
            quotes.append(Quote(
                symbol=sym.upper(), asset_class="crypto",
                price=float(t.get("lastPrice", 0) or 0),
                change_abs=float(t.get("priceChange", 0) or 0),
                change_pct=float(t.get("priceChangePercent", 0) or 0),
                high=float(t.get("highPrice", 0) or 0),
                low=float(t.get("lowPrice", 0) or 0),
                volume=float(t.get("quoteVolume", 0) or 0),
                source="binance", as_of=_utc_now_iso(), stale=False, error=None,
            ))
        return quotes

    def _fetch_yf_quote(self, symbol: str, asset_class: str) -> Quote:
        """Blocking yfinance quote fetch — run via asyncio.to_thread."""
        yf_sym = _yf_symbol_for(symbol, asset_class)
        ticker = yf.Ticker(yf_sym)
        price: Optional[float] = None
        prev_close: Optional[float] = None
        high = low = volume = None

        try:
            fi = ticker.fast_info
            price = _fi_get(fi, "last_price", "lastPrice")
            prev_close = _fi_get(fi, "previous_close", "previousClose")
            high = _fi_get(fi, "day_high", "dayHigh")
            low = _fi_get(fi, "day_low", "dayLow")
            volume = _fi_get(fi, "last_volume", "lastVolume")
        except Exception:
            logger.debug("UnifiedFeed: yfinance fast_info unavailable for %s", yf_sym, exc_info=True)

        if price is None:
            hist = ticker.history(period="5d", interval="1d")
            if hist is None or hist.empty:
                raise RuntimeError(f"no yfinance data for {yf_sym}")
            last = hist.iloc[-1]
            price = float(last["Close"])
            high = float(last["High"])
            low = float(last["Low"])
            volume = float(last.get("Volume", 0) or 0)
            if len(hist) >= 2:
                prev_close = float(hist["Close"].iloc[-2])

        price = float(price)
        change_abs = (price - float(prev_close)) if prev_close else None
        change_pct = ((price - float(prev_close)) / float(prev_close) * 100.0) if prev_close else None
        return Quote(
            symbol=symbol, asset_class=asset_class, price=price,
            change_abs=change_abs, change_pct=change_pct,
            high=float(high) if high is not None else None,
            low=float(low) if low is not None else None,
            volume=float(volume) if volume is not None else None,
            source="yfinance", as_of=_utc_now_iso(), stale=False, error=None,
        )

    async def _yfinance_quote(self, symbol: str, asset_class: str) -> Quote:
        cached = self._yf_cache.get(symbol)
        if cached and (time.time() - cached[0]) < QUOTE_CACHE_TTL_SEC:
            return cached[1]
        try:
            quote = await asyncio.to_thread(self._fetch_yf_quote, symbol, asset_class)
        except Exception as e:
            logger.warning("UnifiedFeed: yfinance quote failed for %s: %s", symbol, e)
            quote = self._error_quote(symbol, asset_class, "yfinance", str(e))
        self._yf_cache[symbol] = (time.time(), quote)
        return quote

    async def _ctrader_or_yf_quote(self, symbol: str, asset_class: str) -> Quote:
        try:
            if ctrader_service.is_connected():
                price = ctrader_service.get_mark_price(symbol)
                if price:
                    return Quote(
                        symbol=symbol, asset_class=asset_class, price=float(price),
                        change_abs=None, change_pct=None, high=None, low=None,
                        volume=None, source="ctrader", as_of=_utc_now_iso(),
                        stale=False, error=None,
                    )
        except Exception as e:
            logger.debug("UnifiedFeed: cTrader mark price unavailable for %s: %s", symbol, e)
        return await self._yfinance_quote(symbol, asset_class)

    async def get_quote(self, symbol: str) -> Quote:
        sym = symbol.upper().replace("/", "").replace("-", "").strip()
        asset_class = classify_symbol(sym)
        if asset_class == "crypto":
            quotes = await self._crypto_quotes([sym])
            quote = quotes[0]
        elif asset_class in ("metal", "forex"):
            quote = await self._ctrader_or_yf_quote(sym, asset_class)
        else:
            quote = await self._yfinance_quote(sym, asset_class)
        self._publish(quote)
        return quote

    async def get_quotes(
        self,
        symbols: Optional[List[str]] = None,
        asset_class: Optional[AssetClass] = None,
    ) -> List[Quote]:
        if symbols is None:
            universe = self.default_universe()
            if asset_class == "crypto":
                symbols = universe["crypto"]
            elif asset_class == "metal":
                symbols = universe["metals"]
            elif asset_class == "forex":
                symbols = _env_list("FEED_SYMBOLS_FOREX", _DEFAULT_FOREX)
            elif asset_class in ("stock", "index", "oil"):
                symbols = universe["equities"]
            else:
                symbols = universe["crypto"] + universe["equities"] + universe["metals"]

        normalized = [s.upper().replace("/", "").replace("-", "").strip() for s in symbols]
        if asset_class is not None:
            normalized = [s for s in normalized if classify_symbol(s) == asset_class]

        crypto_syms = [s for s in normalized if classify_symbol(s) == "crypto"]
        other_syms = [s for s in normalized if classify_symbol(s) != "crypto"]

        crypto_task = self._crypto_quotes(crypto_syms) if crypto_syms else None
        other_tasks = [self.get_quote(s) for s in other_syms]

        results: List[Quote] = []
        gathered = await asyncio.gather(
            *([crypto_task] if crypto_task else []),
            *other_tasks,
            return_exceptions=True,
        )
        for item in gathered:
            if isinstance(item, Exception):
                logger.error("UnifiedFeed: quote task failed: %s", item)
                continue
            if isinstance(item, list):
                results.extend(item)
            else:
                results.append(item)

        for q in results:
            if q["asset_class"] == "crypto":
                self._publish(q)
        order = {s: i for i, s in enumerate(normalized)}
        results.sort(key=lambda q: order.get(q["symbol"], len(order)))
        return results

    async def get_bars(self, symbol: str, timeframe: str = "1h", limit: int = 100) -> Dict[str, Any]:
        """OHLCV bars via multi_asset_bars.fetch_bars, DataHub-cached 60s."""
        sym = symbol.upper().replace("/", "").strip()
        topic = f"feed:bars:{sym}:{timeframe}:{limit}"
        try:
            cached = DataHub().peek(topic)
            if cached is not None:
                return cached
        except Exception:
            logger.debug("UnifiedFeed: DataHub peek skipped for %s", topic, exc_info=True)

        payload = await fetch_bars(symbol=sym, timeframe=timeframe, limit=limit)
        payload["as_of"] = _utc_now_iso()
        try:
            DataHub().publish(topic, payload, ttl_ms=60_000)
        except Exception:
            logger.debug("UnifiedFeed: DataHub publish skipped for %s", topic, exc_info=True)
        return payload


unified_feed = UnifiedFeedService()
