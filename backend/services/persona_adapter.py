"""
Persona Agent Adapter for Crypto
================================
Adapts ai-hedge-fund investor persona agents for crypto trading.
Each persona receives crypto-specific metrics (funding, OI, volatility,
exchange flows, recent returns) and responds in their signature style.

Supported personas: buffett, burry, druckenmiller, cathie_wood, peter_lynch,
phil_fisher, munger, taleb, ackman, damodaran, pabrai, jhunjhunwala
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# LLM config (reuses existing backend LLM infra)
_DEFAULT_PROVIDER = os.getenv("PERSONA_LLM_PROVIDER", "groq")
_DEFAULT_MODEL = os.getenv("PERSONA_LLM_MODEL", "deepseek-r1-distill-llama-70b")
_DEFAULT_API_KEY = os.getenv("GROQ_API_KEY", "")
_DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"


@dataclass
class PersonaOpinion:
    persona: str
    signal: str       # bullish | bearish | neutral
    confidence: float # 0-1
    reasoning: str


# ═══════════════════════════════════════════════════════════════════════════════
# Persona system prompts (crypto-adapted)
# ═══════════════════════════════════════════════════════════════════════════════

_PERSONA_PROMPTS = {
    "warren_buffett": (
        "You are Warren Buffett analyzing a crypto asset.\n"
        "Principles: Only buy assets with durable competitive advantage (network effect, \n"
        "developer activity, institutional adoption). Avoid speculation.\n"
        "Look for: strong holder base, low leverage, consistent fees/revenue, \n"
        "growing institutional treasury allocation.\n"
        "Style: plain-spoken, patient, value-oriented. Cite specific metrics."
    ),
    "michael_burry": (
        "You are Michael Burry analyzing a crypto asset.\n"
        "Principles: Deep value contrarian. Hunt for mispriced assets hated by the crowd.\n"
        "Look for: extreme funding-rate negativity, high short interest, \n"
        "strong on-chain fundamentals despite price weakness, insider/whale accumulation.\n"
        "Style: terse, data-driven, skeptical of consensus. Flag hidden risks."
    ),
    "stanley_druckenmiller": (
        "You are Stanley Druckenmiller analyzing a crypto asset.\n"
        "Principles: Macro-driven, asymmetric bets. Cut losses fast, let winners run.\n"
        "Look for: liquidity inflection points, central bank policy tailwinds, \n"
        "dollar weakness signals, momentum shifts in futures open interest.\n"
        "Style: bold, macro-aware, willing to take concentrated positions."
    ),
    "cathie_wood": (
        "You are Cathie Wood analyzing a crypto asset.\n"
        "Principles: Disruptive innovation. 5-year horizon. Ignore short-term volatility.\n"
        "Look for: protocol innovation, developer growth, new use cases (DeFi, RWA, AI), \n"
        "declining issuance schedules, staking yield sustainability.\n"
        "Style: optimistic about technology, long-term focused, high conviction."
    ),
    "peter_lynch": (
        "You are Peter Lynch analyzing a crypto asset.\n"
        "Principles: Invest in what you understand. Growth at a reasonable price.\n"
        "Look for: undervalued relative to user growth, revenue per user expanding, \n"
        "narrative gaining real-world traction, not just hype.\n"
        "Style: accessible, practical, \"ten-bagger\" hunting."
    ),
    "charlie_munger": (
        "You are Charlie Munger analyzing a crypto asset.\n"
        "Principles: Invert, always invert. Avoid stupidity rather than seek brilliance.\n"
        "Look for: what could kill this asset? Regulatory risk, centralization, \n"
        "unsustainable yield, concentration of supply.\n"
        "Style: blunt, wisdom-driven, focuses on what NOT to do."
    ),
    "nassim_taleb": (
        "You are Nassim Taleb analyzing a crypto asset.\n"
        "Principles: Skin in the game. Antifragility. Tail-risk obsessed.\n"
        "Look for: convex payoff structures, options skew, liquidation cascade risks, \n"
        "protocol death spirals, correlation breakdowns during stress.\n"
        "Style: philosophical, skeptical of models, obsessed with tail risks."
    ),
    "bill_ackman": (
        "You are Bill Ackman analyzing a crypto asset.\n"
        "Principles: Activist, high-conviction, platform-quality focus.\n"
        "Look for: governance improvements, treasury management, fee switch potential, \n"
        "undervalued but fixable protocols.\n"
        "Style: confident, detailed thesis, willing to be loud and contrarian."
    ),
    "phil_fisher": (
        "You are Phil Fisher analyzing a crypto asset.\n"
        "Principles: Scuttlebutt research. Talk to developers, users, competitors.\n"
        "Look for: product quality, R&D velocity, community health, \n"
        "competitive moat through continuous innovation.\n"
        "Style: meticulous, research-intensive, growth-at-any-price if quality is there."
    ),
    "aswath_damodaran": (
        "You are Aswath Damodaran analyzing a crypto asset.\n"
        "Principles: Valuation is story + numbers. Every asset has a fair value.\n"
        "Look for: token cash flows (fees burned, staking yield), network value / transaction ratio, \n"
        "discount rates reflecting crypto risk premium.\n"
        "Style: academic, structured, builds valuation models from first principles."
    ),
    "mohnish_pabrai": (
        "You are Mohnish Pabrai analyzing a crypto asset.\n"
        "Principles: Dhando investor. Heads I win big, tails I don't lose much.\n"
        "Look for: asymmetric setups, deeply discounted to liquidation value, \n"
        "strong community support at low prices, minimal downside.\n"
        "Style: patient, risk-averse, focused on margin of safety."
    ),
    "rakesh_jhunjhunwala": (
        "You are Rakesh Jhunjhunwala analyzing a crypto asset.\n"
        "Principles: The Big Bull. Conviction + patience. Buy fear, sell euphoria.\n"
        "Look for: mass retail fear but whale accumulation, strong domestic adoption, \n"
        "undervalued relative to historical cycles.\n"
        "Style: bold, optimistic, long-term holder mentality."
    ),
}

_PERSONA_WEIGHTS = {
    "warren_buffett": 0.08,
    "michael_burry": 0.10,
    "stanley_druckenmiller": 0.10,
    "cathie_wood": 0.08,
    "peter_lynch": 0.07,
    "charlie_munger": 0.08,
    "nassim_taleb": 0.12,
    "bill_ackman": 0.07,
    "phil_fisher": 0.07,
    "aswath_damodaran": 0.08,
    "mohnish_pabrai": 0.08,
    "rakesh_jhunjhunwala": 0.07,
}


# ═══════════════════════════════════════════════════════════════════════════════
# LLM caller
# ═══════════════════════════════════════════════════════════════════════════════

async def _call_llm(system: str, user: str) -> dict:
    """Call the LLM and return parsed JSON with signal, confidence, reasoning."""
    api_key = _DEFAULT_API_KEY
    if not api_key:
        logger.warning("No LLM API key for persona adapter")
        return {"signal": "neutral", "confidence": 0.0, "reasoning": "No API key"}

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{_DEFAULT_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": _DEFAULT_MODEL,
                    "messages": messages,
                    "temperature": 0.4,
                    "max_tokens": 512,
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            return {
                "signal": parsed.get("signal", "neutral").lower(),
                "confidence": float(parsed.get("confidence", 0)) / 100.0,
                "reasoning": parsed.get("reasoning", ""),
            }
    except Exception as e:
        logger.warning(f"Persona LLM call failed: {e}")
        return {"signal": "neutral", "confidence": 0.0, "reasoning": f"Error: {e}"}


def _build_crypto_context(symbol: str, bars: list, metrics: dict) -> str:
    """Build a compact crypto context string from bars and metrics."""
    if not bars:
        return "No price data available."

    df_last = bars[-1]
    price = df_last.get("close", 0)
    vol_24h = sum(b.get("volume", 0) for b in bars[-24:]) if len(bars) >= 24 else sum(b.get("volume", 0) for b in bars)

    # Calculate returns
    returns = []
    for i in range(1, len(bars)):
        prev = bars[i - 1].get("close", 0)
        curr = bars[i].get("close", 0)
        if prev > 0:
            returns.append((curr - prev) / prev)
    vol_annual = 0.0
    if returns:
        import numpy as np
        vol_annual = float(np.std(returns) * np.sqrt(len(returns)) * 100)

    # Recent performance
    if len(bars) >= 24:
        ret_1d = (bars[-1]["close"] - bars[-24]["close"]) / bars[-24]["close"] * 100
    else:
        ret_1d = 0.0
    if len(bars) >= 7:
        ret_7d = (bars[-1]["close"] - bars[-7]["close"]) / bars[-7]["close"] * 100
    else:
        ret_7d = 0.0
    if len(bars) >= 30:
        ret_30d = (bars[-1]["close"] - bars[-30]["close"]) / bars[-30]["close"] * 100
    else:
        ret_30d = 0.0

    funding = metrics.get("funding_rate", "N/A")
    oi = metrics.get("open_interest", "N/A")
    oi_change = metrics.get("oi_change_24h", "N/A")

    lines = [
        f"Asset: {symbol}",
        f"Current Price: ${price:,.2f}" if price > 1 else f"Current Price: ${price:.6f}",
        f"24h Volume: {vol_24h:,.0f}",
        f"1d Return: {ret_1d:+.2f}%",
        f"7d Return: {ret_7d:+.2f}%",
        f"30d Return: {ret_30d:+.2f}%",
        f"Annualized Volatility: {vol_annual:.1f}%",
        f"Funding Rate: {funding}",
        f"Open Interest: {oi} (24h change: {oi_change})",
    ]

    # Add technical snapshot
    if len(bars) >= 20:
        closes = [b["close"] for b in bars]
        sma_20 = sum(closes[-20:]) / 20
        lines.append(f"Price vs SMA20: {((price - sma_20) / sma_20 * 100):+.2f}%")
    if len(bars) >= 50:
        sma_50 = sum([b["close"] for b in bars[-50:]]) / 50
        lines.append(f"Price vs SMA50: {((price - sma_50) / sma_50 * 100):+.2f}%")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════════

async def run_persona(
    persona: str,
    symbol: str,
    bars: list,
    metrics: Optional[dict] = None,
) -> PersonaOpinion:
    """
    Run a single persona agent on crypto data.

    Args:
        persona: One of the keys in _PERSONA_PROMPTS
        symbol: Trading symbol
        bars: List of OHLCV dicts
        metrics: Optional dict with funding_rate, open_interest, etc.

    Returns:
        PersonaOpinion with signal, confidence, reasoning
    """
    system = _PERSONA_PROMPTS.get(persona)
    if not system:
        return PersonaOpinion(
            persona=persona, signal="neutral", confidence=0.0,
            reasoning=f"Unknown persona: {persona}"
        )

    metrics = metrics or {}
    context = _build_crypto_context(symbol, bars, metrics)

    user = (
        f"Analyze the following crypto asset and return a JSON object with exactly these keys:\n"
        f'  "signal": "bullish" | "bearish" | "neutral"\n'
        f'  "confidence": integer 0-100 (how sure you are)\n'
        f'  "reasoning": concise explanation in your character\'s voice\n\n'
        f"Context:\n{context}\n\n"
        f"Respond ONLY with valid JSON. No markdown."
    )

    result = await _call_llm(system, user)

    # Normalize signal
    sig = result.get("signal", "neutral")
    if sig not in ("bullish", "bearish", "neutral"):
        sig = "neutral"

    return PersonaOpinion(
        persona=persona,
        signal=sig,
        confidence=min(max(result.get("confidence", 0.0), 0.0), 1.0),
        reasoning=result.get("reasoning", ""),
    )


async def run_all_personas(
    symbol: str,
    bars: list,
    metrics: Optional[dict] = None,
    selected: Optional[List[str]] = None,
) -> List[PersonaOpinion]:
    """
    Run all (or selected) persona agents concurrently.

    Args:
        symbol: Trading symbol
        bars: OHLCV list
        metrics: Optional extra metrics dict
        selected: List of persona names to run (None = all)

    Returns:
        List of PersonaOpinion
    """
    personas = selected or list(_PERSONA_PROMPTS.keys())
    tasks = [run_persona(p, symbol, bars, metrics) for p in personas]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    opinions = []
    for persona, result in zip(personas, results):
        if isinstance(result, Exception):
            logger.warning(f"Persona {persona} failed: {result}")
            opinions.append(PersonaOpinion(
                persona=persona, signal="neutral", confidence=0.0,
                reasoning=f"Error: {result}"
            ))
        else:
            opinions.append(result)

    return opinions


def get_persona_weights() -> Dict[str, float]:
    """Get default voting weights for persona agents."""
    return dict(_PERSONA_WEIGHTS)


def set_persona_weight(persona: str, weight: float):
    """Adjust a persona's voting weight."""
    _PERSONA_WEIGHTS[persona] = weight
    logger.info(f"Persona weight updated: {persona} = {weight}")
