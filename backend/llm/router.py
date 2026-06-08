"""
LLM Model Router — Task-Based Model Selection
===============================================
Central registry that maps task types to model configurations.
Eliminates scattered model name hardcoding across the codebase.

Usage:
    from backend.llm.router import pick_model
    cfg = pick_model("persona_analysis")
    # cfg.name, cfg.provider, cfg.base_url, cfg.max_tokens, cfg.temperature
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Literal, Optional

logger = logging.getLogger(__name__)


@dataclass
class ModelConfig:
    """Configuration for a single LLM model."""
    name: str
    provider: str
    tier: Literal["cheap", "balanced", "premium"] = "balanced"
    base_url: Optional[str] = None
    max_tokens: int = 1024
    temperature: float = 0.4
    api_key_env: str = ""  # env var name for the API key


# ── Default model registry ───────────────────────────────────────────────────
# These can be overridden via environment variables per task type.

# LiteLLM model alias → Kie.ai claude-sonnet-4-6 (see litellm-config.yaml)
_KIE_SONNET_LITELLM_MODEL = os.getenv("KIE_LITELLM_MODEL", "claude-sonnet")
_KIE_SONNET_DIRECT_MODEL = os.getenv("KIE_MODEL", "claude-sonnet-4-6")
_LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", os.getenv("PERSONA_LLM_BASE_URL", "http://litellm:4000/v1"))

_DEFAULT_REGISTRY: dict[str, ModelConfig] = {
    # PRIMARY: Kie.ai Claude Sonnet 4.6 via LiteLLM proxy (docker: http://litellm:4000/v1)
    "persona_analysis": ModelConfig(
        name=os.getenv("PERSONA_LLM_MODEL", _KIE_SONNET_LITELLM_MODEL),
        provider=os.getenv("PERSONA_LLM_PROVIDER", "litellm"),
        tier="balanced",
        base_url=_LITELLM_BASE_URL,
        max_tokens=1024,
        temperature=0.3,
        api_key_env="LITELLM_API_KEY",
    ),

    # Deep trading analysis — same Kie Sonnet 4.6 route as personas
    "deep_analysis": ModelConfig(
        name=os.getenv("DEEP_ANALYSIS_LLM_MODEL", os.getenv("PERSONA_LLM_MODEL", _KIE_SONNET_LITELLM_MODEL)),
        provider=os.getenv("DEEP_ANALYSIS_LLM_PROVIDER", os.getenv("PERSONA_LLM_PROVIDER", "litellm")),
        tier="balanced",
        base_url=_LITELLM_BASE_URL,
        max_tokens=1024,
        temperature=0.3,
        api_key_env="LITELLM_API_KEY",
    ),

    # General LLM tasks (news scoring, etc.) — Kie Sonnet via LiteLLM
    "general": ModelConfig(
        name=os.getenv("GENERAL_LLM_MODEL", _KIE_SONNET_LITELLM_MODEL),
        provider=os.getenv("GENERAL_LLM_PROVIDER", "litellm"),
        tier="balanced",
        base_url=_LITELLM_BASE_URL,
        max_tokens=1024,
        temperature=0.4,
        api_key_env="LITELLM_API_KEY",
    ),

    # Direct Kie.ai fallback (bypasses LiteLLM proxy)
    "fallback_1": ModelConfig(
        name=_KIE_SONNET_DIRECT_MODEL,
        provider="kie",
        tier="balanced",
        base_url=os.getenv("KIE_BASE_URL", "https://api.kie.ai/claude"),
        api_key_env="KIE_API_KEY",
    ),
    "fallback_2": ModelConfig(
        name=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        provider="anthropic",
        tier="balanced",
        api_key_env="ANTHROPIC_API_KEY",
    ),
    "fallback_3": ModelConfig(
        name=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        provider="google",
        tier="balanced",
        api_key_env="GOOGLE_API_KEY",
    ),
}


def pick_model(task_type: str) -> ModelConfig:
    """
    Select the appropriate model configuration for a given task type.

    Args:
        task_type: One of the keys in the registry
                   ('persona_analysis', 'deep_analysis', 'general', etc.)

    Returns:
        ModelConfig for the requested task, or the 'general' fallback.
    """
    config = _DEFAULT_REGISTRY.get(task_type)
    if config is None:
        logger.warning(f"Unknown task type '{task_type}', falling back to 'general'")
        config = _DEFAULT_REGISTRY["general"]
    return config


def get_api_key(config: ModelConfig) -> str:
    """Resolve the API key for a model config from environment variables."""
    if config.api_key_env:
        key = os.getenv(config.api_key_env, "")
        if key:
            return key
    # Cascade through common key env vars
    # LITELLM_API_KEY is the LiteLLM master key (also used for KieAI proxy)
    return (
        os.getenv("LITELLM_API_KEY", "")
        or os.getenv("KIE_API_KEY", "")   # KieAI proxy key also accepted by LiteLLM
        or os.getenv("PERSONA_LLM_API_KEY", "")
        or os.getenv("GROQ_API_KEY", "")
    )


def list_models() -> dict[str, dict]:
    """Return a summary of all registered models (useful for debug/API)."""
    return {
        task: {
            "model": cfg.name,
            "provider": cfg.provider,
            "tier": cfg.tier,
        }
        for task, cfg in _DEFAULT_REGISTRY.items()
    }
