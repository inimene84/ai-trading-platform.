from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="AI Hedge Fund API", description="Backend API for AI Hedge Fund", version="0.1.0")

# Initialize database tables (this is safe to run multiple times)
Base.metadata.create_all(bind=engine)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for VPS access
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
        ut.init_session(
            broker="binance_futures",
            mode="paper",
            paper_balance=100_000.0,
            leverage=1.0,
        )
        logger.info("✓ Unified Trading Router initialized (paper mode)")
    except Exception as e:
        logger.warning(f"⚠ Unified Trading init warning: {e}")
