"""
Kronos Financial Foundation Model Inference Sidecar
===================================================
Standalone microservice running NeoQuasar/Kronos-mini via the official
KronosPredictor API (vendored from shiyu-coder/Kronos, MIT).

Keeps PyTorch execution out of the main FastAPI backend container.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import List, Optional

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("kronos-infer")

# Vendor path: ./model from shiyu-coder/Kronos
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

app = FastAPI(title="Kronos Inference Sidecar", version="1.1.0")

_predictor = None
_model_backend = "unloaded"  # "neoquasar" | "analytical" | "unloaded"

KRONOS_MODEL_NAME = os.getenv("KRONOS_MODEL", "NeoQuasar/Kronos-mini")
KRONOS_TOKENIZER_NAME = os.getenv("KRONOS_TOKENIZER", "NeoQuasar/Kronos-Tokenizer-2k")
ALLOW_ANALYTICAL = os.getenv("KRONOS_ALLOW_ANALYTICAL_FALLBACK", "false").lower() == "true"
DEVICE = os.getenv("KRONOS_DEVICE", "cpu")


class BarInput(BaseModel):
    date: Optional[str] = None
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: Optional[float] = None


class PredictRequest(BaseModel):
    symbol: str
    bars: List[BarInput]
    pred_len: int = Field(default=5, ge=1, le=20)


class PredictResponse(BaseModel):
    signal: str
    confidence: float
    predicted_close: float
    predicted_change_pct: float
    cum_change_5_pct: float
    cum_change_10_pct: float
    path_volatility: float
    max_adverse_excursion_pct: float
    reversal_risk: bool
    inference_time_ms: float
    model_backend: str
    error: Optional[str] = None


class AnalyticalKronosPredictor:
    """Dev-only fallback. Not NeoQuasar weights."""

    def predict(self, df, x_timestamp=None, y_timestamp=None, pred_len=5, **kwargs):
        closes = df["close"].astype(float).values
        last_price = float(closes[-1])
        returns = np.diff(np.log(np.clip(closes, 1e-12, None)))
        mean_ret = float(np.mean(returns[-10:])) if len(returns) >= 10 else 0.0
        vol = float(np.std(returns[-10:])) if len(returns) >= 10 else 0.01
        future_prices = []
        curr = last_price
        for i in range(pred_len):
            curr = curr * np.exp(mean_ret * (0.9 ** i))
            future_prices.append(curr)
        return pd.DataFrame({
            "close": future_prices,
            "high": [p * (1 + vol) for p in future_prices],
            "low": [p * (1 - vol) for p in future_prices],
        })


def _load_model_singleton():
    global _predictor, _model_backend
    if _predictor is not None:
        return _predictor

    try:
        from model import Kronos, KronosPredictor, KronosTokenizer

        logger.info(
            "Loading NeoQuasar Kronos tokenizer=%s model=%s device=%s",
            KRONOS_TOKENIZER_NAME, KRONOS_MODEL_NAME, DEVICE,
        )
        tokenizer = KronosTokenizer.from_pretrained(KRONOS_TOKENIZER_NAME)
        model = Kronos.from_pretrained(KRONOS_MODEL_NAME)
        _predictor = KronosPredictor(model, tokenizer, device=DEVICE, max_context=512)
        _model_backend = "neoquasar"
        logger.info("✓ NeoQuasar Kronos weights loaded")
        return _predictor
    except Exception as e:
        logger.error("Failed to load NeoQuasar Kronos weights: %s", e)
        if ALLOW_ANALYTICAL:
            logger.warning("Using AnalyticalKronosPredictor (NOT production weights)")
            _predictor = AnalyticalKronosPredictor()
            _model_backend = "analytical"
            return _predictor
        _model_backend = "unloaded"
        return None


@app.on_event("startup")
async def startup_event():
    _load_model_singleton()


@app.get("/health")
async def health_check():
    return {
        "status": "ok" if _predictor is not None else "degraded",
        "model": KRONOS_MODEL_NAME,
        "loaded": _predictor is not None,
        "backend": _model_backend,
    }


def _build_timestamps(df: pd.DataFrame, lookback: int, pred_len: int):
    if "date" in df.columns and df["date"].notna().any():
        try:
            x_ts = pd.to_datetime(df["date"].iloc[-lookback:], utc=True).reset_index(drop=True)
        except Exception:
            x_ts = pd.date_range(end=pd.Timestamp.utcnow(), periods=lookback, freq="1h")
    else:
        x_ts = pd.date_range(end=pd.Timestamp.utcnow(), periods=lookback, freq="1h")
    last_ts = x_ts.iloc[-1] if hasattr(x_ts, "iloc") else x_ts[-1]
    y_ts = pd.date_range(start=last_ts, periods=pred_len + 1, freq="1h")[1:]
    return pd.Series(x_ts), pd.Series(y_ts)


@app.post("/predict", response_model=PredictResponse)
async def predict_trajectory(req: PredictRequest):
    start_ts = time.time()
    if not req.bars or len(req.bars) < 5:
        raise HTTPException(status_code=400, detail="Insufficient bar count (minimum 5 required)")

    predictor = _load_model_singleton()
    if not predictor:
        raise HTTPException(
            status_code=503,
            detail="Kronos model not available (set KRONOS_ALLOW_ANALYTICAL_FALLBACK=true for stub)",
        )

    rows = [b.model_dump() if hasattr(b, "model_dump") else b.dict() for b in req.bars]
    df = pd.DataFrame(rows)
    if "amount" not in df.columns or df["amount"].isnull().all():
        df["amount"] = df["close"] * df["volume"]

    pred_len = max(int(req.pred_len), 10)
    lookback = min(400, len(df))
    x_df = df.iloc[-lookback:][["open", "high", "low", "close", "volume", "amount"]].copy()
    x_ts, y_ts = _build_timestamps(df, lookback, pred_len)

    try:
        if _model_backend == "neoquasar":
            pred_df = predictor.predict(
                df=x_df.reset_index(drop=True),
                x_timestamp=x_ts.reset_index(drop=True),
                y_timestamp=y_ts.reset_index(drop=True),
                pred_len=pred_len,
                T=1.0,
                top_p=0.9,
                sample_count=1,
                verbose=False,
            )
        else:
            pred_df = predictor.predict(df=x_df, pred_len=pred_len)
    except Exception as e:
        logger.exception("Kronos predict failed for %s", req.symbol)
        raise HTTPException(status_code=500, detail=f"Prediction failed: {e}") from e

    if pred_df is None or len(pred_df) == 0:
        raise HTTPException(status_code=500, detail="Empty prediction")

    last_close = float(df["close"].iloc[-1])
    pred_closes = pred_df["close"].astype(float).values
    next_close = float(pred_closes[0])
    next_change_pct = ((next_close - last_close) / last_close) * 100.0
    p5_close = float(pred_closes[min(4, len(pred_closes) - 1)])
    cum_5 = ((p5_close - last_close) / last_close) * 100.0
    p10_close = float(pred_closes[min(9, len(pred_closes) - 1)])
    cum_10 = ((p10_close - last_close) / last_close) * 100.0

    changes = ((pred_closes - last_close) / last_close) * 100.0
    # Signed adverse excursion relative to a long entry (most negative path move).
    max_adverse_excursion = float(np.min(changes))
    path_volatility = float(np.std(changes)) if len(changes) > 1 else 0.0
    reversal_risk = (next_change_pct > 0.3 and cum_5 < -0.8) or (
        next_change_pct < -0.3 and cum_5 > 0.8
    )

    if cum_5 > 0.8 and next_change_pct > -0.2:
        signal = "BUY"
    elif cum_5 < -0.8 and next_change_pct < 0.2:
        signal = "SELL"
    else:
        signal = "NEUTRAL"

    confidence = min(abs(cum_5) / 4.0, 1.0)
    elapsed_ms = (time.time() - start_ts) * 1000.0

    return PredictResponse(
        signal=signal,
        confidence=round(confidence, 4),
        predicted_close=round(next_close, 6),
        predicted_change_pct=round(next_change_pct, 4),
        cum_change_5_pct=round(cum_5, 4),
        cum_change_10_pct=round(cum_10, 4),
        path_volatility=round(path_volatility, 4),
        max_adverse_excursion_pct=round(max_adverse_excursion, 4),
        reversal_risk=reversal_risk,
        inference_time_ms=round(elapsed_ms, 2),
        model_backend=_model_backend,
        error=None if _model_backend == "neoquasar" else "analytical_fallback",
    )
