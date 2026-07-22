"""
Kronos Financial Foundation Model Service (WP3)
==============================================
Client wrapper for Kronos sidecar microservice (ai-trading-kronos).
Performs 5/10-candle trajectory prediction, path metric extraction, and response caching.
"""

import asyncio
import logging
import os
import time
from typing import Dict, List, Optional, Any
import httpx
import pandas as pd

logger = logging.getLogger(__name__)

# Sidecar configuration
KRONOS_SIDECAR_URL = os.getenv("KRONOS_SIDECAR_URL", "http://kronos-infer:8001")
FALLBACK_LOCAL_URL = "http://localhost:8001"
SIDECAR_TIMEOUT_SEC = 10.0

# Prediction Cache: (symbol, interval, last_bar_date) -> (timestamp, prediction_dict)
_prediction_cache: Dict[str, tuple] = {}
CACHE_TTL_SEC = 3600  # 1 hour


def _make_neutral(error: Optional[str] = None) -> Dict[str, Any]:
    return {
        "signal": "NEUTRAL",
        "confidence": 0.0,
        "predicted_close": None,
        "predicted_change_pct": 0.0,
        "cum_change_5_pct": 0.0,
        "cum_change_10_pct": 0.0,
        "path_volatility": 0.0,
        "max_adverse_excursion_pct": 0.0,
        "reversal_risk": False,
        "error": error,
    }


async def predict(bars: Any, symbol: str, interval: str = "1h") -> Dict[str, Any]:
    """
    Query Kronos sidecar for 5/10-bar trajectory prediction and path metrics.
    Deduplicated by (symbol, interval, last_bar_date).
    """
    if bars is None:
        return _make_neutral("No bars provided")

    # Standardize input bars to list of dicts
    if isinstance(bars, pd.DataFrame):
        bar_list = bars.to_dict(orient="records")
    elif isinstance(bars, list):
        bar_list = bars
    else:
        return _make_neutral("Invalid bars format")

    if len(bar_list) < 5:
        return _make_neutral("Insufficient bar count (minimum 5 required)")

    last_bar = bar_list[-1]
    last_bar_date = str(last_bar.get("date", last_bar.get("timestamp", "")))
    cache_key = f"{symbol.upper()}:{interval}:{last_bar_date}"

    # Check cache
    if cache_key in _prediction_cache:
        ts, cached_res = _prediction_cache[cache_key]
        if (time.time() - ts) < CACHE_TTL_SEC:
            logger.debug(f"KronosService: cache hit for {cache_key}")
            return cached_res

    # Prepare payload for sidecar
    payload = {
        "symbol": symbol.upper(),
        "bars": [
            {
                "date": str(b.get("date", "")),
                "open": float(b["open"]),
                "high": float(b["high"]),
                "low": float(b["low"]),
                "close": float(b["close"]),
                "volume": float(b["volume"]),
                "amount": float(b.get("amount", float(b["close"]) * float(b["volume"]))),
            }
            for b in bar_list[-400:]  # Limit lookback to 400 bars
        ],
        "pred_len": 10
    }

    urls_to_try = [f"{KRONOS_SIDECAR_URL}/predict", f"{FALLBACK_LOCAL_URL}/predict"]

    for url in urls_to_try:
        try:
            async with httpx.AsyncClient(timeout=SIDECAR_TIMEOUT_SEC) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    _prediction_cache[cache_key] = (time.time(), data)
                    logger.info(f"KronosService: predicted {data.get('signal')} ({data.get('cum_change_5_pct'):+.2f}% 5-bar) for {symbol}")
                    return data
        except Exception as e:
            logger.debug(f"KronosService: sidecar endpoint {url} unavailable: {e}")
            continue

    # Fallback to local stub in kronos.py if sidecar is unreachable
    try:
        from backend.services.kronos import KronosPredictor
        df = pd.DataFrame(bar_list)
        predictor = KronosPredictor()
        pred_df = predictor.predict(df, pred_len=5)

        last_close = float(df["close"].iloc[-1])
        p5_close = float(pred_df["close"].iloc[-1]) if "close" in pred_df.columns else last_close
        cum_5 = ((p5_close - last_close) / last_close) * 100.0

        fallback_res = {
            "signal": "BUY" if cum_5 > 1.0 else ("SELL" if cum_5 < -1.0 else "NEUTRAL"),
            "confidence": round(min(abs(cum_5) / 5.0, 1.0), 4),
            "predicted_close": round(p5_close, 6),
            "predicted_change_pct": round(cum_5, 4),
            "cum_change_5_pct": round(cum_5, 4),
            "cum_change_10_pct": round(cum_5, 4),
            "path_volatility": 0.01,
            "max_adverse_excursion_pct": round(cum_5, 4),
            "reversal_risk": False,
            "error": "Local fallback used (sidecar offline)",
        }
        _prediction_cache[cache_key] = (time.time(), fallback_res)
        return fallback_res

    except Exception as e:
        logger.warning(f"KronosService fallback error for {symbol}: {e}")
        return _make_neutral(str(e))
