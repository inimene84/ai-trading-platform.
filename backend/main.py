from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import logging
import asyncio
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

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
from backend.security import is_sensitive_request, validate_admin_request

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="AI Hedge Fund API", description="Backend API for AI Hedge Fund", version="0.1.0")

# Initialize database tables (this is safe to run multiple times)
Base.metadata.create_all(bind=engine)

# Configure CORS
allowed_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:8081").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def require_admin_token_for_sensitive_requests(request: Request, call_next):
    if is_sensitive_request(request):
        try:
            validate_admin_request(request)
        except Exception as exc:
            status_code = getattr(exc, "status_code", 500)
            detail = getattr(exc, "detail", "Authentication failed")
            headers = getattr(exc, "headers", None)
            return JSONResponse(status_code=status_code, content={"detail": detail}, headers=headers)
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
        paper_trading = os.getenv("PAPER_TRADING", "true").lower() == "true"
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
        auto_start = os.getenv("AUTO_START_TRADING_LOOP", "false").lower() == "true"
        paper_trading = os.getenv("PAPER_TRADING", "true").lower() == "true"
        if auto_start:
            asyncio.create_task(trading_loop.start())
            mode_str = "PAPER" if paper_trading else "LIVE"
            logger.info(f"✓ Trading loop auto-started ({mode_str} MODE)")
        else:
            logger.info("✓ Trading loop auto-start disabled (set AUTO_START_TRADING_LOOP=true to enable)")
    except Exception as e:
        logger.warning(f"⚠ Trading loop auto-start failed: {e}")
