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
#
# PRIMARY PROVIDER: OmniRoute (https://omni.allikas.online)
#   Automatically selects the best free/cheapest available model per task.
#   auto/smart   → highest quality (default for analysis)
#   auto/coding  → best for code tasks
#   auto/reasoning → best for complex multi-step reasoning
#   auto/fast    → lowest latency
#   auto/cheap   → lowest cost
#   auto/best-free → best completely free model available
#
# Fallback chain: OmniRoute → KieAI → OpenRouter → xAI → OpenAI → Anthropic → Gemini

# OmniRoute config
_OMNIROUTE_BASE_URL = os.getenv("OMNIROUTE_BASE_URL", "https://omni.allikas.online/v1")
_OMNIROUTE_DEFAULT_MODEL = os.getenv("OMNIROUTE_DEFAULT_MODEL", "auto/smart")

# Kie.ai direct model IDs (fallback)
_KIE_MODEL = os.getenv("KIE_MODEL", "gpt-5-6-terra")
_KIE_OPUS_DIRECT_MODEL = os.getenv("KIE_OPUS_MODEL", "gpt-5-6-terra")
_KIE_BASE_URL = os.getenv("KIE_BASE_URL", "https://api.kie.ai")
_LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", os.getenv("PERSONA_LLM_BASE_URL", "http://litellm:4000/v1"))

_DEFAULT_REGISTRY: dict[str, ModelConfig] = {
    # PRIMARY: OmniRoute auto/smart — selects the best available LLM automatically
    # Task-specific presets give the router hints for optimal model selection.
    "persona_analysis": ModelConfig(
        name=os.getenv("PERSONA_LLM_MODEL", "auto/smart"),
        provider="omniroute",
        tier="balanced",
        base_url=_OMNIROUTE_BASE_URL,
        max_tokens=1024,
        temperature=0.3,
        api_key_env="OMNIROUTE_API_KEY",
    ),

    # Deep trading analysis — OmniRoute auto/smart for robust analysis
    "deep_analysis": ModelConfig(
        name=os.getenv("DEEP_ANALYSIS_LLM_MODEL", "auto/smart"),
        provider="omniroute",
        tier="balanced",
        base_url=_OMNIROUTE_BASE_URL,
        max_tokens=1500,
        temperature=0.3,
        api_key_env="OMNIROUTE_API_KEY",
    ),

    # Premium/complex reasoning — OmniRoute auto/smart (highest quality preset)
    "premium_analysis": ModelConfig(
        name=os.getenv("PREMIUM_ANALYSIS_LLM_MODEL", "auto/smart"),
        provider="omniroute",
        tier="premium",
        base_url=_OMNIROUTE_BASE_URL,
        max_tokens=2048,
        temperature=0.3,
        api_key_env="OMNIROUTE_API_KEY",
    ),

    # General LLM tasks (news scoring, alerts, etc.) — fast + cheap preset
    "general": ModelConfig(
        name=os.getenv("GENERAL_LLM_MODEL", "auto/fast"),
        provider="omniroute",
        tier="balanced",
        base_url=_OMNIROUTE_BASE_URL,
        max_tokens=1024,
        temperature=0.4,
        api_key_env="OMNIROUTE_API_KEY",
    ),

    # Dashboard assistant (Gemini UI → OmniRoute)
    "assistant_chat": ModelConfig(
        name=os.getenv("ASSISTANT_LLM_MODEL", "auto/smart"),
        provider="omniroute",
        tier="balanced",
        base_url=_OMNIROUTE_BASE_URL,
        max_tokens=1200,
        temperature=0.35,
        api_key_env="OMNIROUTE_API_KEY",
    ),

    # GrokBOT overseer summaries (prefers xAI when configured; chain falls back)
    "grok_overseer": ModelConfig(
        name=os.getenv("XAI_MODEL", "grok-beta"),
        provider="xai",
        tier="balanced",
        base_url=os.getenv("XAI_BASE_URL", "https://api.x.ai/v1"),
        max_tokens=900,
        temperature=0.2,
        api_key_env="XAI_API_KEY",
    ),

    # ── Fallback chain entries (used if OmniRoute unavailable) ────────────────
    # KieAI fallback (direct Kie.ai GPT-5.6 Terra / Luna)
    "fallback_kie": ModelConfig(
        name=_KIE_MODEL,
        provider="kie",
        tier="balanced",
        base_url=_KIE_BASE_URL,
        max_tokens=1024,
        temperature=0.3,
        api_key_env="KIE_API_KEY",
    ),

    # OpenRouter fallback
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

    if prov == "omniroute":
        # OmniRoute — OpenAI-compatible endpoint that auto-selects the best available model.
        # Supports all auto/* presets: auto/smart, auto/fast, auto/cheap, auto/reasoning,
        # auto/coding, auto/best-free, etc. Falls back internally if a model is unavailable.
        #
        # IMPORTANT: OmniRoute defaults to SSE streaming (text/event-stream).
        # We MUST set stream=False to get a standard JSON response body.
        base_url = cfg.base_url or _OMNIROUTE_BASE_URL
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict = {
            "model": cfg.name,
            "messages": messages,
            "max_tokens": tokens,
            "temperature": temp,
            "stream": False,  # Force non-streaming JSON response
        }
        if response_json:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://ai-trading-platform.local",
            "X-Title": "AI Trading Platform",
        }
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
            )
            if not resp.is_success:
                raise httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}: {resp.text[:300]}",
                    request=resp.request,
                    response=resp,
                )

            content_type = resp.headers.get("content-type", "")

            # Handle SSE stream defensively (shouldn't happen with stream=False but guard anyway)
            if "text/event-stream" in content_type:
                text_pieces = []
                for line in resp.text.splitlines():
                    line = line.strip()
                    if line.startswith("data:"):
                        raw = line[5:].strip()
                        if raw and raw != "[DONE]":
                            try:
                                chunk = json.loads(raw)
                                choices = chunk.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    piece = delta.get("content") or delta.get("reasoning_content", "")
                                    if piece:
                                        text_pieces.append(piece)
                            except json.JSONDecodeError:
                                pass
                content = "".join(text_pieces)
                if not content:
                    raise ValueError("OmniRoute SSE stream returned no content")
                return content

            # Standard JSON response
            try:
                data = resp.json()
            except Exception:
                raise ValueError(
                    f"OmniRoute returned non-JSON body (status={resp.status_code}): {resp.text[:200]}"
                )
            choices = data.get("choices", [])
            if not choices:
                raise ValueError(f"OmniRoute returned no choices: {data}")
            msg = choices[0].get("message", {})
            content = msg.get("content") or msg.get("reasoning_content") or ""
            if not content:
                raise ValueError("OmniRoute returned empty content in message")
            used_model = data.get("model", cfg.name)
            logger.info(f"OmniRoute: success via model={used_model} provider={data.get('provider', '?')}")
            return content

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
            
    elif prov == "kie":
        # Kie.ai supports Claude (/claude/v1/messages) and GPT/ChatGPT models (/codex/v1/responses)
        is_claude = "claude" in cfg.name.lower()
        if is_claude:
            url = f"{cfg.base_url.rstrip('/')}/v1/messages" if "/claude" in (cfg.base_url or "") else "https://api.kie.ai/claude/v1/messages"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }
            messages = [{"role": "user", "content": prompt}]
            payload = {
                "model": cfg.name,
                "messages": messages,
                "max_tokens": tokens,
            }
            if system:
                payload["system"] = [
                    {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
                ]
            async with httpx.AsyncClient(timeout=45.0) as client:
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
                        stripped = text.lstrip()
                        if not stripped.startswith("{") and not stripped.startswith("```"):
                            text = "{" + text
                    out_tokens = (data.get("usage") or {}).get("output_tokens", 0)
                    truncated = data.get("stop_reason") == "max_tokens" or out_tokens >= attempt_tokens
                    if truncated and response_json:
                        logger.warning(
                            f"LLM output truncated at max_tokens={attempt_tokens} for {cfg.name}; retrying with larger budget"
                        )
                        continue
                    return text
                raise ValueError(f"Output still truncated at max_tokens={tokens * 2} for {cfg.name}")
        else:
            # GPT / ChatGPT / Codex models on Kie.ai (e.g. gpt-5-6-terra, gpt-5-6-luna)
            url = f"{cfg.base_url.rstrip('/')}/codex/v1/responses" if cfg.base_url and "api.kie.ai" in cfg.base_url else "https://api.kie.ai/codex/v1/responses"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }
            input_msgs = []
            if system:
                input_msgs.append({
                    "role": "system",
                    "content": [{"type": "text", "text": system}]
                })
            input_msgs.append({
                "role": "user",
                "content": [{"type": "text", "text": prompt}]
            })
            payload = {
                "model": cfg.name,
                "stream": False,
                "input": input_msgs,
            }
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if not resp.is_success:
                    raise httpx.HTTPStatusError(f"HTTP {resp.status_code}: {resp.text[:200]}", request=resp.request, response=resp)
                data = resp.json()
                text = ""
                if isinstance(data.get("output"), list):
                    for item in data["output"]:
                        for part in item.get("content", []):
                            if isinstance(part, dict) and part.get("type") in ("text", "output_text"):
                                text += part.get("text", "")
                elif isinstance(data.get("choices"), list) and data["choices"]:
                    choice = data["choices"][0]
                    msg = choice.get("message") or {}
                    text = msg.get("content", "") if isinstance(msg, dict) else str(choice.get("text", ""))
                elif "data" in data and isinstance(data["data"], dict):
                    text = data["data"].get("content", "") or data["data"].get("text", "")
                elif "text" in data and isinstance(data["text"], str):
                    text = data["text"]
                elif "response" in data and isinstance(data["response"], str):
                    text = data["response"]
                if not text and isinstance(data, dict):
                    text = json.dumps(data)
                return text

    elif prov == "anthropic":
        # Anthropic messages format
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        messages = [{"role": "user", "content": prompt}]
        if response_json:
            messages.append({"role": "assistant", "content": "{"})
        payload = {
            "model": cfg.name,
            "messages": messages,
            "max_tokens": tokens,
        }
        if system:
            payload["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
            
        async with httpx.AsyncClient(timeout=45.0) as client:
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
                    stripped = text.lstrip()
                    if not stripped.startswith("{") and not stripped.startswith("```"):
                        text = "{" + text
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
        # ── PRIMARY: OmniRoute (auto-selects best available model / free tier) ──
        ("Primary (OmniRoute)", primary_cfg),
        # ── FALLBACK 1: KieAI direct (Kie.ai GPT-5.6 Terra / Luna) ──────────────
        ("Fallback 1 (KieAI)", _DEFAULT_REGISTRY["fallback_kie"]),
        # ── FALLBACK 2: OpenRouter multi-model gateway ────────────────────────
        ("Fallback 2 (OpenRouter)", _DEFAULT_REGISTRY["fallback_1"]),
        # ── FALLBACK 3: xAI Grok ─────────────────────────────────────────────
        ("Fallback 3 (xAI)", ModelConfig(
            name=os.getenv('XAI_MODEL', 'grok-4-1-fast-reasoning'),
            provider='xai',
            base_url=os.getenv('XAI_BASE_URL', 'https://api.x.ai/v1'),
            api_key_env='XAI_API_KEY'
        )),
        # ── FALLBACK 4: OpenAI GPT ────────────────────────────────────────────
        ("Fallback 4 (OpenAI)", ModelConfig(
            name=os.getenv('OPENAI_MODEL', 'gpt-4o-mini'),
            provider='openai',
            base_url=os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1'),
            api_key_env='OPENAI_API_KEY'
        )),
        # ── FALLBACK 5: Anthropic direct ──────────────────────────────────────
        ("Fallback 5 (Anthropic)", _DEFAULT_REGISTRY["fallback_2"]),
        # ── FALLBACK 6: Gemini via OpenRouter ─────────────────────────────────
        ("Fallback 6 (Gemini)", _DEFAULT_REGISTRY["fallback_3"]),
    ]
    if os.getenv("OLLAMA_ENABLED", "false").lower() == "true":
        chain.append(("Fallback 7 (Ollama)", ModelConfig(
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
