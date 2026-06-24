"""
Broker Registry — unified execution interface for all supported brokers.

Supported brokers:
  paper     — dry-run / no real orders (always safe)
  binance   — Binance Spot (via python-binance or direct REST)
  ccxt      — ANY exchange via CCXT (100+ exchanges: Kraken, Bybit, OKX, KuCoin, etc.)
  alpaca    — US stocks, ETFs, crypto (Alpaca Markets API)
  ctrader   — Forex/CFD via cTrader Open API (IC Markets, etc.)
  oanda     — Forex via OANDA REST API v20

All brokers implement the same interface:
  place_market_order(symbol, side, quantity, ...)
  place_limit_order(symbol, side, quantity, price, ...)
  cancel_order(order_id, symbol)
  get_balance() -> dict
  get_positions() -> list
  get_open_orders() -> list
"""

from __future__ import annotations
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ── Common order/position types ───────────────────────────────────────────────

@dataclass
class Order:
    order_id: str
    symbol: str
    side: str           # BUY or SELL
    quantity: float
    order_type: str     # MARKET, LIMIT, STOP_LIMIT
    status: str         # NEW, FILLED, PARTIALLY_FILLED, CANCELLED
    price: Optional[float] = None
    filled_price: Optional[float] = None
    broker: str = ""
    raw: dict = None


@dataclass
class Position:
    symbol: str
    side: str
    quantity: float
    entry_price: float
    unrealized_pnl: float = 0.0
    broker: str = ""


@dataclass
class AccountInfo:
    balance: float
    equity: float
    currency: str = "USD"
    broker: str = ""
    margin_used: float = 0.0
    margin_free: float = 0.0


# ── Abstract base ─────────────────────────────────────────────────────────────

class BrokerBase(ABC):
    name: str = "base"
    dry_run: bool = True

    @abstractmethod
    async def place_market_order(
        self, symbol: str, side: str, quantity: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        comment: str = "",
    ) -> Order: ...

    @abstractmethod
    async def place_limit_order(
        self, symbol: str, side: str, quantity: float, price: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> Order: ...

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> bool: ...

    @abstractmethod
    async def get_balance(self) -> AccountInfo: ...

    @abstractmethod
    async def get_positions(self) -> list[Position]: ...

    @abstractmethod
    async def get_open_orders(self) -> list[Order]: ...


# ── Paper broker (always safe — logs but never sends real orders) ─────────────

class PaperBroker(BrokerBase):
    name = "paper"
    dry_run = True

    def __init__(self):
        self._orders: list[Order] = []
        self._positions: dict[str, Position] = {}
        self._balance = float(os.getenv("PAPER_BALANCE", "10000"))

    async def place_market_order(self, symbol, side, quantity, stop_loss=None, take_profit=None, comment="") -> Order:
        import random
        import time
        order = Order(
            order_id=f"PAPER-{int(time.time())}-{random.randint(1000,9999)}",
            symbol=symbol, side=side, quantity=quantity,
            order_type="MARKET", status="FILLED",
            broker="paper",
        )
        self._orders.append(order)
        logger.info(f"[PAPER] {side} {quantity} {symbol}  comment={comment}")
        return order

    async def place_limit_order(self, symbol, side, quantity, price, stop_loss=None, take_profit=None) -> Order:
        import random
        import time
        order = Order(
            order_id=f"PAPER-L-{int(time.time())}-{random.randint(1000,9999)}",
            symbol=symbol, side=side, quantity=quantity, price=price,
            order_type="LIMIT", status="NEW", broker="paper",
        )
        self._orders.append(order)
        logger.info(f"[PAPER] LIMIT {side} {quantity} {symbol} @ {price}")
        return order

    async def cancel_order(self, order_id, symbol) -> bool:
        logger.info(f"[PAPER] Cancel order {order_id}")
        return True

    async def get_balance(self) -> AccountInfo:
        return AccountInfo(balance=self._balance, equity=self._balance, broker="paper")

    async def get_positions(self) -> list[Position]:
        return list(self._positions.values())

    async def get_open_orders(self) -> list[Order]:
        return [o for o in self._orders if o.status == "NEW"]


# ── Binance Broker ────────────────────────────────────────────────────────────

class BinanceBroker(BrokerBase):
    name = "binance"

    def __init__(self, testnet: bool = True):
        self.testnet = testnet or os.getenv("BINANCE_TESTNET", "true").lower() == "true"
        self._client = None

    def _get_client(self):
        if self._client:
            return self._client
        try:
            from binance.client import Client
            api_key    = os.getenv("BINANCE_API_KEY", "")
            api_secret = os.getenv("BINANCE_API_SECRET", "")
            self._client = Client(api_key, api_secret, testnet=self.testnet)
            logger.info(f"Binance client ready (testnet={self.testnet})")
        except ImportError:
            raise ImportError("Run: pip install python-binance")
        return self._client

    async def place_market_order(self, symbol, side, quantity, stop_loss=None, take_profit=None, comment="") -> Order:
        import asyncio
        client = self._get_client()
        sym = symbol.replace("/", "")
        try:
            raw = await asyncio.get_event_loop().run_in_executor(
                None, lambda: client.order_market(symbol=sym, side=side.upper(), quantity=quantity)
            )
            return Order(
                order_id=str(raw["orderId"]),
                symbol=symbol, side=side, quantity=quantity,
                order_type="MARKET", status=raw.get("status", "NEW"),
                filled_price=float(raw.get("fills", [{}])[0].get("price", 0) or 0),
                broker="binance", raw=raw,
            )
        except Exception as e:
            logger.error(f"Binance order error: {e}")
            raise

    async def place_limit_order(self, symbol, side, quantity, price, stop_loss=None, take_profit=None) -> Order:
        import asyncio
        client = self._get_client()
        sym = symbol.replace("/", "")
        raw = await asyncio.get_event_loop().run_in_executor(
            None, lambda: client.order_limit(symbol=sym, side=side.upper(), quantity=quantity, price=str(price), timeInForce="GTC")
        )
        return Order(
            order_id=str(raw["orderId"]),
            symbol=symbol, side=side, quantity=quantity, price=price,
            order_type="LIMIT", status=raw.get("status", "NEW"), broker="binance", raw=raw,
        )

    async def cancel_order(self, order_id, symbol) -> bool:
        import asyncio
        client = self._get_client()
        sym = symbol.replace("/", "")
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: client.cancel_order(symbol=sym, orderId=int(order_id))
        )
        return True

    async def get_balance(self) -> AccountInfo:
        import asyncio
        client = self._get_client()
        info = await asyncio.get_event_loop().run_in_executor(None, client.get_account)
        usdt = next((b for b in info["balances"] if b["asset"] == "USDT"), {})
        balance = float(usdt.get("free", 0))
        return AccountInfo(balance=balance, equity=balance, currency="USDT", broker="binance")

    async def get_positions(self) -> list[Position]:
        return []  # spot trading has no positions concept — use open orders

    async def get_open_orders(self) -> list[Order]:
        import asyncio
        client = self._get_client()
        raw_orders = await asyncio.get_event_loop().run_in_executor(None, client.get_open_orders)
        return [
            Order(order_id=str(o["orderId"]), symbol=o["symbol"], side=o["side"],
                  quantity=float(o["origQty"]), price=float(o["price"]),
                  order_type=o["type"], status=o["status"], broker="binance")
            for o in raw_orders
        ]


# ── CCXT Broker (100+ exchanges: Kraken, Bybit, OKX, KuCoin, Coinbase, etc.) ─

class CCXTBroker(BrokerBase):
    """
    Universal broker via CCXT library.
    Supports 100+ exchanges with the same interface.

    Usage:
        broker = CCXTBroker(exchange_id="kraken")
        broker = CCXTBroker(exchange_id="bybit")
        broker = CCXTBroker(exchange_id="okx")
        broker = CCXTBroker(exchange_id="kucoin")
    """
    def __init__(self, exchange_id: str = "kraken", sandbox: bool = True):
        self.exchange_id = exchange_id
        self.sandbox = sandbox
        self._exchange = None

    def _get_exchange(self):
        if self._exchange:
            return self._exchange
        try:
            import ccxt
        except ImportError:
            raise ImportError("Run: pip install ccxt")

        exchange_class = getattr(ccxt, self.exchange_id)
        creds = self._load_creds()
        self._exchange = exchange_class({
            "apiKey":    creds.get("api_key", ""),
            "secret":    creds.get("api_secret", ""),
            "password":  creds.get("passphrase", ""),  # for OKX / KuCoin
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        if self.sandbox:
            self._exchange.set_sandbox_mode(True)
        self.name = self.exchange_id
        logger.info(f"CCXT {self.exchange_id} ready (sandbox={self.sandbox})")
        return self._exchange

    def _load_creds(self) -> dict:
        """Load credentials from env using exchange-specific prefix."""
        prefix = self.exchange_id.upper()
        return {
            "api_key":    os.getenv(f"{prefix}_API_KEY",    os.getenv("CCXT_API_KEY", "")),
            "api_secret": os.getenv(f"{prefix}_API_SECRET", os.getenv("CCXT_API_SECRET", "")),
            "passphrase": os.getenv(f"{prefix}_PASSPHRASE", os.getenv("CCXT_PASSPHRASE", "")),
        }

    async def place_market_order(self, symbol, side, quantity, stop_loss=None, take_profit=None, comment="") -> Order:
        import asyncio
        ex = self._get_exchange()
        params = {}
        if stop_loss:
            params["stopLossPrice"] = stop_loss
        if take_profit:
            params["takeProfitPrice"] = take_profit
        raw = await asyncio.get_event_loop().run_in_executor(
            None, lambda: ex.create_market_order(symbol, side.lower(), quantity, params=params)
        )
        return Order(
            order_id=str(raw["id"]), symbol=symbol, side=side.upper(), quantity=quantity,
            order_type="MARKET", status=raw.get("status", "closed"),
            filled_price=raw.get("average") or raw.get("price"),
            broker=self.exchange_id, raw=raw,
        )

    async def place_limit_order(self, symbol, side, quantity, price, stop_loss=None, take_profit=None) -> Order:
        import asyncio
        ex = self._get_exchange()
        raw = await asyncio.get_event_loop().run_in_executor(
            None, lambda: ex.create_limit_order(symbol, side.lower(), quantity, price)
        )
        return Order(
            order_id=str(raw["id"]), symbol=symbol, side=side.upper(), quantity=quantity, price=price,
            order_type="LIMIT", status=raw.get("status", "open"), broker=self.exchange_id, raw=raw,
        )

    async def cancel_order(self, order_id, symbol) -> bool:
        import asyncio
        ex = self._get_exchange()
        await asyncio.get_event_loop().run_in_executor(None, lambda: ex.cancel_order(order_id, symbol))
        return True

    async def get_balance(self) -> AccountInfo:
        import asyncio
        ex = self._get_exchange()
        bal = await asyncio.get_event_loop().run_in_executor(None, ex.fetch_balance)
        total = bal["total"].get("USDT") or bal["total"].get("USD") or 0
        return AccountInfo(balance=float(total), equity=float(total), broker=self.exchange_id)

    async def get_positions(self) -> list[Position]:
        import asyncio
        ex = self._get_exchange()
        try:
            raw = await asyncio.get_event_loop().run_in_executor(None, ex.fetch_positions)
            return [
                Position(symbol=p["symbol"], side=p["side"], quantity=float(p["contracts"] or 0),
                         entry_price=float(p["entryPrice"] or 0),
                         unrealized_pnl=float(p["unrealizedPnl"] or 0), broker=self.exchange_id)
                for p in raw if p.get("contracts") and float(p["contracts"]) != 0
            ]
        except Exception:
            return []

    async def get_open_orders(self) -> list[Order]:
        import asyncio
        ex = self._get_exchange()
        raw = await asyncio.get_event_loop().run_in_executor(None, ex.fetch_open_orders)
        return [
            Order(order_id=str(o["id"]), symbol=o["symbol"], side=o["side"].upper(),
                  quantity=float(o["amount"]), price=o.get("price"),
                  order_type=o["type"].upper(), status=o["status"], broker=self.exchange_id)
            for o in raw
        ]


# ── Alpaca Broker (stocks, ETFs, crypto) ──────────────────────────────────────

class AlpacaBroker(BrokerBase):
    name = "alpaca"

    def __init__(self, paper: bool = True):
        self.paper = paper or os.getenv("ALPACA_PAPER", "true").lower() == "true"
        self._client = None
        self._trade_client = None

    def _get_clients(self):
        if self._client:
            return self._client, self._trade_client
        try:
            from alpaca.trading.client import TradingClient
        except ImportError:
            raise ImportError("Run: pip install alpaca-py")

        api_key    = os.getenv("ALPACA_API_KEY", "")
        api_secret = os.getenv("ALPACA_API_SECRET", "")
        self._trade_client = TradingClient(api_key, api_secret, paper=self.paper)
        logger.info(f"Alpaca client ready (paper={self.paper})")
        return self._trade_client, self._trade_client

    async def place_market_order(self, symbol, side, quantity, stop_loss=None, take_profit=None, comment="") -> Order:
        import asyncio
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        client, _ = self._get_clients()
        req = MarketOrderRequest(
            symbol=symbol,
            qty=quantity,
            side=OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        raw = await asyncio.get_event_loop().run_in_executor(None, lambda: client.submit_order(req))
        return Order(
            order_id=str(raw.id), symbol=symbol, side=side.upper(), quantity=quantity,
            order_type="MARKET", status=str(raw.status), broker="alpaca",
        )

    async def place_limit_order(self, symbol, side, quantity, price, stop_loss=None, take_profit=None) -> Order:
        import asyncio
        from alpaca.trading.requests import LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        client, _ = self._get_clients()
        req = LimitOrderRequest(
            symbol=symbol, qty=quantity, limit_price=price,
            side=OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
        )
        raw = await asyncio.get_event_loop().run_in_executor(None, lambda: client.submit_order(req))
        return Order(
            order_id=str(raw.id), symbol=symbol, side=side.upper(), quantity=quantity, price=price,
            order_type="LIMIT", status=str(raw.status), broker="alpaca",
        )

    async def cancel_order(self, order_id, symbol) -> bool:
        import asyncio
        client, _ = self._get_clients()
        await asyncio.get_event_loop().run_in_executor(None, lambda: client.cancel_order_by_id(order_id))
        return True

    async def get_balance(self) -> AccountInfo:
        import asyncio
        client, _ = self._get_clients()
        acct = await asyncio.get_event_loop().run_in_executor(None, client.get_account)
        return AccountInfo(
            balance=float(acct.cash), equity=float(acct.equity),
            broker="alpaca", margin_used=float(acct.initial_margin or 0),
        )

    async def get_positions(self) -> list[Position]:
        import asyncio
        client, _ = self._get_clients()
        raw = await asyncio.get_event_loop().run_in_executor(None, client.get_all_positions)
        return [
            Position(symbol=p.symbol, side="BUY" if float(p.qty) > 0 else "SELL",
                     quantity=abs(float(p.qty)), entry_price=float(p.avg_entry_price),
                     unrealized_pnl=float(p.unrealized_pl), broker="alpaca")
            for p in raw
        ]

    async def get_open_orders(self) -> list[Order]:
        import asyncio
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        client, _ = self._get_clients()
        req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
        raw = await asyncio.get_event_loop().run_in_executor(None, lambda: client.get_orders(req))
        return [
            Order(order_id=str(o.id), symbol=o.symbol, side=str(o.side).upper(),
                  quantity=float(o.qty), price=float(o.limit_price or 0),
                  order_type=str(o.type).upper(), status=str(o.status), broker="alpaca")
            for o in raw
        ]


# ── OANDA Broker (forex CFDs) ─────────────────────────────────────────────────

class OANDABroker(BrokerBase):
    name = "oanda"

    def __init__(self, practice: bool = True):
        self.practice = practice or os.getenv("OANDA_PRACTICE", "true").lower() == "true"
        base = "https://api-fxpractice.oanda.com" if self.practice else "https://api-fxtrade.oanda.com"
        self._base = base
        self._headers = {
            "Authorization": f"Bearer {os.getenv('OANDA_API_KEY','')}",
            "Content-Type": "application/json",
        }
        self._account_id = os.getenv("OANDA_ACCOUNT_ID", "")

    async def place_market_order(self, symbol, side, quantity, stop_loss=None, take_profit=None, comment="") -> Order:
        import httpx
        import time
        # OANDA symbols: EURUSD → EUR_USD
        oanda_sym = symbol[:3] + "_" + symbol[3:6] if len(symbol) == 6 else symbol.replace("/", "_")
        units = quantity if side.upper() == "BUY" else -quantity
        body = {"order": {"type": "MARKET", "instrument": oanda_sym, "units": str(int(units))}}
        if stop_loss:
            body["order"]["stopLossOnFill"] = {"price": str(stop_loss)}
        if take_profit:
            body["order"]["takeProfitOnFill"] = {"price": str(take_profit)}
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._base}/v3/accounts/{self._account_id}/orders",
                json=body, headers=self._headers, timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        fill = data.get("orderFillTransaction", {})
        return Order(
            order_id=fill.get("orderID", str(time.time())),
            symbol=symbol, side=side.upper(), quantity=abs(quantity),
            order_type="MARKET", status="FILLED",
            filled_price=float(fill.get("price", 0)),
            broker="oanda", raw=data,
        )

    async def place_limit_order(self, symbol, side, quantity, price, stop_loss=None, take_profit=None) -> Order:
        import httpx
        oanda_sym = symbol[:3] + "_" + symbol[3:6] if len(symbol) == 6 else symbol
        units = quantity if side.upper() == "BUY" else -quantity
        body = {"order": {"type": "LIMIT", "instrument": oanda_sym, "units": str(int(units)), "price": str(price)}}
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._base}/v3/accounts/{self._account_id}/orders",
                json=body, headers=self._headers, timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        return Order(
            order_id=data.get("orderCreateTransaction", {}).get("orderID", ""),
            symbol=symbol, side=side.upper(), quantity=quantity, price=price,
            order_type="LIMIT", status="NEW", broker="oanda",
        )

    async def cancel_order(self, order_id, symbol) -> bool:
        import httpx
        async with httpx.AsyncClient() as client:
            await client.put(
                f"{self._base}/v3/accounts/{self._account_id}/orders/{order_id}/cancel",
                headers=self._headers, timeout=8,
            )
        return True

    async def get_balance(self) -> AccountInfo:
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base}/v3/accounts/{self._account_id}/summary",
                headers=self._headers, timeout=8,
            )
            data = resp.json().get("account", {})
        return AccountInfo(
            balance=float(data.get("balance", 0)),
            equity=float(data.get("NAV", 0)),
            currency=data.get("currency", "USD"),
            broker="oanda",
        )

    async def get_positions(self) -> list[Position]:
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base}/v3/accounts/{self._account_id}/openPositions",
                headers=self._headers, timeout=8,
            )
            data = resp.json().get("positions", [])
        positions = []
        for p in data:
            long_units = float(p["long"]["units"])
            short_units = float(p["short"]["units"])
            if long_units != 0:
                positions.append(Position(
                    symbol=p["instrument"].replace("_", ""), side="BUY",
                    quantity=abs(long_units), entry_price=float(p["long"].get("averagePrice", 0)),
                    unrealized_pnl=float(p["long"].get("unrealizedPL", 0)), broker="oanda"
                ))
            if short_units != 0:
                positions.append(Position(
                    symbol=p["instrument"].replace("_", ""), side="SELL",
                    quantity=abs(short_units), entry_price=float(p["short"].get("averagePrice", 0)),
                    unrealized_pnl=float(p["short"].get("unrealizedPL", 0)), broker="oanda"
                ))
        return positions

    async def get_open_orders(self) -> list[Order]:
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base}/v3/accounts/{self._account_id}/pendingOrders",
                headers=self._headers, timeout=8,
            )
            data = resp.json().get("orders", [])
        return [
            Order(order_id=o["id"], symbol=o.get("instrument", "").replace("_", ""),
                  side="BUY" if float(o.get("units", 0)) > 0 else "SELL",
                  quantity=abs(float(o.get("units", 0))), price=float(o.get("price", 0)),
                  order_type=o["type"], status="PENDING", broker="oanda")
            for o in data
        ]


# ── Registry / Factory ────────────────────────────────────────────────────────

_broker_cache: dict[str, BrokerBase] = {}

def get_broker(broker_name: str, force_dry_run: bool = False) -> BrokerBase:
    """
    Get a broker instance by name. Cached — returns same instance on repeated calls.

    broker_name: paper, binance, ccxt:kraken, ccxt:bybit, ccxt:okx, alpaca, ctrader, oanda
    """
    # Global dry-run override
    if force_dry_run or os.getenv("DRY_RUN_ALL", "true").lower() == "true":
        if broker_name not in _broker_cache:
            _broker_cache[broker_name] = PaperBroker()
        return _broker_cache[broker_name]

    if broker_name in _broker_cache:
        return _broker_cache[broker_name]

    if broker_name == "paper":
        broker = PaperBroker()
    elif broker_name == "binance":
        broker = BinanceBroker(testnet=os.getenv("BINANCE_TESTNET", "true").lower() == "true")
    elif broker_name.startswith("ccxt:"):
        exchange_id = broker_name.split(":")[1]
        sandbox_key = f"{exchange_id.upper()}_SANDBOX"
        sandbox = os.getenv(sandbox_key, os.getenv("CCXT_SANDBOX", "true")).lower() == "true"
        broker = CCXTBroker(exchange_id=exchange_id, sandbox=sandbox)
    elif broker_name == "alpaca":
        broker = AlpacaBroker(paper=os.getenv("ALPACA_PAPER", "true").lower() == "true")
    elif broker_name == "oanda":
        broker = OANDABroker(practice=os.getenv("OANDA_PRACTICE", "true").lower() == "true")
    else:
        logger.warning(f"Unknown broker '{broker_name}' — falling back to paper")
        broker = PaperBroker()

    _broker_cache[broker_name] = broker
    return broker


def list_configured_brokers() -> list[str]:
    """Return names of brokers that have API keys configured."""
    configured = ["paper"]  # always available
    if os.getenv("BINANCE_API_KEY"):
        configured.append("binance")
    if os.getenv("KRAKEN_API_KEY"):
        configured.append("ccxt:kraken")
    if os.getenv("BYBIT_API_KEY"):
        configured.append("ccxt:bybit")
    if os.getenv("OKX_API_KEY"):
        configured.append("ccxt:okx")
    if os.getenv("KUCOIN_API_KEY"):
        configured.append("ccxt:kucoin")
    if os.getenv("ALPACA_API_KEY"):
        configured.append("alpaca")
    if os.getenv("CTRADER_ACCESS_TOKEN"):
        configured.append("ctrader")
    if os.getenv("OANDA_API_KEY"):
        configured.append("oanda")
    return configured
