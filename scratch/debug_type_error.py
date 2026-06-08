import asyncio
import logging
import traceback
from backend.services.decision_engine import DecisionEngine
from backend.services.trading_loop import TradingLoopService

logging.basicConfig(level=logging.INFO)

async def debug():
    loop = TradingLoopService()
    bars = await loop._fetch_bars("BTCUSDT")
    engine = DecisionEngine(loop.risk_config)
    try:
        res = await engine.evaluate_symbol(
            symbol="BTCUSDT",
            bars=bars,
            existing_position=None,
            open_count=0,
            pyramid_layers=[],
            cooldown_active=False
        )
        print("Result:", res)
    except Exception as e:
        print("Caught exception:")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(debug())
