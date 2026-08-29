"""Dashboard assistant chat — routed through OmniRoute with resilient fallbacks."""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assistant", tags=["assistant"])

_SYSTEM = (
    "You are a professional trading assistant for QuantumTrade Pro. "
    "Help users with multi-asset market analysis (forex, crypto, metals, oil, equities), "
    "strategy timing, risk management, and workflow automation. Be concise and actionable."
)


class ChatMessage(BaseModel):
    role: str = Field(..., description="user or model")
    text: str


class AssistantChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    history: List[ChatMessage] = Field(default_factory=list)
    task: str = Field(default="assistant_chat", description="LLM router task key")


class AssistantChatResponse(BaseModel):
    reply: str
    provider: str = "omniroute"


@router.post("/chat", response_model=AssistantChatResponse)
async def assistant_chat(payload: AssistantChatRequest):
    """Gemini-branded UI assistant — actually routed via OmniRoute + fallback chain."""
    from backend.llm.router import call_llm_resilient

    history_lines = []
    for msg in payload.history[-12:]:
        role = "User" if msg.role == "user" else "Assistant"
        history_lines.append(f"{role}: {msg.text}")
    prompt = "\n".join(history_lines + [f"User: {payload.message}", "Assistant:"])

    try:
        reply = await call_llm_resilient(
            payload.task,
            prompt=prompt,
            system=_SYSTEM,
            temperature=0.35,
            max_tokens=1200,
        )
    except Exception as exc:
        logger.error("Assistant chat failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Assistant unavailable: {exc}") from exc

    if not reply or not str(reply).strip():
        raise HTTPException(status_code=502, detail="Assistant returned an empty response")

    return AssistantChatResponse(reply=str(reply).strip(), provider="omniroute")
