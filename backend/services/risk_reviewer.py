import json
import logging
import os
import re

from backend.services.trading_mode import TradingMode, get_trading_mode

logger = logging.getLogger("risk_reviewer")

_APPROVED_RE = re.compile(r'"approved"\s*:\s*(true|false)', re.IGNORECASE)


def _reviewer_outage_fail_open() -> bool:
    """Whether a risk-reviewer OUTAGE (LLM chain down) may approve trades.

    Fail-open on outage is only acceptable in paper/backtest. In LIVE mode an
    LLM outage must fail-closed: with all providers down, every trade was
    previously auto-approved, silently removing the veto gate exactly when
    conditions are most degraded. RISK_REVIEWER_FAIL_OPEN=true overrides.
    """
    if os.getenv("RISK_REVIEWER_FAIL_OPEN", "false").lower() == "true":
        return True
    return get_trading_mode() != TradingMode.LIVE


def _parse_reviewer_response(content: str) -> tuple[bool, str]:
    """Parse reviewer output; ambiguous output fails closed in live mode."""
    if not content or not content.strip():
        if _reviewer_outage_fail_open():
            return True, "Approved (empty LLM response — fail-open)."
        return False, "Rejected (empty LLM response — fail-closed in live mode)."

    text = content.strip()
    # Strip markdown code fences if the model wrapped JSON
    if "```" in text:
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        if fence:
            text = fence.group(1).strip()

    # If the model added prose around JSON, extract the first object block
    if not text.startswith("{"):
        obj = re.search(r"\{[\s\S]*\}", text)
        if obj:
            text = obj.group(0).strip()

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

    if _reviewer_outage_fail_open():
        logger.warning("Could not parse reviewer response; fail-open outside live mode. Content: %s", text[:500])
        return True, "Approved (unparseable LLM response — fail-open)."
    logger.error("Could not parse reviewer response; rejecting in live mode. Content: %s", text[:500])
    return False, "Rejected (unparseable LLM response — fail-closed in live mode)."

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
    system_prompt = (
        "You are the Chief Risk Officer (CRO) of a systematic quantitative hedge fund. "
        "The trading engine already filtered this signal; your job is a final sanity check — "
        "not to block every trade. APPROVE by default when risk/reward is reasonable, "
        "SL/TP are set, and news does not strongly contradict the direction. "
        "VETO only for clear problems: catastrophic opposing news, uneconomic funding, "
        "obvious counter-trend setup, or confidence below 0.5. "
        "Signals at confidence >= 0.75 with 2:1+ reward-to-risk should usually be APPROVED. "
        "Respond ONLY with valid JSON: "
        '{"approved": true|false, "reasoning": "A concise 1-2 sentence explanation."}'
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
        from backend.llm.router import call_llm_resilient
        res_str = await call_llm_resilient(
            task_type="deep_analysis",
            prompt=user_prompt,
            system=system_prompt,
            temperature=0.1,
            max_tokens=512,
            response_json=True
        )
        return _parse_reviewer_response(res_str)
    except Exception as e:
        logger.error(f"Error calling risk reviewer LLM: {type(e).__name__}: {e}")
        if _reviewer_outage_fail_open():
            return True, f"Approved (Error invoking reviewer: {type(e).__name__}: {e} — fail-open)"
        return False, (
            f"Rejected (risk reviewer unavailable: {type(e).__name__}: {e} — "
            "fail-closed in live mode; set RISK_REVIEWER_FAIL_OPEN=true to override)"
        )

