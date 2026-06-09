import os
import json
import logging
import re
import httpx
from typing import Optional, List, Dict, Any
from backend.llm.router import pick_model, get_api_key

logger = logging.getLogger("risk_reviewer")

_APPROVED_RE = re.compile(r'"approved"\s*:\s*(true|false)', re.IGNORECASE)


def _parse_reviewer_response(content: str) -> tuple[bool, str]:
    """Parse LLM reviewer output; fail-open (approve) when format is ambiguous."""
    if not content or not content.strip():
        return True, "Approved (empty LLM response — fail-open)."

    text = content.strip()
    # Strip markdown code fences if the model wrapped JSON
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()

    try:
        result = json.loads(text)
        approved = bool(result.get("approved", True))
        reasoning = str(result.get("reasoning", "No reasoning provided."))
        return approved, reasoning
    except Exception:
        pass

    match = _APPROVED_RE.search(text)
    if match:
        approved = match.group(1).lower() == "true"
        reason_match = re.search(r'"reasoning"\s*:\s*"([^"]*)"', text, re.IGNORECASE)
        reasoning = reason_match.group(1) if reason_match else "Parsed approved field from non-JSON response."
        return approved, reasoning

    logger.warning("Could not parse risk reviewer response; defaulting to APPROVED. Content: %s", text[:500])
    return True, "Approved (unparseable LLM response — fail-open)."

async def fetch_news_summary(symbol: str) -> str:
    """Fetch recent news analyses for a symbol from Qdrant vector store."""
    try:
        from backend.services.qdrant_client import qdrant
        base = symbol.replace("USDT", "").replace("USDC", "").replace("PERP", "").upper()
        keywords = [base.lower()]
        
        # Simple hardcoded coin name mapping to improve search query coverage
        coin_names = {
            "BTC": "bitcoin",
            "ETH": "ethereum",
            "SOL": "solana",
            "BNB": "binance coin",
            "XRP": "ripple",
            "ADA": "cardano",
            "LINK": "chainlink",
            "POL": "polygon",
            "OP": "optimism",
            "ARB": "arbitrum",
        }
        if base in coin_names:
            keywords.append(coin_names[base])
            
        docs = await qdrant.search_content(keywords, limit=5)
        if not docs:
            return "No recent news found in archive."
            
        summaries = []
        for i, doc in enumerate(docs):
            content = doc.get("content", "").strip()
            if len(content) > 250:
                content = content[:250] + "..."
            summaries.append(f"[{i+1}] {content}")
        return "\n".join(summaries)
    except Exception as e:
        logger.warning(f"Failed to fetch news for {symbol}: {e}")
        return f"No news available (fetch error: {e})"

async def review_trade_decision(
    symbol: str,
    action: str,
    quantity: float,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    confidence: float,
    funding_rate: float,
    news_summary: str
) -> tuple[bool, str]:
    """
    Sends a single completion call to Claude via LiteLLM to vet the trade ticket.
    Returns:
        (approved: bool, reasoning: str)
    """
    model_cfg = pick_model("deep_analysis")
    api_key = get_api_key(model_cfg)
    
    if not api_key:
        logger.warning("No API key configured for Risk Reviewer LLM. Defaulting to APPROVED.")
        return True, "Approved (LLM API key not configured)."
        
    base_url = model_cfg.base_url or "http://litellm:4000/v1"
    
    system_prompt = (
        "You are the Chief Risk Officer (CRO) of a systematic quantitative hedge fund. "
        "Your role is to perform a final review of proposed trade signals and VETO any trade that carries "
        "excessive risk, is counter-trend, is economically unviable, or contradicts major news sentiment. "
        "Be extremely critical. Your performance is measured by avoiding bad trades. "
        "You must respond ONLY with a valid JSON object matching this schema: "
        '{"approved": true|false, "reasoning": "A concise 1-2 sentence explanation of your decision."}'
    )
    
    user_prompt = (
        f"Please review the following proposed trade ticket:\n\n"
        f"Asset Symbol: {symbol}\n"
        f"Proposed Action: {action}\n"
        f"Position Size (Notional): ${quantity * entry_price:.2f} ({quantity:.4f} units)\n"
        f"Entry Price: ${entry_price:.4f}\n"
        f"Stop Loss: ${stop_loss:.4f} (risk is {abs(entry_price - stop_loss) / entry_price * 100:.2f}%)\n"
        f"Take Profit: ${take_profit:.4f} (potential gain is {abs(entry_price - take_profit) / entry_price * 100:.2f}%)\n"
        f"Signal Confidence: {confidence:.2f}\n"
        f"Current Funding Rate: {funding_rate * 100:.4f}% ({'LONG pays SHORT' if funding_rate > 0 else 'SHORT pays LONG' if funding_rate < 0 else 'Neutral'})\n\n"
        f"=== Relevant News Headlines & Analyses (from Qdrant vector database) ===\n"
        f"{news_summary}\n\n"
        f"Provide your decision strictly in JSON format."
    )
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_cfg.name,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 512,
                    "response_format": {"type": "json_object"},
                }
            )
            
            if not resp.is_success:
                logger.error(f"Risk reviewer LLM HTTP error: {resp.status_code} - {resp.text}")
                return True, f"Approved (LLM error: HTTP {resp.status_code})"
                
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return _parse_reviewer_response(content)
                
    except Exception as e:
        logger.error(f"Error calling risk reviewer LLM: {e}")
        return True, f"Approved (Error invoking reviewer: {str(e)})"
