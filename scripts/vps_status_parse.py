import json
import urllib.request

def get(url):
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read().decode())

print("=== TRADING LOOP ===")
d = get("http://127.0.0.1:8001/trading/status")
tl = d.get("trading_loop", {})
for k in ["state", "running", "cycle_count", "last_cycle", "equity", "cash", "error", "trading_status", "trading_allowed"]:
    print(f"{k}: {tl.get(k)}")
print(f"symbols: {tl.get('symbols')}")

print("\n=== BINANCE ===")
try:
    b = get("http://127.0.0.1:8001/trading/binance/status")
    w = b.get("wallet", {})
    print(f"active: {b.get('active')}")
    print(f"equity: {w.get('equity')}")
    print(f"available: {w.get('available')}")
    print(f"positions: {b.get('positions_count')}")
    print(f"orders: {b.get('orders_count')}")
    for p in (b.get("positions") or [])[:5]:
        print(f"  pos: {p.get('symbol')} {p.get('side')} qty={p.get('quantity')} uPnL={p.get('unrealized_pnl')}")
except Exception as e:
    print(f"binance status error: {e}")

print("\n=== QDRANT ===")
try:
    q = get("http://127.0.0.1:6333/collections/crypto-news")
    print(f"points: {q.get('result',{}).get('points_count')}")
except Exception as e:
    print(f"qdrant error: {e}")
