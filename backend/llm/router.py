"""
LLM Model Router — Task-Based Model Selection
===============================================
Central registry that maps task types to model configurations.
Eliminates scattered model name hardcoding across the codebase.

Usage:
    from backend.llm.router import pick_model, call_llm_resilient
    cfg = pick_model("persona_analysis")
    # cfg.name, cfg.provider, cfg.base_url, cfg.max_tokens, cfg.temperature
"""

from __future__ import annotations

import logging
import os
import httpx
import asyncio
import json
import re
from dataclasses import dataclass
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

# Kie.ai direct model IDs
_KIE_SONNET_DIRECT_MODEL = os.getenv("KIE_MODEL", "claude-sonnet-4-6")
_KIE_OPUS_DIRECT_MODEL = os.getenv("KIE_OPUS_MODEL", "claude-opus-4-6")
_KIE_BASE_URL = os.getenv("KIE_BASE_URL", "https://api.kie.ai/claude")
_LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", os.getenv("PERSONA_LLM_BASE_URL", "http://litellm:4000/v1"))

_DEFAULT_REGISTRY: dict[str, ModelConfig] = {
    # PRIMARY: Direct Kie.ai Claude Sonnet 4.6 (bypasses LiteLLM proxy)
    "persona_analysis": ModelConfig(
        name=os.getenv("PERSONA_LLM_MODEL", _KIE_SONNET_DIRECT_MODEL),
        provider=os.getenv("PERSONA_LLM_PROVIDER", "kie"),
        tier="balanced",
        base_url=_KIE_BASE_URL,
        max_tokens=1024,
        temperature=0.3,
        api_key_env="KIE_API_KEY",
    ),

    # Deep trading analysis — Kie Sonnet 4.6 by default; flip to Opus for complex reasoning
    "deep_analysis": ModelConfig(
        name=os.getenv("DEEP_ANALYSIS_LLM_MODEL", os.getenv("PERSONA_LLM_MODEL", _KIE_SONNET_DIRECT_MODEL)),
        provider=os.getenv("DEEP_ANALYSIS_LLM_PROVIDER", os.getenv("PERSONA_LLM_PROVIDER", "kie")),
        tier="balanced",
        base_url=_KIE_BASE_URL,
        max_tokens=1024,
        temperature=0.3,
        api_key_env="KIE_API_KEY",
    ),

    # Premium/complex reasoning tier — Kie.ai Claude Opus 4.6
    "premium_analysis": ModelConfig(
        name=_KIE_OPUS_DIRECT_MODEL,
        provider="kie",
        tier="premium",
        base_url=_KIE_BASE_URL,
        max_tokens=2048,
        temperature=0.3,
        api_key_env="KIE_API_KEY",
    ),

    # General LLM tasks (news scoring, etc.) — Kie Sonnet directly
    "general": ModelConfig(
        name=os.getenv("GENERAL_LLM_MODEL", _KIE_SONNET_DIRECT_MODEL),
        provider=os.getenv("GENERAL_LLM_PROVIDER", "kie"),
        tier="balanced",
        base_url=_KIE_BASE_URL,
        max_tokens=1024,
        temperature=0.4,
        api_key_env="KIE_API_KEY",
    ),

    # Direct OpenRouter fallback (bypasses LiteLLM proxy)
    "fallback_1": ModelConfig(
        name=os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-5"),
        provider="openrouter",
        tier="balanced",
        base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        api_key_env="OPENROUTER_API_KEY",
    ),
    "fallback_2": ModelConfig(
        name=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        provider="anthropic",
        tier="balanced",
        api_key_env="ANTHROPIC_API_KEY",
    ),
    "fallback_3": ModelConfig(
        name=os.getenv("GEMINI_MODEL", "google/gemini-2.5-flash"),
        provider="openrouter-gemini",
        tier="balanced",
        base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        api_key_env="OPENROUTER_API_KEY",
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
        # Never substitute an unrelated provider's token. The old cascade sent
        # LiteLLM/Kie keys to Anthropic and Gemini, producing repeated 401/400s.
        return os.getenv(config.api_key_env, "")
    # Legacy configs without an explicit key name may use the local proxy key.
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


# ── Resilient LLM Execution Engine ────────────────────────────────────────────

_LLM_SEMAPHORE = asyncio.Semaphore(3)


def _clean_and_parse_json(content: str) -> dict:
    """Clean LLM output and parse it as JSON."""
    content = str(content).strip()
    
    # Remove <think>...</think> tags if present
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    
    # Try direct parsing first
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
        
    # Try markdown json block extraction (with or without outer braces)
    m = re.search(r'```(?:json)?\s*\n?(.*?)\s*\n?```', content, re.DOTALL)
    if m:
        inner = m.group(1).strip()
        for candidate in (inner, f"{{{inner}}}"):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass
            
    # Try searching for anything between first { and last }
    m = re.search(r'\{.*\}', content, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
            
    logger.error(f"Failed to parse JSON. Raw LLM output: {content}")
    raise ValueError("Could not parse JSON from LLM response")


async def _invoke_provider(
    cfg: ModelConfig,
    api_key: str,
    prompt: str,
    system: str,
    temperature: Optional[float],
    max_tokens: Optional[int],
    response_json: bool,
) -> str:
    prov = cfg.provider.lower()
    temp = temperature if temperature is not None else cfg.temperature
    tokens = max_tokens if max_tokens is not None else cfg.max_tokens
    
    if prov in ("litellm", "xai", "groq", "openai", "openrouter", "openrouter-gemini"):
        # OpenAI chat completions format
        base_url = cfg.base_url or "https://api.openai.com/v1"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        
        payload = {
            "model": cfg.name,
            "messages": messages,
            "max_tokens": tokens,
        }
        
        is_reasoning_model = any(x in cfg.name.lower() for x in ("o1-", "o3-", "reasoning"))
        if not is_reasoning_model:
            payload["temperature"] = temp
            if response_json:
                payload["response_format"] = {"type": "json_object"}
                
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(f"{base_url.rstrip('/')}/chat/completions", headers=headers, json=payload)
            if not resp.is_success:
                raise httpx.HTTPStatusError(f"HTTP {resp.status_code}: {resp.text[:200]}", request=resp.request, response=resp)
            return resp.json()["choices"][0]["message"]["content"]
            
    elif prov in ("kie", "anthropic"):
        # Anthropic messages format
        is_kie = prov == "kie"
        url = "https://api.kie.ai/claude/v1/messages" if is_kie else "https://api.anthropic.com/v1/messages"
        
        headers = {
            "Content-Type": "application/json",
        }
        if is_kie:
            headers["Authorization"] = f"Bearer {api_key}"
        else:
            headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
            
        messages = [{"role": "user", "content": prompt}]
        # Anthropic API has no JSON mode; prefilling the assistant turn with "{"
        # forces raw JSON output (no markdown fences) and saves output tokens.
        if response_json:
            messages.append({"role": "assistant", "content": "{"})
        payload = {
            "model": cfg.name,
            "messages": messages,
            "max_tokens": tokens,
        }
        if system:
            payload["system"] = system
            
        async with httpx.AsyncClient(timeout=45.0) as client:
            # If the model hits the token cap mid-JSON the output is unusable,
            # so retry once with double the budget before falling through.
            for attempt_tokens in (tokens, tokens * 2):
                payload["max_tokens"] = attempt_tokens
                resp = await client.post(url, headers=headers, json=payload)
                if not resp.is_success:
                    raise httpx.HTTPStatusError(f"HTTP {resp.status_code}: {resp.text[:200]}", request=resp.request, response=resp)
                data = resp.json()
                text = ""
                for block in data.get("content", []):
                    if block.get("type") == "text":
                        text += block.get("text", "")
                if response_json:
                    # Kie's proxy ignores the assistant prefill and returns the
                    # full response; only re-attach "{" when the model actually
                    # continued from the prefilled brace.
                    stripped = text.lstrip()
                    if not stripped.startswith("{") and not stripped.startswith("```"):
                        text = "{" + text
                # Kie's proxy reports end_turn even when the cap is hit, so also
                # treat an output that consumed the full budget as truncated.
                out_tokens = (data.get("usage") or {}).get("output_tokens", 0)
                truncated = data.get("stop_reason") == "max_tokens" or out_tokens >= attempt_tokens
                if truncated and response_json:
                    logger.warning(
                        f"LLM output truncated at max_tokens={attempt_tokens} for {cfg.name}; retrying with larger budget"
                    )
                    continue
                return text
            raise ValueError(f"Output still truncated at max_tokens={tokens * 2} for {cfg.name}")
            
    elif prov in ("google", "gemini"):
        # Google Gemini generateContent format
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{cfg.name}:generateContent?key={api_key}"
        
        contents = []
        if system:
            contents.append({"role": "user", "parts": [{"text": f"System: {system}"}]})
            contents.append({"role": "model", "parts": [{"text": "Understood."}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})
        
        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": temp,
                "maxOutputTokens": tokens,
            }
        }
        if response_json:
            payload["generationConfig"]["responseMimeType"] = "application/json"
            
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(url, json=payload)
            if not resp.is_success:
                raise httpx.HTTPStatusError(f"HTTP {resp.status_code}: {resp.text[:200]}", request=resp.request, response=resp)
            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                raise ValueError("Gemini returned no candidates")
            return candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            
    elif prov == "ollama":
        # Ollama local chat format
        base_url = cfg.base_url or "http://localhost:11434"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        
        payload = {
            "model": cfg.name,
            "messages": messages,
            "stream": False,
        }
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{base_url.rstrip('/')}/api/chat", json=payload)
            if not resp.is_success:
                raise httpx.HTTPStatusError(f"HTTP {resp.status_code}: {resp.text[:200]}", request=resp.request, response=resp)
            return resp.json().get("message", {}).get("content", "")
            
    else:
        raise ValueError(f"Unsupported LLM provider: {prov}")


async def call_llm_resilient(
    task_type: str,
    prompt: str,
    system: str = "",
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    response_json: bool = False,
) -> str:
    """
    Highly resilient LLM executor.
    
    1. Acquires a semaphore to limit concurrency.
    2. Tries the primary model configuration with 3 retries (exponential backoff).
    3. If primary fails, cascades through fallback configurations sequentially.
    4. Cleans and parses output (removing <think> tags, extracting JSON if requested).
    """
    global _LLM_SEMAPHORE
    
    primary_cfg = pick_model(task_type)
    
    chain = [
        ("Primary (" + primary_cfg.provider + ")", primary_cfg),
        ("Fallback 1 (OpenRouter)", _DEFAULT_REGISTRY["fallback_1"]),
        ("Fallback 2 (xAI)", ModelConfig(
            name=os.getenv('XAI_MODEL', 'grok-4-1-fast-reasoning'),
            provider='xai',
            base_url=os.getenv('XAI_BASE_URL', 'https://api.x.ai/v1'),
            api_key_env='XAI_API_KEY'
        )),
        ("Fallback 3 (OpenAI)", ModelConfig(
            name=os.getenv('OPENAI_MODEL', 'gpt-4o-mini'),
            provider='openai',
            base_url=os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1'),
            api_key_env='OPENAI_API_KEY'
        )),
        ("Fallback 4 (Anthropic)", _DEFAULT_REGISTRY["fallback_2"]),
        ("Fallback 5 (Gemini)", _DEFAULT_REGISTRY["fallback_3"]),
    ]
    if os.getenv("OLLAMA_ENABLED", "false").lower() == "true":
        chain.append(("Fallback 6 (Ollama)", ModelConfig(
            name=os.getenv('OLLAMA_PRIMARY_MODEL', 'phi3.5'),
            provider='ollama',
            base_url=os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434'),
        )))
    
    configs_to_try = []
    seen_models: set[str] = set()
    
    # Primary is always first
    configs_to_try.append((chain[0][0], chain[0][1]))
    seen_models.add(chain[0][1].name)
    
    for name, cfg in chain[1:]:
        key = get_api_key(cfg)
        is_configured = True
        if cfg.provider not in ("ollama",) and not key:
            is_configured = False
        if key and len(key) < 20 and cfg.api_key_env in ("XAI_API_KEY", "GOOGLE_API_KEY"):
            is_configured = False
        if key and any(marker in key.lower() for marker in (
            "changeme", "placeholder", "your_", "xxx",
        )):
            is_configured = False
        if cfg.provider == "anthropic" and key and not key.startswith("sk-ant-"):
            logger.warning("LLM Router: skipping malformed Anthropic API key")
            is_configured = False
            
        if is_configured and cfg.name not in seen_models:
            configs_to_try.append((name, cfg))
            seen_models.add(cfg.name)
            
    async with _LLM_SEMAPHORE:
        last_error = None
        for attempt_name, cfg in configs_to_try:
            api_key = get_api_key(cfg)
            max_retries = 3
            backoff = 1.0
            
            for attempt in range(max_retries):
                try:
                    logger.info(f"LLM Router: Trying {attempt_name} (model={cfg.name}, attempt={attempt+1}/{max_retries})")
                    text = await _invoke_provider(cfg, api_key, prompt, system, temperature, max_tokens, response_json)
                    
                    if response_json:
                        parsed = _clean_and_parse_json(text)
                        text = json.dumps(parsed)
                        
                    logger.info(f"LLM Router: Success using {attempt_name}")
                    return text
                except Exception as e:
                    last_error = e
                    logger.warning(f"LLM Router: {attempt_name} attempt {attempt+1} failed: {type(e).__name__}: {e}")
                    
                    if attempt < max_retries - 1:
                        await asyncio.sleep(backoff)
                        backoff *= 2
                        
            logger.error(f"LLM Router: All {max_retries} attempts failed for {attempt_name}. Moving to next fallback.")
            
        err_msg = f"All LLM providers in the chain failed. Last error: {last_error}"
        logger.critical(err_msg)
        raise RuntimeError(err_msg)
