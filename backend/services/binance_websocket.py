"""
Binance Futures WebSocket Service
Real-time price streaming via Binance Futures WebSocket.
Subscribes to kline_1h for all 20 symbols and stores latest prices in-memory.
"""

import asyncio
import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_SYMBOLS = [
    'btcusdt', 'ethusdt', 'solusdt', 'bnbusdt', 'xrpusdt',
    'adausdt', 'dogeusdt', 'avaxusdt', 'dotusdt', 'linkusdt',
    'maticusdt', 'ltcusdt', 'uniusdt', 'atomusdt', 'nearusdt',
    'opusdt', 'arbusdt', 'aptusdt', 'injusdt', 'suiusdt',
]

WS_BASE = 'wss://fstream.binance.com/ws'


class BinanceWebSocketService:
    """Real-time price streaming via Binance Futures WebSocket."""

    def __init__(self):
        self._prices: dict[str, dict] = {}
        self._connected = False
        self._task: Optional[asyncio.Task] = None
        self._symbols = DEFAULT_SYMBOLS

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

    async def start(self):
        """Start the WebSocket connection."""
        if self._task and not self._task.done():
            logger.info("WebSocket already running")
            return
        self._task = asyncio.create_task(self._connect())
        logger.info("Binance WebSocket task started")

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

    async def _connect(self):
        """Connect to Binance WebSocket and subscribe to streams."""
        try:
            import websockets
        except ImportError:
            logger.warning("websockets package not installed - WebSocket service disabled")
            logger.warning("Install with: pip install websockets")
            return

        # Build combined stream URL
        streams = '/'.join(f"{s}@kline_1h" for s in self._symbols)
        url = f"wss://fstream.binance.com/stream?streams={streams}"

        while True:
            try:
                logger.info("Connecting to Binance Futures WebSocket...")
                async with websockets.connect(url, ping_interval=20) as ws:
                    self._connected = True
                    logger.info(f"WebSocket connected - streaming {len(self._symbols)} symbols")

                    async for message in ws:
                        try:
                            data = json.loads(message)
                            stream_data = data.get('data', {})
                            if stream_data.get('e') == 'kline':
                                kline = stream_data.get('k', {})
                                symbol = kline.get('s', '').lower()
                                self._prices[symbol] = {
                                    'price': float(kline.get('c', 0)),
                                    'open': float(kline.get('o', 0)),
                                    'high': float(kline.get('h', 0)),
                                    'low': float(kline.get('l', 0)),
                                    'volume': float(kline.get('v', 0)),
                                    'closed': kline.get('x', False),
                                    'updated_at': time.time(),
                                }
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


# ── Singleton instance ────────────────────────────────────────────────────────
binance_ws = BinanceWebSocketService()
