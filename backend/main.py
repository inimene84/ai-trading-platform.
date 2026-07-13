from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import logging
import asyncio
import os
from dotenv import load_dotenv
import sentry_sdk
import structlog

from backend.routes import api_router
from backend.database.connection import engine
from backend.database.models import Base
from backend.services.ollama_service import ollama_service
from backend.services.binance_wallet_poller import start_wallet_poller
from backend.services.binance_order_poller import start_order_poller
from backend.services.unified_trading import UnifiedTrading
from backend.services.ctrader_service import ctrader_broker
from backend.services.binance_futures_service import binance_futures_broker
from backend.services.trading_loop import trading_loop
from backend.security import admin_auth_enabled, is_sensitive_request, validate_admin_request
# Load environment variables
load_dotenv()

# Configure Sentry (skip invalid/placeholder DSNs — must not crash startup)
sentry_dsn = (os.getenv("SENTRY_DSN") or "").strip()
_placeholder_markers = ("<", ">", "your_", "example", "changeme", "xxx")
if sentry_dsn and not any(m in sentry_dsn.lower() for m in _placeholder_markers):
    try:
        sentry_sdk.init(
            dsn=sentry_dsn,
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.2")),
            profiles_sample_rate=float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0.1")),
        )
    except Exception as exc:
        logging.getLogger(__name__).warning("Sentry init skipped: %s", exc)
elif sentry_dsn:
    logging.getLogger(__name__).warning("Sentry init skipped: SENTRY_DSN looks like a placeholder")

# Configure structlog
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer() if os.getenv("JSON_LOGS", "false").lower() == "true" else structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)



# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def validate_live_startup_security() -> None:
    """Refuse to start a live trading process without admin authentication."""
    from backend.security import admin_auth_enabled
    from backend.services.trading_mode import TradingMode, get_trading_mode

    if get_trading_mode() == TradingMode.LIVE and not admin_auth_enabled():
        raise RuntimeError(
            "Refusing LIVE startup without ADMIN_API_KEY/API_AUTH_TOKEN/"
            "BACKEND_API_KEY. Configure a strong token before enabling live trading."
        )


async def run_supervised_task(task_name: str, coro_func, *args, **kwargs):
    """Helper to run a task with an auto-restart supervisor.
    
    If the task crashes with an unhandled exception, it logs the error
    and automatically restarts after a short cooldown (10 seconds).
    """
    restart_delay = 10
    while True:
        try:
            logger.info(f"Supervisor: Starting background task '{task_name}'")
            await coro_func(*args, **kwargs)
            logger.info(f"Supervisor: Task '{task_name}' completed normally. Exiting supervisor.")
            break
        except asyncio.CancelledError:
            logger.info(f"Supervisor: Task '{task_name}' has been cancelled.")
            raise
        except Exception as e:
            logger.error(f"Supervisor: Task '{task_name}' crashed with error: {e}. Restarting in {restart_delay}s...", exc_info=True)
            await asyncio.sleep(restart_delay)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events."""
    validate_live_startup_security()
    from backend.services.trading_mode import TradingMode, get_trading_mode
    resolved_mode = get_trading_mode()
    mode = "live" if resolved_mode == TradingMode.LIVE else "paper"
    paper_trading = mode == "paper"

    # 0. Initialize Vector DB Collections
    try:
        from backend.services.qdrant_client import qdrant
        await qdrant.ensure_collection()
        logger.info("✓ Qdrant collections verified")
    except Exception as e:
        logger.warning(f"⚠ Qdrant initialization warning: {e}")

    # 1. Startup Logic
    try:
        logger.info("Checking Ollama availability...")
        status = await ollama_service.check_ollama_status()
        
        if status["installed"]:
            if status["running"]:
                logger.info(f"✓ Ollama is installed and running at {status['server_url']}")
                if status["available_models"]:
                    logger.info(f"✓ Available models: {', '.join(status['available_models'])}")
                else:
                    logger.info("ℹ No models are currently downloaded")
            else:
                logger.info("ℹ Ollama is installed but not running")
                logger.info("ℹ You can start it from the Settings page or manually with 'ollama serve'")
        else:
            logger.info("ℹ Ollama is not installed. Install it to use local models.")
            logger.info("ℹ Visit https://ollama.com to download and install Ollama")
            
    except Exception as e:
        logger.warning(f"Could not check Ollama status: {e}")
        logger.info("ℹ Ollama integration is available if you install it later")

    # Initialize Unified Trading Router (Fincept port)
    try:
        ut = UnifiedTrading()
        ut.register_broker("binance_futures", binance_futures_broker)
        ut.register_broker("ctrader", ctrader_broker)
        # Use the canonical resolver so router/session/loop cannot disagree when
        # TRADING_MODE or legacy PAPER_TRADING defaults are absent.
        init_kwargs = {
            "broker": "binance_futures",
            "mode": mode,
            "leverage": 1.0,
        }
        if paper_trading:
            init_kwargs["paper_balance"] = 100_000.0
        ut.init_session(**init_kwargs)
        logger.info(f"✓ Unified Trading Router initialized ({mode.upper()} MODE)")
    except Exception as e:
        logger.warning(f"⚠ Unified Trading init warning: {e}")

    # Restore exchange SL/TP after restart (sentry halt may have cancelled them).
    if os.getenv("ACTIVE_BROKER", "ctrader") == "binance_futures" and resolved_mode == TradingMode.LIVE:
        try:
            from backend.database.connection import SessionLocal
            from backend.services.trading_loop_helpers import ExchangeProtectionManager

            db = SessionLocal()
            try:
                summary = ExchangeProtectionManager.restore_all_open_positions(
                    db, binance_futures_broker,
                )
                if summary.get("restored") or summary.get("errors"):
                    logger.warning(f"Startup protection restore: {summary}")
                else:
                    logger.info(f"Startup protection check: {summary.get('checked', 0)} symbol(s) OK")
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Startup protection restore failed: {e}")

    # Track spawned background tasks so we can cancel them cleanly on shutdown
    background_tasks = []

    # Start supervised background tasks
    # 1. Wallet Poller
    task = asyncio.create_task(run_supervised_task("Binance Wallet Poller", start_wallet_poller))
    background_tasks.append(task)
    logger.info("✓ Binance wallet poller task scheduled under supervisor")

    # 2. Order Poller
    task = asyncio.create_task(run_supervised_task("Binance Order Poller", start_order_poller))
    background_tasks.append(task)
    logger.info("✓ Binance order poller task scheduled under supervisor")

    # 3. Trading Loop
    interval = int(os.getenv("TRADING_LOOP_INTERVAL_MIN", "15"))
    task = asyncio.create_task(run_supervised_task("Trading Loop", trading_loop.start, interval_minutes=interval))
    background_tasks.append(task)
    mode_str = mode.upper()
    logger.info(f"✓ Trading loop auto-started ({mode_str} MODE) under supervisor")

    # 4. Sentiment Loop
    if os.getenv("SENTIMENT_LOOP_ENABLED", "true").lower() == "true":
        from backend.services.sentiment_loop import sentiment_loop
        task = asyncio.create_task(run_supervised_task("Sentiment Loop", sentiment_loop.start))
        background_tasks.append(task)
        logger.info("✓ Native sentiment loop auto-started under supervisor")
    else:
        logger.info("ℹ Native sentiment loop disabled (SENTIMENT_LOOP_ENABLED=false)")

    # 5. Trade-Memory Recorder
    if os.getenv("TRADE_MEMORY_ENABLED", "true").lower() == "true":
        from backend.services.trade_memory import trade_memory
        task = asyncio.create_task(run_supervised_task("Trade-Memory Recorder", trade_memory.run_recorder_loop))
        background_tasks.append(task)
        logger.info("✓ Trade-memory recorder loop auto-started under supervisor")
    else:
        logger.info("ℹ Trade memory disabled (TRADE_MEMORY_ENABLED=false)")

    # 6. Skill-Miner
    if os.getenv("SKILL_MINER_ENABLED", "true").lower() == "true":
        from backend.services.skill_miner import skill_miner
        task = asyncio.create_task(run_supervised_task("Skill Miner", skill_miner.run_miner_loop))
        background_tasks.append(task)
        logger.info("✓ Skill-miner loop auto-started under supervisor")
    else:
        logger.info("ℹ Skill miner disabled (SKILL_MINER_ENABLED=false)")

    # 7. Market Alerts Loop
    if os.getenv("MARKET_ALERTS_ENABLED", "true").lower() == "true":
        from backend.services.market_alerts import market_alerts_loop
        task = asyncio.create_task(run_supervised_task("Market Alerts Loop", market_alerts_loop.start))
        background_tasks.append(task)
        logger.info("✓ Market alerts loop auto-started under supervisor")
    else:
        logger.info("ℹ Market alerts disabled (MARKET_ALERTS_ENABLED=false)")

    yield

    # 2. Shutdown Logic
    logger.info("Application shutdown: Cancelling all background tasks...")
    for t in background_tasks:
        t.cancel()
    
    # Wait for all tasks to finish cancellation (with a timeout of 5 seconds)
    if background_tasks:
        try:
            await asyncio.wait_for(asyncio.gather(*background_tasks, return_exceptions=True), timeout=5.0)
            logger.info("All background tasks cancelled successfully.")
        except asyncio.TimeoutError:
            logger.warning("Some background tasks did not cancel within the timeout.")


app = FastAPI(title="AI Hedge Fund API", description="Backend API for AI Hedge Fund", version="0.1.0", lifespan=lifespan)

# Initialize database tables (this is safe to run multiple times)
Base.metadata.create_all(bind=engine)

# Configure CORS
_cors_env = os.getenv("CORS_ALLOWED_ORIGINS", "").strip()
if _cors_env:
    _allowed_origins = [origin.strip() for origin in _cors_env.split(",") if origin.strip()]
else:
    # Dashboard is same-origin through nginx; cross-origin access is opt-in.
    # Wildcard + credentials is both invalid and unsafe in production.
    _allowed_origins = []
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=bool(_allowed_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def require_admin_token_for_sensitive_requests(request: Request, call_next):
    if admin_auth_enabled() and is_sensitive_request(request):
        try:
            validate_admin_request(request)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=exc.headers,
            )
    return await call_next(request)

# Include all routes
app.include_router(api_router)

