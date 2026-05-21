"""Market data routes for Binance-native data (funding rates, OI, 24h tickers, liquidations)."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/market-data", tags=["market-data"])


@router.get("/funding-rates")
async def get_funding_rates():
    """Get funding rates for all 20 symbols."""
    try:
        from backend.services.binance_market_data import binance_market_data
        rates = await binance_market_data.get_all_funding_rates()
        return JSONResponse(content={"status": "ok", "data": rates, "count": len(rates)})
    except Exception as e:
        logger.error(f"Funding rates error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/open-interest")
async def get_open_interest():
    """Get open interest for all 20 symbols."""
    try:
        from backend.services.binance_market_data import binance_market_data
        oi = await binance_market_data.get_all_open_interest()
        return JSONResponse(content={"status": "ok", "data": oi, "count": len(oi)})
    except Exception as e:
        logger.error(f"Open interest error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/overview")
async def get_market_overview():
    """Get combined 24h tickers for all symbols."""
    try:
        from backend.services.binance_market_data import binance_market_data
        tickers = await binance_market_data.get_all_tickers_24h()
        return JSONResponse(content={"status": "ok", "data": tickers, "count": len(tickers)})
    except Exception as e:
        logger.error(f"Market overview error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/liquidations/{symbol}")
async def get_liquidations(symbol: str):
    """Get recent liquidations for a symbol. Requires BINANCE_API_KEY."""
    try:
        from backend.services.binance_market_data import binance_market_data
        liquidations = await binance_market_data.get_recent_liquidations(symbol.upper())
        return JSONResponse(content={
            "status": "ok",
            "symbol": symbol.upper(),
            "data": liquidations,
            "count": len(liquidations),
        })
    except Exception as e:
        logger.error(f"Liquidations error for {symbol}: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/crypto-news")
async def get_crypto_news():
    """Get aggregated crypto news with sentiment."""
    try:
        from backend.services.crypto_news_service import crypto_news_service
        summary = await crypto_news_service.get_market_summary()
        return JSONResponse(content={"status": "ok", "data": summary})
    except Exception as e:
        logger.error(f"Crypto news error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/fear-greed")
async def get_fear_greed():
    """Get current Fear & Greed index."""
    try:
        from backend.services.crypto_news_service import crypto_news_service
        fng = await crypto_news_service.get_fear_greed()
        return JSONResponse(content={"status": "ok", "data": fng})
    except Exception as e:
        logger.error(f"Fear & Greed error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/bars")
async def get_bars(symbol: str, timeframe: str = "1h", limit: int = 100):
    """Fetch OHLCV klines for a symbol from Binance Futures."""
    try:
        from backend.services.binance_market_data import binance_market_data
        bars = await binance_market_data.get_klines(
            symbol=symbol.upper(),
            interval=timeframe,
            limit=limit,
        )
        return JSONResponse(content={"status": "ok", "symbol": symbol.upper(), "data": bars, "count": len(bars)})
    except Exception as e:
        logger.error(f"Bars error for {symbol}: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/trending")
async def get_trending():
    """Get trending coins from CoinGecko."""
    try:
        from backend.services.crypto_news_service import crypto_news_service
        trending = await crypto_news_service.get_trending_coins()
        return JSONResponse(content={"status": "ok", "data": trending, "count": len(trending)})
    except Exception as e:
        logger.error(f"Trending error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
