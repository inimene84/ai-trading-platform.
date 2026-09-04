"""
Kronos Financial Foundation Model Service (WP3)
==============================================
Client wrapper for Kronos sidecar microservice (ai-trading-kronos).
Performs 5/10-candle trajectory prediction, path metric extraction, and response caching.

When the sidecar is unreachable, defaults to NEUTRAL (fail-closed for gating).
Set KRONOS_ALLOW_LOCAL_STUB=true only for offline unit tests / local dev.
"""

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

KRONOS_SIDECAR_URL = os.getenv("KRONOS_SIDECAR_URL", "http://kronos-infer:8001")
FALLBACK_LOCAL_URL = os.getenv("KRONOS_LOCAL_URL", "http://127.0.0.1:8002")
SIDECAR_TIMEOUT_SEC = float(os.getenv("KRONOS_TIMEOUT_SEC", "10.0"))
ALLOW_LOCAL_STUB = os.getenv("KRONOS_ALLOW_LOCAL_STUB", "false").lower() == "true"

_prediction_cache: Dict[str, tuple] = {}
CACHE_TTL_SEC = int(os.getenv("KRONOS_CACHE_TTL_SEC", "3600"))


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
        "forecast_path": None,
        "error": error,
    }


def _bar_date_str(bar: Dict[str, Any]) -> str:
    """Best-effort ISO-8601 date for a bar dict.

    Bars may carry `date` (ISO str), `timestamp`, or `time` (ISO str or
    epoch seconds, e.g. yfinance rows from multi_asset_bars).
    """
    raw = bar.get("date") or bar.get("timestamp") or bar.get("time") or ""
    if isinstance(raw, (int, float)) and raw > 0:
        try:
            return datetime.fromtimestamp(float(raw), tz=timezone.utc).isoformat()
        except Exception:
            return ""
    return str(raw)


async def predict(
    bars: Any,
    symbol: str,
    interval: str = "1h",
    include_path: bool = False,
    pred_len: int = 10,
) -> Dict[str, Any]:
    """
    Query Kronos sidecar for 5/10-bar trajectory prediction and path metrics.
    Deduplicated by (symbol, interval, last_bar_date, include_path, pred_len).
    When include_path=True the sidecar also returns forecast_path
    ([{date, close}, ...]) for chart overlays.
    """
    if bars is None:
        return _make_neutral("No bars provided")

    if isinstance(bars, pd.DataFrame):
        bar_list = bars.to_dict(orient="records")
    elif isinstance(bars, list):
        bar_list = bars
    else:
        return _make_neutral("Invalid bars format")

    if len(bar_list) < 5:
        return _make_neutral("Insufficient bar count (minimum 5 required)")

    last_bar = bar_list[-1]
    last_bar_date = _bar_date_str(last_bar)
    cache_key = (
        f"{symbol.upper()}:{interval}:{last_bar_date}"
        f":path={int(include_path)}:n={int(pred_len)}"
    )

    if cache_key in _prediction_cache:
        ts, cached_res = _prediction_cache[cache_key]
        if (time.time() - ts) < CACHE_TTL_SEC:
            logger.debug(f"KronosService: cache hit for {cache_key}")
            return cached_res

    payload = {
        "symbol": symbol.upper(),
        "bars": [
            {
                "date": _bar_date_str(b),
                "open": float(b["open"]),
                "high": float(b["high"]),
                "low": float(b["low"]),
                "close": float(b["close"]),
                "volume": float(b["volume"]),
                "amount": float(b.get("amount", float(b["close"]) * float(b["volume"]))),
            }
            for b in bar_list[-400:]
        ],
        "pred_len": int(pred_len),
        "include_path": bool(include_path),
    }

    urls_to_try = [f"{KRONOS_SIDECAR_URL.rstrip('/')}/predict"]
    if FALLBACK_LOCAL_URL and FALLBACK_LOCAL_URL.rstrip("/") not in KRONOS_SIDECAR_URL:
        urls_to_try.append(f"{FALLBACK_LOCAL_URL.rstrip('/')}/predict")

    for url in urls_to_try:
        try:
            async with httpx.AsyncClient(timeout=SIDECAR_TIMEOUT_SEC) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    _prediction_cache[cache_key] = (time.time(), data)
                    logger.info(
                        "KronosService: predicted %s (%+.2f%% 5-bar) for %s via %s",
                        data.get("signal"),
                        float(data.get("cum_change_5_pct") or 0.0),
                        symbol,
                        url,
                    )
                    return data
                logger.warning(
                    "KronosService: sidecar %s returned HTTP %s", url, resp.status_code
                )
        except Exception as e:
            logger.debug("KronosService: sidecar endpoint %s unavailable: %s", url, e)
            continue

    if not ALLOW_LOCAL_STUB:
        logger.warning(
            "KronosService: sidecar unreachable for %s — returning NEUTRAL (fail-closed)",
            symbol,
        )
        return _make_neutral("Sidecar unreachable (fail-closed)")

    # Explicit opt-in local stub for offline tests only.
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
            "max_adverse_excursion_pct": round(min(cum_5, 0.0), 4),
            "reversal_risk": False,
            "forecast_path": None,
            "error": "Local stub used (KRONOS_ALLOW_LOCAL_STUB=true)",
        }
        _prediction_cache[cache_key] = (time.time(), fallback_res)
        return fallback_res
    except Exception as e:
        logger.warning("KronosService stub error for %s: %s", symbol, e)
        return _make_neutral(str(e))
