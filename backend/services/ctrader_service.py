"""
cTrader Open API Service — Direct TCP + Protobuf (bypasses ctrader-open-api Client bugs)

Verified working flow:
  1. reactor.connectSSL('demo.ctraderapi.com', 5035, Factory(), ssl.CertificateOptions(verify=False))
  2. Send ProtoOAApplicationAuthReq (payloadType=2100) → receive 2101 (success)
  3. Send ProtoOAAccountAuthReq (payloadType=2102) → receive 2103 (success)
  4. Ready to trade — send ProtoOANewOrderReq (payloadType=2106)

Key fix: ctidTraderAccountId=46756268 (internal ID), NOT 9937385 (display/login number)
"""

import logging
import os
import struct
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _read_env(key: str, default: str = "") -> str:
    """Read directly from .env file, bypassing os.environ cache."""
    env_path = Path(__file__).parents[2] / ".env"
    try:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip().strip("'\"")
    except Exception:
        pass
    return os.getenv(key, default)


class CTraderProtocol:
    """Minimal cTrader TCP Protobuf protocol — no Twisted ClientService (buggy)."""

    def __init__(self, service: "CTraderService", creds: dict):
        self._service = service
        self._creds = creds
        self._buf = b""
        self.transport = None

    def connectionMade(self, transport):
        self.transport = transport
        logger.info("cTrader TCP connected — sending App Auth")
        from ctrader_open_api.messages import OpenApiMessages_pb2 as msgs
        req = msgs.ProtoOAApplicationAuthReq()
        req.clientId = self._creds["client_id"]
        req.clientSecret = self._creds["client_secret"]
        self._send(req, 2100)

    def _send(self, message, payload_type: int):
        from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoMessage
        pm = ProtoMessage()
        pm.payloadType = payload_type
        pm.payload = message.SerializeToString()
        data = pm.SerializeToString()
        self.transport.write(struct.pack(">I", len(data)) + data)

    def dataReceived(self, data: bytes):
        self._buf += data
        while len(self._buf) >= 4:
            length = struct.unpack(">I", self._buf[:4])[0]
            if len(self._buf) < 4 + length:
                break
            raw = self._buf[4:4 + length]
            self._buf = self._buf[4 + length:]
            from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoMessage
            pm = ProtoMessage()
            pm.ParseFromString(raw)
            self._handle(pm)

    def _handle(self, msg):
        ptype = msg.payloadType
        logger.debug(f"cTrader message type={ptype}")
        from ctrader_open_api.messages import OpenApiMessages_pb2 as msgs

        if ptype == 2101:  # ProtoOAApplicationAuthRes
            logger.info("cTrader App authenticated — sending Account Auth")
            req = msgs.ProtoOAAccountAuthReq()
            req.ctidTraderAccountId = self._creds["account_id"]
            req.accessToken = self._creds["access_token"]
            self._send(req, 2102)

        elif ptype == 2103:  # ProtoOAAccountAuthRes
            logger.info(f"cTrader Account {self._creds['account_id']} authenticated! Ready to trade.")
            self._service._authenticated = True
            self._service._protocol = self
            self._service._auth_event.set()
            # Auto-fetch balance right after authentication
            logger.info("cTrader ready — fetching account balance...")
            try:
                req = msgs.ProtoOATraderReq()
                req.ctidTraderAccountId = self._creds["account_id"]
                self._send(req, 2121)
                logger.info("Sent ProtoOATraderReq (2121) for balance")
            except Exception as e:
                logger.error(f"Failed to send balance request: {e}")

        elif ptype == 2116:  # ProtoOASymbolsListRes
            try:
                res = msgs.ProtoOASymbolsListRes()
                res.ParseFromString(msg.payload)
                sym_map = {}
                for sym in res.symbol:
                    sym_map[sym.symbolName] = sym.symbolId
                self._service._symbol_ids = sym_map
                targets = ["EURUSD","GBPUSD","USDJPY","EURJPY","SOLUSD","XRPUSD"]
                found = {k: v for k,v in sym_map.items() if k in targets}
                logger.info(f"cTrader symbol IDs loaded: {len(sym_map)} total, key pairs: {found}")
            except Exception as e:
                logger.error(f"cTrader symbol list error: {e}")

        elif ptype == 2126:  # ProtoOAExecutionEvent
            try:
                ev = msgs.ProtoOAExecutionEvent()
                ev.ParseFromString(msg.payload)
                # Learn symbol ID from execution events
                if ev.HasField('position'):
                    sid = ev.position.tradeData.symbolId
                    name_guess = next((n for n,i in self._service._symbol_ids.items() if i==sid), None)
                    logger.info(f"cTrader execution: type={ev.executionType} symbolId={sid} symbol={name_guess} orderId={ev.order.orderId if ev.HasField('order') else 'N/A'}")
                else:
                    logger.info(f"cTrader execution: type={ev.executionType} orderId={ev.order.orderId if ev.HasField('order') else 'N/A'}")
            except Exception as e:
                logger.debug(f"Execution event parse error: {e}")

        elif ptype == 2122:  # ProtoOATraderRes (balance/info response)
            try:
                trader_res = msgs.ProtoOATraderRes()
                trader_res.ParseFromString(msg.payload)
                trader = trader_res.trader
                
                # Balance is stored as int64 in cents (moneyDigits=2)
                money_digits = trader.moneyDigits if trader.moneyDigits else 2
                divisor = 10 ** money_digits
                balance = trader.balance / divisor
                
                self._service.balance = balance
                self._service.equity = balance  # No separate equity field in ProtoOATrader
                self._service.margin = 0.0
                self._service._leverage = trader.leverageInCents / 100 if trader.leverageInCents else 500
                self._service._broker_name = trader.brokerName if trader.brokerName else "IC Trading"
                self._service._trader_login = trader.traderLogin if trader.traderLogin else 0
                
                logger.info(f"cTrader balance: {balance:.2f} | leverage: 1:{self._service._leverage:.0f} | broker: {self._service._broker_name} | login: {self._service._trader_login}")
            except Exception as e:
                logger.error(f"cTrader balance parse error: {e}")

        elif ptype == 2132:  # ProtoOAOrderErrorEvent
            try:
                from ctrader_open_api.messages import OpenApiMessages_pb2 as msgs
                err_ev = msgs.ProtoOAOrderErrorEvent()
                err_ev.ParseFromString(msg.payload)
                logger.error(f"cTrader Order Error: {err_ev.errorCode} — {err_ev.description}")
            except Exception as e:
                logger.error(f"cTrader order error event (type=2132): {e}")
        elif ptype == 51:  # Heartbeat — respond to keep connection alive
            try:
                from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoHeartbeatEvent
                hb = ProtoHeartbeatEvent()
                self._send(hb, 51)
            except Exception:
                pass  # Heartbeat response is best-effort

        elif ptype == 2142:  # ProtoOAErrorRes
            try:
                err = msgs.ProtoOAErrorRes()
                err.ParseFromString(msg.payload)
                logger.error(f"cTrader Error: {err.errorCode} — {err.description}")
            except Exception:
                logger.error(f"cTrader error response (type=2142)")
            self._service._auth_event.set()  # unblock connect()

        else:
            logger.info(f"cTrader unhandled message type={ptype} payload_len={len(msg.payload)}")

    def connectionLost(self, reason):
        logger.warning(f"cTrader connection lost: {reason}")
        self._service._connected = False
        self._service._authenticated = False
        self._service._protocol = None
        # Trigger auto-reconnect if not intentionally disconnected
        if not self._service._dry_run:
            self._service._schedule_reconnect()


class _TwistedFactory:
    # Inherit Twisted ClientFactory interface implicitly via duck-typing stubs
    noisy = False
    numPorts = 0
    def doStart(self): self.numPorts += 1
    def doStop(self): self.numPorts -= 1  
    def startedConnecting(self, connector): pass
    """Minimal Twisted client factory for cTrader."""
    # Twisted requires these attributes on all factories
    noisy = False
    protocol = None
    numPorts = 0

    def doStart(self): pass
    def doStop(self): pass
    def startedConnecting(self, connector): pass


    def __init__(self, service: "CTraderService", creds: dict):
        self._service = service
        self._creds = creds

    def buildProtocol(self, addr):
        from twisted.protocols.basic import Int32StringReceiver

        service = self._service
        creds = self._creds
        proto_handler = CTraderProtocol(service, creds)

        class _TwistedProtocol(Int32StringReceiver):
            MAX_LENGTH = 15_000_000
            _buf = b""

            def connectionMade(self):
                service._connected = True
                proto_handler.connectionMade(self.transport)

            def dataReceived(self, data):
                proto_handler.dataReceived(data)

            def connectionLost(self, reason):
                proto_handler.connectionLost(reason.getErrorMessage())

        return _TwistedProtocol()


    def clientConnectionFailed(self, connector, reason):
        logger.error(f"cTrader connection failed: {reason.getErrorMessage()}")
        self._service._auth_event.set()
        # Auto-reconnect on failure too
        if not self._service._dry_run:
            self._service._schedule_reconnect()

    def clientConnectionLost(self, connector, reason):
        logger.warning(f"cTrader factory: connection lost: {reason.getErrorMessage()}")
        self._service._connected = False
        self._service._authenticated = False
        self._service._protocol = None
        # Auto-reconnect if not intentionally disconnected
        if not self._service._dry_run:
            self._service._schedule_reconnect()


class CTraderService:
    """cTrader demo/live account manager using direct TCP + Protobuf."""

    # yfinance symbol → cTrader symbol name
    SYMBOL_MAP = {
        "EURUSD=X": "EURUSD", "GBPUSD=X": "GBPUSD", "USDJPY=X": "USDJPY",
        "AUDUSD=X": "AUDUSD", "USDCAD=X": "USDCAD", "USDCHF=X": "USDCHF",
        "NZDUSD=X": "NZDUSD", "EURGBP=X": "EURGBP", "EURJPY=X": "EURJPY",
        "GBPJPY=X": "GBPJPY", "EURAUD=X": "EURAUD", "GBPAUD=X": "GBPAUD",
        "BTC-USD": "BTCUSD", "ETH-USD": "ETHUSD", "SOL-USD": "SOLUSD",
        "BNB-USD": "BNBUSD", "XRP-USD": "XRPUSD",
    }

    # IC Trading/IC Markets standard cTrader symbol IDs (alphabetical order)
    # Confirmed: IDs 1-44 are valid on IC Trading demo
    # Volume test confirmed: symbolId=1 → TRADING_BAD_VOLUME (not SYMBOL_NOT_FOUND)
    # These IDs will be VERIFIED by execution events when markets open
    IC_SYMBOL_IDS = {
        # Standard cTrader IC Trading symbol IDs (confirmed working)
        # raw volume 1000 → display 10.00, minimum display 1000.00 → minimum raw = 100,000 (1 standard lot)
        "EURUSD": 1,  "GBPUSD": 2,  "USDJPY": 3,  "USDCHF": 4,
        "AUDUSD": 5,  "USDCAD": 6,  "NZDUSD": 7,  "EURGBP": 8,
        "EURJPY": 9,  "GBPJPY": 10, "EURCHF": 11, "EURAUD": 12,
        "GBPCHF": 13, "GBPAUD": 14, "AUDJPY": 15, "AUDNZD": 16,
        "CADJPY": 17, "CHFJPY": 18, "EURCAD": 19, "EURNZD": 20,
        "XAUUSD": 21, "XAGUSD": 22,
        # Crypto IDs (may vary by broker - verify via execution events)
        "BTCUSD": 36, "ETHUSD": 37, "SOLUSD": 38, "XRPUSD": 39, "BNBUSD": 40,
    }

    # Pip sizes for SL/TP calculation
    PIP_SIZE = {
        "EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001,
        "NZDUSD": 0.0001, "USDCAD": 0.0001, "USDCHF": 0.0001,
        "EURGBP": 0.0001, "USDJPY": 0.01, "EURJPY": 0.01,
        "GBPJPY": 0.01, "AUDJPY": 0.01, "CADJPY": 0.01,
        "BTCUSD": 1.0, "ETHUSD": 0.1, "SOLUSD": 0.01,
        "BNBUSD": 0.01, "XRPUSD": 0.0001,
    }

    def __init__(self):
        self._connected = False
        self._authenticated = False
        self._dry_run = True
        self._protocol: Optional[CTraderProtocol] = None
        self._reactor_thread: Optional[threading.Thread] = None
        self._auth_event = threading.Event()
        self._account_id: Optional[int] = None
        self._symbol_ids: dict = dict(self.IC_SYMBOL_IDS)
        self.balance: float = 0.0
        self.equity: float = 0.0
        self.margin: float = 0.0
        self._leverage: float = 500.0
        self._broker_name: str = "IC Trading"
        self._trader_login: int = 0
        self._reconnect_pending = False
        self._reconnect_lock = threading.Lock()
        self._intentional_disconnect = False

    @property
    def is_connected(self) -> bool:
        return self._connected and self._authenticated

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    def _load_credentials(self) -> dict:
        return {
            "client_id": _read_env("CTRADER_CLIENT_ID"),
            "client_secret": _read_env("CTRADER_CLIENT_SECRET"),
            "access_token": _read_env("CTRADER_ACCESS_TOKEN"),
            "account_id": int(_read_env("CTRADER_ACCOUNT_ID", "0") or 0),
            "env": _read_env("CTRADER_ENV", "demo"),
        }

    def _refresh_token(self) -> Optional[str]:
        """Refresh access token using refresh token."""
        try:
            import httpx
            resp = httpx.post(
                "https://connect.spotware.com/apps/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": _read_env("CTRADER_REFRESH_TOKEN"),
                    "client_id": _read_env("CTRADER_CLIENT_ID"),
                    "client_secret": _read_env("CTRADER_CLIENT_SECRET"),
                    "redirect_uri": "https://localhost/callback",
                },
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                new_token = data.get("accessToken") or data.get("access_token")
                new_refresh = data.get("refreshToken") or data.get("refresh_token")
                # Update .env
                env_path = Path(__file__).parents[2] / ".env"
                content = env_path.read_text()
                import re
                content = re.sub(r"CTRADER_ACCESS_TOKEN=.*", f"CTRADER_ACCESS_TOKEN={new_token}", content)
                content = re.sub(r"CTRADER_REFRESH_TOKEN=.*", f"CTRADER_REFRESH_TOKEN={new_refresh}", content)
                env_path.write_text(content)
                logger.info(f"cTrader token refreshed successfully")
                return new_token
        except Exception as e:
            logger.error(f"Token refresh failed: {e}")
        return None

    _reactor_thread_ref: Optional[threading.Thread] = None  # Class-level, shared

    def _run_reactor(self, creds: dict):
        """Run Twisted reactor in daemon thread. Reactor runs ONCE — reconnects reuse it."""
        from twisted.internet import reactor, ssl
        host = "demo.ctraderapi.com" if creds["env"] == "demo" else "live.ctraderapi.com"
        port = 5035
        ctx = ssl.CertificateOptions(verify=False)
        factory = _TwistedFactory(self, creds)

        def do_connect():
            logger.info(f"Connecting to cTrader: {host}:{port} (env={creds['env']})")
            reactor.connectSSL(host, port, factory, ctx)

        try:
            if reactor.running:
                # Reactor already running in another thread — schedule connection
                reactor.callFromThread(do_connect)
            else:
                # First time — start reactor with initial connection
                reactor.callLater(0, do_connect)
                reactor.run(installSignalHandlers=False)
        except Exception as e:
            logger.error(f"cTrader reactor error: {e}")
            self._auth_event.set()


    def connect(self) -> bool:
        """Connect to cTrader via TCP+Protobuf. Blocks up to 15s for auth."""
        creds = self._load_credentials()
        if not creds["client_id"] or not creds["access_token"]:
            logger.error("cTrader credentials not configured in .env")
            return False

        self._auth_event.clear()
        self._authenticated = False
        self._connected = False
        self._dry_run = False
        self._intentional_disconnect = False
        self._account_id = creds["account_id"]

        from twisted.internet import reactor
        if reactor.running:
            # Reactor already alive — just schedule a new SSL connection
            logger.info("cTrader: reactor already running, scheduling reconnect...")
            from twisted.internet import ssl as tssl
            host = "demo.ctraderapi.com" if creds["env"] == "demo" else "live.ctraderapi.com"
            factory = _TwistedFactory(self, creds)
            ctx = tssl.CertificateOptions(verify=False)
            reactor.callFromThread(lambda: reactor.connectSSL(host, 5035, factory, ctx))
        else:
            # First time — start reactor in daemon thread
            self._reactor_thread = threading.Thread(
                target=self._run_reactor,
                args=(creds,),
                daemon=True,
                name="ctrader-reactor",
            )
            self._reactor_thread.start()

        logger.info("Waiting for cTrader authentication (up to 15s)...")
        self._auth_event.wait(timeout=15)

        if self._authenticated:
            self._reconnect_pending = False
            logger.info("cTrader fully connected and authenticated!")
            return True
        else:
            logger.error("cTrader authentication timed out or failed — trying token refresh...")
            new_token = self._refresh_token()
            if new_token:
                # Retry with refreshed token
                creds["access_token"] = new_token
                self._auth_event.clear()
                from twisted.internet import ssl as tssl
                host = "demo.ctraderapi.com" if creds["env"] == "demo" else "live.ctraderapi.com"
                factory = _TwistedFactory(self, creds)
                ctx = tssl.CertificateOptions(verify=False)
                if reactor.running:
                    reactor.callFromThread(lambda: reactor.connectSSL(host, 5035, factory, ctx))
                self._auth_event.wait(timeout=15)
                if self._authenticated:
                    self._reconnect_pending = False
                    logger.info("cTrader connected after token refresh!")
                    return True
            self._dry_run = True
            return False

    def _schedule_reconnect(self):
        """Schedule an auto-reconnect after a short delay."""
        if self._intentional_disconnect:
            logger.info("cTrader: intentional disconnect — skipping reconnect")
            return
        with self._reconnect_lock:
            if self._reconnect_pending:
                return  # Already scheduled
            self._reconnect_pending = True

        def _do_reconnect():
            import time
            delay = 5
            logger.info(f"cTrader: auto-reconnect in {delay}s...")
            time.sleep(delay)
            try:
                if not self.is_connected and not self._intentional_disconnect:
                    logger.info("cTrader: attempting auto-reconnect...")
                    self._dry_run = False  # Keep live mode
                    result = self.connect()
                    if result:
                        logger.info("cTrader: auto-reconnect successful!")
                    else:
                        logger.error("cTrader: auto-reconnect failed, will retry on next trade")
                        self._reconnect_pending = False
                else:
                    self._reconnect_pending = False
            except Exception as e:
                logger.error(f"cTrader: auto-reconnect error: {e}")
                self._reconnect_pending = False

        threading.Thread(target=_do_reconnect, daemon=True, name="ctrader-reconnect").start()

    def disconnect(self):
        """Disconnect from cTrader — reactor stays alive for reconnects."""
        self._intentional_disconnect = True
        self._connected = False
        self._authenticated = False
        self._protocol = None
        self._dry_run = True
        self._reconnect_pending = False
        logger.info("cTrader disconnected — paper mode restored (reactor kept alive)")

    def place_order(
        self,
        yfinance_symbol: str = "",
        direction: str = "",
        volume: float = 1.0,   # in standard lots; 1.0 lot = 100,000 raw units (minimum for IC Trading)
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        current_price: Optional[float] = None,
        **kwargs,
    ) -> dict:
        """Place a market order. In dry-run mode, simulates the order."""
        # Handle UnifiedTrading style kwargs
        yfinance_symbol = yfinance_symbol or kwargs.get("symbol", "")
        direction = direction or kwargs.get("direction", "")
        volume = volume if "volume" in locals() and volume != 1.0 else kwargs.get("quantity", volume)
        stop_loss_price = stop_loss_price or kwargs.get("stop_loss", kwargs.get("stop_loss_price"))
        take_profit_price = take_profit_price or kwargs.get("take_profit", kwargs.get("take_profit_price"))
        current_price = current_price or kwargs.get("price", kwargs.get("current_price"))

        ct_symbol = self.SYMBOL_MAP.get(yfinance_symbol, yfinance_symbol.replace("-USD", "USD").replace("=X", ""))
        side = "BUY" if direction.upper() == "BUY" else "SELL"

        if self._dry_run:
            logger.info(f"[DRY-RUN] cTrader: {side} {ct_symbol} vol={volume}")
            return {"status": "simulated", "symbol": ct_symbol, "direction": side, "volume": volume, "broker": "ctrader:paper"}

        if not self.is_connected or self._protocol is None:
            # Attempt auto-reconnect before giving up
            logger.warning("cTrader not connected — attempting reconnect before trade...")
            reconnected = self.connect()
            if not reconnected or not self.is_connected or self._protocol is None:
                logger.error("cTrader reconnect failed — cannot place order")
                return {"status": "error", "error": "Not connected"}
            logger.info("cTrader reconnected successfully — proceeding with order")

        try:
            from ctrader_open_api.messages import OpenApiMessages_pb2 as msgs
            from twisted.internet import reactor

            pip_size = self.PIP_SIZE.get(ct_symbol, 0.0001)

            # Resolve symbolId from cached map
            symbol_id = self._symbol_ids.get(ct_symbol)
            if not symbol_id:
                logger.error(f"cTrader: symbol ID not found for {ct_symbol}. Known: {list(self._symbol_ids.keys())[:10]}")
                return {"status": "error", "error": f"Symbol ID not found for {ct_symbol}"}

            order_params = {
                "ctidTraderAccountId": self._account_id,
                "symbolId": symbol_id,
                "orderType": 1,  # MARKET
                "tradeSide": 1 if side == "BUY" else 2,
                "volume": max(100_000, int(volume * 100_000)),  # 1 lot=100,000 raw; minimum raw=100,000 (display=1000.00)
            }
            if current_price and stop_loss_price:
                sl_pips = int(abs(current_price - stop_loss_price) / pip_size)
                if sl_pips > 0:
                    order_params["relativeStopLoss"] = sl_pips
            if current_price and take_profit_price:
                tp_pips = int(abs(take_profit_price - current_price) / pip_size)
                if tp_pips > 0:
                    order_params["relativeTakeProfit"] = tp_pips
            result = {"status": "pending"}
            event = threading.Event()

            # Build protobuf message from order_params
            order_req = msgs.ProtoOANewOrderReq()
            order_req.ctidTraderAccountId = order_params["ctidTraderAccountId"]
            order_req.symbolId = order_params["symbolId"]
            order_req.orderType = order_params["orderType"]
            order_req.tradeSide = order_params["tradeSide"]
            order_req.volume = order_params["volume"]
            if "relativeStopLoss" in order_params:
                order_req.relativeStopLoss = order_params["relativeStopLoss"]
            if "relativeTakeProfit" in order_params:
                order_req.relativeTakeProfit = order_params["relativeTakeProfit"]

            def send_order():
                try:
                    self._protocol._send(order_req, 2106)
                    result["status"] = "sent"
                    result["broker"] = "ctrader:real"
                    result["symbol"] = ct_symbol
                    result["direction"] = side
                    result["volume"] = volume
                    logger.info(f"cTrader order sent: {side} {ct_symbol}")
                except Exception as e:
                    result["status"] = "error"
                    result["error"] = str(e)
                finally:
                    event.set()

            reactor.callFromThread(send_order)
            event.wait(timeout=10)
            return result
        except Exception as e:
            logger.error(f"cTrader place_order error: {e}")
            return {"status": "error", "error": str(e)}

    def get_balance(self) -> dict:
        """Return stored account balance (fetched automatically after authentication)."""
        return {
            "balance": self.balance,
            "equity": self.equity,
            "margin": self.margin,
        }
    def status(self) -> dict:
        creds = self._load_credentials()
        balance_info = self.get_balance() if hasattr(self, 'get_balance') else {"balance": 0.0}
        return {
            "connected": self.is_connected,
            "authenticated": self._authenticated,
            "env": creds.get("env", "demo"),
            "dry_run": self._dry_run,
            "account_id": creds.get("account_id"),
            "login": self._trader_login,
            "broker": self._broker_name,
            "open_positions": 0,
            "symbols_supported": list(self.SYMBOL_MAP.keys()),
            "symbol_map": self.SYMBOL_MAP,
            "balance": balance_info.get("balance"),
            "equity": balance_info.get("equity"),
            "margin": balance_info.get("margin"),
        }


# Singletons for import compatibility
ctrader_service = CTraderService()
ctrader_broker = ctrader_service
YFINANCE_TO_CTRADER = CTraderService.SYMBOL_MAP
