"""
Kronos Financial Foundation Model Service
Integrates the Kronos time-series prediction model for market forecasting.
Model: NeoQuasar/Kronos-mini | Tokenizer: NeoQuasar/Kronos-Tokenizer-2k
"""

import asyncio
import logging
import os
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MODEL_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),  # backend/services/
    '..', '..', 'models', 'kronos'
)
_MODEL_CACHE_DIR = os.path.realpath(_MODEL_CACHE_DIR)

_MODEL_NAME = "NeoQuasar/Kronos-mini"
_TOKENIZER_NAME = "NeoQuasar/Kronos-Tokenizer-2k"

# Module-level singleton
_predictor = None
_load_failed = False  # If loading fails, we don't retry every call


def _load_predictor():
    """Lazy-load Kronos model. Returns predictor or None on failure."""
    global _predictor, _load_failed
    if _predictor is not None:
        return _predictor
    if _load_failed:
        return None

    try:
        import sys
        # Ensure the backend/services dir is in path so 'kronos' package is importable
        _svc_dir = os.path.dirname(os.path.abspath(__file__))
        if _svc_dir not in sys.path:
            sys.path.insert(0, _svc_dir)

        from kronos import Kronos, KronosTokenizer, KronosPredictor

        os.makedirs(_MODEL_CACHE_DIR, exist_ok=True)
        logger.info(f"Loading Kronos tokenizer from {_TOKENIZER_NAME} (cache: {_MODEL_CACHE_DIR})")
        tokenizer = KronosTokenizer.from_pretrained(
            _TOKENIZER_NAME,
            cache_dir=_MODEL_CACHE_DIR,
        )
        logger.info(f"Loading Kronos model from {_MODEL_NAME}")
        model = Kronos.from_pretrained(
            _MODEL_NAME,
            cache_dir=_MODEL_CACHE_DIR,
        )
        _predictor = KronosPredictor(model, tokenizer, max_context=512)
        logger.info("Kronos model loaded successfully")
        return _predictor
    except Exception as e:
        logger.warning(f"Kronos model load failed (will return NEUTRAL): {e}")
        _load_failed = True
        return None


def _make_neutral(error: Optional[str] = None) -> dict:
    return {
        "signal": "NEUTRAL",
        "confidence": 0.0,
        "predicted_close": None,
        "predicted_change_pct": 0.0,
        "error": error,
    }


def _run_prediction(bars: pd.DataFrame, symbol: str) -> dict:
    """Synchronous prediction — runs in a thread pool."""
    predictor = _load_predictor()
    if predictor is None:
        return _make_neutral("Model not available")

    try:
        pred_len = 5
        lookback = min(400, len(bars))
        x_df = bars.iloc[-lookback:][['open', 'high', 'low', 'close', 'volume', 'amount']].copy()

        # Build timestamps
        if 'date' in bars.columns:
            try:
                x_ts = pd.to_datetime(bars['date'].iloc[-lookback:]).reset_index(drop=True)
            except Exception:
                x_ts = pd.date_range(end=pd.Timestamp.utcnow(), periods=lookback, freq='1h')
        else:
            x_ts = pd.date_range(end=pd.Timestamp.utcnow(), periods=lookback, freq='1h')

        # Generate future timestamps (hourly)
        last_ts = x_ts.iloc[-1] if hasattr(x_ts, 'iloc') else x_ts[-1]
        y_ts = pd.date_range(start=last_ts, periods=pred_len + 1, freq='1h')[1:]

        pred_df = predictor.predict(
            df=x_df.reset_index(drop=True),
            x_timestamp=x_ts.reset_index(drop=True) if hasattr(x_ts, 'reset_index') else pd.Series(x_ts),
            y_timestamp=pd.Series(y_ts),
            pred_len=pred_len,
            T=1.0,
            top_p=0.9,
            sample_count=1,
            verbose=False,
        )

        if pred_df is None or len(pred_df) == 0:
            return _make_neutral("Empty prediction")

        current_close = float(bars['close'].iloc[-1])
        predicted_close = float(pred_df['close'].iloc[0])  # next candle
        predicted_change_pct = ((predicted_close - current_close) / current_close) * 100.0

        # Signal logic
        if predicted_change_pct > 1.0:
            signal = "BUY"
        elif predicted_change_pct < -1.0:
            signal = "SELL"
        else:
            signal = "NEUTRAL"

        confidence = min(abs(predicted_change_pct) / 5.0, 1.0)

        return {
            "signal": signal,
            "confidence": round(confidence, 4),
            "predicted_close": round(predicted_close, 6),
            "predicted_change_pct": round(predicted_change_pct, 4),
            "error": None,
        }

    except Exception as e:
        logger.warning(f"Kronos prediction error for {symbol}: {e}")
        return _make_neutral(str(e))


async def predict(bars: pd.DataFrame, symbol: str) -> dict:
    """
    Run Kronos prediction for the next candle.

    Args:
        bars: DataFrame with columns open, high, low, close, volume
              (date column optional but used for timestamps)
        symbol: Trading symbol for logging

    Returns:
        dict with keys: signal, confidence, predicted_close,
                        predicted_change_pct, error
    """
    try:
        # Validate input
        if bars is None or len(bars) < 10:
            return _make_neutral("Insufficient data")

        # Ensure required columns
        required = ['open', 'high', 'low', 'close', 'volume']
        for col in required:
            if col not in bars.columns:
                return _make_neutral(f"Missing column: {col}")

        # Add 'amount' column if not present (Kronos requires this)
        if 'amount' not in bars.columns:
            bars = bars.copy()
            bars['amount'] = bars['close'] * bars['volume']

        # Run in thread pool to avoid blocking event loop
        result = await asyncio.to_thread(_run_prediction, bars, symbol)
        return result

    except Exception as e:
        logger.warning(f"Kronos service error for {symbol}: {e}")
        return _make_neutral(str(e))
