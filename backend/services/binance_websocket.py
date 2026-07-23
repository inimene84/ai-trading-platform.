"""
Binance Futures WebSocket Service + Candle Cache (WP1 + Gap Fixes)
=================================================================
Real-time price streaming via Binance Futures WebSocket.
Subscribes to kline_1h and kline_15m for all watched symbols (USDT & USDC).

Features:
  - Ring buffer of completed OHLCV bars per (symbol, interval).
  - Dual timeframe streaming: 1h (primary structure) + 15m (entry timing).
  - Support for both USDT and USDC perpetual pairs matching TRADING_SYMBOLS.
  - Publishes completed candles to DataHub topic ``ohlcv:{SYMBOL}:{INTERVAL}``.
  - ``get_candle_history(symbol, interval, limit)`` returns cached bars for zero-REST reads.
  - Cold-start backfill via ``_backfill()`` from Binance REST on connect.
"""

import asyncio
import json
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Ring buffer depth — 500 bars per (symbol, interval)
MAX_RING_SIZE = int(os.getenv("CANDLE_CACHE_SIZE", "500"))

DEFAULT_SYMBOLS = [
    'btcusdc', 'ethusdc', 'solusdc', 'bnbusdc', 'xrpusdc',
    'adausdc', 'avaxusdc', 'linkusdc', 'uniusdc',
    'btcusdt', 'ethusdt', 'solusdt', 'bnbusdt', 'xrpusdt',
    'adausdt', 'dogeusdt', 'avaxusdt', 'dotusdt', 'linkusdt',
]

WS_BASE = 'wss://fstream.binance.com/ws'


def _ms_to_iso(ms_timestamp: int) -> str:
    """Convert millisecond timestamp to ISO 8601 string."""
    if not ms_timestamp:
        return ''
    return datetime.fromtimestamp(ms_timestamp / 1000, tz=timezone.utc).isoformat()


class BinanceWebSocketService:
    """Real-time price streaming + dual timeframe (1h/15m) candle ring buffer via Binance Futures WS."""

    def __init__(self):
        self._prices: Dict[str, dict] = {}
        self._connected = False
        self._task: Optional[asyncio.Task] = None
        
        env_syms = os.getenv('TRADING_SYMBOLS', '')
        if env_syms:
            self._symbols = [s.strip().lower() for s in env_syms.split(',') if s.strip()]
        else:
            self._symbols = list(DEFAULT_SYMBOLS)

        # Ring buffers: key = "symbol:interval" (e.g. "btcusdc:1h"), value = deque of bar dicts
        self._candles: Dict[str, deque] = {}
        # Track open-time of last completed bar per "symbol:interval"
        self._last_closed_ts: Dict[str, int] = {}

    @property
    def is_connected(self) -> bool:
        return self._connected

    def get_latest_price(self, symbol: str) -> Optional[float]:
        """Get current price without API call."""
        entry = self._prices.get(symbol.lower())
        if entry:
            return entry.get('price')
        return None

    def get_all_prices(self) -> dict:
        """Get all latest prices."""
        return {
            sym.upper(): data
            for sym, data in self._prices.items()
        }

    def get_candle_history(self, symbol: str, interval: str = "1h", limit: int = 500) -> List[dict]:
        """
        Return up to *limit* completed OHLCV bars from the ring buffer for specified interval ("1h" or "15m").
        Returns list[dict] in standard schema [{date, open, high, low, close, volume}, ...].
        """
        key = f"{symbol.lower()}:{interval.lower()}"
        ring = self._candles.get(key)
        if not ring:
            return []
        items = list(ring)
        return items[-limit:]

    def candle_count(self, symbol: str, interval: str = "1h") -> int:
        """Number of cached bars for a symbol and interval."""
        key = f"{symbol.lower()}:{interval.lower()}"
        ring = self._candles.get(key)
        return len(ring) if ring else 0

    def seed_candles(self, symbol: str, interval: str, bars: List[dict]) -> None:
        """Replace the ring buffer from REST (completed + optional forming bar).

        Used when the live kline stream is silent/stale so the new-bar gate and
        strategy still see advancing timestamps.
        """
        if not bars:
            return
        key = f"{symbol.lower()}:{interval.lower()}"
        ring = deque(maxlen=MAX_RING_SIZE)
        ring.extend(bars)
        self._candles[key] = ring
        self._last_closed_ts[key] = _bar_open_ms(bars[-1])

    async def start(self):
        """Start the WebSocket connection and keep it supervised.

        Awaits the reconnect loop so ``run_supervised_task`` restarts us on
        crash. Previously ``create_task`` returned immediately, the supervisor
        exited, and a hung/silent stream was never restarted.
        """
        logger.info("Binance WebSocket streamer starting")
        await self._connect()

    async def stop(self):
        """Stop the WebSocket connection."""
        self._connected = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Binance WebSocket stopped")

    async def _backfill(self):
        """Seed 1h and 15m candle ring buffers from Binance REST on cold start."""
        try:
            from backend.services.binance_market_data import binance_market_data
        except ImportError:
            logger.warning("CandleCache: binance_market_data not importable — skipping backfill")
            return

        for sym in self._symbols:
            for interval in ["1h", "15m"]:
                key = f"{sym.lower()}:{interval.lower()}"
                try:
                    bars = await binance_market_data.get_klines(
                        sym.upper(), interval=interval, limit=MAX_RING_SIZE,
                    )
                    if bars:
                        ring = deque(maxlen=MAX_RING_SIZE)
                        ring.extend(bars)
                        self._candles[key] = ring
                        self._last_closed_ts[key] = _bar_open_ms(bars[-1])
                        logger.info(f"CandleCache: backfilled {len(bars)} {interval} bars for {sym.upper()}")
                except Exception as e:
                    logger.warning(f"CandleCache: backfill failed for {sym.upper()} {interval}: {e}")

    async def _connect(self):
        """Connect to Binance WebSocket and subscribe to 1h and 15m streams."""
        try:
            import websockets
        except ImportError:
            logger.warning("websockets package not installed - WebSocket service disabled")
            return

        # Build dual timeframe stream URL for all watched symbols (e.g. btcusdc@kline_1h/btcusdc@kline_15m)
        streams = []
        for s in self._symbols:
            streams.append(f"{s}@kline_1h")
            streams.append(f"{s}@kline_15m")

        stream_str = '/'.join(streams)
        url = f"wss://fstream.binance.com/stream?streams={stream_str}"

        while True:
            try:
                logger.info("Connecting to Binance Futures WebSocket...")
                async with websockets.connect(url, ping_interval=20) as ws:
                    self._connected = True
                    logger.info(f"WebSocket connected - streaming {len(self._symbols)} symbols (1h & 15m)")

                    await self._backfill()

                    async for message in ws:
                        try:
                            data = json.loads(message)
                            stream_data = data.get('data', {})
                            if stream_data.get('e') == 'kline':
                                self._handle_kline(stream_data)
                        except (json.JSONDecodeError, KeyError, ValueError) as e:
                            logger.debug(f"WebSocket parse error: {e}")
                            continue

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._connected = False
                logger.warning(f"WebSocket disconnected: {e}, reconnecting in 5s...")
                await asyncio.sleep(5)

        self._connected = False

    def _handle_kline(self, stream_data: dict):
        """Process a kline WS message."""
        kline = stream_data.get('k', {})
        symbol = kline.get('s', '').lower()
        interval = kline.get('i', '1h').lower()
        is_closed = kline.get('x', False)

        # Update price snapshot for 1h stream
        if interval == '1h':
            self._prices[symbol] = {
                'price': float(kline.get('c', 0)),
                'open': float(kline.get('o', 0)),
                'high': float(kline.get('h', 0)),
                'low': float(kline.get('l', 0)),
                'volume': float(kline.get('v', 0)),
                'closed': is_closed,
                'updated_at': time.time(),
            }

        # Append completed bar to ring buffer
        if is_closed:
            open_time = int(kline.get('t', 0))
            key = f"{symbol}:{interval}"

            if open_time and open_time <= self._last_closed_ts.get(key, 0):
                return

            bar = {
                'date': _ms_to_iso(open_time),
                'open': float(kline.get('o', 0)),
                'high': float(kline.get('h', 0)),
                'low': float(kline.get('l', 0)),
                'close': float(kline.get('c', 0)),
                'volume': float(kline.get('v', 0)),
            }

            if key not in self._candles:
                self._candles[key] = deque(maxlen=MAX_RING_SIZE)
            self._candles[key].append(bar)
            self._last_closed_ts[key] = open_time

            # Publish to DataHub
            try:
                from backend.services.data_hub import DataHub
                topic = f"ohlcv:{symbol.upper()}:{interval}"
                DataHub().publish(topic, bar, ttl_ms=3_700_000)
            except Exception:
                pass


def _bar_open_ms(bar: dict) -> int:
    d = bar.get('date', '')
    if not d:
        return 0
    try:
        dt = datetime.fromisoformat(d)
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


# Singleton instance
binance_ws = BinanceWebSocketService()
