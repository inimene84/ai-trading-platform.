"""
Agent Types — Canonical Data Models
====================================
Shared types for all agent outputs across the trading platform.
Ensures consistent structure regardless of whether the agent is a
persona, technical analyser, Kronos ML model, or social sentiment reader.
"""

from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field


class AgentOpinion(BaseModel):
    """
    Unified output type for any agent in the opinion pipeline.

    Confidence is ALWAYS normalised to 0.0–1.0.
    ``view`` uses the human-readable vocabulary ('bullish' / 'bearish' / 'neutral')
    that the downstream opinion aggregator expects.
    """

    agent_name: str = Field(
        ..., description="Identifier for the agent (e.g. 'warren_buffett', 'technical_analyst')"
    )
    symbol: str = Field(default="", description="Trading symbol evaluated (e.g. 'BTCUSDT')")
    view: Literal["bullish", "bearish", "neutral"] = Field(
        default="neutral", description="Directional opinion"
    )
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Normalised confidence 0-1"
    )
    time_horizon: Literal["intraday", "swing", "position", "long_term"] = Field(
        default="swing", description="Intended holding period"
    )
    risk_flags: list[str] = Field(
        default_factory=list, description="Risk warnings surfaced by this agent"
    )
    reasoning: str = Field(default="", description="Human-readable rationale")
    metadata: dict = Field(
        default_factory=dict, description="Extra data (kronos predictions, social scores, etc.)"
    )

    # ── Convenience helpers ──────────────────────────────────────────────────

    @property
    def numeric_signal(self) -> int:
        """Map view to numeric: bullish=+1, bearish=−1, neutral=0."""
        return {"bullish": 1, "bearish": -1, "neutral": 0}.get(self.view, 0)

    def to_legacy_dict(self) -> dict:
        """Convert to the dict format expected by older code paths."""
        return {
            "agent": self.agent_name,
            "signal": self.view,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "metadata": self.metadata,
        }
