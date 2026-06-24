import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import List, Tuple

from backend.llm.router import call_llm_resilient
from backend.services.crypto_news_service import crypto_news_service
from backend.services.influxdb_writer import influx
from backend.utils.telegram import send_telegram_message

logger = logging.getLogger("market_alerts")

_ALERT_SYMBOLS = ("CRYPTO", "BTC", "ETH", "SOL")


def derive_alert_points(output: dict) -> List[Tuple[str, float]]:
    """Map LLM JSON to Influx alert_type + score pairs for opinion_layer."""
    confidence = float(output.get("confidence", 0) or 0)
    bias = str(output.get("bias", "HOLD")).upper()
    mood = str(output.get("marketMood", "NEUTRAL")).upper()
    points: List[Tuple[str, float]] = []
    if bias == "BUY" or mood == "RISK_ON":
        points.append(("trending", confidence))
    if bias == "SELL" or mood == "RISK_OFF":
        points.append(("dump", confidence))
    if not points and confidence > 0:
        points.append(("neutral", confidence * 0.5))
    return points

class MarketAlertsLoop:
    def __init__(self):
        self._running = False
        self._task = None
        self._interval_minutes = int(os.getenv("MARKET_ALERTS_INTERVAL_MINUTES", "120"))
        
    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(f"MarketAlertsLoop started (interval: {self._interval_minutes}m)")
        
    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("MarketAlertsLoop stopped")
        
    async def _loop(self):
        # Initial sleep so we don't blast telegram immediately on every restart/crash loop
        await asyncio.sleep(60) 
        while self._running:
            try:
                await self.run_once()
            except Exception as e:
                logger.error(f"Error in MarketAlertsLoop: {e}")
            
            for _ in range(self._interval_minutes * 60):
                if not self._running:
                    break
                await asyncio.sleep(1)
                
    async def run_once(self, *, dry_run: bool = False, skip_telegram: bool = False):
        logger.info("Running Market Alerts cycle...")
        # 1. Fetch Global Fear & Greed
        fng = await crypto_news_service.get_fear_greed()
        fng_value = fng.get('value', 'Unknown')
        fng_class = fng.get('value_classification', 'Unknown')
        
        # 2. Get Recent News for top coins (BTC, ETH, SOL)
        symbols = ["BTC", "ETH", "SOL"]
        news_summaries = []
        for sym in symbols:
            articles = await crypto_news_service.get_crypto_news([sym])
            headlines = [a.get("title") for a in articles[:3] if a.get("title")]
            if headlines:
                news_summaries.append(f"{sym}: " + " | ".join(headlines))
                
        news_context = "\n".join(news_summaries)
        
        system_prompt = (
            "You are an expert Crypto Market Sentiment Analyst. "
            "You synthesize market conditions into a high-impact Telegram alert. "
            "Return ONLY raw JSON (no markdown blocks, no formatting). "
            "Output must be a JSON object with these exact keys: "
            "marketMood (RISK_ON, RISK_OFF, NEUTRAL), bias (BUY, SELL, HOLD), "
            "confidence (integer 0-100), tradingRecommendation (string), "
            "keyNarratives (array of strings), topRisks (array of strings)."
        )
        
        user_prompt = (
            f"Current Fear & Greed Index: {fng_value} ({fng_class})\n\n"
            f"Recent Headlines:\n{news_context}\n\n"
            "Analyze the market and provide your JSON output."
        )
        
        res_str = await call_llm_resilient(
            task_type="deep_analysis",
            prompt=user_prompt,
            system=system_prompt,
            temperature=0.3,
            max_tokens=300,
            response_json=True
        )
        
        try:
            output = json.loads(res_str)
        except Exception as e:
            logger.error(f"Failed to parse LLM JSON: {e}\nRaw output: {res_str}")
            return

        if not dry_run:
            await self._write_alerts_to_influx(output)

        bias = str(output.get("bias", "HOLD")).upper()
        mood = str(output.get("marketMood", "NEUTRAL")).upper()
        
        emoji = '🟢' if bias == 'BUY' else '🔴' if bias == 'SELL' else '🟡'
        mood_emoji = '🚀' if mood == 'RISK_ON' else '⚠️' if mood == 'RISK_OFF' else '😐'
        
        narratives = "\n".join(f"• {n}" for n in output.get("keyNarratives", []))
        risks = "\n".join(f"• {r}" for r in output.get("topRisks", []))
        
        msg = (
            f"{emoji} <b>Market Sentiment Update</b> {mood_emoji}\n\n"
            f"📊 <b>Market Mood:</b> {mood}\n"
            f"🎯 <b>Bias:</b> {bias} ({output.get('confidence', 0)}% confidence)\n\n"
            f"💡 <b>Recommendation:</b>\n{output.get('tradingRecommendation', 'N/A')}\n\n"
            f"🔑 <b>Key Narratives:</b>\n{narratives}\n\n"
            f"⚠️ <b>Risks:</b>\n{risks}\n\n"
            f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"
        )
        
        if not dry_run and not skip_telegram:
            sent = await send_telegram_message(msg, parse_mode="HTML")
            if sent:
                logger.info("Market alert sent to Telegram.")
            else:
                logger.warning("Market alert Telegram send skipped or failed.")
        else:
            logger.info("Market alert cycle complete (telegram skipped).")

    async def _write_alerts_to_influx(self, output: dict) -> int:
        """Persist alert scores so opinion_layer can read market_alert points."""
        alert_points = derive_alert_points(output)
        if not alert_points:
            logger.warning("No alert points derived from LLM output")
            return 0

        written = 0
        for symbol in _ALERT_SYMBOLS:
            for alert_type, score in alert_points:
                await influx.write_market_alert(
                    symbol=symbol,
                    alert_type=alert_type,
                    score=score,
                    source="market_alerts_loop",
                )
                written += 1
        logger.info(
            "Wrote %d market_alert point(s) to Influx (%s)",
            written,
            ", ".join(f"{t}:{s:.0f}" for t, s in alert_points),
        )
        return written

    def status(self) -> dict:
        return {
            "running": self._running,
            "interval_minutes": self._interval_minutes,
        }


market_alerts_loop = MarketAlertsLoop()
