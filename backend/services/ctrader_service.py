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
        self._subscribed_spot_ids: set[int] = set()

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

    def _subscribe_spots(self, symbol_ids):
        """Ask for streaming quotes on symbols we hold.

        Spot events (2131) are the only price source for marking a forex
        position. Without this request the broker never sends them, so every
        position reported a 0.00 P&L.
        """
        wanted = {int(sid) for sid in symbol_ids if sid}
        new_ids = wanted - self._subscribed_spot_ids
        if not new_ids:
            return
        try:
            from ctrader_open_api.messages import OpenApiMessages_pb2 as msgs

            req = msgs.ProtoOASubscribeSpotsReq()
            req.ctidTraderAccountId = self._creds.get("account_id", 0)
            req.symbolId.extend(sorted(new_ids))
            self._send(req, 2127)
            self._subscribed_spot_ids |= new_ids
            logger.info(f"cTrader subscribed to spot quotes for {sorted(new_ids)}")
        except Exception as e:
            logger.error(f"cTrader spot subscription failed: {e}")

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

            elif ptype == 2115:  # ProtoOASymbolsListRes (2116 is SymbolByIdReq)
                res = msgs.ProtoOASymbolsListRes()
                res.ParseFromString(msg.payload)
                sym_map = {}
                for sym in res.symbol:
                    name = CTraderService.normalize_catalog_name(getattr(sym, "symbolName", "") or "")
                    if name:
                        sym_map[name] = sym.symbolId
                self._service._symbol_ids = CTraderService.merge_symbol_catalog(
                    dict(self._service.DEFAULT_SYMBOL_IDS),
                    sym_map,
                )
                self._service.relabel_cached_symbols()
                logger.info(
                    "cTrader dynamic symbol catalog updated: %s instruments (NZDJPY id=%s)",
                    len(self._service._symbol_ids),
                    self._service._symbol_ids.get("NZDJPY"),
                )
                # Reconcile often arrives before this large catalog. Refresh names
                # after IDs are unique so NZDJPY is not left labeled XAUUSD.
                req_rec = msgs.ProtoOAReconcileReq()
                req_rec.ctidTraderAccountId = self._creds.get("account_id", 0)
                self._send(req_rec, 2124)

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
                    sym_name = self._service.symbol_name_for_id(p.tradeData.symbolId)
                    volume_lots = self._service.protocol_volume_to_lots(p.tradeData.volume)
                    # Surface the protection the broker actually holds. A stop
                    # inside the broker's stop level is dropped silently, so
                    # "requested" and "attached" are not the same thing.
                    positions.append({
                        "symbol": sym_name,
                        "symbol_id": p.tradeData.symbolId,
                        "side": side,
                        "quantity": volume_lots,
                        "entry_price": self._service.normalize_position_price(
                            getattr(p, "price", None),
                            sym_name,
                        ),
                        "unrealized_pnl": 0.0,
                        "stop_loss": float(p.stopLoss) if getattr(p, "stopLoss", 0) else None,
                        "take_profit": float(p.takeProfit) if getattr(p, "takeProfit", 0) else None,
                        "position_id": str(p.positionId),
                        "broker": "ctrader",
                    })
                self._service._positions = positions
                logger.info(f"cTrader positions reconciled: {len(positions)} open")
                self._subscribe_spots({p["symbol_id"] for p in positions})

            elif ptype == 2126:  # ProtoOAExecutionEvent
                ev = msgs.ProtoOAExecutionEvent()
                ev.ParseFromString(msg.payload)
                logger.info(f"cTrader execution event received: type={ev.executionType}")
                if ev.executionType in (2, 3):  # ORDER_ACCEPTED / ORDER_FILLED
                    self._service._last_order_error = None
                    self._service._order_ack_event.set()
                # Refresh positions on fill/close
                req_rec = msgs.ProtoOAReconcileReq()
                req_rec.ctidTraderAccountId = self._creds.get("account_id", 0)
                self._send(req_rec, 2124)

            elif ptype == 2132:  # ProtoOAOrderErrorEvent
                err_ev = msgs.ProtoOAOrderErrorEvent()
                err_ev.ParseFromString(msg.payload)
                desc = f"{err_ev.errorCode} — {err_ev.description}"
                logger.error(f"cTrader Order Error: {desc}")
                self._service._last_order_error = desc
                self._service._order_ack_event.set()

            elif ptype == 2131:  # ProtoOASpotEvent (2128 is SubscribeSpotsRes)
                spot_ev = msgs.ProtoOASpotEvent()
                spot_ev.ParseFromString(msg.payload)
                sym_id = spot_ev.symbolId
                sym_name = self._service.symbol_name_for_id(sym_id)
                bid = self._service.decode_spotware_price(spot_ev.bid, sym_name) if spot_ev.bid else None
                ask = self._service.decode_spotware_price(spot_ev.ask, sym_name) if spot_ev.ask else None
                self._service._last_spots[sym_name] = {
                    "symbol": sym_name,
                    "symbol_id": sym_id,
                    "bid": bid,
                    "ask": ask,
                    "timestamp": int(time.time() * 1000)
                }

            elif ptype == 2138:  # ProtoOAGetTrendbarsRes
                tb_res = msgs.ProtoOAGetTrendbarsRes()
                tb_res.ParseFromString(msg.payload)
                sym_id = tb_res.symbolId
                sym_name = self._service.symbol_name_for_id(sym_id)
                digits = self._service.digits_for(sym_name)
                scale = float(self._service.SPOTWARE_PRICE_SCALE)
                bars = []
                for tb in tb_res.trendbar:
                    low = tb.low / scale
                    open_p = (tb.low + (tb.deltaOpen or 0)) / scale
                    high = (tb.low + (tb.deltaHigh or 0)) / scale
                    close_p = (tb.low + (tb.deltaClose or 0)) / scale
                    # Timestamp in milliseconds
                    ts = (tb.utcTimestampInMinutes or 0) * 60 * 1000
                    bars.append({
                        "timestamp": ts,
                        "time": int(ts / 1000),
                        "open": round(open_p, digits),
                        "high": round(high, digits),
                        "low": round(low, digits),
                        "close": round(close_p, digits),
                        "volume": float(tb.volume or 1.0)
                    })
                self._service._trendbar_cache[f"{sym_name}_{tb_res.period}"] = bars
                logger.info(f"cTrader received {len(bars)} trendbars for {sym_name} (period={tb_res.period})")
                waiter = self._service._trendbar_events.get(f"{sym_name}_{tb_res.period}")
                if waiter is not None:
                    waiter.set()

            elif ptype == 2146:  # ProtoOAGetTickDataRes
                tick_res = msgs.ProtoOAGetTickDataRes()
                tick_res.ParseFromString(msg.payload)
                ticks = []
                for t in tick_res.tickData:
                    ticks.append({
                        "timestamp": t.timestamp,
                        "tick": t.tick / 100000.0
                    })
                logger.info(f"cTrader received {len(ticks)} ticks")

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
        "NZDJPY=X": "NZDJPY", "USDNOK=X": "USDNOK", "CHFJPY=X": "CHFJPY",
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
        "CHFJPY": Decimal("0.01"),   "NZDJPY": Decimal("0.01"),
        "USDNOK": Decimal("0.0001"),
        "XAUUSD": Decimal("0.01"),   "XAGUSD": Decimal("0.001"),
        "BTCUSD": Decimal("1.0"),    "ETHUSD": Decimal("0.1"),    "SOLUSD": Decimal("0.01"),
        "BNBUSD": Decimal("0.01"),   "XRPUSD": Decimal("0.0001"),
    }

    DIGITS = {
        "EURUSD": 5, "GBPUSD": 5, "AUDUSD": 5, "NZDUSD": 5, "USDCAD": 5, "USDCHF": 5,
        "EURGBP": 5, "USDJPY": 3, "EURJPY": 3, "GBPJPY": 3, "AUDJPY": 3, "CADJPY": 3,
        "CHFJPY": 3, "NZDJPY": 3, "USDNOK": 5,
        "XAUUSD": 2, "XAGUSD": 3, "BTCUSD": 2, "ETHUSD": 2, "SOLUSD": 2, "BNBUSD": 2, "XRPUSD": 4
    }

    PIP_POSITION = {
        "EURUSD": 4, "GBPUSD": 4, "AUDUSD": 4, "NZDUSD": 4, "USDCAD": 4, "USDCHF": 4,
        "EURGBP": 4, "USDJPY": 2, "EURJPY": 2, "GBPJPY": 2, "AUDJPY": 2, "CADJPY": 2,
        "CHFJPY": 2, "NZDJPY": 2, "USDNOK": 4,
        "XAUUSD": 2, "XAGUSD": 2, "BTCUSD": 0, "ETHUSD": 1, "SOLUSD": 2, "BNBUSD": 2, "XRPUSD": 4
    }

    BASE_PRICES = {
        "EURUSD": 1.0850, "GBPUSD": 1.2950, "USDJPY": 154.20, "AUDUSD": 0.6550,
        "USDCAD": 1.3650, "USDCHF": 0.8850, "NZDUSD": 0.5950, "EURGBP": 0.8375,
        "EURJPY": 167.30, "GBPJPY": 199.70, "NZDJPY": 94.50, "CHFJPY": 180.00,
        "USDNOK": 9.35, "XAUUSD": 2500.00, "XAGUSD": 29.50,
        "BTCUSD": 64500.00, "ETHUSD": 2750.00, "SOLUSD": 155.00, "BNBUSD": 570.00, "XRPUSD": 0.5850
    }

    PERIOD_MAP = {
        "1T": 1, "10S": 1, "30S": 1,
        "1M": 1, "M1": 1,
        "2M": 2, "M2": 2,
        "3M": 3, "M3": 3,
        "4M": 4, "M4": 4,
        "5M": 5, "M5": 5,
        "15M": 6, "M15": 6,
        "30M": 7, "M30": 7,
        "1H": 8, "H1": 8,
        "4H": 9, "H4": 9,
        "12H": 10, "H12": 10,
        "1D": 11, "D1": 11,
        "1W": 12, "W1": 12,
        "1MO": 13, "MN1": 13
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
        self._trendbar_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._last_spots: Dict[str, Dict[str, Any]] = {}
        self._tick_cache: Dict[str, List[Dict[str, Any]]] = {}

        self.balance: float = 0.0
        self.equity: float = 0.0
        self.margin: float = 0.0
        self._leverage: float = 500.0
        self._broker_name: str = "cTrader"
        self._trader_login: int = 0
        self._reconnect_pending = False
        self._reconnect_lock = threading.Lock()
        self._intentional_disconnect = False
        self._last_order_error: Optional[str] = None
        self._order_ack_event = threading.Event()
        self._trendbar_last_req: Dict[str, float] = {}
        self._trendbar_events: Dict[str, threading.Event] = {}

    # Spotware volume is in cents of a unit (0.01 of 1 unit of the base asset).
    # 1.00 standard FX lot = 100,000 units = 10,000,000 protocol volume.
    VOLUME_CENTS_PER_LOT = 10_000_000
    MIN_VOLUME_CENTS = 100_000  # 0.01 lot
    STEP_VOLUME_CENTS = 1_000
    CONTRACT_UNITS_PER_LOT = 100_000
    # Bid/ask/trendbar integers are always 1/100000 of a price unit (not 10^digits).
    SPOTWARE_PRICE_SCALE = 100_000
    MAX_FX_STOP_PIPS = 120
    # Broker stop level. Protection closer than this is silently discarded.
    MIN_FX_STOP_PIPS = float(os.getenv("CTRADER_MIN_STOP_PIPS", "10"))

    @classmethod
    def lots_to_protocol_volume(cls, lots: float) -> int:
        raw = int(round(float(lots) * cls.VOLUME_CENTS_PER_LOT))
        raw = max(cls.MIN_VOLUME_CENTS, raw)
        step = cls.STEP_VOLUME_CENTS
        return max(cls.MIN_VOLUME_CENTS, int(round(raw / step) * step))

    @classmethod
    def protocol_volume_to_lots(cls, raw: int) -> float:
        return float(raw) / cls.VOLUME_CENTS_PER_LOT

    @classmethod
    def relative_stop_units(cls, current_price: float, target_price: float, digits: int) -> int:
        """Convert a price distance to Spotware relativeStopLoss/TP (1/100000 of price)."""
        diff = abs(Decimal(str(current_price)) - Decimal(str(target_price)))
        units = int((diff * Decimal(cls.SPOTWARE_PRICE_SCALE)).to_integral_value())
        step = 10 ** max(0, 5 - int(digits))
        units = (units // step) * step
        return units

    @classmethod
    def normalize_symbol_name(cls, sym: str) -> str:
        if not sym:
            return ""
        mapped = cls.SYMBOL_MAP.get(sym)
        if mapped:
            return mapped
        return cls.normalize_catalog_name(sym.replace("-USD", "USD").replace("=X", ""))

    @staticmethod
    def normalize_catalog_name(name: str) -> str:
        """IC Markets light-symbol names may be 'NZD/JPY' rather than 'NZDJPY'."""
        return (name or "").replace("/", "").replace("-", "").replace(" ", "").upper()

    @classmethod
    def digits_for(cls, symbol: str) -> int:
        """Tick decimals for rounding. Not used to decode Spotware integers."""
        sym = cls.normalize_symbol_name(symbol)
        if sym in cls.DIGITS:
            return cls.DIGITS[sym]
        if sym.endswith("JPY"):
            return 3
        if sym.startswith("XAU"):
            return 2
        if sym.startswith("XAG"):
            return 3
        return 5

    @classmethod
    def pip_size_for(cls, symbol: str) -> Decimal:
        """Pip size in price units. JPY quotes are 0.01, not the 0.0001 FX default."""
        sym = cls.normalize_symbol_name(symbol)
        if sym in cls.PIP_SIZE:
            return cls.PIP_SIZE[sym]
        if sym.endswith("JPY"):
            return Decimal("0.01")
        if sym.startswith("XAU"):
            return Decimal("0.01")
        if sym.startswith("XAG"):
            return Decimal("0.001")
        return Decimal("0.0001")

    @classmethod
    def pip_position_for(cls, symbol: str) -> int:
        sym = cls.normalize_symbol_name(symbol)
        if sym in cls.PIP_POSITION:
            return cls.PIP_POSITION[sym]
        return max(0, cls.digits_for(sym) - 1)

    @classmethod
    def normalize_position_price(cls, raw: Optional[float], symbol: str = "") -> float:
        """Decode reconcile position prices whether Spotware sent ints or floats."""
        if raw is None:
            return 0.0
        val = float(raw)
        if val <= 0:
            return 0.0
        # Spotware sends prices as integers scaled by 1/100000. Already-decoded
        # floats for FX/metals sit in a narrow band (≈0.5–250).
        if val > 1000:
            decoded = cls.decode_spotware_price(val, symbol)
            return float(decoded or 0.0)
        return val

    @classmethod
    def decode_spotware_price(cls, raw: Optional[float], symbol: str = "") -> Optional[float]:
        """Convert a Spotware integer price (always 1/100000) to a float."""
        if raw is None:
            return None
        price = float(raw) / float(cls.SPOTWARE_PRICE_SCALE)
        if symbol:
            return round(price, cls.digits_for(symbol))
        return price

    @classmethod
    def max_protective_distance(cls, symbol: str, entry: float) -> float:
        pip = float(cls.pip_size_for(symbol))
        entry_abs = abs(float(entry) or 0.0)
        return max(pip * cls.MAX_FX_STOP_PIPS, entry_abs * 0.012)

    @classmethod
    def min_protective_distance(cls, symbol: str, entry: float) -> float:
        """Closest a stop may sit and still be accepted by the broker.

        IC Markets rejects protection inside its stop level, and does so
        silently: the order fills and the stop is simply absent. M5 ATR on a
        quiet pair produced 2 pip stops, so live positions ran with no stop
        at all while the wider take-profit survived.
        """
        return float(cls.pip_size_for(symbol)) * cls.MIN_FX_STOP_PIPS

    @classmethod
    def clamp_protective_prices(
        cls,
        symbol: str,
        entry: float,
        stop_loss: Optional[float],
        take_profit: Optional[float],
        direction: Optional[str] = None,
    ) -> tuple[Optional[float], Optional[float]]:
        """Force SL/TP onto the correct side of entry and into a workable band.

        Levels too far out (thousands of pips on JPY) are capped; levels too
        close are pushed out to the broker minimum so they are not dropped.
        """
        if not entry:
            return stop_loss, take_profit
        max_dist = cls.max_protective_distance(symbol, entry)
        min_dist = min(cls.min_protective_distance(symbol, entry), max_dist)
        digits = cls.digits_for(symbol)
        side = (direction or "").upper()
        entry_f = float(entry)

        def _clamp(target: Optional[float], want_above: Optional[bool]) -> Optional[float]:
            if target is None:
                return None
            dist = abs(float(target) - float(entry))
            if want_above is None:
                sign = 1.0 if float(target) > entry_f else -1.0
            else:
                sign = 1.0 if want_above else -1.0
                if (float(target) > entry_f) is not want_above:
                    logger.warning(
                        "%s protective level %s is on the wrong side of entry %s; mirroring",
                        symbol, target, entry,
                    )
            bounded = min(max(dist, min_dist), max_dist)
            if bounded != dist:
                logger.warning(
                    "Adjusting %s protective distance %s -> %s (entry=%s min=%s max=%s)",
                    symbol, dist, bounded, entry, min_dist, max_dist,
                )
            return round(entry_f + sign * bounded, digits)

        if side in ("BUY", "LONG"):
            return _clamp(stop_loss, False), _clamp(take_profit, True)
        if side in ("SELL", "SHORT"):
            return _clamp(stop_loss, True), _clamp(take_profit, False)
        return _clamp(stop_loss, None), _clamp(take_profit, None)

    @staticmethod
    def merge_symbol_catalog(
        defaults: Dict[str, int],
        catalog: Dict[str, int],
    ) -> Dict[str, int]:
        """Broker catalog wins. Drop default names whose IDs now belong to another symbol.

        IC Markets NZDJPY can reuse id 21 (our default XAUUSD). Keeping both names
        for the same id made reconcile report NZDJPY 94.54 as XAUUSD.
        """
        merged = dict(catalog)
        used_ids = set(merged.values())
        for name, sid in defaults.items():
            if name in merged:
                continue
            if sid in used_ids:
                continue
            merged[name] = sid
        return merged

    def relabel_cached_symbols(self) -> None:
        """Re-apply catalog names after merge (reconcile can beat ProtoOASymbolsListRes)."""
        for pos in self._positions:
            sid = pos.get("symbol_id")
            if sid is None:
                continue
            pos["symbol"] = self.symbol_name_for_id(sid)
        relabeled_spots: Dict[str, Dict[str, Any]] = {}
        for spot in self._last_spots.values():
            sid = spot.get("symbol_id")
            if sid is None:
                continue
            name = self.symbol_name_for_id(sid)
            spot["symbol"] = name
            relabeled_spots[name] = spot
        if relabeled_spots:
            self._last_spots = relabeled_spots

    def symbol_name_for_id(self, symbol_id: int) -> str:
        """Resolve broker symbolId → name. Catalog-first merge keeps IDs unique."""
        try:
            sid = int(symbol_id)
        except (TypeError, ValueError):
            return str(symbol_id)
        for name, mapped in self._symbol_ids.items():
            if mapped == sid:
                return name
        return str(sid)

    @property
    def is_connected(self) -> bool:
        return self._connected and self._authenticated

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    def has_credentials(self) -> bool:
        """True when OAuth tokens or env vars are present for a live session."""
        tokens = token_store.get_tokens()
        client_id = tokens.get("client_id") or os.getenv("CTRADER_CLIENT_ID", "")
        access_token = (
            token_store.refresh_if_needed()
            or tokens.get("access_token")
            or os.getenv("CTRADER_ACCESS_TOKEN", "")
        )
        return bool(client_id and access_token)

    def ensure_connected(self) -> bool:
        """Connect to cTrader when credentials exist.

        Without this, backend restarts leave the service in paper mode and
        n8n execute-candidate calls silently return status=simulated.
        """
        if self.is_connected and not self._dry_run:
            return True
        if not self.has_credentials():
            return False
        self._intentional_disconnect = False
        return self.connect()

    def _normalize_symbol(self, sym: str) -> str:
        """Map generic/yfinance symbols to cTrader symbol string."""
        return self.normalize_symbol_name(sym)

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
        paper_mode = (
            os.getenv("CTRADER_PAPER_MODE", os.getenv("CTRADE_PAPER_MODE", "true")).lower() == "true"
        )
        live_confirm = os.getenv("CTRADER_LIVE_CONFIRM", os.getenv("CTRADE_LIVE_CONFIRM", "")).strip()
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

    def ensure_spot_quotes(self, symbols: List[str]) -> None:
        """Subscribe to spot streams for symbols that need a live mark."""
        if self._dry_run or not self.is_connected or self._protocol is None:
            return
        wanted: set[int] = set()
        for raw_sym in symbols:
            sym = self._normalize_symbol(raw_sym)
            sid = self._symbol_ids.get(sym)
            if sid:
                wanted.add(int(sid))
        if wanted:
            self._protocol._subscribe_spots(wanted)

    def get_mark_price(self, symbol: str, side: Optional[str] = None) -> Optional[float]:
        """Last streamed price for a symbol.

        A position is closed against the opposite side of the book, so a BUY
        exits on the bid and a SELL exits on the ask. Without a side, return the
        mid. Returns None when no spot has been streamed yet, so callers can
        record "unknown" instead of inventing a price.
        """
        spot = self._last_spots.get(self._normalize_symbol(symbol)) or self._last_spots.get(
            self.normalize_symbol_name(symbol)
        )
        if not spot:
            return None
        bid = spot.get("bid")
        ask = spot.get("ask")
        wanted = (side or "").upper()
        if wanted in ("BUY", "LONG"):
            price = bid or ask
        elif wanted in ("SELL", "SHORT"):
            price = ask or bid
        elif bid and ask:
            price = (float(bid) + float(ask)) / 2
        else:
            price = bid or ask
        return float(price) if price else None

    def quote_to_usd_rate(self, symbol: str, mark: float) -> Optional[float]:
        """Rate that converts this pair's quote currency into USD.

        Raw P&L lands in the quote currency, so a USDJPY move reads in yen
        while EURUSD reads in dollars. Showing both in one column made a
        -490 JPY position look ~150x worse than a -3 USD one.
        """
        sym = self.normalize_symbol_name(symbol)
        if len(sym) != 6:
            return None
        base, quote = sym[:3], sym[3:]
        if quote == "USD":
            return 1.0
        if base == "USD":
            # Quote per USD is the pair itself (USDJPY -> 159.7 JPY per USD).
            return 1.0 / mark if mark else None
        # Cross pair: price the quote currency against USD directly.
        direct = self.get_mark_price(f"{quote}USD")
        if direct:
            return float(direct)
        inverse = self.get_mark_price(f"USD{quote}")
        if inverse:
            return 1.0 / float(inverse)
        return None

    def get_positions(self, raise_on_error: bool = False) -> List[Dict[str, Any]]:
        """Return all open positions across cTrader, marked to the latest spot.

        `raise_on_error` exists for parity with the Binance broker so callers
        can treat the two interchangeably.
        """
        positions = []
        for pos in self._positions:
            enriched = dict(pos)
            symbol = str(enriched.get("symbol") or "")
            mark = self.get_mark_price(symbol, enriched.get("side"))
            entry = float(enriched.get("entry_price") or 0)
            lots = float(enriched.get("quantity") or 0)
            if mark and entry and lots:
                units = lots * self.CONTRACT_UNITS_PER_LOT
                direction = 1 if str(enriched.get("side", "")).upper() == "BUY" else -1
                quote_pnl = (mark - entry) * units * direction
                enriched["current_price"] = mark
                rate = self.quote_to_usd_rate(symbol, mark)
                if rate:
                    enriched["unrealized_pnl"] = round(quote_pnl * rate, 2)
                    enriched["pnl_currency"] = "USD"
                else:
                    enriched["unrealized_pnl"] = round(quote_pnl, 2)
                    enriched["pnl_currency"] = symbol[3:] if len(symbol) == 6 else ""
            else:
                # Always expose a mark for the dashboard — entry until spot arrives.
                enriched["current_price"] = mark if mark and mark > 0 else entry
            positions.append(enriched)
        return positions

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

        # A close is a different protocol message. This method only ever sent
        # ProtoOANewOrderReq, so a caller asking to close silently doubled the
        # position instead of flattening it.
        if str(action).lower() == "close":
            held = next(
                (p for p in self._positions if str(p.get("symbol")) == ct_symbol),
                None,
            )
            if not held:
                return {
                    "status": "already_flat",
                    "broker": "ctrader",
                    "symbol": ct_symbol,
                }
            return self.close_position(
                position_id=held.get("position_id"),
                symbol=ct_symbol,
                volume=lots if quantity is not None or volume is not None else held.get("quantity"),
            )
        current_price = price or kwargs.get("current_price")
        stop_loss_price = stop_loss or kwargs.get("stop_loss_price")
        take_profit_price = take_profit or kwargs.get("take_profit_price")
        if current_price:
            stop_loss_price, take_profit_price = self.clamp_protective_prices(
                ct_symbol, float(current_price), stop_loss_price, take_profit_price,
                direction=side,
            )

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

            # Spotware volume is cents of a unit: 0.01 lot EURUSD = 100_000
            raw_volume = self.lots_to_protocol_volume(lots)
            digits = self.digits_for(ct_symbol)

            order_req = msgs.ProtoOANewOrderReq()
            order_req.ctidTraderAccountId = self._account_id or 0
            order_req.symbolId = symbol_id
            order_req.orderType = 1  # MARKET
            order_req.tradeSide = 1 if side == "BUY" else 2
            order_req.volume = raw_volume

            # relativeStopLoss/TP are 1/100000 of a price unit, aligned to symbol digits
            if current_price and stop_loss_price:
                sl_units = self.relative_stop_units(float(current_price), float(stop_loss_price), digits)
                if sl_units > 0:
                    order_req.relativeStopLoss = sl_units
                else:
                    # Sending the entry anyway would open an unprotected
                    # position, which is how live forex ended up running naked.
                    logger.critical(
                        f"Refusing {ct_symbol} entry: stop loss {stop_loss_price} "
                        f"rounds to zero distance from {current_price}"
                    )
                    return {
                        "status": "error",
                        "error": "stop loss too close to entry to encode",
                        "symbol": ct_symbol,
                    }

            if current_price and take_profit_price:
                tp_units = self.relative_stop_units(float(current_price), float(take_profit_price), digits)
                if tp_units > 0:
                    order_req.relativeTakeProfit = tp_units

            event = threading.Event()
            result = {"status": "pending"}
            self._last_order_error = None
            self._order_ack_event.clear()

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
            if result.get("status") == "sent":
                self._order_ack_event.wait(timeout=3.0)
                if self._last_order_error:
                    result["status"] = "error"
                    result["error"] = self._last_order_error
                    logger.error(f"cTrader order rejected: {self._last_order_error}")
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

    def close_position(
        self,
        position_id: str | int,
        symbol: Optional[str] = None,
        volume: Optional[float] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Close open position by cTrader position ID. Volume is required on the live protocol."""
        lots = volume if volume is not None else kwargs.get("quantity")
        matched = next(
            (p for p in self._positions if str(p.get("position_id")) == str(position_id)),
            None,
        )
        if lots is None and matched is not None:
            lots = matched.get("quantity")
        if not symbol and matched is not None:
            symbol = matched.get("symbol")

        if self._dry_run or not self.is_connected or self._protocol is None:
            self._positions = [p for p in self._positions if str(p.get("position_id")) != str(position_id)]
            return {
                "status": "closed",
                "position_id": position_id,
                "symbol": symbol,
                "quantity": lots,
                "broker": "ctrader:paper",
            }

        if lots is None:
            return {
                "status": "error",
                "error": "volume is required to close a live cTrader position",
                "position_id": position_id,
            }

        try:
            from ctrader_open_api.messages import OpenApiMessages_pb2 as msgs
            from twisted.internet import reactor

            req = msgs.ProtoOAClosePositionReq()
            req.ctidTraderAccountId = self._account_id or 0
            req.positionId = int(position_id) if str(position_id).isdigit() else 0
            req.volume = self.lots_to_protocol_volume(float(lots))

            reactor.callFromThread(lambda: self._protocol._send(req, 2111))
            return {
                "status": "sent",
                "position_id": position_id,
                "symbol": symbol,
                "quantity": lots,
                "broker": "ctrader:live",
            }
        except Exception as e:
            return {"status": "error", "error": str(e), "position_id": position_id}

    def get_symbol_specification(self, symbol: str) -> Dict[str, Any]:
        """Returns standard specification parameters for a symbol (digits, pip position, lot size, etc.)."""
        ct_symbol = self._normalize_symbol(symbol)
        digits = self.digits_for(ct_symbol)
        pip_pos = self.pip_position_for(ct_symbol)
        pip_size = float(self.pip_size_for(ct_symbol))
        tick_size = 1.0 / (10 ** digits)
        base_price = self.BASE_PRICES.get(ct_symbol)
        if base_price is None:
            base_price = 154.20 if ct_symbol.endswith("JPY") else 1.0

        # Determine base and quote assets
        if len(ct_symbol) == 6 and not any(ct_symbol.startswith(k) for k in ("BTC", "ETH", "SOL", "BNB", "XRP", "XAU", "XAG")):
            base_asset = ct_symbol[:3]
            quote_asset = ct_symbol[3:]
        elif any(ct_symbol.startswith(k) for k in ("BTC", "ETH", "SOL", "BNB", "XRP")):
            base_asset = ct_symbol.replace("USD", "")
            quote_asset = "USD"
        elif ct_symbol in ("XAUUSD", "XAGUSD"):
            base_asset = ct_symbol[:3]
            quote_asset = "USD"
        else:
            base_asset = ct_symbol
            quote_asset = "USD"

        return {
            "symbol": ct_symbol,
            "raw_symbol": symbol,
            "symbol_id": self._symbol_ids.get(ct_symbol, 0),
            "digits": digits,
            "pip_position": pip_pos,
            "pip_size": pip_size,
            "tick_size": tick_size,
            "lot_size": 100_000,
            "min_volume": 100_000,
            "max_volume": 100_000_000,
            "step_volume": 1_000,
            "base_asset": base_asset,
            "quote_asset": quote_asset,
            "base_price": base_price,
        }

    def calculate_pip_margin(
        self,
        symbol: str,
        lots: float,
        price: Optional[float] = None,
        leverage: float = 100.0,
        deposit_asset: str = "USD"
    ) -> Dict[str, Any]:
        """
        Calculates pip value, tick value, required margin, and lot volume conversion
        following OpenAPI.Net SymbolExtensions specifications.
        """
        spec = self.get_symbol_specification(symbol)
        curr_price = float(price) if price else float(spec["base_price"])
        pip_size = float(spec["pip_size"])
        tick_size = float(spec["tick_size"])
        lot_size = float(spec["lot_size"])
        volume_units = max(1000, int(round(lots * lot_size)))

        # Pip Value calculation based on quote asset vs deposit asset
        if spec["quote_asset"] == deposit_asset:
            pip_value = pip_size * volume_units
            tick_value = tick_size * volume_units
        elif spec["base_asset"] == deposit_asset and curr_price > 0:
            pip_value = (pip_size / curr_price) * volume_units
            tick_value = (tick_size / curr_price) * volume_units
        else:
            pip_value = pip_size * volume_units
            tick_value = tick_size * volume_units

        # Required Margin = (volume_units * curr_price) / leverage
        notional_value = volume_units * (curr_price if spec["base_asset"] != deposit_asset else 1.0)
        required_margin = notional_value / max(1.0, float(leverage))

        return {
            "symbol": spec["symbol"],
            "lots": float(lots),
            "volume_units": volume_units,
            "price": curr_price,
            "leverage": float(leverage),
            "pip_size": pip_size,
            "tick_size": tick_size,
            "pip_value": round(pip_value, 4),
            "tick_value": round(tick_value, 4),
            "notional_value": round(notional_value, 2),
            "required_margin": round(required_margin, 2),
            "deposit_asset": deposit_asset,
        }

    def _generate_synthetic_trendbars(
        self,
        ct_symbol: str,
        period: str,
        count: int,
    ) -> List[Dict[str, Any]]:
        """Paper-mode OHLCV generator. Never used on a live connected session."""
        import math
        import random

        digits = self.digits_for(ct_symbol)
        pip_size = float(self.pip_size_for(ct_symbol))
        base_p = self.BASE_PRICES.get(ct_symbol)
        if base_p is None:
            if ct_symbol.endswith("JPY"):
                base_p = 154.20 if ct_symbol.startswith("USD") else 94.50
            else:
                base_p = 1.0850

        step_seconds = 300  # default 5m
        p_up = period.upper()
        if "1M" in p_up or p_up == "M1":
            step_seconds = 60
        elif "15M" in p_up or p_up == "M15":
            step_seconds = 900
        elif "30M" in p_up or p_up == "M30":
            step_seconds = 1800
        elif "1H" in p_up or p_up == "H1":
            step_seconds = 3600
        elif "4H" in p_up or p_up == "H4":
            step_seconds = 14400
        elif "1D" in p_up or p_up == "D1":
            step_seconds = 86400
        elif "1W" in p_up or p_up == "W1":
            step_seconds = 604800

        now_sec = int(time.time())
        aligned_end = (now_sec // step_seconds) * step_seconds
        rnd = random.Random(hash(ct_symbol) + step_seconds)

        bars = []
        p = base_p
        volatility = pip_size * 8.0

        for i in range(count, 0, -1):
            bar_time = aligned_end - (i * step_seconds)
            drift = math.sin(i / 15.0) * (pip_size * 4.0)
            noise = (rnd.random() - 0.49) * volatility
            open_price = p
            close_price = max(pip_size * 10, open_price + drift + noise)
            high_price = max(open_price, close_price) + rnd.random() * (volatility * 0.6)
            low_price = min(open_price, close_price) - rnd.random() * (volatility * 0.6)
            volume = round(rnd.uniform(50, 500), 2)

            p = close_price
            bars.append({
                "timestamp": bar_time * 1000,
                "time": bar_time,
                "open": round(open_price, digits),
                "high": round(high_price, digits),
                "low": round(low_price, digits),
                "close": round(close_price, digits),
                "volume": volume,
                "synthetic": True,
            })

        return bars

    def get_trendbars(
        self,
        symbol: str,
        period: str = "M5",
        from_ts: Optional[int] = None,
        to_ts: Optional[int] = None,
        count: int = 120
    ) -> List[Dict[str, Any]]:
        """
        Get OHLCV trendbars for a symbol and timeframe period.

        Live sessions use ProtoOAGetTrendbarsReq and return cached broker data only.
        When live and no real bars are available (timeout, missing symbol id, empty
        cache), returns an empty list so callers skip the symbol instead of trading
        on fabricated prices.

        Paper/dry-run mode may synthesize bars; those rows include ``synthetic: true``.
        """
        ct_symbol = self._normalize_symbol(symbol)
        period_enum = self.PERIOD_MAP.get(period.upper(), 5)
        cache_key = f"{ct_symbol}_{period_enum}"
        live_mode = not self._dry_run and self.is_connected and self._protocol is not None

        if live_mode:
            last = self._trendbar_last_req.get(cache_key, 0)
            if time.time() - last < 45:
                cached = self._trendbar_cache.get(cache_key) or []
                if cached:
                    return cached[-count:]
            else:
                try:
                    from ctrader_open_api.messages import OpenApiMessages_pb2 as msgs
                    from twisted.internet import reactor

                    sym_id = self._symbol_ids.get(ct_symbol)
                    if not sym_id:
                        logger.warning(
                            "No cTrader symbol id for %s; skipping live trendbar request",
                            ct_symbol,
                        )
                    else:
                        now_ms = int(time.time() * 1000)
                        f_ts = from_ts or (now_ms - 7 * 86400 * 1000)
                        t_ts = to_ts or now_ms

                        req = msgs.ProtoOAGetTrendbarsReq()
                        req.ctidTraderAccountId = self._account_id or 0
                        req.symbolId = sym_id
                        req.period = period_enum
                        req.fromTimestamp = f_ts
                        req.toTimestamp = t_ts

                        # Register the waiter before sending so a fast response cannot
                        # arrive (and be missed) before we start waiting.
                        waiter = threading.Event()
                        self._trendbar_events[cache_key] = waiter
                        reactor.callFromThread(lambda: self._protocol._send(req, 2137))
                        self._trendbar_last_req[cache_key] = time.time()
                        waiter.wait(timeout=2.0)
                except Exception as e:
                    logger.warning(f"Live trendbar request failed: {e}")

            cached = self._trendbar_cache.get(cache_key) or []
            if cached:
                return cached[-count:]

            logger.warning(
                "Live trendbar fetch returned no data for %s %s; refusing synthetic fallback",
                ct_symbol,
                period,
            )
            return []

        cached = self._trendbar_cache.get(cache_key) or []
        if cached:
            return cached[-count:]

        return self._generate_synthetic_trendbars(ct_symbol, period, count)

    def get_tick_data(
        self,
        symbol: str,
        quote_type: str = "BID",
        from_ts: Optional[int] = None,
        to_ts: Optional[int] = None,
        hours: int = 4
    ) -> List[Dict[str, Any]]:
        """Get historical tick stream for a symbol."""
        import random
        ct_symbol = self._normalize_symbol(symbol)
        digits = self.digits_for(ct_symbol)
        base_p = self.BASE_PRICES.get(ct_symbol, 1.0850)
        pip_size = float(self.pip_size_for(ct_symbol))

        now_ms = int(time.time() * 1000)
        start_ms = now_ms - (hours * 3600 * 1000)

        rnd = random.Random(hash(ct_symbol) + 42)
        ticks = []
        curr = base_p

        for i in range(150):
            t_offset = int((i / 150.0) * (hours * 3600 * 1000))
            delta = (rnd.random() - 0.495) * (pip_size * 0.8)
            curr = round(curr + delta, digits)
            ticks.append({
                "timestamp": start_ms + t_offset,
                "symbol": ct_symbol,
                "type": quote_type.upper(),
                "price": curr,
                "volume": rnd.randint(1000, 50000)
            })

        return ticks

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
