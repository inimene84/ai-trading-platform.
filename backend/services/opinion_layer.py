"""
Opinion Layer — Multi-Agent Market Analysis Pipeline
====================================================
Integrates:
  1. ai-hedge-fund agents (technical, sentiment, risk, portfolio)
  2. Kronos foundation model forecast
  3. n8n social sentiment (X/Reddit/Discord/News)
  4. Market alerts (trending/pumps/whales)

Produces a unified TradingOpinion with direction, confidence, and reasoning.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import numpy as np

from backend.services import kronos_service
from backend.services.influxdb_sentiment_reader import sentiment_reader
from backend.services.persona_adapter import run_all_personas, get_persona_weights, set_persona_weight

logger = logging.getLogger(__name__)

from backend.database.connection import SessionLocal, engine
from backend.database.models import Trade, TradingSignal
import sqlalchemy as sa

def _get_trade_memory(symbol: str, limit: int = 5) -> dict:
    try:
        with SessionLocal() as db:
            trades = db.query(Trade).filter(
                sa.and_(Trade.symbol == symbol, Trade.status == "closed")
            ).order_by(Trade.closed_at.desc()).limit(limit).all()
            if not trades:
                return {"count": 0, "summary": "No recent closed trades"}
            total_pnl = sum((t.pnl or 0) for t in trades)
            wins = sum(1 for t in trades if (t.pnl or 0) > 0)
            avg_pnl = total_pnl / len(trades)
            summary = (
                f"Last {len(trades)} trades: {wins}W/{len(trades)-wins}L, "
                f"Avg PnL: {avg_pnl:+.4f}, Total: {total_pnl:+.4f}"
            )
            return {"count": len(trades), "wins": wins, "losses": len(trades)-wins,
                    "avg_pnl": avg_pnl, "total_pnl": total_pnl, "summary": summary}
    except Exception as e:
        logging.getLogger(__name__).warning(f"Trade memory query failed for {symbol}: {e}")
        return {"count": 0, "summary": f"Error: {e}"}


# ═══════════════════════════════════════════════════════════════════════════════
# Types
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class AgentOpinion:
    agent: str
    signal: str          # "bullish" | "bearish" | "neutral"
    confidence: float    # 0.0 – 1.0
    reasoning: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class TradingOpinion:
    symbol: str
    direction: str       # "BUY" | "SELL" | "HOLD"
    confidence: float    # 0.0 – 1.0
    reasoning: str
    agent_opinions: List[AgentOpinion]
    kronos: dict = field(default_factory=dict)
    social: dict = field(default_factory=dict)
    alerts: List[dict] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ═══════════════════════════════════════════════════════════════════════════════
# Technical Analysis Adapter (uses existing hedge-fund agent logic)
# ═══════════════════════════════════════════════════════════════════════════════

def _run_technical_opinion(bars: pd.DataFrame, symbol: str) -> AgentOpinion:
    """
    Compute technical opinion using the same logic as technical_analyst_agent
    but directly on pre-fetched Binance bars (no FINANCIAL_DATASETS_API_KEY needed).
    """
    try:
        # Ensure DataFrame has the right columns
        df = bars.copy()
        if 'close' not in df.columns and 'Close' in df.columns:
            df = df.rename(columns={
                'Open': 'open', 'High': 'high', 'Low': 'low',
                'Close': 'close', 'Volume': 'volume'
            })

        # Import signal calculation functions from existing agent
        from backend.agents.technicals import (
            calculate_trend_signals, calculate_mean_reversion_signals,
            calculate_momentum_signals, calculate_volatility_signals,
            calculate_stat_arb_signals, weighted_signal_combination
        )

        strategy_weights = {
            "trend": 0.25,
            "mean_reversion": 0.20,
            "momentum": 0.25,
            "volatility": 0.15,
            "stat_arb": 0.15,
        }

        trend = calculate_trend_signals(df)
        mean_rev = calculate_mean_reversion_signals(df)
        momentum = calculate_momentum_signals(df)
        volatility = calculate_volatility_signals(df)
        stat_arb = calculate_stat_arb_signals(df)

        combined = weighted_signal_combination({
            "trend": trend,
            "mean_reversion": mean_rev,
            "momentum": momentum,
            "volatility": volatility,
            "stat_arb": stat_arb,
        }, strategy_weights)

        sig = combined["signal"]
        conf = combined["confidence"]

        # Map to BUY/SELL/NEUTRAL for reasoning
        direction_map = {"bullish": "BUY", "bearish": "SELL", "neutral": "HOLD"}

        reasoning_parts = []
        for name, data in [
            ("Trend", trend), ("Mean Reversion", mean_rev),
            ("Momentum", momentum), ("Volatility", volatility),
            ("Stat Arb", stat_arb)
        ]:
            reasoning_parts.append(f"{name}: {data['signal']}({data['confidence']:.0%})")

        return AgentOpinion(
            agent="technical_analyst",
            signal=sig,
            confidence=conf,
            reasoning=f"Technical composite: {sig} | " + " | ".join(reasoning_parts),
            metadata={
                "trend": trend, "mean_reversion": mean_rev,
                "momentum": momentum, "volatility": volatility, "stat_arb": stat_arb
            }
        )
    except Exception as e:
        logger.warning(f"Technical opinion error for {symbol}: {e}")
        return AgentOpinion(
            agent="technical_analyst", signal="neutral", confidence=0.0,
            reasoning=f"Error: {e}"
        )


# NOTE: _run_kronos_opinion() was removed — Kronos is now handled inline in
# analyze_symbol() to avoid asyncio.run() inside an already-running event loop.


# ═══════════════════════════════════════════════════════════════════════════════
# Social Sentiment Opinion
# ═══════════════════════════════════════════════════════════════════════════════

def _run_social_opinion(symbol: str) -> AgentOpinion:
    """Get social sentiment opinion from n8n/InfluxDB."""
    try:
        sent = sentiment_reader.get_sentiment(symbol, lookback_minutes=60)
        if not sent:
            return AgentOpinion(
                agent="social_sentiment", signal="neutral", confidence=0.0,
                reasoning="No social sentiment data available"
            )

        direction = sent.get("direction", "NEUTRAL")
        signal_map = {"BUY": "bullish", "SELL": "bearish", "NEUTRAL": "neutral"}
        signal = signal_map.get(direction, "neutral")
        confidence = sent.get("confidence", 0.0)
        sources = sent.get("sources", {})

        def _fmt_src(k, v):
            try:
                return f"{k}({float(v):+.2f})"
            except (TypeError, ValueError):
                return f"{k}({v})"

        reasoning = (
            f"Social score {sent.get('sentiment_score', 0):+.3f} "
            f"from {sent.get('article_count', 0)} posts. "
            f"Sources: {', '.join(_fmt_src(k, v) for k, v in list(sources.items())[:3])}"
        )

        return AgentOpinion(
            agent="social_sentiment",
            signal=signal,
            confidence=confidence,
            reasoning=reasoning,
            metadata=sent
        )
    except Exception as e:
        logger.warning(f"Social opinion error for {symbol}: {e}")
        return AgentOpinion(
            agent="social_sentiment", signal="neutral", confidence=0.0,
            reasoning=f"Error: {e}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Market Alert Opinion
# ═══════════════════════════════════════════════════════════════════════════════

def _run_alert_opinion(symbol: str) -> AgentOpinion:
    """Get market alert opinion (trending/pumps/whales)."""
    try:
        alerts = sentiment_reader.get_market_alerts(symbol, lookback_minutes=60)
        if not alerts:
            return AgentOpinion(
                agent="market_alerts", signal="neutral", confidence=0.0,
                reasoning="No market alerts"
            )

        # Score alerts: pump = bullish, dump = bearish
        bullish_score = 0.0
        bearish_score = 0.0
        reasons = []
        for a in alerts[:5]:
            atype = a.get("alert_type", "").lower()
            score = a.get("score", 0)
            if "pump" in atype or "trending" in atype or "outflow" in atype:
                bullish_score += score
                reasons.append(f"{atype}(+{score})")
            elif "dump" in atype or "inflow" in atype or "crash" in atype:
                bearish_score += score
                reasons.append(f"{atype}(-{score})")

        if bullish_score > bearish_score * 1.5:
            signal = "bullish"
            confidence = min(bullish_score / 200, 1.0)
        elif bearish_score > bullish_score * 1.5:
            signal = "bearish"
            confidence = min(bearish_score / 200, 1.0)
        else:
            signal = "neutral"
            confidence = 0.0

        return AgentOpinion(
            agent="market_alerts",
            signal=signal,
            confidence=confidence,
            reasoning="Alerts: " + ", ".join(reasons) if reasons else "No directional alerts",
            metadata={"alerts": alerts}
        )
    except Exception as e:
        logger.warning(f"Alert opinion error for {symbol}: {e}")
        return AgentOpinion(
            agent="market_alerts", signal="neutral", confidence=0.0,
            reasoning=f"Error: {e}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Opinion Aggregator (Portfolio Manager logic, simplified)
# ═══════════════════════════════════════════════════════════════════════════════

# Agent weights loaded from config at module level.
# Falls back to hardcoded defaults if YAML config is missing.
def _load_agent_config() -> dict:
    """Load agent_config.yaml and return the raw dict."""
    import yaml as _yaml
    _cfg_path = Path(__file__).resolve().parent.parent / "agents" / "config" / "agent_config.yaml"
    try:
        with open(_cfg_path, "r", encoding="utf-8") as f:
            return _yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning(f"Could not load agent_config.yaml: {e}. Using defaults.")
        return {}

_LOADED_CONFIG = _load_agent_config()
_AGENT_WEIGHTS = _LOADED_CONFIG.get("base_agents", {
    "technical_analyst": 0.25,
    "kronos_foundation": 0.20,
    "social_sentiment": 0.20,
    "market_alerts": 0.05,
})
_AGG_CFG = _LOADED_CONFIG.get("aggregation", {})
_BUY_THRESHOLD = float(_AGG_CFG.get("buy_threshold", 0.15))
_SELL_THRESHOLD = float(_AGG_CFG.get("sell_threshold", -0.15))
_CONF_SCORE_W = float(_AGG_CFG.get("confidence_score_weight", 0.6))
_CONF_CONVICTION_W = float(_AGG_CFG.get("confidence_conviction_weight", 0.4))

_SIGNAL_MAP = {
    "bullish": 1,
    "neutral": 0,
    "bearish": -1,
}


def _get_combined_weights() -> dict:
    """Merge base agent weights with persona weights."""
    combined = dict(_AGENT_WEIGHTS)
    combined.update(get_persona_weights())
    return combined


def _aggregate_opinions(
    symbol: str,
    opinions: List[AgentOpinion],
    kronos_result: dict,
    social_result: dict,
    alerts: List[dict],
) -> TradingOpinion:
    """
    Weighted-vote aggregation with confidence scaling.
    Adapted from portfolio_management_agent logic.
    """
    weighted_sum = 0.0
    total_possible_weight = 0.0
    conviction_sum = 0.0   # sum of weighted confidence magnitudes (direction-agnostic)
    opinion_lines = []

    for op in opinions:
        weight = _get_combined_weights().get(op.agent, 0.05)
        numeric = _SIGNAL_MAP.get(op.signal, 0)
        
        # Directional score: bullish pushes positive, bearish negative
        weighted_sum += numeric * weight * op.confidence
        # Conviction: how sure any agent is (regardless of direction)
        conviction_sum += weight * op.confidence
        total_possible_weight += weight
        
        opinion_lines.append(
            f"  • {op.agent}: {op.signal.upper()} (conf={op.confidence:.2f}, weight={weight})"
        )

    if total_possible_weight > 0:
        final_score = weighted_sum / total_possible_weight
        # Average conviction (0-1): how strongly agents feel overall
        avg_conviction = conviction_sum / total_possible_weight
    else:
        final_score = 0.0
        avg_conviction = 0.0

    # Map score to direction (thresholds from config)
    if final_score > _BUY_THRESHOLD:
        direction = "BUY"
    elif final_score < _SELL_THRESHOLD:
        direction = "SELL"
    else:
        direction = "HOLD"

    # Confidence = directional clarity blended with average conviction
    # abs(final_score) measures directional consensus; avg_conviction measures overall engagement
    # Weights from config (default: 60% directional + 40% conviction)
    confidence = min(_CONF_SCORE_W * abs(final_score) + _CONF_CONVICTION_W * avg_conviction, 1.0)

    # Build reasoning
    reasoning = (
        f"Opinion Layer consensus: {direction} (score={final_score:+.3f}, conf={confidence:.2f})\n\n"
        f"Agent votes:\n" + "\n".join(opinion_lines) + "\n\n"
        f"Kronos: {kronos_result.get('signal', 'N/A')} "
        f"({kronos_result.get('predicted_change_pct', 0):+.2f}%)\n"
        f"Social: {social_result.get('direction', 'N/A')} "
        f"score={social_result.get('sentiment_score', 0):+.3f}\n"
        f"Alerts: {len(alerts)} active"
    )

    return TradingOpinion(
        symbol=symbol,
        direction=direction,
        confidence=confidence,
        reasoning=reasoning,
        agent_opinions=opinions,
        kronos=kronos_result,
        social=social_result,
        alerts=alerts,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════════

async def analyze_symbol(
    symbol: str,
    bars: list[dict],
    include_kronos: bool = True,
    include_social: bool = True,
    include_alerts: bool = True,
    include_personas: bool = True,
    metrics: Optional[dict] = None,
) -> TradingOpinion:
    """
    Run the full Opinion Layer pipeline for a symbol.

    Args:
        symbol: Trading symbol (e.g. "BTCUSDT")
        bars: List of OHLCV dicts from Binance
        include_kronos: Whether to run Kronos forecast
        include_social: Whether to fetch n8n social sentiment
        include_alerts: Whether to fetch market alerts
        include_personas: Whether to run hedge-fund persona agents
        metrics: Optional extra metrics (funding_rate, open_interest, etc.)

    Returns:
        TradingOpinion with unified direction/confidence/reasoning
    """
    started = datetime.now(timezone.utc)
    logger.info(f"Opinion Layer starting for {symbol}")

    # Convert bars to DataFrame
    df = pd.DataFrame(bars)
    if df.empty or len(df) < 20:
        return TradingOpinion(
            symbol=symbol, direction="HOLD", confidence=0.0,
            reasoning="Insufficient bar data",
            agent_opinions=[],
        )

    # Ensure column names match what technical agent expects
    col_map = {}
    for c in df.columns:
        lower = c.lower()
        if lower in ("open", "high", "low", "close", "volume"):
            col_map[c] = lower
    if col_map:
        df = df.rename(columns=col_map)

    # Run all opinion generators concurrently
    opinions: List[AgentOpinion] = []
    kronos_result: dict = {}
    social_result: dict = {}
    alerts: List[dict] = []

    # Technical (sync, runs in thread pool)
    tech_op = await asyncio.to_thread(_run_technical_opinion, df, symbol)
    opinions.append(tech_op)

    # Kronos (async)
    if include_kronos:
        try:
            kronos_result = await kronos_service.predict(df, symbol)
            k_sig = kronos_result.get("signal", "NEUTRAL").lower()
            if k_sig == "up":
                k_sig = "bullish"
            elif k_sig == "down":
                k_sig = "bearish"
            elif k_sig not in ("bullish", "bearish", "neutral"):
                k_sig = "neutral"
            opinions.append(AgentOpinion(
                agent="kronos_foundation",
                signal=k_sig,
                confidence=kronos_result.get("confidence", 0.0),
                reasoning=f"Kronos: {kronos_result.get('predicted_change_pct', 0):+.2f}%",
                metadata=kronos_result,
            ))
        except Exception as e:
            logger.warning(f"Kronos failed for {symbol}: {e}")
            opinions.append(AgentOpinion(
                agent="kronos_foundation", signal="neutral", confidence=0.0,
                reasoning=f"Error: {e}"
            ))

    # Social sentiment (sync, InfluxDB query)
    if include_social:
        try:
            social_op = await asyncio.to_thread(_run_social_opinion, symbol)
            opinions.append(social_op)
            social_result = social_op.metadata
        except Exception as e:
            logger.warning(f"Social sentiment failed for {symbol}: {e}")

    # Market alerts (sync, InfluxDB query)
    if include_alerts:
        try:
            alert_op = await asyncio.to_thread(_run_alert_opinion, symbol)
            opinions.append(alert_op)
            alerts = alert_op.metadata.get("alerts", [])
        except Exception as e:
            logger.warning(f"Market alerts failed for {symbol}: {e}")

    # Persona agents (async LLM calls, concurrent)
    # Trade memory injection
    trade_mem = _get_trade_memory(symbol)
    if not metrics:
        metrics = {}
    metrics["trade_memory"] = trade_mem
    if trade_mem.get("count", 0) > 0:
        opinions.append(AgentOpinion(
            agent="trade_memory",
            signal="neutral",
            confidence=min(0.3, trade_mem.get("count", 0) * 0.06),
            reasoning=trade_mem.get("summary", ""),
            metadata=trade_mem,
        ))
    if include_personas:
        try:
            persona_results = await run_all_personas(symbol, bars, metrics)
            for po in persona_results:
                opinions.append(AgentOpinion(
                    agent=po.persona,
                    signal=po.signal,
                    confidence=po.confidence,
                    reasoning=po.reasoning,
                    metadata={"persona": po.persona},
                ))
        except Exception as e:
            logger.warning(f"Persona agents failed for {symbol}: {e}")

    # Aggregate
    opinion = _aggregate_opinions(symbol, opinions, kronos_result, social_result, alerts)

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()

    # ── DEBUG: per-agent vote table emitted every cycle ──────────────────────
    sep = "─" * 55
    vote_lines = [f"┌{sep}┐", f"│  OPINION LAYER DEBUG — {symbol:<28}│",
                  f"│  Result: {opinion.direction:<6} conf={opinion.confidence:.2f}  score={opinion.confidence:.2f}  ({elapsed:.1f}s){'':>5}│",
                  f"├{sep}┤"]
    weights = _get_combined_weights()
    for op in opinion.agent_opinions:
        w = weights.get(op.agent, 0.05)
        bar = "▲" if op.signal == "bullish" else ("▼" if op.signal == "bearish" else "━")
        vote_lines.append(
            f"│  {bar} {op.agent:<24} {op.signal.upper():<8} conf={op.confidence:.2f}  w={w:.2f}  │"
        )
    vote_lines.append(f"├{sep}┤")
    vote_lines.append(f"│  Kronos: {kronos_result.get('signal','N/A'):<8} "
                       f"{kronos_result.get('predicted_change_pct',0):+.2f}%   "
                       f"Social: {social_result.get('direction','N/A'):<8} "
                       f"score={social_result.get('sentiment_score',0):+.3f}  │")
    vote_lines.append(f"│  Alerts active: {len(alerts):<4}  Trade memory: {opinion.agent_opinions and any(o.agent=='trade_memory' for o in opinion.agent_opinions)}{'':>14}│")
    vote_lines.append(f"└{sep}┘")
    logger.info("\n".join(vote_lines))
    # ─────────────────────────────────────────────────────────────────────────

    logger.info(
        f"Opinion Layer for {symbol}: {opinion.direction} "
        f"conf={opinion.confidence:.2f} ({elapsed:.1f}s, {len(opinions)} agents)"
    )
    return opinion


def register_agent_weight(agent_name: str, weight: float):
    """Dynamically adjust an agent's voting weight."""
    _AGENT_WEIGHTS[agent_name] = weight
    logger.info(f"Opinion Layer weight updated: {agent_name} = {weight}")


def register_persona_weight(persona_name: str, weight: float):
    """Dynamically adjust a persona agent's voting weight."""
    set_persona_weight(persona_name, weight)
