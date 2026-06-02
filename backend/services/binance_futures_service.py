"""
Binance USDT-M Futures Service
Handles live order execution on Binance Futures (perpetuals).
Supports LONG/SHORT positions with configurable leverage (default 10x).

Interface compatible with ctrader_service for drop-in integration with trading_loop.py.
"""

import logging
import os
from pathlib import Path
from typing import Dict, Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / '.env', override=True)

logger = logging.getLogger(__name__)

# ── Symbol Mapping ────────────────────────────────────────────────────────────
SYMBOL_MAP = {
    # Legacy yfinance-style → Binance Futures
    'BTC-USD': 'BTCUSDT',
    'ETH-USD': 'ETHUSDT',
    'SOL-USD': 'SOLUSDT',
    'BNB-USD': 'BNBUSDT',
    'XRP-USD': 'XRPUSDT',
    'ADA-USD': 'ADAUSDT',
    'DOGE-USD': 'DOGEUSDT',
    'AVAX-USD': 'AVAXUSDT',
    'DOT-USD': 'DOTUSDT',
    'LINK-USD': 'LINKUSDT',
    'MATIC-USD': 'POLUSDT',
    'LTC-USD': 'LTCUSDT',
    'UNI-USD': 'UNIUSDT',
    'ATOM-USD': 'ATOMUSDT',
    'NEAR-USD': 'NEARUSDT',
    'OP-USD': 'OPUSDT',
    'ARB-USD': 'ARBUSDT',
    'APT-USD': 'APTUSDT',
    'INJ-USD': 'INJUSDT',
    'SUI-USD': 'SUIUSDT',
    # Native Binance format passthrough
    'BTCUSDT': 'BTCUSDT', 'ETHUSDT': 'ETHUSDT', 'SOLUSDT': 'SOLUSDT',
    'BNBUSDT': 'BNBUSDT', 'XRPUSDT': 'XRPUSDT', 'ADAUSDT': 'ADAUSDT',
    'DOGEUSDT': 'DOGEUSDT', 'AVAXUSDT': 'AVAXUSDT', 'DOTUSDT': 'DOTUSDT',
    'LINKUSDT': 'LINKUSDT', 'POLUSDT': 'POLUSDT', 'LTCUSDT': 'LTCUSDT',
    'UNIUSDT': 'UNIUSDT', 'ATOMUSDT': 'ATOMUSDT', 'NEARUSDT': 'NEARUSDT',
    'OPUSDT': 'OPUSDT', 'ARBUSDT': 'ARBUSDT', 'APTUSDT': 'APTUSDT',
    'INJUSDT': 'INJUSDT', 'SUIUSDT': 'SUIUSDT',
}

# Forex / stock symbols not tradeable on Binance Futures — skip gracefully
UNSUPPORTED_SYMBOLS = {'EURUSD=X', 'GBPUSD=X', 'USDJPY=X', 'EURJPY=X', 'EURGBP=X'}

# Minimum order quantity per symbol
MIN_QTY = {
    'BTCUSDT': 0.001, 'ETHUSDT': 0.001, 'SOLUSDT': 0.1,
    'BNBUSDT': 0.01,  'XRPUSDT': 1.0,   'ADAUSDT': 1.0,
    'DOGEUSDT': 1.0,  'AVAXUSDT': 1.0,  'DOTUSDT': 0.1,
    'LINKUSDT': 0.01, 'POLUSDT': 1.0, 'LTCUSDT': 0.001,
    'UNIUSDT': 0.1,   'ATOMUSDT': 0.01, 'NEARUSDT': 0.1,
    'OPUSDT': 0.1,    'ARBUSDT': 1.0,   'APTUSDT': 0.1,
    'INJUSDT': 0.1,   'SUIUSDT': 1.0,
}

# Precision (decimal places) for quantity
QTY_PRECISION = {
    'BTCUSDT': 3, 'ETHUSDT': 3, 'SOLUSDT': 1,
    'BNBUSDT': 2, 'XRPUSDT': 0, 'ADAUSDT': 0,
    'DOGEUSDT': 0, 'AVAXUSDT': 0, 'DOTUSDT': 1,
    'LINKUSDT': 2, 'POLUSDT': 0, 'LTCUSDT': 3,
    'UNIUSDT': 1, 'ATOMUSDT': 2, 'NEARUSDT': 1,
    'OPUSDT': 1, 'ARBUSDT': 0, 'APTUSDT': 1,
    'INJUSDT': 1,   'SUIUSDT': 0,
}

# Price precision per symbol (loaded from futures_exchange_info at runtime)
PRICE_PRECISION: Dict[str, int] = {}

# Price tick sizes per symbol (min increment — from /fapi/v1/exchangeInfo PRICE_FILTER)
# Used by _round_price() to avoid Binance -1111 precision errors on SL/TP orders.
TICK_SIZES = {
    'BTCUSDT':  0.1,       'ETHUSDT':  0.01,     'SOLUSDT':  0.001,
    'BNBUSDT':  0.01,      'XRPUSDT':  0.0001,   'ADAUSDT':  0.00001,
    'DOGEUSDT': 0.00001,   'AVAXUSDT': 0.001,    'DOTUSDT':  0.001,
    'LINKUSDT': 0.001,     'POLUSDT':  0.0001,   'LTCUSDT':  0.01,
    'UNIUSDT':  0.001,     'ATOMUSDT': 0.001,    'NEARUSDT': 0.0001,
    'OPUSDT':   0.0001,    'ARBUSDT':  0.0001,   'APTUSDT':  0.001,
    'INJUSDT':  0.001,     'SUIUSDT':  0.00001,
}


class BinanceFuturesService:
    """Binance USDT-M Futures broker — compatible with trading_loop.py interface."""

    def __init__(self):
        self.api_key    = os.getenv('BINANCE_API_KEY', '')
        self.api_secret = os.getenv('BINANCE_SECRET_KEY', '') or os.getenv('BINANCE_API_SECRET', '')
        self.testnet    = os.getenv('BINANCE_TESTNET', 'false').lower() == 'true'
        self.leverage   = int(os.getenv('BINANCE_LEVERAGE', '10'))
        self.margin_type = os.getenv('BINANCE_MARGIN_TYPE', 'ISOLATED')
        self.dry_run    = os.getenv('BINANCE_DRY_RUN', 'false').lower() == 'true'
        self._client    = None
        self._leverage_set: set = set()
        logger.info(
            f"BinanceFuturesService: testnet={self.testnet} "
            f"leverage={self.leverage}x margin={self.margin_type} dry_run={self.dry_run}"
        )

        # Load price precision per symbol from exchange info
        try:
            client = self._get_client()
            exchange_info = client.futures_exchange_info()
            for sym_info in exchange_info.get('symbols', []):
                PRICE_PRECISION[sym_info['symbol']] = sym_info.get('pricePrecision', 2)
            logger.info(f"Price precision loaded for {len(PRICE_PRECISION)} symbols")
        except Exception as e:
            logger.warning(f"Could not load price precision: {e} — defaulting to 2dp")

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_client(self):
        if self._client:
            return self._client
        from binance.client import Client
        self._client = Client(
            api_key=self.api_key,
            api_secret=self.api_secret,
            testnet=self.testnet,
        )
        logger.info(f"Binance Futures client connected (testnet={self.testnet})")
        return self._client

    def _to_futures_symbol(self, symbol: str) -> Optional[str]:
        """Convert internal symbol → Binance Futures format. Returns None if unsupported."""
        if symbol in UNSUPPORTED_SYMBOLS:
            return None
        if symbol in SYMBOL_MAP:
            return SYMBOL_MAP[symbol]
        # Generic: ETH-USD → ETHUSDT, BTC/USDT → BTCUSDT
        cleaned = symbol.replace('-USD', 'USDT').replace('=X', '').replace('/', '').upper()
        if not cleaned.endswith('USDT'):
            cleaned += 'USDT'
        return cleaned

    def _setup_symbol(self, client, sym: str) -> None:
        """Configure leverage + margin type once per symbol per session."""
        if sym in self._leverage_set:
            return
        # Margin type
        try:
            client.futures_change_margin_type(symbol=sym, marginType=self.margin_type)
            logger.info(f"[{sym}] Margin → {self.margin_type}")
        except Exception as e:
            err = str(e)
            if '-4046' in err or '-4175' in err:  # -4046=already set, -4175=credit status (BNFCR)
                pass  # safe to ignore
            else:
                logger.warning(f"[{sym}] margin_type: {e}")
        # Leverage
        try:
            client.futures_change_leverage(symbol=sym, leverage=self.leverage)
            logger.info(f"[{sym}] Leverage → {self.leverage}x")
        except Exception as e:
            logger.warning(f"[{sym}] leverage: {e}")
        self._leverage_set.add(sym)

    def _round_qty(self, sym: str, qty: float) -> float:
        decimals = QTY_PRECISION.get(sym, 3)
        min_q = MIN_QTY.get(sym, 0.001)
        if decimals == 0:
            rounded = float(int(qty))
        else:
            rounded = round(qty, decimals)
        # Never go below min_qty after rounding (e.g. BTC 0.000314 rounds to 0.0)
        if rounded < min_q:
            rounded = min_q
        return rounded

    def _round_price(self, symbol: str, price: float) -> float:
        """Round price to exchange-specified precision for the symbol.
        Falls back to tick-size rounding if exchange info was not loaded."""
        precision = PRICE_PRECISION.get(symbol)
        if precision is not None:
            return round(price, precision)
        # Fallback: tick-size based (legacy)
        import math
        tick = TICK_SIZES.get(symbol, 0.001)
        rounded = round(price / tick) * tick
        if tick >= 1:
            decimals = 0
        else:
            decimals = max(0, -int(math.floor(math.log10(tick))))
        return round(rounded, decimals)

    def _min_quantity(self, sym: str, price: float) -> float:
        """Return tradeable quantity based on TRADE_USDT_AMOUNT env var (default 10 USDT)."""
        min_qty = MIN_QTY.get(sym, 0.001)
        trade_usdt = float(os.getenv("TRADE_USDT_AMOUNT", "10.0"))
        # Per-symbol min notional (Binance varies: BTC=50, ETH/LTC/LINK=20, others=5)
        min_notional_map = {
            'BTCUSDT': 50.0, 'ETHUSDT': 20.0, 'LTCUSDT': 20.0, 'LINKUSDT': 20.0,
        }
        min_notional = min_notional_map.get(sym, 5.0)
        if price > 0:
            notional_qty = trade_usdt / price
            min_notional_qty = min_notional / price
            qty = max(min_qty, notional_qty, min_notional_qty)
        else:
            qty = min_qty
        rounded = self._round_qty(sym, qty)
        # Final safety: ensure rounded qty still meets min_qty
        if rounded < min_qty:
            rounded = self._round_qty(sym, min_qty)
        logger.info(f"[Binance] Quantity for {sym}: {rounded:.6f} @ ${price:.2f} (${trade_usdt} USDT, minNotional=${min_notional})")
        return rounded

    # ── Public interface ──────────────────────────────────────────────────────

    def get_balance(self) -> dict:
        """Fetch Binance Futures wallet balance (sync).
        Uses futures_account() totals which aggregate ALL margin assets
        (USDT, USDC, BNFCR, BFUSD, etc.) into a single USD-equivalent value.
        The old USDT-only filter returned 0 on USDC/BNFCR accounts.
        """
        try:
            client = self._get_client()
            acct = client.futures_account()
            total_wallet = float(acct.get('totalWalletBalance', 0))
            available    = float(acct.get('availableBalance', 0))
            margin_bal   = float(acct.get('totalMarginBalance', total_wallet))
            return {
                'balance':         total_wallet,
                'available':       available,
                'equity':          margin_bal,
                'unrealized_pnl':  float(acct.get('totalUnrealizedProfit', 0)),
                'margin_used':     float(acct.get('totalInitialMargin', 0)),
                'broker':          'binance_futures',
            }
        except Exception as e:
            logger.error(f"get_balance error: {e}")
            return {'balance': 0.0, 'equity': 0.0, 'available': 0.0,
                    'broker': 'binance_futures', 'error': str(e)}

    def get_positions(self) -> list:
        """Return all non-zero open futures positions."""
        try:
            client = self._get_client()
            all_pos = client.futures_position_information()
            return [
                {
                    'symbol':          p['symbol'],
                    'side':            'BUY' if float(p['positionAmt']) > 0 else 'SELL',
                    'quantity':        abs(float(p['positionAmt'])),
                    'entry_price':     float(p['entryPrice']),
                    'unrealized_pnl':  float(p['unRealizedProfit']),
                    'leverage':        int(p.get('leverage', self.leverage)),
                    'margin_type':     p.get('marginType', self.margin_type),
                    'mark_price':      float(p.get('markPrice', 0)),
                    'liquidation_price': float(p.get('liquidationPrice', 0)),
                }
                for p in all_pos
                if abs(float(p.get('positionAmt', 0))) > 0
            ]
        except Exception as e:
            logger.error(f"get_positions error: {e}")
            return []

    def get_open_orders(self) -> list:
        """Return all open futures orders."""
        try:
            client = self._get_client()
            orders = client.futures_get_open_orders()
            return [
                {
                    'order_id': str(o['orderId']),
                    'symbol':   o['symbol'],
                    'side':     o['side'],
                    'type':     o['type'],
                    'quantity': float(o['origQty']),
                    'price':    float(o.get('price', 0)),
                    'status':   o['status'],
                }
                for o in orders
            ]
        except Exception as e:
            logger.error(f"get_open_orders error: {e}")
            return []

    def place_order(
        self,
        symbol: str,
        direction: str,
        action: str = 'open',           # 'open' | 'close'
        quantity: Optional[float] = None,
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        comment: str = '',
        **kwargs,          # accepts ctrader-style kwarg aliases
    ) -> dict:
        """
        Place a Binance Futures market order.
        Compatible with ctrader_broker.place_order() return format.

        Returns: {'status': 'sent'|'simulated'|'skipped'|'error',
                  'broker': str, 'order_id': str, ...}
        """
        # ── ctrader-style kwarg aliases (backward-compat) ─────────────────────
        symbol      = kwargs.get('yfinance_symbol', symbol)
        quantity    = kwargs.get('volume', quantity)
        price       = kwargs.get('current_price', price)
        stop_loss   = kwargs.get('stop_loss_price', stop_loss)
        take_profit = kwargs.get('take_profit_price', take_profit)
        futures_sym = self._to_futures_symbol(symbol)
        if not futures_sym:
            logger.info(f"[Binance Futures] Skipping unsupported symbol: {symbol}")
            return {'status': 'skipped', 'broker': 'binance_futures',
                    'reason': f'{symbol} not supported on Binance Futures'}

        if self.dry_run:
            logger.info(f"[Binance Futures DRY-RUN] {action.upper()} {direction} {futures_sym}")
            return {'status': 'simulated', 'broker': 'binance_futures_dry',
                    'symbol': futures_sym, 'direction': direction, 'action': action}

        try:
            client = self._get_client()
            self._setup_symbol(client, futures_sym)

            # Resolve current price
            if not price:
                ticker = client.futures_symbol_ticker(symbol=futures_sym)
                price  = float(ticker['price'])

            # Resolve quantity
            if not quantity:
                quantity = self._min_quantity(futures_sym, price)
            else:
                quantity = self._round_qty(futures_sym, quantity)

            # ── Pre-trade margin check ──────────────────────────────────────────
            acct = client.futures_account()
            available = float(acct.get('availableBalance', 0))
            notional = quantity * price
            # Isolated margin requirement ≈ notional / leverage
            required_margin = notional / self.leverage
            if action != 'close' and available < required_margin:
                logger.warning(
                    f"[Binance Futures] SKIP {futures_sym}: available={available:.4f} < "
                    f"required_margin={required_margin:.4f} (notional={notional:.2f} / "
                    f"leverage={self.leverage}x)"
                )
                return {
                    'status': 'skipped',
                    'broker': 'binance_futures',
                    'reason': f'Insufficient margin: available={available:.4f}, '
                              f'required={required_margin:.4f}',
                    'available': available,
                    'required_margin': required_margin,
                }

            # Determine side and reduceOnly flag
            passed_reduce_only = kwargs.get('reduce_only', False)
            if passed_reduce_only or action == 'close':
                # Closing a position: flip side vs original direction
                side        = 'SELL' if direction.upper() == 'BUY' else 'BUY'
                reduce_only = True
            else:
                side        = direction.upper()
                reduce_only = False

            # Hedge Mode positionSide: always the POSITION side (LONG/SHORT)
            position_side = 'LONG' if direction.upper() == 'BUY' else 'SHORT'

            logger.info(
                f"[Binance Futures] {action.upper()} {side} ({position_side}) {quantity} {futures_sym} "
                f"@ ~{price:.4f} leverage={self.leverage}x comment={comment}"
            )

            # ── Main order ────────────────────────────────────────────────────
            order_params = {
                "symbol": futures_sym,
                "side": side,
                "type": 'MARKET',
                "quantity": quantity,
                "positionSide": position_side,
            }
            # Note: In Hedge Mode, we DO NOT send reduceOnly as positionSide handles it.
            # Sending it causes APIError -1106.
                
            result = client.futures_create_order(**order_params)
            order_id = str(result.get('orderId', ''))
            filled_price = float(result.get('avgPrice') or result.get('price') or price or 0.0)
            logger.info(f"  ✓ Order {order_id} filled @ {filled_price}")

            logger.info(f"  [DEBUG] SL/TP Check -> reduce_only={reduce_only}, stop_loss={stop_loss}, take_profit={take_profit}")

            # ── Cleanup Orphaned Orders on Close ──────────────────────────────
            if reduce_only:
                try:
                    # In hedge mode, we need to specify positionSide to cancel orders
                    client.futures_cancel_all_open_orders(symbol=futures_sym)
                    logger.info(f"  ✓ Cancelled all remaining open orders for {futures_sym}")
                except Exception as e:
                    logger.warning(f"  [!] Failed to cancel open orders for {futures_sym}: {e}")

            # ── Stop-loss order (STOP_MARKET, reduceOnly) ─────────────────────
            if not reduce_only and stop_loss:
                sl_valid = (side == 'BUY' and stop_loss < price) or (side == 'SELL' and stop_loss > price)
                if not sl_valid:
                    logger.warning(f"  [SL] Invalid stop_loss={stop_loss} for {side} @ {price} — skipping")
                else:
                    try:
                        sl_side = 'SELL' if side == 'BUY' else 'BUY'
                        sl_params = {
                            "symbol": futures_sym,
                            "side": sl_side,
                            "type": 'STOP_MARKET',
                            "stopPrice": self._round_price(futures_sym, stop_loss),
                            "quantity": quantity,
                            "timeInForce": 'GTC',
                            "positionSide": position_side,
                        }
                        sl_order = self._safe_create_order(client, sl_params)
                        sl_algo_id = sl_order.get('algoId') or sl_order.get('orderId')
                        logger.info(f"  [SL] Placed for {futures_sym} @ {stop_loss} (id={sl_algo_id})")
                    except Exception as sl_e:
                        # C1 FIX: SL failed → position is naked → EMERGENCY CLOSE immediately.
                        # Never silently continue; fail-closed is the only safe choice.
                        logger.error(
                            f"  [SL] FAILED to place stop-loss for {futures_sym}: {sl_e}"
                            f" — initiating emergency close to prevent naked position"
                        )
                        try:
                            emg_side = 'SELL' if side == 'BUY' else 'BUY'
                            client.futures_create_order(
                                symbol=futures_sym,
                                side=emg_side,
                                type='MARKET',
                                quantity=quantity,
                                positionSide=position_side,
                            )
                            logger.critical(
                                f"  [SL-FAILSAFE] {futures_sym} closed at market due to SL failure"
                            )
                        except Exception as close_e:
                            logger.critical(
                                f"  [SL-FAILSAFE] MARKET CLOSE ALSO FAILED for {futures_sym}: {close_e}"
                                f" — MANUAL INTERVENTION REQUIRED"
                            )
                        return {
                            'status': 'error',
                            'broker': 'binance_futures',
                            'message': f'SL placement failed ({sl_e}) — emergency close attempted',
                            'order_id': order_id,
                            'symbol': futures_sym,
                            'sl_error': str(sl_e),
                        }

            # ── Take-profit order (TAKE_PROFIT_MARKET, reduceOnly) ────────────
            if not reduce_only and take_profit:
                tp_valid = (side == 'BUY' and take_profit > price) or (side == 'SELL' and take_profit < price)
                if not tp_valid:
                    logger.warning(f"  [TP] Invalid take_profit={take_profit} for {side} @ {price} — skipping")
                else:
                    try:
                        tp_side = 'SELL' if side == 'BUY' else 'BUY'
                        tp_params = {
                            "symbol": futures_sym,
                            "side": tp_side,
                            "type": 'TAKE_PROFIT_MARKET',
                            "stopPrice": self._round_price(futures_sym, take_profit),
                            "quantity": quantity,
                            "timeInForce": 'GTC',
                            "positionSide": position_side,
                        }
                        tp_order = self._safe_create_order(client, tp_params)
                        tp_algo_id = tp_order.get('algoId') or tp_order.get('orderId')
                        logger.info(f"  [TP] Placed for {futures_sym} @ {take_profit} (id={tp_algo_id})")
                    except Exception as tp_e:
                        logger.warning(f"  [TP] Failed: {tp_e}")

            return {
                'status':       'sent',
                'message':      f'Order {order_id} filled @ {filled_price}',
                'broker':       'binance_futures',
                'order_id':     order_id,
                'symbol':       futures_sym,
                'side':         side,
                'quantity':     quantity,
                'filled_price': filled_price,
                'action':       action,
                'raw':          result,
            }

        except Exception as e:
            # -2022: position already flat (exchange SL fired before this close reached Binance)
            if '-2022' in str(e) or 'ReduceOnly Order is rejected' in str(e):
                logger.info(f'[Binance Futures] {symbol} already flat (-2022) -- marking closed')
                return {'status': 'already_flat', 'broker': 'binance_futures',
                        'already_closed': True, 'order_id': '', 'symbol': symbol,
                        'message': '-2022 position already closed on exchange'}
            logger.error(f"[Binance Futures] place_order error: {e}")
            return {'status': 'error', 'broker': 'binance_futures', 'message': str(e), 'error': str(e)}

    def _safe_create_order(self, client, order_params):
        """Try placing an order and dynamically reduce precision if Binance complains about it."""
        import copy
        import math
        params = copy.deepcopy(order_params)
        
        while True:
            try:
                return client.futures_create_order(**params)
            except Exception as e:
                err_str = str(e)
                if "-1111" in err_str and "stopPrice" in params:
                    # Too much precision. Let's find the current decimals and reduce by 1
                    sp_str = str(params["stopPrice"])
                    if "." in sp_str:
                        decimals = len(sp_str.split(".")[1])
                        if decimals > 0:
                            new_decimals = decimals - 1
                            params["stopPrice"] = float(f"{params['stopPrice']:.{new_decimals}f}")
                            continue
                raise e


    def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> dict:
        try:
            client = self._get_client()
            if symbol:
                futures_sym = self._to_futures_symbol(symbol)
                result = client.futures_cancel_order(symbol=futures_sym, orderId=order_id)
            else:
                # Binance requires a symbol to cancel an order by ID. 
                # This is a limitation we must work around by trying active symbols or returning an error.
                return {'success': False, 'message': 'Binance Futures requires a symbol to cancel an order.'}
            return {'success': True, 'message': 'Order cancelled', 'raw': result}
        except Exception as e:
            return {'success': False, 'message': str(e)}


# ── Module-level singleton ────────────────────────────────────────────────────
binance_futures_broker = BinanceFuturesService()
