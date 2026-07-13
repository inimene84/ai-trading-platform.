import os
import httpx
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse, JSONResponse
import json
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

from backend.database.connection import SessionLocal
from backend.database.models import TradingSignal, Trade, PortfolioSnapshot
from backend.services.trading_loop import trading_loop
from backend.services.ai_analysis import ai_analysis_service
from backend.services.risk_config import refresh_risk_config
from backend.services.unified_trading import (
    UnifiedTrading, UnifiedOrder, OrderSide, OrderType,
)

import logging
logger = logging.getLogger(__name__)

class StartLoopRequest(BaseModel):
    interval_minutes: int = 15
    symbols: Optional[List[str]] = None
    strategy: str = "combined"

class LoopStatusResponse(BaseModel):
    state: str
    running: bool
    interval_minutes: int
    symbols: List[str]
    strategy: str
    last_cycle: Optional[str]
    next_cycle: Optional[str]
    cycle_count: int
    error: Optional[str]
    cash: Optional[float] = None
    equity: Optional[float] = None
    margin_used: Optional[float] = None

class TradingConfigResponse(BaseModel):
    mode: str
    interval_minutes: int
    symbols: List[str]
    risk_limits: Dict[str, Any]

class ConfigUpdateRequest(BaseModel):
    use_risk_reviewer_llm: Optional[bool] = None
    enable_personas: Optional[bool] = None


class ModifyPositionRequest(BaseModel):
    stop_loss: Optional[float] = Field(default=None, gt=0)
    take_profit: Optional[float] = Field(default=None, gt=0)


class LiveOrderRequest(BaseModel):
    symbol: str = Field(min_length=5, max_length=20, pattern=r"^[A-Za-z0-9=_/-]+$")
    side: Literal["buy", "sell"]
    quantity: float = Field(gt=0, le=1_000_000_000)
    order_type: Literal["market", "limit"] = "market"
    price: float = Field(default=0, ge=0)
    stop_loss: Optional[float] = Field(default=None, gt=0)
    take_profit: Optional[float] = Field(default=None, gt=0)


class AgentTradeRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=8_000)
    model: Optional[str] = Field(default=None, max_length=120)
    provider: Literal["xai", "openai", "groq", "ollama"] = "xai"

load_dotenv()

router = APIRouter(prefix="/trading", tags=["trading"])


def _to_yfinance_symbol(symbol: str) -> str:
    """Convert Binance-native BTCUSDT/BTCUSDC → yfinance BTC-USD format."""
    s = symbol.upper().strip()
    if s.endswith('USDT') or s.endswith('USDC'):
        return s[:-4] + '-USD'
    if s.endswith('BUSD'):
        return s[:-4] + '-USD'
    return s  # already yfinance format or unknown


@router.get("/strategies")
async def list_strategies():
    """List available trading strategies with their current parameters."""
    strategies = [
        {
            "name": "breakout",
            "description": "Donchian channel breakout with volume confirmation",
            "params": {"channel_period": 50, "atr_multiplier": 2.5, "volume_factor": 1.8},
        },
        {
            "name": "mean_reversion",
            "description": "Bollinger Bands + RSI mean reversion with ADX filter",
            "params": {"rsi_oversold": 25, "rsi_overbought": 75, "bb_std": 2.5},
        },
        {
            "name": "trend_following",
            "description": "EMA crossover with ADX trend confirmation",
            "params": {"fast_ema": 50, "slow_ema": 100, "adx_threshold": 25},
        },
        {
            "name": "scalping",
            "description": "EMA ribbon + VWAP with momentum confirmation",
            "params": {"min_body_pct": 0.35, "atr_stop_mult": 1.0, "atr_tp_mult": 2.0},
        },
        {
            "name": "combined",
            "description": "Weighted voting across all strategies",
            "params": {
                "vote_threshold": 0.50,
                "weights": {"trend": 0.50, "mean_rev": 0.20, "breakout": 0.30},
            },
        },
    ]
    return {"strategies": strategies}


@router.post("/run-backtest")
async def run_backtest(request: dict):
    """Run a bar-by-bar backtest using CombinedStrategy on Binance or yfinance data."""
    symbol = request.get("symbol", "ETHUSDT")
    strategy = request.get("strategy", "combined")
    days = int(request.get("days", 90))
    balance = float(request.get("balance", 10_000))

    try:
        from backend.backtesting_ctrader.engine import (
            BacktestEngine,
            download_bars_for_symbol,
            backtest_result_to_dict,
        )
        bars = download_bars_for_symbol(symbol, days=days)
        if len(bars) < 50:
            return {"error": f"Insufficient bars ({len(bars)}) for {symbol}", "symbol": symbol}
        engine = BacktestEngine(strategy_name=strategy, initial_balance=balance)
        result = engine.run(symbol=symbol, bars=bars)
        return backtest_result_to_dict(result)
    except Exception as e:
        logger.error(f"Backtest failed for {symbol}: {e}")
        return {
            "error": str(e),
            "symbol": symbol,
            "strategy": strategy,
            "pnl": 0,
            "trades": 0,
            "win_rate": 0,
            "sharpe": 0,
        }


@router.get("/portfolio")
async def get_portfolio():
    """Get current portfolio state with live Binance balance."""
    db = SessionLocal()
    try:
        open_trades = db.query(Trade).filter(Trade.status.in_(["open", "filled"])).all()
        positions = []
        for t in open_trades:
            positions.append({
                "id": t.id,
                "symbol": t.symbol,
                "direction": t.direction,
                "quantity": t.quantity,
                "entry_price": t.entry_price,
                "stop_loss": t.stop_loss,
                "take_profit": t.take_profit,
                "strategy": t.strategy,
                "opened_at": t.timestamp.isoformat() if t.timestamp else None,
            })

        # Prefer live broker balance over stale DB snapshot
        balance = 0.0
        available = 0.0
        equity = 0.0
        positions_value = 0.0
        try:
            from backend.services.binance_futures_service import binance_futures_broker
            bal = await asyncio.to_thread(binance_futures_broker.get_balance)
            balance = bal.get("balance", 0.0)
            available = bal.get("available", balance)
            equity = bal.get("equity", balance)
            positions_value = bal.get("margin_used", 0.0)
        except Exception:
            snap = (
                db.query(PortfolioSnapshot)
                .order_by(PortfolioSnapshot.id.desc())
                .first()
            )
            if snap:
                balance = snap.cash or 0.0
                available = snap.cash or 0.0
                equity = snap.total_value or balance
                positions_value = snap.positions_value or 0.0

        # Compute realized PnL from all closed trades
        closed_pnl = db.query(Trade).filter(Trade.status == "closed").with_entities(
            Trade.pnl
        ).all()
        total_pnl = round(sum((r.pnl or 0.0) for r in closed_pnl), 4)
        pnl_pct = round((total_pnl / equity * 100) if equity > 0 else 0.0, 2)

        return {
            "balance": round(balance, 6),
            "available": round(available, 6),
            "equity": round(equity, 6),
            "positions": positions,
            "total_pnl": total_pnl,
            "total_pnl_pct": pnl_pct,
            "positions_value": round(positions_value, 6),
            "open_positions_count": len(positions),
            "last_updated": datetime.now().isoformat(),
        }
    finally:
        db.close()


@router.get("/performance")
async def get_performance():
    """Rolling performance: win rate, realized PnL, drawdown, trade counts."""
    db = SessionLocal()
    try:
        closed = db.query(Trade).filter(Trade.status == "closed").all()
        wins = sum(1 for t in closed if (t.pnl or 0) > 0)
        losses = sum(1 for t in closed if (t.pnl or 0) < 0)
        decided = wins + losses
        realized = round(sum((t.pnl or 0.0) for t in closed), 4)
        equity = 0.0
        try:
            from backend.services.binance_futures_service import binance_futures_broker
            bal = await asyncio.to_thread(binance_futures_broker.get_balance)
            equity = bal.get("equity", bal.get("balance", 0.0))
        except Exception:
            pass
        peak = db.query(PortfolioSnapshot.total_value).order_by(
            PortfolioSnapshot.total_value.desc()
        ).first()
        peak_val = (peak[0] if peak else 0.0) or equity
        drawdown_pct = round(((equity - peak_val) / peak_val * 100.0), 3) if peak_val > 0 else 0.0
        return {
            "win_rate": round((wins / decided * 100.0), 2) if decided else 0.0,
            "wins": wins,
            "losses": losses,
            "total_trades": len(closed),
            "realized_pnl": realized,
            "equity": round(equity, 4),
            "drawdown_pct": drawdown_pct,
        }
    finally:
        db.close()


@router.get("/signals")
async def get_recent_signals():
    """Get recent trading signals."""
    db = SessionLocal()
    try:
        signals = (
            db.query(TradingSignal)
            .order_by(TradingSignal.id.desc())
            .limit(20)
            .all()
        )
        return {
            "signals": [
                {
                    "id": s.id,
                    "timestamp": s.timestamp.isoformat() if s.timestamp else None,
                    "symbol": s.symbol,
                    "strategy": s.strategy,
                    "direction": s.direction,
                    "confidence": s.confidence,
                    "entry_price": s.entry_price,
                    "stop_loss": s.stop_loss,
                    "take_profit": s.take_profit,
                    "status": s.status,
                    "reasoning": s.reasoning,
                    "ai_analysis": json.loads(s.ai_analysis) if s.ai_analysis else None,
                }
                for s in signals
            ]
        }
    finally:
        db.close()


@router.get("/status")
async def get_status():
    """Get system status with real configuration."""
    load_dotenv(override=True)

    # Check which LLM providers are configured
    llm_providers = []

    # Cloud providers
    if os.getenv('XAI_API_KEY') and os.getenv('XAI_API_KEY') != 'your_xai_api_key_here':
        llm_providers.append({'name': 'xAI (Grok)', 'model': os.getenv('XAI_MODEL', 'grok-beta'), 'status': 'configured', 'type': 'cloud'})
    if os.getenv('KIE_API_KEY') and os.getenv('KIE_API_KEY') != 'your_kie_api_key_here':
        llm_providers.append({
            'name': 'Kie.ai',
            'model': os.getenv('KIE_MODEL', 'claude-sonnet-4-6'),
            'status': 'configured',
            'type': 'cloud',
            'role': 'primary via LiteLLM',
        })
    if os.getenv('ANTHROPIC_API_KEY') and os.getenv('ANTHROPIC_API_KEY') != 'your_anthropic_api_key_here':
        llm_providers.append({'name': 'Anthropic', 'model': 'claude', 'status': 'configured', 'type': 'cloud'})
    if os.getenv('OPENAI_API_KEY') and os.getenv('OPENAI_API_KEY') != 'your_openai_api_key_here':
        llm_providers.append({'name': 'OpenAI', 'model': 'gpt-4o', 'status': 'configured', 'type': 'cloud'})
    if os.getenv('GROQ_API_KEY') and os.getenv('GROQ_API_KEY') != 'your_groq_api_key_here':
        llm_providers.append({'name': 'Groq', 'model': 'mixtral', 'status': 'configured', 'type': 'cloud'})
    if os.getenv('GOOGLE_API_KEY') and os.getenv('GOOGLE_API_KEY') != 'your_google_api_key_here':
        llm_providers.append({'name': 'Google', 'model': 'gemini', 'status': 'configured', 'type': 'cloud'})

    # Ollama (local models) — skip when pointed at the LiteLLM proxy, which is
    # an OpenAI-compatible endpoint and has no Ollama /api/tags route.
    ollama_url = os.getenv('OLLAMA_BASE_URL', '')
    if ollama_url and 'litellm' not in ollama_url.lower():
        try:
            resp = httpx.get(f"{ollama_url}/api/tags", timeout=5)
            if resp.status_code == 200:
                models = resp.json().get('models', [])
                role_map = {
                    os.getenv('OLLAMA_PRIMARY_MODEL', 'phi3.5'): 'Primary (Reasoning)',
                    os.getenv('OLLAMA_SECONDARY_MODEL', 'phi4'): 'Secondary (Fallback)',
                    os.getenv('OLLAMA_LIGHTWEIGHT_MODEL', 'phi3.5'): 'Lightweight (Fast)',
                }
                for m in models:
                    mname = m.get('name', '')
                    role = 'Local'
                    for key, val in role_map.items():
                        if mname.startswith(key):
                            role = val
                            break
                    size_gb = m.get('size', 0) / (1024**3)
                    param_size = m.get('details', {}).get('parameter_size', '')
                    llm_providers.append({
                        'name': f"Ollama: {mname.split(':')[0]}",
                        'model': f"{mname} ({param_size}, {size_gb:.1f}GB)",
                        'status': 'configured',
                        'type': 'local',
                        'role': role,
                    })
        except Exception:
            llm_providers.append({'name': 'Ollama', 'model': 'connection failed', 'status': 'error', 'type': 'local'})

    # Check brokers
    brokers = []
    if os.getenv('BINANCE_API_KEY'):
        brokers.append({'name': 'Binance', 'env': 'testnet' if os.getenv('BINANCE_TESTNET', 'true') == 'true' else 'live', 'status': 'configured'})
    else:
        brokers.append({'name': 'Binance', 'env': 'testnet', 'status': 'not_configured'})
    if os.getenv('CTRADER_ACCESS_TOKEN'):
        from backend.services.ctrader_service import ctrader_broker
        os.getenv('CTRADER_ENV', 'demo') == 'live'
        status_str = 'configured'
        if ctrader_broker.is_connected:
            status_str = 'online'
        elif ctrader_broker.dry_run:
            status_str = 'configured' # default to configured if not explicitly connecting
            
        brokers.append({'name': 'cTrader', 'env': os.getenv('CTRADER_ENV', 'demo'), 'status': status_str})
    else:
        brokers.append({'name': 'cTrader', 'env': 'demo', 'status': 'not_configured'})
    if os.getenv('ALPACA_API_KEY'):
        brokers.append({'name': 'Alpaca', 'env': 'paper' if os.getenv('ALPACA_PAPER', 'true') == 'true' else 'live', 'status': 'configured'})
    else:
        brokers.append({'name': 'Alpaca', 'env': 'paper', 'status': 'not_configured'})
    if os.getenv('OANDA_API_KEY'):
        brokers.append({'name': 'OANDA', 'env': 'practice' if os.getenv('OANDA_PRACTICE', 'true') == 'true' else 'live', 'status': 'configured'})
    else:
        brokers.append({'name': 'OANDA', 'env': 'practice', 'status': 'not_configured'})

    # Check data providers
    data_providers = []
    data_providers.append({'name': 'Binance (Public)', 'status': 'public', 'note': 'No key needed'})
    data_providers.append({'name': 'yfinance (Public)', 'status': 'public', 'note': 'No key needed'})
    for name, env_key in [('CoinGecko', 'COINGECKO_API_KEY'), ('CoinMarketCap', 'COINMARKETCAP_API_KEY'),
                           ('Alpha Vantage', 'ALPHAVANTAGE_API_KEY'), ('Twelve Data', 'TWELVEDATA_API_KEY'),
                           ('Polygon.io', 'POLYGON_API_KEY'), ('FRED', 'FRED_API_KEY'),
                           ('NewsAPI', 'NEWSAPI_KEY'), ('LunarCrush', 'LUNARCRUSH_API_KEY')]:
        data_providers.append({'name': name, 'status': 'configured' if os.getenv(env_key) else 'not_configured'})

    # Risk settings — read from the SAME RiskConfig the trading loop uses so the
    # UI can never disagree with the live engine.
    from backend.services.risk_config import get_risk_config
    _rc = get_risk_config()
    risk_config = {
        'risk_per_trade': _rc.risk_per_trade_pct,
        'max_positions': _rc.max_positions,
        'min_signal_strength': _rc.min_signal_strength,
        'ai_analysis_threshold': _rc.ai_analysis_threshold,
        'min_risk_reward': float(os.getenv('MIN_RISK_REWARD', '1.5')),
        'equity_sizing': _rc.equity_sizing_enabled,
        # Back-compat: the UI "risk-based sizing" indicator. Now reflects the
        # real equity/risk sizing rather than the previously-fake Kelly flag.
        'use_kelly': _rc.equity_sizing_enabled,
        'vix_threshold': float(os.getenv('VIX_THRESHOLD', '25.0')),
        'weights': {
            'technical': float(os.getenv('WEIGHT_TECHNICAL', '0.50')),
            'sentiment': float(os.getenv('WEIGHT_SENTIMENT', '0.25')),
            'macro': float(os.getenv('WEIGHT_MACRO', '0.25'))
        }
    }

    from backend.services.trading_mode import get_trading_mode
    _trading_mode = get_trading_mode()

    # Monitoring flags
    telegram = bool(os.getenv('TELEGRAM_BOT_TOKEN') and os.getenv('TELEGRAM_CHAT_ID'))
    influxdb = bool(os.getenv('INFLUXDB_TOKEN'))
    n8n = bool(os.getenv('N8N_WEBHOOK_URL'))

    return {
        'backend': 'online',
        'strategies_loaded': 5,
        'dry_run': _trading_mode.value != 'live',
        'mode': _trading_mode.value,
        'active_broker': os.getenv('ACTIVE_BROKER', 'binance_futures'),
        'llm_providers': llm_providers,
        'brokers': brokers,
        'data_providers': data_providers,
        'risk_config': risk_config,
        'telegram': telegram,
        'influxdb': influxdb,
        'n8n': n8n,
        'uptime': 'running',
        'last_cycle': trading_loop.status.get('last_cycle'),
        'trading_loop': trading_loop.status,
    }


@router.get("/config", response_model=TradingConfigResponse)
async def get_config():
    """Get current configuration limits and status."""
    from backend.services.risk_config import get_trading_mode, get_risk_config
    mode = get_trading_mode()
    config = get_risk_config()
    risk_limits = {
        "max_positions": config.max_positions,
        "max_directional_exposure_usdt": config.max_directional_exposure_usdt,
        "trade_usdt_amount": config.trade_usdt_amount,
        "kill_floor_usdt": config.kill_floor_usdt,
        "min_signal_strength": config.min_signal_strength,
        "sl_cooldown_minutes": config.sl_cooldown_minutes,
        "emergency_drawdown_pct": config.emergency_drawdown_pct,
        "use_risk_reviewer_llm": config.use_risk_reviewer_llm,
        "enable_personas": config.enable_personas,
    }
    return TradingConfigResponse(
        mode=mode,
        interval_minutes=trading_loop._interval_minutes,
        symbols=trading_loop._symbols,
        risk_limits=risk_limits,
    )


@router.post("/config/update")
async def update_config(payload: ConfigUpdateRequest):
    """Persist supported toggles to the mounted production .env."""
    updates = {}
    if payload.use_risk_reviewer_llm is not None:
        updates["USE_RISK_REVIEWER_LLM"] = str(payload.use_risk_reviewer_llm).lower()
    if payload.enable_personas is not None:
        updates["ENABLE_PERSONAS"] = str(payload.enable_personas).lower()
    if updates:
        env_path = Path(os.getenv("ENV_FILE_PATH", "/app/.env"))
        existing = env_path.read_text().splitlines() if env_path.exists() else []
        output = []
        remaining = dict(updates)
        for line in existing:
            key = line.split("=", 1)[0] if "=" in line else ""
            if key in remaining:
                output.append(f"{key}={remaining.pop(key)}")
            else:
                output.append(line)
        output.extend(f"{key}={value}" for key, value in remaining.items())
        env_path.write_text("\n".join(output) + "\n")
        os.environ.update(updates)

    config = refresh_risk_config()
    return {
        "status": "success",
        "config": {
            "use_risk_reviewer_llm": config.use_risk_reviewer_llm,
            "enable_personas": config.enable_personas
        }
    }


# ── Market Data Feeds ────────────────────────────────────────────────────────

@router.get("/markets/stocks")
async def get_stocks():
    """Fetch live data for top stocks using yfinance."""
    import yfinance as yf
    symbols = ["SPY", "AAPL", "MSFT", "NVDA", "TSLA", "META"]
    data = []
    for sym in symbols:
        try:
            ticker = yf.Ticker(sym)
            hist = ticker.history(period="2d")
            if len(hist) >= 2:
                prev_close = hist["Close"].iloc[-2]
                current = hist["Close"].iloc[-1]
                change_pct = ((current - prev_close) / prev_close) * 100
                vol = float(hist["Volume"].iloc[-1])
            elif len(hist) == 1:
                current = hist["Close"].iloc[-1]
                change_pct = 0.0
                vol = float(hist["Volume"].iloc[-1])
            else:
                continue
                
            data.append({
                "symbol": sym,
                "price": float(current),
                "change24h": round(float(change_pct), 2),
                "volume24h": float(vol),
                "up": bool(change_pct >= 0)
            })
        except Exception:
            pass
    return {"data": data}




# Binance public spot endpoints the frontend is allowed to proxy through us.
_BINANCE_PROXY_ALLOWED = {
    "ticker/24hr", "ticker/price", "ticker/bookTicker",
    "klines", "depth", "exchangeInfo", "avgPrice",
}

# Proxy protection state: short-TTL response cache, in-flight coalescing and a
# global backoff window honoured after any 418/429 from Binance. All dashboard
# traffic funnels through this proxy from one VPS IP — without these guards a
# few open dashboards can burn the IP-weight budget and get the *trading*
# engine IP-banned (-1003, 10-60 min cooldown).
_binance_proxy_cache: Dict[str, tuple] = {}
_binance_proxy_inflight: Dict[str, "asyncio.Future"] = {}
_binance_proxy_backoff_until: float = 0.0
_BINANCE_PROXY_TTL = {
    "ticker/24hr": 10.0, "ticker/price": 5.0, "ticker/bookTicker": 5.0,
    "klines": 20.0, "depth": 5.0, "avgPrice": 10.0, "exchangeInfo": 300.0,
}


# Proxy endpoint moved below to avoid shadowing static routes


# ── Trading Loop Control ──────────────────────────────────────────────────────

@router.post("/loop/start", response_model=LoopStatusResponse)
async def start_loop(req: StartLoopRequest = None):
    """Start the automated trading loop."""
    if req is None:
        req = StartLoopRequest()
    await trading_loop.start(
        interval_minutes=req.interval_minutes,
        symbols=req.symbols,
        strategy=req.strategy,
    )
    return LoopStatusResponse(**trading_loop.status)


@router.post("/loop/stop", response_model=LoopStatusResponse)
async def stop_loop():
    """Stop the automated trading loop."""
    await trading_loop.stop()
    return LoopStatusResponse(**trading_loop.status)


@router.get("/loop/status", response_model=LoopStatusResponse)
async def loop_status():
    """Get trading loop status."""
    return LoopStatusResponse(**trading_loop.status)


# ── Positions ─────────────────────────────────────────────────────────────────

def _fetch_mark_prices_for_symbols(symbols: set[str]) -> dict[str, float]:
    """Mark prices for open positions — Binance when live, else yfinance."""
    prices: dict[str, float] = {}
    if os.getenv("ACTIVE_BROKER", "ctrader") == "binance_futures":
        try:
            from backend.services.binance_futures_service import binance_futures_broker
            for p in binance_futures_broker.get_positions():
                sym = p.get("symbol")
                mark = float(p.get("mark_price") or 0)
                if sym and mark > 0:
                    prices[sym] = mark
        except Exception:
            pass
        missing = symbols - set(prices.keys())
        if missing:
            try:
                from backend.services.binance_futures_service import BinanceFuturesService
                bfs = BinanceFuturesService()
                client = bfs._get_client()
                for sym in missing:
                    info = client.futures_mark_price(symbol=sym)
                    mark = float(info.get("markPrice") or 0)
                    if mark > 0:
                        prices[sym] = mark
            except Exception:
                pass
        return prices

    import yfinance as yf
    for sym in symbols:
        try:
            yf_sym = _to_yfinance_symbol(sym)
            hist = yf.Ticker(yf_sym).history(period="5d")
            if not hist.empty:
                prices[sym] = float(hist["Close"].iloc[-1])
        except Exception:
            pass
    return prices


@router.get("/positions")
async def get_positions():
    """Get all open positions with current P&L."""
    db = SessionLocal()
    try:
        open_trades = db.query(Trade).filter(Trade.status.in_(["open", "filled"])).all()
        mark_prices = _fetch_mark_prices_for_symbols({t.symbol for t in open_trades})
        positions = []
        for t in open_trades:
            current_price = mark_prices.get(t.symbol) or t.entry_price

            if t.direction == "BUY":
                unrealized_pnl = (current_price - t.entry_price) * t.quantity
            else:
                unrealized_pnl = (t.entry_price - current_price) * t.quantity

            pnl_pct = 0.0
            if t.entry_price and t.quantity and t.entry_price * t.quantity > 0:
                pnl_pct = (unrealized_pnl / (t.entry_price * t.quantity)) * 100

            positions.append({
                "id": t.id,
                "symbol": t.symbol,
                "direction": t.direction,
                "quantity": t.quantity,
                "entry_price": t.entry_price,
                "current_price": current_price,
                "stop_loss": t.stop_loss,
                "take_profit": t.take_profit,
                "unrealized_pnl": round(unrealized_pnl, 2),
                "unrealized_pnl_pct": round(pnl_pct, 2),
                "strategy": t.strategy,
                "opened_at": t.timestamp.isoformat() if t.timestamp else None,
            })
        return {"positions": positions, "count": len(positions)}
    finally:
        db.close()


# ── Trade History ─────────────────────────────────────────────────────────────

@router.get("/trades")
async def get_trades(
    symbol: Optional[str] = Query(None),
    strategy: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Get trade history with filtering."""
    db = SessionLocal()
    try:
        q = db.query(Trade).order_by(Trade.id.desc())
        if symbol:
            q = q.filter(Trade.symbol == symbol)
        if strategy:
            q = q.filter(Trade.strategy == strategy)
        if status:
            q = q.filter(Trade.status == status)

        total = q.count()
        trades = q.offset(offset).limit(limit).all()

        return {
            "trades": [
                {
                    "id": t.id,
                    "timestamp": t.timestamp.isoformat() if t.timestamp else None,
                    "closed_at": t.closed_at.isoformat() if t.closed_at else None,
                    "symbol": t.symbol,
                    "direction": t.direction,
                    "quantity": t.quantity,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "stop_loss": t.stop_loss,
                    "take_profit": t.take_profit,
                    "status": t.status,
                    "pnl": t.pnl,
                    "strategy": t.strategy,
                    "notes": t.notes,
                }
                for t in trades
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    finally:
        db.close()


# ── Signals History ───────────────────────────────────────────────────────────

@router.get("/signals/history")
async def get_signals_history(
    symbol: Optional[str] = Query(None),
    strategy: Optional[str] = Query(None),
    direction: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """Get all generated signals with pagination."""
    db = SessionLocal()
    try:
        q = db.query(TradingSignal).order_by(TradingSignal.id.desc())
        if symbol:
            q = q.filter(TradingSignal.symbol == symbol)
        if strategy:
            q = q.filter(TradingSignal.strategy == strategy)
        if direction:
            q = q.filter(TradingSignal.direction == direction)

        total = q.count()
        signals = q.offset(offset).limit(limit).all()

        return {
            "signals": [
                {
                    "id": s.id,
                    "timestamp": s.timestamp.isoformat() if s.timestamp else None,
                    "symbol": s.symbol,
                    "strategy": s.strategy,
                    "direction": s.direction,
                    "confidence": s.confidence,
                    "entry_price": s.entry_price,
                    "stop_loss": s.stop_loss,
                    "take_profit": s.take_profit,
                    "status": s.status,
                    "reasoning": s.reasoning,
                    "ai_analysis": json.loads(s.ai_analysis) if s.ai_analysis else None,
                }
                for s in signals
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    finally:
        db.close()


# ── Portfolio History ─────────────────────────────────────────────────────────

@router.get("/portfolio/history")
async def get_portfolio_history(
    limit: int = Query(100, ge=1, le=1000),
):
    """Get portfolio snapshots for equity curve."""
    db = SessionLocal()
    try:
        snapshots = (
            db.query(PortfolioSnapshot)
            .order_by(PortfolioSnapshot.id.desc())
            .limit(limit)
            .all()
        )
        snapshots.reverse()
        return {
            "snapshots": [
                {
                    "id": s.id,
                    "timestamp": s.timestamp.isoformat() if s.timestamp else None,
                    "total_value": s.total_value,
                    "cash": s.cash,
                    "positions_value": s.positions_value,
                    "total_pnl": s.total_pnl,
                    "open_positions": s.open_positions,
                    "cycle_number": s.cycle_number,
                }
                for s in snapshots
            ],
            "count": len(snapshots),
        }
    finally:
        db.close()


# ── AI Analysis Pipeline ─────────────────────────────────────────────────────

@router.get("/models")
async def get_ai_models():
    """Show which AI models are configured and their roles."""
    return ai_analysis_service.models_info


@router.get("/analysis/{signal_id}")
async def get_signal_analysis(signal_id: int):
    """Get full AI analysis for a specific signal."""
    db = SessionLocal()
    try:
        signal = db.query(TradingSignal).filter(TradingSignal.id == signal_id).first()
        if not signal:
            return {"error": "Signal not found"}
        if signal.ai_analysis:
            return json.loads(signal.ai_analysis)
        return {"error": "No AI analysis available for this signal"}
    finally:
        db.close()


@router.post("/analyze")
async def run_analysis_on_demand(request: dict):
    """Run AI analysis on-demand for a symbol."""
    symbol = request.get("symbol", "BTC-USD")

    if not ai_analysis_service.enabled:
        return {"error": "AI analysis is disabled. Set AI_ANALYSIS_ENABLED=true in .env"}

    # Fetch bars using the trading loop's method
    import yfinance as yf
    try:
        yf_sym = _to_yfinance_symbol(symbol)
        ticker = yf.Ticker(yf_sym)
        df = ticker.history(period="30d", interval="1h")
        if df.empty:
            df = ticker.history(period="60d", interval="1d")
        if df.empty:
            return {"error": f"Could not fetch data for {symbol}"}

        bars = []
        for idx, row in df.iterrows():
            bars.append({
                "timestamp": int(idx.timestamp()) if hasattr(idx, 'timestamp') else 0,
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row.get("Volume", 0)),
            })
    except Exception as e:
        return {"error": f"Failed to fetch data for {symbol}: {str(e)}"}

    # Run AI analysis
    try:
        result = await ai_analysis_service.analyze_symbol(symbol, bars)

        # Store as a signal in DB
        db = SessionLocal()
        try:
            db_signal = TradingSignal(
                symbol=symbol,
                strategy="ai_analysis",
                direction=result.get('direction', 'HOLD'),
                confidence=result.get('confidence', 0.0),
                entry_price=result.get('entry_price'),
                stop_loss=result.get('stop_loss'),
                take_profit=result.get('take_profit'),
                status="ai_analyzed",
                reasoning=result.get('reasoning', ''),
                ai_analysis=json.dumps(result),
            )
            db.add(db_signal)
            db.commit()
            result['signal_id'] = db_signal.id
        finally:
            db.close()

        return result
    except Exception as e:
        return {"error": f"AI analysis failed: {str(e)}"}


@router.get("/ctrader/status")
async def ctrader_status():
    """Get cTrader broker status."""
    from backend.services.ctrader_service import ctrader_broker
    return ctrader_broker.status()


@router.post("/ctrader/enable")
async def enable_ctrader_live():
    """Enable real cTrader trading (disable DRY_RUN for cTrader)."""
    from backend.services.ctrader_service import ctrader_broker
    import asyncio
    loop = asyncio.get_event_loop()
    connected = await loop.run_in_executor(None, ctrader_broker.connect)
    st = ctrader_broker.status()
    return {
        "message": "cTrader live mode enabled" if connected else "cTrader connection failed",
        "connected": connected,
        "dry_run": st["dry_run"],
        "env": st["env"],
    }


@router.post("/ctrader/disable")
async def disable_ctrader_live():
    """Disable real cTrader trading (enable DRY_RUN)."""
    from backend.services.ctrader_service import ctrader_broker
    ctrader_broker.disconnect()
    return {"message": "cTrader paper mode enabled", "dry_run": True}


# ── Position Management ────────────────────────────────────────────────────────

@router.post("/positions/{position_id}/close")
async def close_position(position_id: int):
    """Close an exchange position layer, then persist the actual fill."""
    db = SessionLocal()
    try:
        trade = db.query(Trade).filter(Trade.id == position_id, Trade.status.in_(["open", "filled"])).first()
        if not trade:
            raise HTTPException(status_code=404, detail=f"Open position {position_id} not found")

        close_side = OrderSide.SELL if trade.direction == "BUY" else OrderSide.BUY
        response = UnifiedTrading().place_order(UnifiedOrder(
            symbol=trade.symbol,
            side=close_side,
            order_type=OrderType.MARKET,
            quantity=trade.quantity,
            reduce_only=True,
        ))
        if not response.success:
            raise HTTPException(
                status_code=502,
                detail=f"Exchange close failed; DB left open: {response.message}",
            )

        exit_price = response.filled_price
        if not exit_price:
            from backend.services.binance_futures_service import binance_futures_broker
            exit_price = binance_futures_broker.get_exit_price(trade.symbol)

        from backend.services.trading_loop_helpers import is_plausible_exit_price
        if is_plausible_exit_price(trade.entry_price, exit_price):
            if response.realized_pnl is not None:
                pnl = float(response.realized_pnl) - response.commission
            else:
                if trade.direction == "BUY":
                    pnl = (exit_price - trade.entry_price) * trade.quantity
                else:
                    pnl = (trade.entry_price - exit_price) * trade.quantity
                pnl -= response.commission
            trade.exit_price = exit_price
            trade.pnl = round(pnl, 4)
        else:
            # Exchange is flat but the fill price is unavailable: close the row
            # honestly with unknown P&L rather than inventing a yfinance price.
            trade.exit_price = None
            trade.pnl = None
        trade.status = "closed"
        trade.closed_at = datetime.now(timezone.utc)
        trade.notes = (trade.notes or "") + " | Closed on exchange via manual dashboard"
        db.commit()
        return {
            "success": True,
            "position_id": position_id,
            "exit_price": trade.exit_price,
            "pnl": trade.pnl,
            "message": f"Position {position_id} closed on exchange",
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.put("/positions/{position_id}/modify")
async def modify_position(position_id: int, body: ModifyPositionRequest):
    """Modify exchange SL/TP first, then persist levels that succeeded."""
    db = SessionLocal()
    try:
        trade = db.query(Trade).filter(Trade.id == position_id, Trade.status.in_(["open", "filled"])).first()
        if not trade:
            raise HTTPException(status_code=404, detail=f"Open position {position_id} not found")

        from backend.services.binance_futures_service import binance_futures_broker
        results = {}
        failures = []
        if body.stop_loss is not None:
            result = binance_futures_broker.replace_stop_loss(
                trade.symbol, trade.direction, body.stop_loss,
            )
            results["stop_loss"] = result
            if result.get("status") in {"replaced", "simulated"}:
                trade.stop_loss = body.stop_loss
            else:
                failures.append(f"SL: {result.get('reason') or result.get('message')}")
        if body.take_profit is not None:
            result = binance_futures_broker.replace_take_profit(
                trade.symbol, trade.direction, body.take_profit,
            )
            results["take_profit"] = result
            if result.get("status") in {"replaced", "simulated"}:
                trade.take_profit = body.take_profit
            else:
                failures.append(f"TP: {result.get('reason') or result.get('message')}")

        db.commit()
        if failures:
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "One or more exchange protection updates failed",
                    "failures": failures,
                    "results": results,
                },
            )
        return {
            "success": True,
            "position_id": position_id,
            "stop_loss": trade.stop_loss,
            "take_profit": trade.take_profit,
            "exchange_results": results,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ── Binance Futures Endpoints ─────────────────────────────────────────────────

@router.get("/binance/status")
async def binance_status():
    """Get Binance Futures broker status: wallet, positions, open orders."""
    try:
        from backend.services.binance_futures_service import binance_futures_broker as bf
        active_broker = os.getenv("ACTIVE_BROKER", "ctrader")
        balance   = bf.get_balance()
        positions = bf.get_positions()
        orders    = bf.get_open_orders()
        return {
            "broker":       "binance_futures",
            "active":       active_broker == "binance_futures",
            "testnet":      os.getenv("BINANCE_TESTNET", "false") == "true",
            "leverage":     int(os.getenv("BINANCE_LEVERAGE", "10")),
            "margin_type":  bf.margin_type,
            "wallet": {
                "balance":         balance.get("balance", 0),
                "available":       balance.get("available", 0),
                "equity":          balance.get("equity", 0),
                "unrealized_pnl":  balance.get("unrealized_pnl", 0),
                "margin_used":     balance.get("margin_used", 0),
            },
            "positions":     positions,
            "open_orders":   orders,
            "positions_count": len(positions),
            "orders_count":    len(orders),
        }
    except Exception as e:
        return {"broker": "binance_futures", "active": False, "error": str(e)}


@router.get("/binance/wallet")
async def binance_wallet():
    """Get Binance Futures USDT-M wallet balance."""
    try:
        from backend.services.binance_futures_service import binance_futures_broker as bf
        return bf.get_balance()
    except Exception as e:
        return {"error": str(e)}


@router.get("/binance/positions")
async def binance_positions():
    """Get all open Binance Futures positions."""
    try:
        from backend.services.binance_futures_service import binance_futures_broker as bf
        return {"positions": bf.get_positions()}
    except Exception as e:
        return {"error": str(e)}


@router.post("/binance/enable")
async def binance_enable():
    """Switch active broker to Binance Futures."""
    os.environ["ACTIVE_BROKER"] = "binance_futures"
    return {"status": "ok", "active_broker": "binance_futures"}


@router.post("/binance/disable")
async def binance_disable():
    """Switch active broker back to paper/ctrader."""
    os.environ["ACTIVE_BROKER"] = "ctrader"
    return {"status": "ok", "active_broker": "ctrader"}


@router.get("/binance/{endpoint:path}")
async def binance_proxy(endpoint: str, request: Request):
    """Server-side proxy for Binance public spot market data.

    The dashboard queries api.binance.com directly from the browser, which
    fails where Binance is geo-blocked (e.g. US/EEA IPs return HTTP 451) or via
    CORS. The backend reaches Binance reliably, so the frontend falls back to
    this passthrough. Only read-only public market-data endpoints are allowed.
    """
    endpoint = endpoint.strip("/")
    if endpoint not in _BINANCE_PROXY_ALLOWED:
        return JSONResponse({"error": f"endpoint not allowed: {endpoint}"}, status_code=400)

    params = dict(request.query_params)
    key = endpoint + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    now = asyncio.get_event_loop().time()

    # 1) Global backoff: if Binance told us to back off, fail fast locally.
    if now < _binance_proxy_backoff_until:
        retry_in = int(_binance_proxy_backoff_until - now) + 1
        return JSONResponse(
            {"error": "binance rate limited, backing off", "retry_after": retry_in},
            status_code=429,
            headers={"Retry-After": str(retry_in)},
        )

    # 2) Serve from short TTL cache (dashboard polls are highly repetitive).
    cached = _binance_proxy_cache.get(key)
    if cached and now - cached[0] < _BINANCE_PROXY_TTL.get(endpoint, 10.0):
        return JSONResponse(content=cached[1], status_code=200)

    # 3) Coalesce identical concurrent requests into one upstream call.
    fut = _binance_proxy_inflight.get(key)
    if fut is None:
        fut = asyncio.get_event_loop().create_future()
        _binance_proxy_inflight[key] = fut
        try:
            resp = None
            # data-api.binance.vision is Binance's dedicated public market-data
            # host: keeps dashboard traffic off the IP-weight budget of
            # api.binance.com that the live trading engine depends on.
            for host in ("https://data-api.binance.vision", "https://api.binance.com"):
                try:
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        resp = await client.get(f"{host}/api/v3/{endpoint}", params=params)
                    if resp.status_code < 500:
                        break
                except Exception:
                    resp = None
                    continue
            if resp is None:
                raise RuntimeError("all binance hosts unreachable")
            if resp.status_code in (418, 429):
                retry_after = int(resp.headers.get("Retry-After", "60"))
                globals()["_binance_proxy_backoff_until"] = (
                    asyncio.get_event_loop().time() + min(retry_after, 3600)
                )
                fut.set_result((resp.status_code, {"error": "binance rate limited", "retry_after": retry_after}))
            else:
                body = resp.json()
                if resp.status_code == 200:
                    _binance_proxy_cache[key] = (asyncio.get_event_loop().time(), body)
                    if len(_binance_proxy_cache) > 512:
                        oldest = min(_binance_proxy_cache, key=lambda k: _binance_proxy_cache[k][0])
                        _binance_proxy_cache.pop(oldest, None)
                fut.set_result((resp.status_code, body))
        except Exception as exc:
            fut.set_result((502, {"error": f"binance proxy failed: {exc}"}))
        finally:
            _binance_proxy_inflight.pop(key, None)

    status, body = await fut
    return JSONResponse(content=body, status_code=status)


@router.get("/crypto-news")
async def get_crypto_news(limit: int = 20):
    """
    Fetch real crypto news via yfinance for major symbols.
    Used by n8n sentiment pipeline. No API key required.
    Only major coins - no meme coins.
    """
    import yfinance as yf
    from datetime import datetime, timezone

    # Use configured trading symbols (crypto only, no forex)
    symbols_env = os.getenv('TRADING_SYMBOLS', 'BTC-USD,ETH-USD,SOL-USD,BNB-USD,XRP-USD')
    symbols = [s.strip() for s in symbols_env.split(',') if '-USD' in s][:6]
    if not symbols:
        symbols = ['BTC-USD', 'ETH-USD', 'SOL-USD', 'BNB-USD', 'XRP-USD']

    SYMBOL_MAP = {
        'BTC-USD': 'BTC', 'ETH-USD': 'ETH', 'SOL-USD': 'SOL',
        'BNB-USD': 'BNB', 'XRP-USD': 'XRP', 'ADA-USD': 'ADA',
        'AVAX-USD': 'AVAX', 'DOT-USD': 'DOT', 'LINK-USD': 'LINK',
    }

    seen_ids = set()
    articles = []

    for symbol in symbols:
        try:
            ticker = yf.Ticker(symbol)
            news = ticker.news or []
            crypto_sym = SYMBOL_MAP.get(symbol, symbol.replace('-USD', ''))

            for item in news:
                article_id = item.get('id', '')
                if article_id in seen_ids:
                    continue
                seen_ids.add(article_id)

                content = item.get('content', {})
                title = content.get('title', item.get('title', ''))
                summary = content.get('summary', content.get('description', ''))
                pub_date_str = content.get('pubDate', content.get('displayTime', ''))

                # Parse timestamp
                try:
                    if pub_date_str:
                        dt = datetime.fromisoformat(pub_date_str.replace('Z', '+00:00'))
                        published_at = int(dt.timestamp())
                    else:
                        published_at = int(datetime.now(timezone.utc).timestamp())
                except Exception:
                    published_at = int(datetime.now(timezone.utc).timestamp())

                if not title:
                    continue

                # Determine which symbols are mentioned
                all_syms = list(SYMBOL_MAP.values())
                text = f"{title} {summary}".upper()
                mentioned = [s for s in all_syms if s in text]
                if crypto_sym not in mentioned:
                    mentioned.insert(0, crypto_sym)

                articles.append({
                    'id': article_id or f'{symbol}_{published_at}',
                    'title': title,
                    'body': summary,
                    'source': 'yfinance',
                    'published_at': published_at,
                    'categories': 'Crypto',
                    'url': content.get('canonicalUrl', {}).get('url', '') if isinstance(content.get('canonicalUrl'), dict) else '',
                    'symbols': mentioned[:3],
                })
        except Exception as e:
            logger.warning(f"crypto-news error for {symbol}: {e}")

    # Sort by publication time, newest first
    articles.sort(key=lambda x: x['published_at'], reverse=True)
    articles = articles[:limit]

    return {'articles': articles, 'count': len(articles), 'symbols': symbols}


# ═══════════════════════════════════════════════════════════════════════════════
# Unified Trading Routes  (Fincept port)
# ════════════════════════════════════════════════════════════════════════════════


@router.post("/session/init")
async def init_session(request: dict):
    """Initialize a trading session (paper or live)."""
    broker = request.get("broker", "binance_futures")
    mode = request.get("mode", "paper")
    balance = float(request.get("paper_balance", 100_000.0))
    leverage = float(request.get("leverage", 1.0))
    ut = UnifiedTrading()
    session = ut.init_session(broker=broker, mode=mode, paper_balance=balance, leverage=leverage)
    return {
        "broker": session.broker,
        "mode": session.mode,
        "paper_portfolio_id": session.paper_portfolio_id,
    }


@router.get("/session/status")
async def session_status():
    """Get current trading session status."""
    ut = UnifiedTrading()
    sess = ut.get_session()
    if not sess:
        return {"active": False}
    return {
        "active": True,
        "broker": sess.broker,
        "mode": sess.mode,
        "paper_portfolio_id": sess.paper_portfolio_id,
    }


@router.post("/paper/order")
async def paper_place_order(request: dict):
    """Place a paper/simulated order."""
    ut = UnifiedTrading()
    order = UnifiedOrder(
        symbol=request.get("symbol", "").upper(),
        side=OrderSide(request.get("side", "buy").lower()),
        order_type=OrderType(request.get("order_type", "market").lower()),
        quantity=float(request.get("quantity", 0)),
        price=float(request.get("price", 0) or 0),
        stop_loss=float(request.get("stop_loss", 0) or 0),
        take_profit=float(request.get("take_profit", 0) or 0),
    )
    resp = ut.place_order(order)
    return {
        "success": resp.success,
        "order_id": resp.order_id,
        "message": resp.message,
        "mode": resp.mode,
        "filled_price": resp.filled_price,
        "filled_qty": resp.filled_qty,
    }

@router.post("/order")
async def place_live_order(request: LiveOrderRequest):
    """Place a validated live order and persist its exchange fill."""
    from backend.services.sentry_state import is_trading_allowed
    if not is_trading_allowed():
        raise HTTPException(status_code=400, detail="Trading is currently halted by Sentry.")
    from backend.services.risk_config import get_risk_config
    from backend.services.decision_engine import compute_sl_tp_levels
    from backend.services.trading_loop import trading_loop

    symbol = request.symbol.upper()
    side = request.side
    direction = "BUY" if side == "buy" else "SELL"

    raw_sl = request.stop_loss
    raw_tp = request.take_profit
    stop_loss = float(raw_sl) if raw_sl not in (None, "", 0) else None
    take_profit = float(raw_tp) if raw_tp not in (None, "", 0) else None

    # Manual/workflow orders historically sent no SL/TP → naked hedge legs on Binance.
    if stop_loss is None or take_profit is None:
        bars = await trading_loop._fetch_bars(symbol)
        if bars and len(bars) >= 15:
            entry = request.price or float(bars[-1]["close"])
            stop_loss, take_profit = compute_sl_tp_levels(
                bars, direction, entry, get_risk_config(),
                signal_sl=stop_loss, signal_tp=take_profit,
            )

    ut = UnifiedTrading()
    order = UnifiedOrder(
        symbol=symbol,
        side=OrderSide(side),
        order_type=OrderType(request.order_type),
        quantity=request.quantity,
        price=request.price,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )
    resp = ut.place_order(order)
    if not resp.success:
        raise HTTPException(status_code=502, detail=f"Exchange order failed: {resp.message}")

    # Live manual orders must enter the same DB lifecycle as loop orders so
    # reconciliation, protection, risk counts, dashboard, and exits all see
    # the exchange leg. Paper mode has its own persistence engine.
    if resp.mode == "live":
        entry_price = float(resp.filled_price or request.price or 0)
        if entry_price <= 0:
            # The exchange order exists but cannot be managed honestly without
            # its fill. Flatten immediately rather than create an orphan leg.
            close_side = OrderSide.SELL if direction == "BUY" else OrderSide.BUY
            ut.place_order(UnifiedOrder(
                symbol=symbol,
                side=close_side,
                order_type=OrderType.MARKET,
                quantity=float(resp.filled_qty or request.quantity),
                reduce_only=True,
            ))
            raise HTTPException(
                status_code=502,
                detail="Exchange fill had no price; emergency close attempted",
            )

        db = SessionLocal()
        try:
            trade = Trade(
                symbol=symbol,
                direction=direction,
                quantity=float(resp.filled_qty or request.quantity),
                entry_price=entry_price,
                filled_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                status="filled",
                strategy="manual_api",
                notes="Manual live order recorded from exchange fill",
                binance_order_id=str(resp.order_id or ""),
                exchange="binance_futures",
            )
            db.add(trade)
            db.commit()
            db.refresh(trade)
            trade_id = trade.id
        except Exception as db_error:
            db.rollback()
            # Never leave a filled-but-unrecorded manual position unmanaged.
            close_side = OrderSide.SELL if direction == "BUY" else OrderSide.BUY
            close_resp = ut.place_order(UnifiedOrder(
                symbol=symbol,
                side=close_side,
                order_type=OrderType.MARKET,
                quantity=float(resp.filled_qty or request.quantity),
                reduce_only=True,
            ))
            logger.critical(
                "Manual order filled but DB persistence failed; emergency close "
                "success=%s: %s", close_resp.success, db_error,
            )
            raise HTTPException(
                status_code=500,
                detail="Order persistence failed; emergency close attempted",
            )
        finally:
            db.close()
    else:
        trade_id = None

    return {
        "success": resp.success,
        "order_id": resp.order_id,
        "message": resp.message,
        "mode": resp.mode,
        "filled_price": resp.filled_price,
        "filled_qty": resp.filled_qty,
        "trade_id": trade_id,
    }



@router.post("/paper/cancel")
async def paper_cancel_order(request: dict):
    """Cancel a paper order."""
    ut = UnifiedTrading()
    resp = ut.cancel_order(request.get("order_id", ""))
    return {
        "success": resp.success,
        "order_id": resp.order_id,
        "message": resp.message,
    }


@router.get("/paper/portfolio")
async def paper_portfolio():
    """Get current paper portfolio state."""
    ut = UnifiedTrading()
    pf = ut.get_paper_portfolio()
    stats = ut.get_paper_stats()
    return {"portfolio": pf, "stats": stats}


@router.get("/paper/positions")
async def paper_positions():
    """Get open paper positions."""
    ut = UnifiedTrading()
    positions = ut.get_paper_positions()
    return {
        "positions": [
            {
                "symbol": p.symbol,
                "side": p.side,
                "quantity": p.quantity,
                "avg_price": p.avg_price,
            }
            for p in positions
        ],
        "count": len(positions),
    }


@router.get("/paper/orders")
async def paper_orders(status: Optional[str] = Query("")):
    """Get paper orders."""
    ut = UnifiedTrading()
    orders = ut.get_paper_orders(status)
    return {"orders": orders, "count": len(orders)}


@router.get("/paper/stats")
async def paper_stats():
    """Get paper trading statistics."""
    ut = UnifiedTrading()
    return ut.get_paper_stats()


# ═══════════════════════════════════════════════════════════════════════════════
# AI Tool Execution  (Fincept LlmService port)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/ai/agent-trade")
async def ai_agent_trade(request: AgentTradeRequest):
    """
    Let the LLM trade autonomously via tool calls.
    Uses the Fincept-style tool execution loop.
    """
    from backend.services.sentry_state import is_trading_allowed
    if not is_trading_allowed():
        raise HTTPException(status_code=400, detail="Trading is currently halted by Sentry.")
    import os
    from backend.services.llm_tool_loop import LlmToolClient, build_trading_tools
    from backend.services.unified_trading import UnifiedTrading
    from backend.services.binance_market_data import binance_market_data

    prompt = request.prompt
    model = request.model or os.getenv("XAI_MODEL", "grok-beta")
    provider = request.provider

    # Pick API key based on provider
    api_key = ""
    base_url = ""
    if provider == "xai":
        api_key = os.getenv("XAI_API_KEY", "")
        base_url = "https://api.x.ai/v1"
    elif provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "")
        base_url = "https://api.openai.com/v1"
    elif provider == "groq":
        api_key = os.getenv("GROQ_API_KEY", "")
        base_url = "https://api.groq.com/openai/v1"
    elif provider == "ollama":
        api_key = "ollama"
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434") + "/v1"

    if not api_key or api_key == "your_xai_api_key_here":
        return {"error": f"API key not configured for {provider}"}

    ut = UnifiedTrading()
    # The tool is explicitly paper-only. Never let the LLM inherit the
    # process-wide default session, which is live in production.
    paper_session_id = "ai-agent-paper"
    if not ut.get_session(paper_session_id):
        ut.init_session(
            "binance_futures",
            mode="paper",
            paper_balance=100_000.0,
            session_id=paper_session_id,
        )
    tools = build_trading_tools(
        ut, binance_market_data, paper_session_id=paper_session_id,
    )

    client = LlmToolClient(
        api_key=api_key,
        base_url=base_url,
        model=model,
        provider=provider,
    )
    client.tools = tools

    system = (
        "You are an autonomous trading agent. You have access to tools that let you "
        "inspect market prices, your portfolio, and place/cancel paper orders. "
        "Analyze carefully, then act decisively. Always report your reasoning."
    )

    result = client.chat(
        user_message=prompt,
        system_prompt=system,
        max_tool_rounds=5,
    )
    return {
        "response": result["content"],
        "tool_calls_used": result.get("tool_calls_used", False),
        "tool_rounds": result.get("tool_rounds", 0),
        "usage": result.get("usage", {}),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# DataHub Diagnostics
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/datahub/topics")
async def datahub_topics():
    """List active DataHub topics and subscriber counts."""
    from backend.services.data_hub import DataHub
    return DataHub().stats()


@router.get("/datahub/peek/{topic}")
async def datahub_peek(topic: str):
    """Peek cached value for a DataHub topic."""
    from backend.services.data_hub import DataHub
    val = DataHub().peek(topic)
    return {"topic": topic, "value": val, "cached": val is not None}


# ═══════════════════════════════════════════════════════════════════════════════
# B: SSE Real-time Stream
# ═══════════════════════════════════════════════════════════════════════════════



@router.get("/stream")
async def event_stream(topics: str = ""):
    """
    SSE endpoint for real-time DataHub events.
    Usage: /trading/stream?topics=market:quote:BTCUSDT,paper:fill
    """
    from backend.services.data_hub import DataHub

    requested = [t.strip() for t in topics.split(",") if t.strip()] if topics else []
    queue: asyncio.Queue[dict] = asyncio.Queue()

    def on_event(value: any):
        try:
            queue.put_nowait({"topic": "unknown", "data": value})
        except Exception:
            pass

    # Subscribe to all requested topics
    hub = DataHub()
    for t in requested:
        hub.subscribe(t, lambda v, topic=t: queue.put_nowait({"topic": topic, "data": v}), immediate=False)

    async def generator():
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(msg)}\n\n"
                except asyncio.TimeoutError:
                    # Keep the connection alive instead of ending the stream.
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
        except asyncio.CancelledError:
            pass

    return StreamingResponse(generator(), media_type="text/event-stream")


# ═══════════════════════════════════════════════════════════════════════════════
# ─── Price helper (used by ActiveTradeCard for live P&L) ─────────────────────

@router.get("/price")
async def get_price(symbol: str = "BTCUSDT"):
    """Return current mark/last price for a Binance Futures symbol."""
    from backend.services.binance_market_data import binance_market_data
    sym = symbol.upper()
    try:
        ticker = await binance_market_data.get_ticker_24h(sym)
        if ticker:
            return {
                "symbol": sym,
                "price": ticker.get("lastPrice", 0.0),
                "change24h": ticker.get("priceChangePercent", 0.0),
            }
    except Exception:
        pass
    return {"symbol": sym, "price": 0.0, "change24h": 0.0}


@router.get("/account/summary")
async def get_account_summary():
    """Return live account equity and balance for workflow engine."""
    try:
        from backend.services.binance_futures_service import binance_futures_broker
        bal = await asyncio.to_thread(binance_futures_broker.get_balance)
        equity = bal.get("equity", bal.get("balance", 0.0))
        available = bal.get("available", equity)
        db = SessionLocal()
        try:
            from sqlalchemy import func
            today = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0,
            )
            daily_pnl = db.query(func.sum(Trade.pnl)).filter(
                Trade.status == "closed",
                Trade.closed_at >= today,
                Trade.pnl.isnot(None),
            ).scalar() or 0.0
        finally:
            db.close()
        return {
            "equity": round(equity, 4),
            "available_balance": round(available, 4),
            "daily_pnl": round(float(daily_pnl), 4),
            "currency": "USDT",
            "source": "binance_futures",
        }
    except Exception:
        raise HTTPException(status_code=503, detail="Account data unavailable")


# ═══════════════════════════════════════════════════════════════════════════════
# C: Multi-Account Routes
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/sessions")
async def list_sessions():
    """List all active trading sessions."""
    ut = UnifiedTrading()
    return {"sessions": ut.list_sessions()}


@router.post("/session/switch")
async def switch_session(request: dict):
    """Switch default session by ID."""
    ut = UnifiedTrading()
    sid = request.get("session_id", "")
    ut.set_default_session(sid)
    return {"message": "Default session switched", "session_id": sid}


# ═══════════════════════════════════════════════════════════════════════════════
# D: Opinion Layer Routes
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/opinion/analyze")
async def analyze_opinion(request: dict):
    """
    Run the full Opinion Layer multi-agent analysis on a symbol.
    Body: {"symbol": "BTCUSDT", "bars": [...], "include_kronos": true, "include_social": true}
    """
    from backend.services.opinion_layer import analyze_symbol as analyze_opinion
    symbol = request.get("symbol", "")
    bars = request.get("bars", [])
    include_kronos = request.get("include_kronos", True)
    include_social = request.get("include_social", True)
    include_alerts = request.get("include_alerts", True)

    if not symbol or not bars:
        return {"error": "symbol and bars required"}

    opinion = await analyze_opinion(
        symbol=symbol,
        bars=bars,
        include_kronos=include_kronos,
        include_social=include_social,
        include_alerts=include_alerts,
    )
    return {
        "symbol": opinion.symbol,
        "direction": opinion.direction,
        "confidence": opinion.confidence,
        "reasoning": opinion.reasoning,
        "agent_opinions": [
            {"agent": op.agent, "signal": op.signal, "confidence": op.confidence, "reasoning": op.reasoning}
            for op in opinion.agent_opinions
        ],
        "kronos": opinion.kronos,
        "social": opinion.social,
        "alerts": opinion.alerts,
        "timestamp": opinion.timestamp,
    }


@router.post("/opinion/weights")
async def update_opinion_weights(request: dict):
    """
    Dynamically adjust agent voting weights in the Opinion Layer.
    Body: {"weights": {"technical_analyst": 0.35, "kronos_foundation": 0.25, ...}}
    """
    from backend.services.opinion_layer import register_agent_weight
    weights = request.get("weights", {})
    for agent, weight in weights.items():
        register_agent_weight(agent, weight)
    return {"message": "Weights updated", "weights": weights}


@router.get("/opinion/weights")
async def get_opinion_weights():
    """Get current agent voting weights."""
    from backend.services import opinion_layer as ol
    return {"weights": ol._AGENT_WEIGHTS}




# ── Trade Memory (Track C): semantic recall of past trades ───────────────────

@router.get("/trade-memory/status")
async def trade_memory_status():
    """Status of the semantic trade-memory Qdrant collection."""
    from backend.services.trade_memory import trade_memory
    return await trade_memory.status()


@router.post("/trade-memory/backfill")
async def trade_memory_backfill(limit: int = Query(1000, ge=1, le=10000)):
    """Vectorise existing closed trades from SQL into Qdrant (idempotent)."""
    from backend.services.trade_memory import trade_memory
    return await trade_memory.backfill_from_sql(limit=limit)


@router.post("/trade-memory/recall")
async def trade_memory_recall(request: Dict[str, Any]):
    """Debug: recall similar past setups for an arbitrary market context.

    Body: {"context": {...feature keys...}, "symbol": "BTCUSDT",
           "same_symbol_only": false, "limit": 8}
    """
    from backend.services.trade_memory import trade_memory
    ctx = request.get("context", {}) or {}
    result = await trade_memory.recall_similar(
        ctx,
        symbol=request.get("symbol"),
        limit=request.get("limit"),
        same_symbol_only=bool(request.get("same_symbol_only", False)),
    )
    return result.to_dict()


# ── Strategy Skills (skill miner): learned, named strategies ─────────────────

@router.get("/skills/status")
async def skills_status():
    """Skill miner status: counts + config."""
    from backend.services.skill_miner import skill_miner
    return skill_miner.status()


@router.get("/skills")
async def skills_list(active_only: bool = Query(True), limit: int = Query(50, ge=1, le=500)):
    """List learned strategy skills (the leaderboard)."""
    from backend.services.skill_miner import skill_miner
    skills = await asyncio.to_thread(skill_miner.list_skills, active_only, limit)
    return {"count": len(skills), "skills": skills}


@router.get("/skills/leaderboard")
async def skills_leaderboard(limit: int = Query(10, ge=1, le=100)):
    """Top skills by edge score — compact leaderboard view."""
    from backend.services.skill_miner import skill_miner
    skills = await asyncio.to_thread(skill_miner.list_skills, True, limit)
    board = [
        {
            "rank": i + 1,
            "name": s["name"],
            "direction": s["direction"],
            "edge_score": s["edge_score"],
            "win_rate": s["win_rate"],
            "avg_pnl": s["avg_pnl"],
            "sample_count": s["sample_count"],
            "symbols": s["symbols"],
        }
        for i, s in enumerate(skills)
    ]
    return {"count": len(board), "leaderboard": board}


@router.post("/skills/mine")
async def skills_mine(limit: Optional[int] = Query(None, ge=1, le=20000)):
    """Trigger a skill-mining pass over closed-trade history (idempotent)."""
    from backend.services.skill_miner import skill_miner
    return await asyncio.to_thread(skill_miner.mine_and_store, limit)


@router.post("/skills/match")
async def skills_match(request: Dict[str, Any]):
    """Debug: match an arbitrary market context to the best learned skill.

    Body: {"context": {...feature keys...}}
    """
    from backend.services.skill_miner import skill_miner
    ctx = request.get("context", {}) or {}
    skill = await asyncio.to_thread(skill_miner.match_skill, ctx)
    return {"matched": skill is not None, "skill": skill}
