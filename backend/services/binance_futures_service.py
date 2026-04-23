"""
Binance USDT-M Futures Service
Handles live order execution on Binance Futures (perpetuals).
Supports LONG/SHORT positions with configurable leverage (default 10x).

Interface compatible with ctrader_service for drop-in integration with trading_loop.py.
"""

import logging
import os
from pathlib import Path
from typing import Optional

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
    'MATIC-USD': 'MATICUSDT',
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
    'LINKUSDT': 'LINKUSDT', 'MATICUSDT': 'MATICUSDT', 'LTCUSDT': 'LTCUSDT',
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
    'DOGEUSDT': 1.0,  'AVAXUSDT': 0.1,  'DOTUSDT': 0.1,
    'LINKUSDT': 0.1,  'MATICUSDT': 1.0, 'LTCUSDT': 0.001,
    'UNIUSDT': 0.1,   'ATOMUSDT': 0.01, 'NEARUSDT': 0.1,
    'OPUSDT': 0.1,    'ARBUSDT': 1.0,   'APTUSDT': 0.1,
    'INJUSDT': 0.1,   'SUIUSDT': 1.0,
}

# Precision (decimal places) for quantity
QTY_PRECISION = {
    'BTCUSDT': 3, 'ETHUSDT': 3, 'SOLUSDT': 1,
    'BNBUSDT': 2, 'XRPUSDT': 0, 'ADAUSDT': 0,
    'DOGEUSDT': 0, 'AVAXUSDT': 1, 'DOTUSDT': 1,
    'LINKUSDT': 1, 'MATICUSDT': 0, 'LTCUSDT': 3,
    'UNIUSDT': 1, 'ATOMUSDT': 2, 'NEARUSDT': 1,
    'OPUSDT': 1, 'ARBUSDT': 0, 'APTUSDT': 1,
    'INJUSDT': 1, 'SUIUSDT': 0,
}


class BinanceFuturesService:
    """Binance USDT-M Futures broker — compatible with trading_loop.py interface."""

    def __init__(self):
        self.api_key    = os.getenv('BINANCE_API_KEY', '')
        self.api_secret = os.getenv('BINANCE_API_SECRET', '')
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
            if '-4046' not in str(e):  # -4046 = already set, safe to ignore
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
        if decimals == 0:
            return float(int(qty))
        return round(qty, decimals)

    def _min_quantity(self, sym: str, price: float) -> float:
        """Return tradeable quantity based on TRADE_USDT_AMOUNT env var (default 10 USDT)."""
        min_qty = MIN_QTY.get(sym, 0.001)
        trade_usdt = float(os.getenv("TRADE_USDT_AMOUNT", "10.0"))
        if price > 0:
            notional_qty = trade_usdt / price
            # Binance min notional = 20 (without leverage)
            min_notional_qty = 20.0 / price
            qty = max(min_qty, notional_qty, min_notional_qty)
        else:
            qty = min_qty
        logger.info(f"[Binance] Quantity for {sym}: {qty:.6f} @ ${price:.2f} (${trade_usdt} USDT)")
        return self._round_qty(sym, qty)

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

            # Determine side and reduceOnly flag
            if action == 'close':
                # Close long → SELL; close short → BUY
                side        = 'SELL' if direction.upper() == 'BUY' else 'BUY'
                reduce_only = True
            else:
                side        = direction.upper()
                reduce_only = False

            logger.info(
                f"[Binance Futures] {action.upper()} {side} {quantity} {futures_sym} "
                f"@ ~{price:.4f} leverage={self.leverage}x comment={comment}"
            )

            # ── Main order ────────────────────────────────────────────────────
            result = client.futures_create_order(
                symbol=futures_sym,
                side=side,
                type='MARKET',
                quantity=quantity,
                reduceOnly=reduce_only,
            )
            order_id = str(result.get('orderId', ''))
            filled_price = float(result.get('avgPrice') or result.get('price') or price or 0.0)
            logger.info(f"  ✓ Order {order_id} filled @ {filled_price}")

            # ── Stop-loss order (STOP_MARKET, reduceOnly) ─────────────────────
            if not reduce_only and stop_loss:
                try:
                    sl_side = 'SELL' if side == 'BUY' else 'BUY'
                    client.futures_create_order(
                        symbol=futures_sym,
                        side=sl_side,
                        type='STOP_MARKET',
                        stopPrice=round(stop_loss, 2),
                        quantity=quantity,
                        reduceOnly=True,
                        timeInForce='GTC',
                    )
                    logger.info(f"  [SL] @ {stop_loss:.4f}")
                except Exception as sl_e:
                    logger.warning(f"  [SL] Failed: {sl_e}")

            # ── Take-profit order (TAKE_PROFIT_MARKET, reduceOnly) ────────────
            if not reduce_only and take_profit:
                try:
                    tp_side = 'SELL' if side == 'BUY' else 'BUY'
                    client.futures_create_order(
                        symbol=futures_sym,
                        side=tp_side,
                        type='TAKE_PROFIT_MARKET',
                        stopPrice=round(take_profit, 2),
                        quantity=quantity,
                        reduceOnly=True,
                        timeInForce='GTC',
                    )
                    logger.info(f"  [TP] @ {take_profit:.4f}")
                except Exception as tp_e:
                    logger.warning(f"  [TP] Failed: {tp_e}")

            return {
                'status':       'sent',
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
            logger.error(f"[Binance Futures] place_order error: {e}")
            return {'status': 'error', 'broker': 'binance_futures', 'error': str(e)}


# ── Module-level singleton ────────────────────────────────────────────────────
binance_futures_broker = BinanceFuturesService()
