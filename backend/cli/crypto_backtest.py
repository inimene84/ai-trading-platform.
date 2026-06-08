import asyncio
import argparse
import logging
from backend.backtesting.crypto_backtester import CryptoBacktestEngine

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')

async def main():
    parser = argparse.ArgumentParser(description="Run Crypto Backtester for Binance 15m klines")
    parser.add_argument("--symbols", type=str, default="BTCUSDT,ETHUSDT", help="Comma-separated list of symbols")
    parser.add_argument("--interval", type=str, default="15m", help="Kline interval (e.g., 15m, 1h)")
    parser.add_argument("--limit", type=int, default=1500, help="Number of historical candles to fetch")
    parser.add_argument("--capital", type=float, default=1000.0, help="Initial mock portfolio capital")
    
    args = parser.parse_args()
    
    symbols = [s.strip().upper() for s in args.symbols.split(",")]
    
    engine = CryptoBacktestEngine(
        symbols=symbols,
        interval=args.interval,
        initial_capital=args.capital,
        limit=args.limit
    )
    
    await engine.run()

if __name__ == "__main__":
    asyncio.run(main())
