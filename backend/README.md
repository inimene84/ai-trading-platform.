# QuantumTrade Pro - Backend

This is the backend server for QuantumTrade Pro — AI Hedge Fund Platform. It provides a FastAPI-based REST API that powers a complete trading ecosystem, including a unified trading loop, strategy execution, AI analyst agents, and backtesting.

> **Note:** This project began as a fork of `virattt/ai-hedge-fund` but has been heavily customized. It now includes extensive additional features such as Binance and cTrader integrations, Kronos workflow support, n8n webhook pipelines, InfluxDB time-series storage, and Qdrant vector database integration for news and sentiment analysis. This is a fully independent and expanded project.

## Overview

This backend application is the core of the AI Hedge Fund system. It exposes endpoints for running the hedge fund trading loop, executing backtests, and interacting with the AI agent ecosystem. It is designed to work seamlessly with the frontend React dashboard.

## Installation

### Using Poetry

1. Clone the repository:
```bash
git clone https://github.com/virattt/ai-hedge-fund.git
cd ai-hedge-fund
```

2. Install Poetry (if not already installed):
```bash
curl -sSL https://install.python-poetry.org | python3 -
```

3. Install dependencies:
```bash
# From the root directory
poetry install
```

4. Set up your environment variables:
```bash
# Create .env file for your API keys (in the root directory)
cp .env.example .env
```

5. Edit the .env file to add your API keys:
```bash
# For running LLMs hosted by openai (gpt-4o, gpt-4o-mini, etc.)
OPENAI_API_KEY=your-openai-api-key

# For running LLMs hosted by groq (deepseek, llama3, etc.)
GROQ_API_KEY=your-groq-api-key

# For getting financial data to power the hedge fund
FINANCIAL_DATASETS_API_KEY=your-financial-datasets-api-key
```

## Running the Server

To run the development server:

```bash
# Navigate to the backend directory
cd app/backend

# Start the FastAPI server with uvicorn
poetry run uvicorn main:app --reload
```

This will start the FastAPI server with hot-reloading enabled.

The API will be available at:
- API Endpoint: http://localhost:8000
- API Documentation: http://localhost:8000/docs

## API Endpoints

- `POST /hedge-fund/run`: Run the AI Hedge Fund with specified parameters
- `GET /ping`: Simple endpoint to test server connectivity

## Strategies

The platform's trading logic has been unified under a single interface to ensure consistency between live trading and backtesting:
- **`CombinedStrategy`**: The primary entry point for generating signals. It dynamically weights and delegates to underlying sub-strategies (Trend Following, Mean Reversion, Breakout) based on the current market condition.
- **`MarketRegimeDetector`**: Automatically detects the current market condition (e.g., TRENDING, RANGING, VOLATILE, BREAKOUT) based on recent OHLCV bars.
- **`StrategySignal`**: The standard output for all strategies, defined in `backend/strategies/base.py`. It includes `signal` (BUY/SELL/NEUTRAL), `confidence`, `entry_price`, `stop_loss`, and `take_profit`.

All sub-strategies implement `BaseStrategy.generate_signal()`. For more information, see `backend/strategies/base.py`.

## Project Structure

```
app/backend/
├── api/                      # API layer (future expansion)
├── models/                   # Domain models
│   ├── __init__.py
│   └── schemas.py            # Pydantic schema definitions
├── routes/                   # API routes
│   ├── __init__.py           # Router registry
│   ├── hedge_fund.py         # Hedge fund endpoints
│   └── health.py             # Health check endpoints
├── services/                 # Business logic
│   ├── graph.py              # Agent graph functionality
│   └── portfolio.py          # Portfolio management
├── __init__.py               # Package initialization
└── main.py                   # FastAPI application entry point
```

## Disclaimer

This project is for **educational and research purposes only**.

- Not intended for real trading or investment
- No warranties or guarantees provided
- Creator assumes no liability for financial losses
- Consult a financial advisor for investment decisions

By using this software, you agree to use it solely for learning purposes.