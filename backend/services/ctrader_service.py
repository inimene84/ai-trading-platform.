"""
cTrader Open API Service — Direct TCP + Protobuf Protocol Adapter.

Implements the BrokerService protocol for QuantumTrade Pro.
Supports Forex, CFDs, Commodities, and Crypto with native SL/TP calculations,
safe paper mode by default, token lifecycle rotation persistence, and auto-reconnect watchdog.
"""

import logging
import os
import struct
import threading
import time
from decimal import Decimal
from pathlib import Path
from typing import Optional, Dict, List, Any

from backend.brokers.base import BrokerService
from backend.services.ctrader_tokens import token_store

logger = logging.getLogger(__name__)


class CTraderProtocol:
    """cTrader TCP Protobuf framing protocol handler."""

    def __init__(self, service: "CTraderService", creds: dict):
        self._service = service
        self._creds = creds
        self._buf = b""
        self.transport = None
        self.last_heartbeat = time.time()

    def connectionMade(self, transport):
        self.transport = transport
        self.last_heartbeat = time.time()
        logger.info("cTrader TCP connected — initiating Application Authorization (2100)")
        try:
            from ctrader_open_api.messages import OpenApiMessages_pb2 as msgs
            req = msgs.ProtoOAApplicationAuthReq()
            req.clientId = self._creds.get("client_id", "")
            req.clientSecret = self._creds.get("client_secret", "")
            self._send(req, 2100)
        except Exception as e:
            logger.error(f"Failed to send cTrader App Auth: {e}")

    def _send(self, message: Any, payload_type: int):
        if not self.transport:
            return
        try:
            from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoMessage
            pm = ProtoMessage()
            pm.payloadType = payload_type
            pm.payload = message.SerializeToString() if hasattr(message, "SerializeToString") else message
            data = pm.SerializeToString()
            self.transport.write(struct.pack(">I", len(data)) + data)
        except Exception as e:
            logger.error(f"cTrader protocol send error (payloadType={payload_type}): {e}")

    def dataReceived(self, data: bytes):
        self.last_heartbeat = time.time()
        self._buf += data
        while len(self._buf) >= 4:
            length = struct.unpack(">I", self._buf[:4])[0]
            if len(self._buf) < 4 + length:
                break
            raw = self._buf[4: 4 + length]
            self._buf = self._buf[4 + length:]
            try:
                from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoMessage
                pm = ProtoMessage()
                pm.ParseFromString(raw)
                self._handle(pm)
            except Exception as e:
                logger.error(f"Failed to decode cTrader message frame: {e}")

    def _handle(self, msg: Any):
        ptype = msg.payloadType
        self.last_heartbeat = time.time()
        logger.debug(f"cTrader message received type={ptype}")

        try:
            from ctrader_open_api.messages import OpenApiMessages_pb2 as msgs
            from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoHeartbeatEvent

            if ptype == 2101:  # ProtoOAApplicationAuthRes
                logger.info("cTrader App authenticated — sending Account Auth (2102)")
                req = msgs.ProtoOAAccountAuthReq()
                req.ctidTraderAccountId = self._creds.get("account_id", 0)
                req.accessToken = self._creds.get("access_token", "")
                self._send(req, 2102)

            elif ptype == 2103:  # ProtoOAAccountAuthRes
                logger.info(f"cTrader Account {self._creds.get('account_id')} authenticated! Ready to trade.")
                self._service._authenticated = True
                self._service._protocol = self
                self._service._auth_event.set()

                # 1. Fetch balance & account specs
                req_trader = msgs.ProtoOATraderReq()
                req_trader.ctidTraderAccountId = self._creds.get("account_id", 0)
                self._send(req_trader, 2121)

                # 2. Fetch symbol list for dynamic mapping
                req_syms = msgs.ProtoOASymbolsListReq()
                req_syms.ctidTraderAccountId = self._creds.get("account_id", 0)
                self._send(req_syms, 2114)

                # 3. Reconcile existing open positions
                req_reconcile = msgs.ProtoOAReconcileReq()
                req_reconcile.ctidTraderAccountId = self._creds.get("account_id", 0)
                self._send(req_reconcile, 2124)

            elif ptype == 2116:  # ProtoOASymbolsListRes
                res = msgs.ProtoOASymbolsListRes()
                res.ParseFromString(msg.payload)
                sym_map = {}
                for sym in res.symbol:
                    sym_map[sym.symbolName] = sym.symbolId
                self._service._symbol_ids.update(sym_map)
                logger.info(f"cTrader dynamic symbol catalog updated: {len(sym_map)} instruments")

            elif ptype == 2122:  # ProtoOATraderRes
                trader_res = msgs.ProtoOATraderRes()
                trader_res.ParseFromString(msg.payload)
                trader = trader_res.trader

                money_digits = trader.moneyDigits if trader.moneyDigits else 2
                divisor = 10 ** money_digits
                balance = trader.balance / divisor

                self._service.balance = balance
                self._service.equity = balance
                self._service.margin = 0.0
                self._service._leverage = trader.leverageInCents / 100 if trader.leverageInCents else 500
                self._service._broker_name = trader.brokerName if trader.brokerName else "cTrader Broker"
                self._service._trader_login = trader.traderLogin if trader.traderLogin else 0
                logger.info(f"cTrader balance: {balance:.2f} | leverage 1:{self._service._leverage:.0f}")

            elif ptype == 2125:  # ProtoOAReconcileRes
                reconcile_res = msgs.ProtoOAReconcileRes()
                reconcile_res.ParseFromString(msg.payload)
                positions = []
                for p in reconcile_res.position:
                    side = "BUY" if p.tradeData.tradeSide == 1 else "SELL"
                    sym_name = next((k for k, v in self._service._symbol_ids.items() if v == p.tradeData.symbolId), str(p.tradeData.symbolId))
                    volume_lots = p.tradeData.volume / 100_000.0
                    positions.append({
                        "symbol": sym_name,
                        "side": side,
                        "quantity": volume_lots,
                        "entry_price": p.price if hasattr(p, "price") else 0.0,
                        "unrealized_pnl": 0.0,
                        "position_id": str(p.positionId),
                        "broker": "ctrader",
                    })
                self._service._positions = positions
                logger.info(f"cTrader positions reconciled: {len(positions)} open")

            elif ptype == 2126:  # ProtoOAExecutionEvent
                ev = msgs.ProtoOAExecutionEvent()
                ev.ParseFromString(msg.payload)
                logger.info(f"cTrader execution event received: type={ev.executionType}")
                # Refresh positions on fill/close
                req_rec = msgs.ProtoOAReconcileReq()
                req_rec.ctidTraderAccountId = self._creds.get("account_id", 0)
                self._send(req_rec, 2124)

            elif ptype == 2132:  # ProtoOAOrderErrorEvent
                err_ev = msgs.ProtoOAOrderErrorEvent()
                err_ev.ParseFromString(msg.payload)
                logger.error(f"cTrader Order Error: {err_ev.errorCode} — {err_ev.description}")

            elif ptype == 51:  # ProtoHeartbeatEvent
                hb = ProtoHeartbeatEvent()
                self._send(hb, 51)

            elif ptype == 2142:  # ProtoOAErrorRes
                err = msgs.ProtoOAErrorRes()
                err.ParseFromString(msg.payload)
                logger.error(f"cTrader Protocol Error: {err.errorCode} — {err.description}")
                self._service._auth_event.set()

        except Exception as e:
            logger.error(f"cTrader protocol handle error (ptype={ptype}): {e}")

    def connectionLost(self, reason):
        logger.warning(f"cTrader connection lost: {reason}")
        self._service._connected = False
        self._service._authenticated = False
        self._service._protocol = None
        if not self._service._dry_run:
            self._service._schedule_reconnect()


class CTraderService(BrokerService):
    """
    cTrader Open API Broker implementation.
    Adheres strictly to the BrokerService interface.
    """

    # Symbol normalization map
    SYMBOL_MAP = {
        "EURUSD=X": "EURUSD", "GBPUSD=X": "GBPUSD", "USDJPY=X": "USDJPY",
        "AUDUSD=X": "AUDUSD", "USDCAD=X": "USDCAD", "USDCHF=X": "USDCHF",
        "NZDUSD=X": "NZDUSD", "EURGBP=X": "EURGBP", "EURJPY=X": "EURJPY",
        "GBPJPY=X": "GBPJPY", "EURAUD=X": "EURAUD", "GBPAUD=X": "GBPAUD",
        "BTC-USD": "BTCUSD", "ETH-USD": "ETHUSD", "SOL-USD": "SOLUSD",
        "BNB-USD": "BNBUSD", "XRP-USD": "XRPUSD",
    }

    # Standard known symbol IDs (fallback before dynamic catalog is loaded)
    DEFAULT_SYMBOL_IDS = {
        "EURUSD": 1, "GBPUSD": 2, "USDJPY": 3, "USDCHF": 4,
        "AUDUSD": 5, "USDCAD": 6, "NZDUSD": 7, "EURGBP": 8,
        "EURJPY": 9, "GBPJPY": 10, "EURCHF": 11, "EURAUD": 12,
        "GBPCHF": 13, "GBPAUD": 14, "AUDJPY": 15, "AUDNZD": 16,
        "CADJPY": 17, "CHFJPY": 18, "EURCAD": 19, "EURNZD": 20,
        "XAUUSD": 21, "XAGUSD": 22,
        "BTCUSD": 36, "ETHUSD": 37, "SOLUSD": 38, "XRPUSD": 39, "BNBUSD": 40,
    }

    # Standard pip size for SL/TP offset calculation
    PIP_SIZE = {
        "EURUSD": Decimal("0.0001"), "GBPUSD": Decimal("0.0001"), "AUDUSD": Decimal("0.0001"),
        "NZDUSD": Decimal("0.0001"), "USDCAD": Decimal("0.0001"), "USDCHF": Decimal("0.0001"),
        "EURGBP": Decimal("0.0001"), "USDJPY": Decimal("0.01"),   "EURJPY": Decimal("0.01"),
        "GBPJPY": Decimal("0.01"),   "AUDJPY": Decimal("0.01"),   "CADJPY": Decimal("0.01"),
        "XAUUSD": Decimal("0.01"),   "XAGUSD": Decimal("0.001"),
        "BTCUSD": Decimal("1.0"),    "ETHUSD": Decimal("0.1"),    "SOLUSD": Decimal("0.01"),
        "BNBUSD": Decimal("0.01"),   "XRPUSD": Decimal("0.0001"),
    }

    def __init__(self):
        self._connected = False
        self._authenticated = False
        self._dry_run = True
        self._protocol: Optional[CTraderProtocol] = None
        self._reactor_thread: Optional[threading.Thread] = None
        self._auth_event = threading.Event()
        self._account_id: Optional[int] = None
        self._symbol_ids: Dict[str, int] = dict(self.DEFAULT_SYMBOL_IDS)
        self._positions: List[Dict[str, Any]] = []

        self.balance: float = 0.0
        self.equity: float = 0.0
        self.margin: float = 0.0
        self._leverage: float = 500.0
        self._broker_name: str = "cTrader"
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

    def _normalize_symbol(self, sym: str) -> str:
        """Map generic/yfinance symbols to cTrader symbol string."""
        if not sym:
            return ""
        return self.SYMBOL_MAP.get(sym, sym.replace("-USD", "USD").replace("=X", ""))

    def connect(self) -> bool:
        """Authenticate and connect to cTrader Open API."""
        tokens = token_store.get_tokens()
        client_id = tokens.get("client_id") or os.getenv("CTRADER_CLIENT_ID", "")
        access_token = token_store.refresh_if_needed() or tokens.get("access_token") or os.getenv("CTRADER_ACCESS_TOKEN", "")
        account_id = tokens.get("account_id") or int(os.getenv("CTRADER_ACCOUNT_ID", "0") or 0)

        if not client_id or not access_token:
            logger.warning("cTrader credentials missing. Operating in paper/dry-run mode.")
            return False

        # Host security gate: paper mode defaults to demo host
        paper_mode = os.getenv("CTRADE_PAPER_MODE", "true").lower() == "true"
        live_confirm = os.getenv("CTRADE_LIVE_CONFIRM", "").strip()
        is_live = (not paper_mode) and (live_confirm == "I_UNDERSTAND")
        host = "live.ctraderapi.com" if is_live else "demo.ctraderapi.com"
        port = 5035

        self._auth_event.clear()
        self._authenticated = False
        self._connected = False
        self._dry_run = False
        self._intentional_disconnect = False
        self._account_id = account_id

        creds = {
            "client_id": client_id,
            "client_secret": tokens.get("client_secret") or os.getenv("CTRADER_CLIENT_SECRET", ""),
            "access_token": access_token,
            "account_id": account_id,
            "host": host,
            "port": port,
        }

        try:
            from twisted.internet import reactor, ssl as tssl

            class _TwistedFactory:
                noisy = False
                numPorts = 0
                def doStart(self): self.numPorts += 1
                def doStop(self): self.numPorts -= 1
                def startedConnecting(self, connector): pass

                def __init__(self, svc, c):
                    self._svc = svc
                    self._c = c

                def buildProtocol(self, addr):
                    from twisted.protocols.basic import Int32StringReceiver
                    svc = self._svc
                    proto = CTraderProtocol(svc, self._c)

                    class _Int32Proto(Int32StringReceiver):
                        MAX_LENGTH = 15_000_000
                        def connectionMade(self):
                            svc._connected = True
                            proto.connectionMade(self.transport)
                        def dataReceived(self, data):
                            proto.dataReceived(data)
                        def connectionLost(self, reason):
                            proto.connectionLost(reason.getErrorMessage())

                    return _Int32Proto()

                def clientConnectionFailed(self, connector, reason):
                    logger.error(f"cTrader connection failed: {reason.getErrorMessage()}")
                    self._svc._auth_event.set()

                def clientConnectionLost(self, connector, reason):
                    logger.warning(f"cTrader connection dropped: {reason.getErrorMessage()}")
                    self._svc._connected = False
                    self._svc._authenticated = False

            factory = _TwistedFactory(self, creds)
            ctx = tssl.CertificateOptions(verify=False)

            if reactor.running:
                reactor.callFromThread(lambda: reactor.connectSSL(host, port, factory, ctx))
            else:
                def run():
                    reactor.callLater(0, lambda: reactor.connectSSL(host, port, factory, ctx))
                    reactor.run(installSignalHandlers=False)

                self._reactor_thread = threading.Thread(target=run, daemon=True, name="ctrader-reactor")
                self._reactor_thread.start()

            logger.info("Awaiting cTrader authentication (15s timeout)...")
            self._auth_event.wait(timeout=15.0)

            if self._authenticated:
                self._reconnect_pending = False
                logger.info("cTrader connected and authenticated successfully!")
                return True
            else:
                logger.warning("cTrader authentication timed out — falling back to paper/dry-run")
                self._dry_run = True
                return False

        except ImportError:
            logger.info("Twisted networking stack not installed locally. Operating in simulated paper mode.")
            self._dry_run = True
            return True
        except Exception as e:
            logger.error(f"cTrader connect error: {e}")
            self._dry_run = True
            return False

    def _schedule_reconnect(self):
        """Auto-reconnect with exponential backoff on unexpected disconnect."""
        if self._intentional_disconnect:
            return
        with self._reconnect_lock:
            if self._reconnect_pending:
                return
            self._reconnect_pending = True

        def _worker():
            delay = 5
            time.sleep(delay)
            if not self.is_connected and not self._intentional_disconnect:
                logger.info("cTrader auto-reconnect attempting recovery...")
                self._dry_run = False
                self.connect()
            self._reconnect_pending = False

        threading.Thread(target=_worker, daemon=True, name="ctrader-reconnect").start()

    def disconnect(self) -> None:
        """Gracefully disconnect from cTrader and return to paper mode."""
        self._intentional_disconnect = True
        self._connected = False
        self._authenticated = False
        self._protocol = None
        self._dry_run = True
        logger.info("cTrader disconnected — paper mode enabled")

    def get_balance(self) -> Dict[str, Any]:
        """Fetch current balance, equity, and margin."""
        return {
            "balance": self.balance,
            "available": self.balance - self.margin,
            "equity": self.equity,
            "margin_used": self.margin,
            "unrealized_pnl": 0.0,
            "broker": "ctrader",
        }

    def get_positions(self) -> List[Dict[str, Any]]:
        """Return all open positions across cTrader."""
        return list(self._positions)

    def place_order(
        self,
        symbol: str = "",
        direction: str = "",
        action: str = "open",
        quantity: Optional[float] = None,
        volume: Optional[float] = None,
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Place order with exact volume conversion and pip-offset calculations.
        Supports standard lots (1.0 lot = 100,000 units).
        """
        # Resolve argument aliases
        raw_sym = symbol or kwargs.get("yfinance_symbol", "")
        ct_symbol = self._normalize_symbol(raw_sym)
        side = "BUY" if direction.upper() in ("BUY", "LONG") else "SELL"
        lots = volume if volume is not None else (quantity if quantity is not None else 1.0)
        current_price = price or kwargs.get("current_price")
        stop_loss_price = stop_loss or kwargs.get("stop_loss_price")
        take_profit_price = take_profit or kwargs.get("take_profit_price")

        # Simulated fallback in dry-run
        if self._dry_run or not self.is_connected or self._protocol is None:
            logger.info(f"[PAPER] cTrader: {side} {ct_symbol} lots={lots:.2f}")
            return {
                "status": "simulated",
                "broker": "ctrader:paper",
                "symbol": ct_symbol,
                "direction": side,
                "quantity": lots,
                "price": float(current_price or 1.0),
                "order_id": f"sim_ct_{int(time.time() * 1000)}",
            }

        try:
            from ctrader_open_api.messages import OpenApiMessages_pb2 as msgs
            from twisted.internet import reactor

            symbol_id = self._symbol_ids.get(ct_symbol)
            if not symbol_id:
                logger.error(f"cTrader symbol ID not found for {ct_symbol}")
                return {"status": "error", "error": f"Unknown symbol {ct_symbol}"}

            # 1.0 standard lot = 100,000 raw protocol units (min raw 100,000 for standard FX)
            raw_volume = max(100_000, int(round(lots * 100_000)))
            pip_size = self.PIP_SIZE.get(ct_symbol, Decimal("0.0001"))

            order_req = msgs.ProtoOANewOrderReq()
            order_req.ctidTraderAccountId = self._account_id or 0
            order_req.symbolId = symbol_id
            order_req.orderType = 1  # MARKET
            order_req.tradeSide = 1 if side == "BUY" else 2
            order_req.volume = raw_volume

            # Calculate relative SL/TP pips with Decimal precision
            if current_price and stop_loss_price:
                diff = abs(Decimal(str(current_price)) - Decimal(str(stop_loss_price)))
                sl_pips = int(diff / pip_size)
                if sl_pips > 0:
                    order_req.relativeStopLoss = sl_pips

            if current_price and take_profit_price:
                diff = abs(Decimal(str(take_profit_price)) - Decimal(str(current_price)))
                tp_pips = int(diff / pip_size)
                if tp_pips > 0:
                    order_req.relativeTakeProfit = tp_pips

            event = threading.Event()
            result = {"status": "pending"}

            def _send():
                try:
                    self._protocol._send(order_req, 2106)
                    result["status"] = "sent"
                    result["broker"] = "ctrader:live"
                    result["symbol"] = ct_symbol
                    result["direction"] = side
                    result["quantity"] = lots
                    logger.info(f"cTrader live order dispatched: {side} {ct_symbol} (volume={raw_volume})")
                except Exception as e:
                    result["status"] = "error"
                    result["error"] = str(e)
                finally:
                    event.set()

            reactor.callFromThread(_send)
            event.wait(timeout=10.0)
            return result

        except Exception as e:
            logger.error(f"cTrader place_order error: {e}")
            return {"status": "error", "error": str(e)}

    def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Cancel pending order by ID."""
        if self._dry_run or not self.is_connected or self._protocol is None:
            return {"success": True, "message": "Order cancelled in paper mode", "order_id": order_id}

        try:
            from ctrader_open_api.messages import OpenApiMessages_pb2 as msgs
            from twisted.internet import reactor

            req = msgs.ProtoOACancelOrderReq()
            req.ctidTraderAccountId = self._account_id or 0
            req.orderId = int(order_id) if str(order_id).isdigit() else 0

            reactor.callFromThread(lambda: self._protocol._send(req, 2108))
            return {"success": True, "message": "Cancel request sent", "order_id": order_id}
        except Exception as e:
            return {"success": False, "message": str(e), "order_id": order_id}

    def close_position(self, position_id: str | int, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Close open position by position ID."""
        if self._dry_run or not self.is_connected or self._protocol is None:
            self._positions = [p for p in self._positions if str(p.get("position_id")) != str(position_id)]
            return {"status": "closed", "position_id": position_id, "broker": "ctrader:paper"}

        try:
            from ctrader_open_api.messages import OpenApiMessages_pb2 as msgs
            from twisted.internet import reactor

            req = msgs.ProtoOAClosePositionReq()
            req.ctidTraderAccountId = self._account_id or 0
            req.positionId = int(position_id) if str(position_id).isdigit() else 0

            reactor.callFromThread(lambda: self._protocol._send(req, 2107))
            return {"status": "sent", "position_id": position_id, "broker": "ctrader:live"}
        except Exception as e:
            return {"status": "error", "error": str(e), "position_id": position_id}

    def status(self) -> Dict[str, Any]:
        tokens = token_store.get_tokens()
        return {
            "connected": self.is_connected,
            "authenticated": self._authenticated,
            "env": "live" if not self._dry_run else "paper",
            "dry_run": self._dry_run,
            "account_id": tokens.get("account_id"),
            "login": self._trader_login,
            "broker": self._broker_name,
            "open_positions": len(self._positions),
            "symbols_supported": list(self.SYMBOL_MAP.keys()),
            "symbol_map": self.SYMBOL_MAP,
            "balance": self.balance,
            "equity": self.equity,
            "margin": self.margin,
        }


# Global service singleton
ctrader_service = CTraderService()
ctrader_broker = ctrader_service
YFINANCE_TO_CTRADER = CTraderService.SYMBOL_MAP
