import os
from dotenv import load_dotenv
from binance.client import Client

load_dotenv('/app/.env')
api_key = os.getenv('BINANCE_API_KEY')
api_secret = os.getenv('BINANCE_SECRET_KEY') or os.getenv('BINANCE_API_SECRET')

if not api_key:
    print("Error: BINANCE_API_KEY not found in environment")
    exit(1)

client = Client(api_key=api_key, api_secret=api_secret)

try:
    positions = client.futures_position_information()
    open_positions = [p for p in positions if abs(float(p['positionAmt'])) > 0]

    if not open_positions:
        print("No open positions on Binance Futures.")
    else:
        print(f"Found {len(open_positions)} open positions. Closing all...")
        for p in open_positions:
            symbol = p['symbol']
            amt = float(p['positionAmt'])
            side = 'SELL' if amt > 0 else 'BUY'
            qty = abs(amt)
            print(f"Closing {symbol}: {side} {qty}")
            try:
                res = client.futures_create_order(
                    symbol=symbol,
                    side=side,
                    type='MARKET',
                    quantity=qty,
                    reduceOnly=True
                )
                print(f"Success: {res.get('orderId')}")
            except Exception as e:
                print(f"Error closing {symbol}: {e}")

    try:
        for sym in [p['symbol'] for p in open_positions]:
            client.futures_cancel_all_open_orders(symbol=sym)
        print("Canceled open orders for active symbols.")
    except Exception as e:
        print(f"Error canceling orders: {e}")

except Exception as e:
    print(f"Connection or API Error: {e}")
