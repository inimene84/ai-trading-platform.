#!/usr/bin/env python3
"""Emergency script to close all open positions on Binance Futures and update the DB.
Run inside backend container or locally.
"""
import sys
import os
from pathlib import Path
from datetime import datetime, timezone
import logging

# Ensure /app or workspace is in path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.database.connection import SessionLocal
from backend.database.models import Trade
from backend.services.binance_futures_service import binance_futures_broker

# Set up simple logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("emergency_close")

def close_all():
    db = SessionLocal()
    closed_count = 0
    try:
        # 1. Fetch open/filled trades from DB
        open_trades = db.query(Trade).filter(Trade.status.in_(["open", "filled"])).all()
        logger.info(f"Found {len(open_trades)} open/filled trades in DB.")
        
        # Keep track of symbols we closed via DB
        closed_symbols = set()
        
        for trade in open_trades:
            symbol = trade.symbol
            logger.info(f"Closing DB trade {trade.id} ({symbol} {trade.direction} qty={trade.quantity})...")
            
            # Place MARKET close order
            try:
                res = binance_futures_broker.place_order(
                    symbol=symbol,
                    direction=trade.direction,
                    action='close',
                    quantity=trade.quantity,
                    comment='Emergency close script'
                )
                logger.info(f"Broker order response: {res}")
                
                # Retrieve exit price
                exit_price = res.get('price') or res.get('filled_price')
                if not exit_price:
                    # Fallback to ticker
                    try:
                        client = binance_futures_broker._get_client()
                        ticker = client.futures_symbol_ticker(symbol=binance_futures_broker._to_futures_symbol(symbol))
                        exit_price = float(ticker['price'])
                    except Exception:
                        exit_price = trade.entry_price
                
                # Calculate PnL
                if trade.direction == "BUY":
                    pnl = (exit_price - trade.entry_price) * trade.quantity
                else:
                    pnl = (trade.entry_price - exit_price) * trade.quantity
                
                # Update DB trade
                trade.status = "closed"
                trade.exit_price = exit_price
                trade.pnl = round(pnl, 2)
                trade.closed_at = datetime.now(timezone.utc)
                trade.notes = (trade.notes or "") + f" | Closed via emergency close script"
                db.add(trade)
                closed_count += 1
                closed_symbols.add(symbol)
                logger.info(f"Successfully closed DB trade {trade.id} ({symbol}). PnL: {pnl:+.2f}")
            except Exception as e:
                logger.error(f"Error closing DB trade {trade.id} ({symbol}): {e}")
        
        db.commit()
        
        # 2. Reconcile and close any remaining/orphan positions on the broker directly
        logger.info("Checking for any orphan positions on Binance Futures...")
        try:
            broker_positions = binance_futures_broker.get_positions()
            orphan_positions = [p for p in broker_positions if float(p.get('quantity', 0)) > 0]
            
            logger.info(f"Found {len(orphan_positions)} non-zero positions on Binance Futures.")
            for pos in orphan_positions:
                symbol = pos['symbol']
                side = pos['side'] # 'BUY' or 'SELL'
                qty = pos['quantity']
                logger.info(f"Orphan/remaining position found on broker: {symbol} {side} qty={qty}")
                
                # Place close order
                try:
                    res = binance_futures_broker.place_order(
                        symbol=symbol,
                        direction=side,
                        action='close',
                        quantity=qty,
                        comment='Emergency close script orphan cleanup'
                    )
                    logger.info(f"Closed orphan position {symbol}: {res}")
                    closed_count += 1
                except Exception as e:
                    logger.error(f"Error closing orphan position {symbol}: {e}")
        except Exception as e:
            logger.error(f"Error checking/closing orphan positions on broker: {e}")
            
    finally:
        db.close()
        
    logger.info(f"Emergency close completed. Total positions/trades closed: {closed_count}")
    return 0

if __name__ == "__main__":
    sys.exit(close_all())
