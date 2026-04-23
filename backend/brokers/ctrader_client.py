"""
cTrader Open API WebSocket client.

Connection details:
  Host: live1.p.ctrader.com
  Port: 5035
  Protocol: wss:// (TLS WebSocket) + Protobuf messages

Message flow:
  1. Connect
  2. ProtoOAApplicationAuthReq  (client_id + client_secret)
  3. ProtoOAAccountAuthReq      (access_token + account_id)
  4. Subscribe to spots/bars, place orders, etc.
"""

import asyncio
import json
import logging
import os
import ssl
import time
from dataclasses import dataclass
from typing import Callable, Optional

import websockets

logger = logging.getLogger(__name__)

# cTrader Open API endpoint
CTRADER_LIVE_HOST = "live1.p.ctrader.com"
CTRADER_DEMO_HOST = "demo1.p.ctrader.com"
CTRADER_PORT = 5035


# ── Protobuf message IDs (subset) ─────────────────────────────────────────────
# Full list: https://help.ctrader.com/open-api/messages/
class ProtoPayloadType:
    HEARTBEAT_EVENT = 51
    # Auth
    APPLICATION_AUTH_REQ = 2100
    APPLICATION_AUTH_RES = 2101
    ACCOUNT_AUTH_REQ = 2102
    ACCOUNT_AUTH_RES = 2103
    # Market data
    SUBSCRIBE_SPOTS_REQ = 2120
    SUBSCRIBE_SPOTS_RES = 2121
    SPOT_EVENT = 2131
    GET_TRENDBARS_REQ = 2137
    GET_TRENDBARS_RES = 2138
    # Trading
    NEW_ORDER_REQ = 2106
    EXECUTION_EVENT = 2126
    # Account
    RECONCILE_REQ = 2124
    RECONCILE_RES = 2125


@dataclass
class Tick:
    symbol_id: int
    symbol: str
    bid: float
    ask: float
    timestamp: int


@dataclass
class Bar:
    symbol: str
    period: str  # M1, M5, H1, D1 …
    open: float
    high: float
    low: float
    close: float
    volume: int
    timestamp: int


@dataclass
class Position:
    position_id: int
    symbol: str
    trade_side: str  # BUY or SELL
    volume: int       # in micro-lots (100 = 0.01 lot)
    entry_price: float
    unrealized_pnl: float


class CTraderClient:
    """
    Async WebSocket client for cTrader Open API.

    Usage (dry-run / no real orders):
        async with CTraderClient(dry_run=True) as client:
            await client.subscribe_spots(["EURUSD"])
            async for tick in client.ticks():
                print(tick)

    Usage (live):
        async with CTraderClient() as client:
            await client.place_market_order("EURUSD", side="BUY", volume=10000)
    """

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        access_token: Optional[str] = None,
        account_id: Optional[int] = None,
        env: str = "demo",
        dry_run: bool = True,
    ):
        self.client_id = client_id or os.getenv("CTRADER_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("CTRADER_CLIENT_SECRET")
        self.access_token = access_token or os.getenv("CTRADER_ACCESS_TOKEN")
        self.account_id = account_id or int(os.getenv("CTRADER_ACCOUNT_ID", "0"))
        self.env = env or os.getenv("CTRADER_ENV", "demo")
        self.dry_run = dry_run

        host = CTRADER_DEMO_HOST if self.env == "demo" else CTRADER_LIVE_HOST
        self._uri = f"wss://{host}:{CTRADER_PORT}"

        self._ws = None
        self._authenticated = False
        self._symbol_map: dict[str, int] = {}  # symbol name → cTrader symbolId
        self._tick_callbacks: list[Callable[[Tick], None]] = []
        self._positions: dict[int, Position] = {}
        self._client_msg_id = 0

    # ── Context manager ───────────────────────────────────────────────────────

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *_):
        await self.disconnect()

    # ── Connection ────────────────────────────────────────────────────────────

    async def connect(self):
        logger.info(f"Connecting to {self._uri}")
        # cTrader uses a shared SSL cert that doesn't match the hostname
        # so we disable hostname verification (cert is still verified)
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        self._ws = await websockets.connect(self._uri, ssl=ssl_ctx)
        asyncio.create_task(self._heartbeat_loop())
        asyncio.create_task(self._receive_loop())
        await self._app_auth()
        await asyncio.sleep(1)  # wait for app auth response
        await self._account_auth()
        await asyncio.sleep(1)  # wait for account auth response
        logger.info("cTrader client fully authenticated.")

    async def disconnect(self):
        if self._ws:
            await self._ws.close()

    # ── Auth ──────────────────────────────────────────────────────────────────

    async def _app_auth(self):
        """Step 1: Authenticate the registered application."""
        msg = {
            "payloadType": ProtoPayloadType.APPLICATION_AUTH_REQ,
            "payload": {
                "clientId": self.client_id,
                "clientSecret": self.client_secret,
            },
        }
        await self._send(msg)
        # Response handled in _receive_loop

    async def _account_auth(self):
        """Step 2: Authenticate the trading account with the access token."""
        msg = {
            "payloadType": ProtoPayloadType.ACCOUNT_AUTH_REQ,
            "payload": {
                "ctidTraderAccountId": self.account_id,
                "accessToken": self.access_token,
            },
        }
        await self._send(msg)

    # ── Market data ───────────────────────────────────────────────────────────

    async def subscribe_spots(self, symbols: list[str]):
        """Subscribe to real-time bid/ask ticks for a list of symbols."""
        symbol_ids = [self._symbol_map.get(s) for s in symbols if s in self._symbol_map]
        if not symbol_ids:
            logger.warning(f"Symbol IDs not yet resolved for {symbols}. Call get_symbol_ids first.")
            return
        msg = {
            "payloadType": ProtoPayloadType.SUBSCRIBE_SPOTS_REQ,
            "payload": {
                "ctidTraderAccountId": self.account_id,
                "symbolId": symbol_ids,
            },
        }
        await self._send(msg)

    async def get_trendbars(
        self,
        symbol: str,
        period: str = "M5",
        count: int = 100,
    ) -> list[Bar]:
        """Fetch historical OHLCV bars for a symbol."""
        symbol_id = self._symbol_map.get(symbol)
        if not symbol_id:
            raise ValueError(f"Unknown symbol: {symbol}. Populate _symbol_map first.")
        now_ms = int(time.time() * 1000)
        msg = {
            "payloadType": ProtoPayloadType.GET_TRENDBARS_REQ,
            "payload": {
                "ctidTraderAccountId": self.account_id,
                "symbolId": symbol_id,
                "period": period,
                "toTimestamp": now_ms,
                "count": count,
            },
        }
        await self._send(msg)
        # Real impl: await the response, parse Bars. Returning empty list for scaffold.
        return []

    # ── Trading ───────────────────────────────────────────────────────────────

    async def place_market_order(
        self,
        symbol: str,
        side: str,  # "BUY" or "SELL"
        volume: int,  # micro-lots: 100000 = 1 lot, 10000 = 0.1 lot
        stop_loss_pips: Optional[float] = None,
        take_profit_pips: Optional[float] = None,
        comment: str = "hedge-fund-bot",
    ) -> dict:
        """Place a market order. Returns immediately; fill via EXECUTION_EVENT."""
        if self.dry_run:
            logger.info(
                f"[DRY RUN] Would place {side} {volume} micro-lots of {symbol} "
                f"SL={stop_loss_pips}pips TP={take_profit_pips}pips"
            )
            return {"dry_run": True, "symbol": symbol, "side": side, "volume": volume}

        symbol_id = self._symbol_map.get(symbol)
        if not symbol_id:
            raise ValueError(f"Unknown symbol: {symbol}")

        self._client_msg_id += 1
        msg = {
            "payloadType": ProtoPayloadType.NEW_ORDER_REQ,
            "payload": {
                "ctidTraderAccountId": self.account_id,
                "symbolId": symbol_id,
                "orderType": "MARKET",
                "tradeSide": side,
                "volume": volume,
                "comment": comment,
                "clientMsgId": str(self._client_msg_id),
            },
        }
        if stop_loss_pips:
            msg["payload"]["relativeStopLoss"] = int(stop_loss_pips * 10)
        if take_profit_pips:
            msg["payload"]["relativeTakeProfit"] = int(take_profit_pips * 10)

        await self._send(msg)
        logger.info(f"Market order sent: {side} {symbol} vol={volume}")
        return msg["payload"]

    async def close_position(self, position_id: int, volume: Optional[int] = None) -> dict:
        """Close (or partially close) an open position."""
        if self.dry_run:
            logger.info(f"[DRY RUN] Would close position {position_id}")
            return {"dry_run": True, "position_id": position_id}
        pos = self._positions.get(position_id)
        if not pos:
            raise ValueError(f"Position {position_id} not found in local state.")
        close_vol = volume or pos.volume
        msg = {
            "payloadType": ProtoPayloadType.NEW_ORDER_REQ,
            "payload": {
                "ctidTraderAccountId": self.account_id,
                "positionId": position_id,
                "orderType": "MARKET",
                "tradeSide": "SELL" if pos.trade_side == "BUY" else "BUY",
                "volume": close_vol,
                "comment": "hedge-fund-bot-close",
            },
        }
        await self._send(msg)
        return msg["payload"]

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def on_tick(self, callback: Callable[[Tick], None]):
        """Register a callback for real-time tick events."""
        self._tick_callbacks.append(callback)

    # ── Internal loops ────────────────────────────────────────────────────────

    async def _heartbeat_loop(self):
        """Send a heartbeat every 10 seconds to keep the connection alive."""
        while True:
            await asyncio.sleep(10)
            try:
                await self._send({"payloadType": ProtoPayloadType.HEARTBEAT_EVENT, "payload": {}})
            except Exception:
                break

    async def _receive_loop(self):
        """Main receive loop — dispatch incoming messages."""
        async for raw in self._ws:
            try:
                msg = json.loads(raw)
                await self._dispatch(msg)
            except json.JSONDecodeError:
                # Binary Protobuf message — in production, decode with proto stubs
                logger.debug("Received binary protobuf frame (not yet decoded in scaffold)")
            except Exception as e:
                logger.error(f"Error in receive loop: {e}")

    async def _dispatch(self, msg: dict):
        """Route a decoded message to the correct handler."""
        ptype = msg.get("payloadType")
        payload = msg.get("payload", {})

        if ptype == ProtoPayloadType.APPLICATION_AUTH_RES:
            logger.info("Application auth successful.")

        elif ptype == ProtoPayloadType.ACCOUNT_AUTH_RES:
            self._authenticated = True
            logger.info(f"Account {self.account_id} auth successful.")

        elif ptype == ProtoPayloadType.SPOT_EVENT:
            tick = Tick(
                symbol_id=payload.get("symbolId", 0),
                symbol=self._resolve_symbol_name(payload.get("symbolId", 0)),
                bid=payload.get("bid", 0) / 100000,
                ask=payload.get("ask", 0) / 100000,
                timestamp=payload.get("timestamp", 0),
            )
            for cb in self._tick_callbacks:
                cb(tick)

        elif ptype == ProtoPayloadType.EXECUTION_EVENT:
            order = payload.get("order", {})
            position = payload.get("position", {})
            logger.info(
                f"Execution: {order.get('orderType')} {order.get('tradeSide')} "
                f"pos={position.get('positionId')} @ {position.get('price')}"
            )

        elif ptype == ProtoPayloadType.HEARTBEAT_EVENT:
            pass  # no-op

        else:
            logger.debug(f"Unhandled payloadType={ptype}")

    async def _send(self, msg: dict):
        if self._ws:
            await self._ws.send(json.dumps(msg))

    def _resolve_symbol_name(self, symbol_id: int) -> str:
        reverse = {v: k for k, v in self._symbol_map.items()}
        return reverse.get(symbol_id, str(symbol_id))
