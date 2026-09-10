"""
FastAPI Routes for Jesse AI Quant Engine Bridge
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Any, Dict, Optional

from backend.services.jesse_bridge import jesse_bridge

router = APIRouter(tags=["Jesse Quant Engine"])


class StrategySyncRequest(BaseModel):
    sl_atr_mult: float = 2.0
    tp_atr_mult: float = 4.0
    trail_activation_atr: float = 1.8
    trail_atr_mult: float = 1.6


class ValidationBacktestRequest(BaseModel):
    strategy: str = "QuantumAIStrategy"
    symbol: str = "BTC-USDT"
    timeframe: str = "1h"
    start_date: str = "2024-01-01"
    finish_date: str = "2024-04-01"
    starting_balance: float = 10000.0


@router.get("/status")
async def get_jesse_bridge_status() -> Dict[str, Any]:
    """Check connectivity and available datasets in the Jesse AI Quant Engine."""
    return await jesse_bridge.get_status()


@router.post("/sync")
def sync_strategy_parameters(req: StrategySyncRequest = StrategySyncRequest()) -> Dict[str, Any]:
    """Sync validated strategy parameters (SL/TP ATR multipliers) to live RiskConfig."""
    return jesse_bridge.sync_strategy_to_risk_config(
        sl_atr_mult=req.sl_atr_mult,
        tp_atr_mult=req.tp_atr_mult,
        trail_activation_atr=req.trail_activation_atr,
        trail_atr_mult=req.trail_atr_mult,
    )


@router.post("/validate")
async def run_validation_simulation(req: ValidationBacktestRequest) -> Dict[str, Any]:
    """Run an on-demand backtest validation via Jesse API."""
    res = await jesse_bridge.run_backtest_simulation(
        strategy=req.strategy,
        symbol=req.symbol,
        timeframe=req.timeframe,
        start_date=req.start_date,
        finish_date=req.finish_date,
        starting_balance=req.starting_balance,
    )
    if res.get("status") == "error":
        raise HTTPException(status_code=500, detail=res.get("error"))
    return res


class MLPredictionRequest(BaseModel):
    symbol: str = "BTC-USDT"
    timeframe: str = "1h"
    model_type: str = "lightgbm"
    threshold: float = 0.45


@router.get("/ml-models")
async def get_ml_models() -> Dict[str, Any]:
    """List available trained Machine Learning model artifacts and cache status."""
    return await jesse_bridge.get_ml_models()


@router.get("/ml-predict")
async def get_ml_prediction(
    symbol: str = Query("BTC-USDT"),
    timeframe: str = Query("1h"),
    model_type: str = Query("lightgbm"),
    threshold: float = Query(0.45),
) -> Dict[str, Any]:
    """Query real-time Machine Learning prediction and probabilities via GET."""
    res = await jesse_bridge.get_ml_prediction(
        symbol=symbol,
        timeframe=timeframe,
        model_type=model_type,
        threshold=threshold,
    )
    if res.get("status") == "error":
        raise HTTPException(status_code=500, detail=res.get("error"))
    return res


@router.post("/ml-predict")
async def post_ml_prediction(req: MLPredictionRequest = MLPredictionRequest()) -> Dict[str, Any]:
    """Query real-time Machine Learning prediction and probabilities via POST."""
    res = await jesse_bridge.get_ml_prediction(
        symbol=req.symbol,
        timeframe=req.timeframe,
        model_type=req.model_type,
        threshold=req.threshold,
    )
    if res.get("status") == "error":
        raise HTTPException(status_code=500, detail=res.get("error"))
    return res

