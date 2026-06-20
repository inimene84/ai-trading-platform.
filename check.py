
import asyncio
from backend.services.binance_futures_service import BinanceFuturesService
import datetime
async def main():
    broker = BinanceFuturesService()
    for sym in [" XRPUSDT\, \BNBUSDT\]:
 print(\--- \ + sym + " ---\)
        orders = broker.client.futures_get_all_orders(symbol=sym, limit=10)
        for o in orders:
            dt = datetime.datetime.fromtimestamp(o[\updateTime\]/1000).isoformat()
            print(o[\orderId\], o[\type\], o[\side\], o[\status\], dt)
    broker.client.close_connection()
asyncio.run(main())

