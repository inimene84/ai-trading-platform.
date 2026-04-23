# QuantumTrade Pro — AI Trading Platform

An AI-powered multi-asset trading platform with real-time data, multi-agent analysis, workflow automation, backtesting, and risk management.

## Architecture

```
ai-trading-platform/
├── backend/           # Python FastAPI backend
│   ├── agents/        # AI analyst agents (Warren Buffett, Cathie Wood, etc.)
│   ├── backtesting/   # Backtesting engine
│   ├── brokers/       # Broker integrations (cTrader, Binance, etc.)
│   ├── database/      # SQLAlchemy models & connection
│   ├── routes/        # API endpoints
│   ├── services/      # Business logic (trading loop, AI analysis, etc.)
│   ├── strategies/    # Trading strategies (trend, scalping, breakout, etc.)
│   └── main.py        # FastAPI application entry point
│
├── frontend/          # React + Vite + Tailwind frontend
│   ├── src/
│   │   ├── components/   # View components (Portfolio, Signals, Markets, etc.)
│   │   ├── services/     # API + market data services
│   │   └── App.tsx       # Main application shell
│   ├── server.ts         # Express middleware (telemetry proxies)
│   └── vite.config.ts    # Vite + proxy config
│
├── .env.example       # Environment variable template
├── pyproject.toml      # Python dependencies (Poetry)
├── run.bat             # Windows launcher
└── run.sh              # Linux / macOS launcher
```

## Quickstart

### Prerequisites
- **Python 3.11+** with [Poetry](https://python-poetry.org/)
- **Node.js 18+** with npm

### 1. Clone & configure
```bash
git clone <your-repo-url> ai-trading-platform
cd ai-trading-platform
cp .env.example .env
# Edit .env — add your API keys
```

### 2. Run
**Windows:**
```cmd
run.bat
```

**Linux / macOS:**
```bash
chmod +x run.sh
./run.sh
```

This installs dependencies and starts both services:
- **Frontend:** http://localhost:3000
- **Backend API:** http://localhost:8080
- **API Docs:** http://localhost:8080/docs

### 3. Manual start (alternative)
```bash
# Terminal 1 — Backend
poetry install
poetry run uvicorn backend.main:app --reload --host 127.0.0.1 --port 8080

# Terminal 2 — Frontend
cd frontend
npm install
npm run dev
```

## Key Features
- 🤖 **Multi-agent AI analysis** — 12+ analyst personas (Buffett, Munger, Druckenmiller, etc.)
- 📊 **Real-time dashboard** — TradingView charts, order book, portfolio tracking
- 🔄 **Automated trading loop** — configurable interval, multi-symbol support
- 🧪 **Backtesting engine** — historical simulation with performance metrics
- 🔗 **Broker integrations** — cTrader (Forex/CFD), Binance (Crypto), and more
- 📱 **Alerts** — Telegram, InfluxDB, and n8n webhook integrations
- ⚡ **AI workflow builder** — Visual drag-and-drop strategy editor

## Disclaimer

This project is for **educational and research purposes only**. Not intended for real trading. No warranties or guarantees. Consult a financial advisor for investment decisions.
