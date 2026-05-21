
import asyncio
import os
import sys
from dotenv import load_dotenv

# Add project root to sys.path
sys.path.insert(0, os.getcwd())

# Load env before imports
load_dotenv()

async def main():
    from backend.services.trading_loop import trading_loop
    from backend.services.unified_trading import UnifiedTrading
    from backend.services.binance_futures_service import binance_futures_broker
    from backend.services.ctrader_service import ctrader_broker
    
    print("Initializing Unified Trading...")
    ut = UnifiedTrading()
    ut.register_broker("binance_futures", binance_futures_broker)
    ut.register_broker("ctrader", ctrader_broker)
    ut.init_session(broker="binance_futures", mode="live", leverage=1.0)
    
    print("Starting Trading Loop (1 minute interval for test)...")
    # We use a small interval and fewer symbols for testing
    symbols = ['BTCUSDT', 'ARBUSDT', 'MATICUSDT']
    await trading_loop.start(interval_minutes=1, symbols=symbols)
    
    print("Waiting for 90 seconds to see logs...")
    await asyncio.sleep(90)
    
    print("Stopping Trading Loop...")
    await trading_loop.stop()
    print("Done.")

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    # Set levels for noisy loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("ccxt").setLevel(logging.WARNING)
    
    asyncio.run(main())
