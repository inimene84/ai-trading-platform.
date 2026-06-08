from fastapi import APIRouter, Query, BackgroundTasks
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import asyncio

from backend.backtesting.crypto_backtester import CryptoBacktestEngine

router = APIRouter()

class BacktestRequest(BaseModel):
    symbols: List[str]
    interval: str = "15m"
    limit: int = 1500
    initial_capital: float = 1000.0

@router.post("/crypto")
async def run_crypto_backtest(req: BacktestRequest):
    """
    Run the Crypto Backtester directly, bypassing the long LLM calls
    to rapidly test technical strategies on Binance klines.
    """
    engine = CryptoBacktestEngine(
        symbols=req.symbols,
        interval=req.interval,
        initial_capital=req.initial_capital,
        limit=req.limit
    )
    
    results = await engine.run()
    return {"status": "success", "results": results}
