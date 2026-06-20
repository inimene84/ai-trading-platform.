from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import logging
import asyncio
import os
from dotenv import load_dotenv
import sentry_sdk
import structlog

# Load environment variables
load_dotenv()

# Configure Sentry (skip invalid/placeholder DSNs — must not crash startup)
sentry_dsn = (os.getenv("SENTRY_DSN") or "").strip()
_placeholder_markers = ("<", ">", "your_", "example", "changeme", "xxx")
if sentry_dsn and not any(m in sentry_dsn.lower() for m in _placeholder_markers):
    try:
        sentry_sdk.init(
            dsn=sentry_dsn,
            traces_sample_rate=1.0,
            profiles_sample_rate=1.0,
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

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="AI Hedge Fund API", description="Backend API for AI Hedge Fund", version="0.1.0")

# Initialize database tables (this is safe to run multiple times)
Base.metadata.create_all(bind=engine)

# Configure CORS
_cors_env = os.getenv("CORS_ALLOWED_ORIGINS", "").strip()
if _cors_env:
    _allowed_origins = [origin.strip() for origin in _cors_env.split(",") if origin.strip()]
else:
    _allowed_origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
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

@app.on_event("startup")
async def startup_event():
    """Startup event to check Ollama availability."""
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

    # Start Binance wallet poller (writes to InfluxDB every 30s)
    asyncio.create_task(start_wallet_poller())
    logger.info("✓ Binance wallet poller task scheduled")

    # Start Binance order status poller (syncs open trade order status every 30s)
    asyncio.create_task(start_order_poller())
    logger.info("✓ Binance order poller task scheduled")

    # Initialize Unified Trading Router (Fincept port)
    try:
        ut = UnifiedTrading()
        ut.register_broker("binance_futures", binance_futures_broker)
        ut.register_broker("ctrader", ctrader_broker)
        # Auto-init paper session for immediate use
        paper_trading = os.getenv("PAPER_TRADING", "false").lower() == "true"
        mode = "paper" if paper_trading else "live"
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
    
    # Auto-start trading loop
    try:
        # Start trading loop automatically (Safe in both Paper and Live as per user request)
        asyncio.create_task(trading_loop.start())
        paper_trading = os.getenv("PAPER_TRADING", "false").lower() == "true"
        mode_str = "PAPER" if paper_trading else "LIVE"
        logger.info(f"✓ Trading loop auto-started ({mode_str} MODE)")
    except Exception as e:
        logger.warning(f"⚠ Trading loop auto-start failed: {e}")

    # Auto-start native sentiment loop (repo-side replacement for the n8n
    # crypto-news → InfluxDB sentiment pipeline). Toggle via SENTIMENT_LOOP_ENABLED.
    try:
        if os.getenv("SENTIMENT_LOOP_ENABLED", "true").lower() == "true":
            from backend.services.sentiment_loop import sentiment_loop
            asyncio.create_task(sentiment_loop.start())
            logger.info("✓ Native sentiment loop auto-started")
        else:
            logger.info("ℹ Native sentiment loop disabled (SENTIMENT_LOOP_ENABLED=false)")
    except Exception as e:
        logger.warning(f"⚠ Sentiment loop auto-start failed: {e}")

    # Auto-start trade-memory recorder loop (Track C): periodically vectorise
    # newly-closed trades into Qdrant for semantic recall. Toggle via
    # TRADE_MEMORY_ENABLED.
    try:
        if os.getenv("TRADE_MEMORY_ENABLED", "true").lower() == "true":
            from backend.services.trade_memory import trade_memory
            asyncio.create_task(trade_memory.run_recorder_loop())
            logger.info("✓ Trade-memory recorder loop auto-started")
        else:
            logger.info("ℹ Trade memory disabled (TRADE_MEMORY_ENABLED=false)")
    except Exception as e:
        logger.warning(f"⚠ Trade-memory recorder auto-start failed: {e}")
