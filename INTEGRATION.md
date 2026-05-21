# FinceptTerminal → QuantumTrade Pro Integration Guide

## What Was Ported

This integration extracts three core architectural layers from FinceptTerminal (C++/Qt)
and ports them into your Python/FastAPI backend:

1. **Unified Order Router** (`backend/services/unified_trading.py`)
2. **DataHub Pub/Sub** (`backend/services/data_hub.py`)
3. **AI Tool-Execution Loop** (`backend/services/llm_tool_loop.py`)

---

## New API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/trading/session/init` | Initialize paper or live session |
| GET  | `/trading/session/status` | Check active session |
| POST | `/trading/paper/order` | Place a paper order |
| POST | `/trading/paper/cancel` | Cancel a paper order |
| GET  | `/trading/paper/portfolio` | View paper portfolio + stats |
| GET  | `/trading/paper/positions` | List open paper positions |
| GET  | `/trading/paper/orders` | List paper orders |
| GET  | `/trading/paper/stats` | Win rate, PnL, fees |
| POST | `/trading/ai/agent-trade` | **LLM trades autonomously via tools** |
| GET  | `/trading/datahub/topics` | Active pub/sub topics |
| GET  | `/trading/datahub/peek/{topic}` | Peek cached topic value |

---

## How It Works

### 1. Unified Trading Router

**Before:** `trading_loop.py` called `_active_broker.place_order()` directly.
There was no paper engine — trades went straight to the broker or a stub.

**After:** All orders flow through `UnifiedTrading()`, a singleton that:
- Routes to **PaperTradingEngine** when `mode="paper"`
- Routes to **live broker adapters** when `mode="live"`
- Handles margin checks, fees, fill logic, and position tracking

```python
from backend.services.unified_trading import UnifiedTrading, UnifiedOrder, OrderSide, OrderType

ut = UnifiedTrading()
ut.register_broker("binance_futures", binance_futures_broker)
ut.init_session("binance_futures", mode="paper", paper_balance=100_000.0)

resp = ut.place_order(UnifiedOrder(
    symbol="BTCUSDT", side=OrderSide.BUY,
    order_type=OrderType.MARKET, quantity=0.01
))
```

### 2. Paper Trading Engine

A full simulated exchange with:
- **Fill engine**: market orders fill instantly; limit orders stay pending
- **Fee model**: configurable per-portfolio fee_rate
- **Margin checks**: validates cash before filling
- **Position lifecycle**: opposite-side closing, same-side averaging
- **P&L tracking**: realized PnL on every close, unrealized on positions
- **Stats**: win rate, total trades, total fees, final PnL

### 3. DataHub Pub/Sub

Market data is no longer siloed inside `binance_market_data.py`.
When a 24h ticker is fetched, it publishes to `market:quote:BTCUSDT`.
Any subscriber (AI tool, frontend SSE, another service) receives it instantly.

```python
from backend.services.data_hub import DataHub

# Publish
DataHub().publish("market:quote:BTCUSDT", {"price": 65000})

# Subscribe
DataHub().subscribe("market:quote:BTCUSDT", lambda v: print(v))

# Peek cached value
val = DataHub().peek("market:quote:BTCUSDT")
```

### 4. AI Tool-Execution Loop

**Before:** `ai_analysis.py` asked the LLM for a decision and parsed the text.

**After:** The LLM receives **tools** it can call in a loop:
- `get_price(symbol)`
- `get_portfolio()`
- `place_paper_order(symbol, side, quantity, ...)`
- `cancel_paper_order(order_id)`
- `get_paper_orders(status)`

The LLM decides when to gather data and when to act. The conversation continues
until the LLM stops calling tools and returns a final answer (max 5 rounds).

**Try it:**
```bash
curl -X POST http://localhost:8000/trading/ai/agent-trade \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Analyze BTCUSDT. If bullish, buy 0.01 BTC with paper trading.", "provider": "xai"}'
```

---

## Modified Files

| File | Change |
|------|--------|
| `backend/services/unified_trading.py` | **NEW** — Order router + paper engine |
| `backend/services/data_hub.py` | **NEW** — In-memory pub/sub bus |
| `backend/services/llm_tool_loop.py` | **NEW** — LLM client with tool loop |
| `backend/services/trading_loop.py` | Uses `UnifiedTrading` instead of direct broker |
| `backend/services/binance_market_data.py` | Publishes quotes to DataHub |
| `backend/routes/trading.py` | Added 11 new endpoints |
| `backend/main.py` | Registers brokers + auto-inits paper session |

---

## Testing

1. Start the backend:
   ```bash
   cd ai-trading-platform
   uvicorn backend.main:app --reload
   ```

2. Initialize paper session:
   ```bash
   curl -X POST http://localhost:8000/trading/session/init \
     -H "Content-Type: application/json" \
     -d '{"broker": "binance_futures", "mode": "paper", "paper_balance": 50000}'
   ```

3. Place a paper order:
   ```bash
   curl -X POST http://localhost:8000/trading/paper/order \
     -H "Content-Type: application/json" \
     -d '{"symbol": "BTCUSDT", "side": "buy", "quantity": 0.01}'
   ```

4. Check portfolio:
   ```bash
   curl http://localhost:8000/trading/paper/portfolio
   ```

5. Let the AI trade:
   ```bash
   curl -X POST http://localhost:8000/trading/ai/agent-trade \
     -H "Content-Type: application/json" \
     -d '{"prompt": "BTC looks strong. Buy 0.02 BTC if price is under 70000.", "provider": "xai"}'
   ```

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Frontend (React/Vite)                         │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
              ┌────────────────────────┐
              │   FastAPI Routes   │
              │  (/trading/...)   │
              └────────────────────────┘
                        │
        ┌────────────────┼──────────────────┐
        │               │                │
   ┌─────┴─────┐   ┌─────┴─────┐   ┌─────┴─────┐
   │ UnifiedTrading │   │  DataHub     │   │ AI Tool Loop │
   │   (Router)     │   │  (Pub/Sub)   │   │  (LLM Loop)  │
   └─────────────┘   └─────────────┘   └─────────────┘
        │
   ┌─────┴─────┼─────┐
   │               │
┌──────────────────┐   ┌──────────────────┐
│ PaperTradingEngine │   │  Live Broker   │
│   (Simulated)      │   │  (Binance/     │
└──────────────────┘   │   cTrader)     │
                         └──────────────────┘
```

---

## Next Steps

### A) Full Paper Engine with SQLite Persistence
Currently the paper engine stores everything in-memory.
To persist across restarts, add SQLite logging in `PaperTradingEngine`:
- Write every fill to a `paper_trades` table
- Write portfolio snapshots to `paper_snapshots`
- On startup, hydrate the engine from DB

### B) SSE Bridge to React Frontend
Add a `/trading/stream` SSE endpoint that subscribes to DataHub topics
(`market:quote:*`, `paper:fill`, etc.) and pushes JSON events to the browser.

### C) Multi-Account Support
`UnifiedTrading` currently holds one session. Extend it to hold a `dict` of
sessions keyed by account ID, so you can run paper + live simultaneously.
