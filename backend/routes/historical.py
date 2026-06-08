"""Historical OHLCV data for client-side backtesting."""
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["historical"])


def _bars_to_candles(bars: list[dict]) -> list[dict]:
    """Normalize Binance bar dicts to the frontend backtest candle shape."""
    candles = []
    for b in bars:
        ts = b.get("timestamp")
        if ts is None and b.get("date"):
            from datetime import datetime
            try:
                dt = datetime.fromisoformat(str(b["date"]).replace("Z", "+00:00"))
                ts = int(dt.timestamp() * 1000)
            except Exception:
                ts = 0
        candles.append({
            "time": int(ts or 0),
            "open": float(b["open"]),
            "high": float(b["high"]),
            "low": float(b["low"]),
            "close": float(b["close"]),
            "volume": float(b.get("volume", 0)),
        })
    return candles


@router.get("/historical")
async def get_historical(
    symbol: str = Query(..., description="Trading pair, e.g. BTCUSDT or ETH-USD"),
    interval: str = Query("1h", description="Kline interval"),
    limit: int = Query(500, ge=1, le=1500),
):
    """
    Return OHLCV candles for workflow/client backtesting.

    Crypto symbols (USDT/USDC) are fetched from Binance Futures; other symbols
    fall back to yfinance via the backtesting engine downloader.
    """
    sym = symbol.upper().replace("-", "").replace("/", "")
    if sym.endswith("USD") and not sym.endswith("USDT") and not sym.endswith("USDC"):
        sym = sym + "T" if len(sym) <= 6 else sym

    try:
        if sym.endswith("USDT") or sym.endswith("USDC"):
            from backend.services.binance_market_data import binance_market_data
            bars = await binance_market_data.get_klines(sym, interval=interval, limit=limit)
            if not bars:
                return JSONResponse(status_code=404, content={"error": f"No data for {sym}"})
            # Attach ms timestamps for the converter
            from datetime import datetime
            enriched = []
            for b in bars:
                row = dict(b)
                if "timestamp" not in row and row.get("date"):
                    try:
                        dt = datetime.fromisoformat(str(row["date"]).replace("Z", "+00:00"))
                        row["timestamp"] = int(dt.timestamp() * 1000)
                    except Exception:
                        row["timestamp"] = 0
                enriched.append(row)
            return _bars_to_candles(enriched)

        # Non-crypto: yfinance (legacy workflow symbols like ETH-USD)
        from backend.backtesting_ctrader.engine import _download_bars
        days = max(7, min(365, limit // 24 + 1))
        yf_symbol = symbol if "-" in symbol or "=" in symbol else f"{symbol}-USD"
        bars = _download_bars(yf_symbol, days=days)
        return _bars_to_candles(bars[-limit:])
    except Exception as e:
        logger.error(f"Historical data error for {symbol}: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
