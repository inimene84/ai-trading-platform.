from fastapi import APIRouter

from backend.routes.hedge_fund import router as hedge_fund_router
from backend.routes.health import router as health_router
from backend.routes.storage import router as storage_router
from backend.routes.flows import router as flows_router
from backend.routes.flow_runs import router as flow_runs_router
from backend.routes.ollama import router as ollama_router
from backend.routes.language_models import router as language_models_router
from backend.routes.api_keys import router as api_keys_router
from backend.routes.trading import router as trading_router
from backend.routes.news import router as news_router
from backend.routes.market_data import router as market_data_router
from backend.routes.historical import router as historical_router
from backend.routes.telemetry import router as telemetry_router

# Main API router
api_router = APIRouter()

# Include sub-routers
api_router.include_router(health_router, tags=["health"])
api_router.include_router(hedge_fund_router, tags=["hedge-fund"])
api_router.include_router(storage_router, tags=["storage"])
api_router.include_router(flows_router, tags=["flows"])
api_router.include_router(flow_runs_router, tags=["flow-runs"])
api_router.include_router(ollama_router, tags=["ollama"])
api_router.include_router(language_models_router, tags=["language-models"])
api_router.include_router(api_keys_router, tags=["api-keys"])
api_router.include_router(trading_router, tags=["trading"])
api_router.include_router(news_router, tags=["news"])
api_router.include_router(market_data_router, tags=["market-data"])
api_router.include_router(historical_router)
api_router.include_router(telemetry_router)
