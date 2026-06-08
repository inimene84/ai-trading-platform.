import os
import asyncio
import logging
from typing import List, Dict, Any
from collections import defaultdict
from datetime import datetime

from backend.services.binance_market_data import BinanceMarketDataService
from backend.services.decision_engine import DecisionEngine
from backend.services.risk_config import RiskConfig

logger = logging.getLogger(__name__)

class CryptoBacktestEngine:
    def __init__(self, symbols: List[str], interval: str = '15m', initial_capital: float = 10000.0, limit: int = 1500):
        self.symbols = symbols
        self.interval = interval
        self.initial_capital = initial_capital
        self.limit = limit
        
        self.market_data = BinanceMarketDataService()
        self.risk_config = RiskConfig()
        
        # Disable slow LLM API calls for backtesting to prevent massive delays/costs
        os.environ["ENABLE_PERSONAS"] = "false"
        self.decision_engine = DecisionEngine(self.risk_config)
        self.decision_engine.account_equity = initial_capital
        
        self.positions = {} # symbol -> {'entry_price', 'qty', 'direction', 'sl', 'tp', 'entry_time'}
        self.cash = initial_capital
        self.trade_history = []
        
    async def fetch_data(self) -> Dict[str, List[Dict]]:
        logger.info(f"Fetching {self.limit} historical {self.interval} klines for {self.symbols}...")
        data = {}
        for sym in self.symbols:
            klines = await self.market_data.get_klines(sym, self.interval, self.limit)
            if klines:
                data[sym] = klines
            else:
                logger.warning(f"No data returned for {sym}")
        return data

    def _execute_trade(self, symbol: str, direction: str, qty: float, price: float, sl: float, tp: float, timestamp: str):
        notional = qty * price
        
        # Subtract from cash (simulated margin usage) - simplified for testing
        # We just track PnL realistically at close.
        self.positions[symbol] = {
            'direction': direction,
            'qty': qty,
            'entry_price': price,
            'sl': sl,
            'tp': tp,
            'entry_time': timestamp
        }
        logger.info(f"[{timestamp}] OPEN {direction} {symbol}: {qty:.4f} @ ${price:.4f} (Notional: ${notional:.2f}) SL={sl:.4f} TP={tp:.4f}")
        
    def _close_trade(self, symbol: str, exit_price: float, timestamp: str, reason: str):
        pos = self.positions.pop(symbol)
        qty = pos['qty']
        entry_price = pos['entry_price']
        direction = pos['direction']
        
        if direction == 'BUY':
            pnl = (exit_price - entry_price) * qty
        else:
            pnl = (entry_price - exit_price) * qty
            
        self.cash += pnl
        self.decision_engine.account_equity = self.cash
        
        trade = {
            'symbol': symbol,
            'direction': direction,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'entry_time': pos['entry_time'],
            'exit_time': timestamp,
            'pnl': pnl,
            'reason': reason
        }
        self.trade_history.append(trade)
        logger.info(f"[{timestamp}] CLOSE {direction} {symbol}: {qty:.4f} @ ${exit_price:.4f} | PnL: ${pnl:.2f} | Reason: {reason} | Cash: ${self.cash:.2f}")

    def _check_exits(self, symbol: str, candle: Dict):
        if symbol not in self.positions:
            return
            
        pos = self.positions[symbol]
        direction = pos['direction']
        sl = pos['sl']
        tp = pos['tp']
        
        high = candle['high']
        low = candle['low']
        timestamp = candle['date']
        
        # Check SL/TP hits
        if direction == 'BUY':
            if low <= sl:
                self._close_trade(symbol, sl, timestamp, "SL")
            elif high >= tp:
                self._close_trade(symbol, tp, timestamp, "TP")
        elif direction == 'SELL':
            if high >= sl:
                self._close_trade(symbol, sl, timestamp, "SL")
            elif low <= tp:
                self._close_trade(symbol, tp, timestamp, "TP")

    async def run(self):
        data_by_sym = await self.fetch_data()
        
        # Group candles by timestamp to simulate tick-by-tick
        timeline = defaultdict(dict)
        for sym, candles in data_by_sym.items():
            for i, candle in enumerate(candles):
                timeline[candle['date']][sym] = (candle, i) # Store index for slicing history
                
        sorted_times = sorted(timeline.keys())
        
        for t in sorted_times:
            tick_data = timeline[t]
            
            # 1. Check open positions for exits using current candle High/Low
            for sym, (candle, idx) in tick_data.items():
                self._check_exits(sym, candle)
                
            # 2. Evaluate strategies for new entries
            for sym, (candle, idx) in tick_data.items():
                if sym in self.positions:
                    continue # Already in position
                    
                # We need at least 50 bars of history
                if idx < 50:
                    continue
                    
                # Slice history up to current candle
                history = data_by_sym[sym][:idx+1]
                
                decision = await self.decision_engine.evaluate_symbol(
                    symbol=sym,
                    bars=history,
                    existing_position=None,
                    open_count=len(self.positions),
                    pyramid_layers=[],
                    cooldown_active=False
                )
                
                if decision and decision.action in ["BUY", "SELL"]:
                    # Ensure we have a valid notional and risk sizing
                    sl = decision.stop_loss
                    tp = decision.take_profit
                    qty = decision.quantity
                    price = decision.entry_price
                    
                    if qty > 0:
                        self._execute_trade(sym, decision.action, qty, price, sl, tp, t)
                        
        # End of backtest: Force close any remaining open positions
        for sym in list(self.positions.keys()):
            pos = self.positions[sym]
            last_candle = data_by_sym[sym][-1]
            self._close_trade(sym, last_candle['close'], last_candle['date'], "END_OF_TEST")
            
        return self._generate_report()

    def _generate_report(self) -> Dict:
        wins = [t for t in self.trade_history if t['pnl'] > 0]
        losses = [t for t in self.trade_history if t['pnl'] <= 0]
        win_rate = len(wins) / len(self.trade_history) if self.trade_history else 0
        total_pnl = sum(t['pnl'] for t in self.trade_history)
        
        logger.info("\n========== BACKTEST RESULTS ==========")
        logger.info(f"Initial Capital: ${self.initial_capital:.2f}")
        logger.info(f"Final Capital:   ${self.cash:.2f}")
        logger.info(f"Total PnL:       ${total_pnl:.2f} ({(total_pnl/self.initial_capital)*100:.2f}%)")
        logger.info(f"Total Trades:    {len(self.trade_history)}")
        logger.info(f"Win Rate:        {win_rate*100:.1f}% ({len(wins)}W / {len(losses)}L)")
        logger.info("======================================")
        
        return {
            'initial_capital': self.initial_capital,
            'final_capital': self.cash,
            'total_pnl': total_pnl,
            'total_trades': len(self.trade_history),
            'win_rate': win_rate,
            'trades': self.trade_history
        }
