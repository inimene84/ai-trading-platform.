from fastapi import APIRouter
from fastapi.responses import StreamingResponse
import asyncio
import json
from datetime import datetime

from backend.services.sentry_state import get_trading_status, is_trading_allowed

router = APIRouter()


@router.get("/")
async def root():
    return {"message": "Welcome to AI Hedge Fund API"}


@router.get("/health")
async def health_check():
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "service": "trading-platform-api",
        "trading_allowed": is_trading_allowed(),
        "trading_status": get_trading_status().value,
    }


@router.get("/ping")
async def ping():
    async def event_generator():
        for i in range(5):
            # Create a JSON object for each ping
            data = {"ping": f"ping {i+1}/5", "timestamp": i + 1}

            # Format as SSE
            yield f"data: {json.dumps(data)}\n\n"

            # Wait 1 second
            await asyncio.sleep(1)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
