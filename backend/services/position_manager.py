import os
import asyncio
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict
from dataclasses import dataclass, field
import logging

logger = logging.getLogger("position_manager")

@dataclass
class ExitOpinion:
    symbol: str
    exit: bool = False
    direction: str = "HOLD"
    confidence: float = 0.0
    reasoning: str = ""
    urgency: str = "low"
    suggested_action: str = "hold"
    pnl_pct: float = 0.0
    duration_hours: float = 0.0


class PositionManager:
    def __init__(self):
        self.emergency_drawdown_pct = float(os.getenv("EMERGENCY_DRAWDOWN_PCT", "-15.0"))
        self.max_hold_hours = float(os.getenv("MAX_POSITION_HOLD_HOURS", "72.0"))
        self.exit_opinion_threshold = float(os.getenv("EXIT_OPINION_THRESHOLD", "0.65"))
        self.enable_personas = os.getenv("ENABLE_PERSONAS", "true").lower() == "true"

    async def analyze_open_position(self, symbol: str, trade: dict, bars: list,
                                    current_price: float, opinion_layer_fn=None) -> ExitOpinion:
        entry_price = trade.get("entry_price", current_price)
        direction = trade.get("direction", "BUY")
        opened_at = trade.get("opened_at")
        stop_loss = trade.get("stop_loss")
        take_profit = trade.get("take_profit")

        if direction == "BUY":
            pnl_pct = ((current_price - entry_price) / entry_price) * 100 if entry_price else 0
        else:
            pnl_pct = ((entry_price - current_price) / entry_price) * 100 if entry_price else 0

        duration_hours = 0
        if opened_at:
            from dateutil import parser as dateutil_parser
            try:
                if isinstance(opened_at, str):
                    opened_at = dateutil_parser.parse(opened_at)
                duration_hours = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
            except Exception:
                pass

        result = ExitOpinion(symbol=symbol, pnl_pct=pnl_pct, duration_hours=duration_hours)

        # 1. EMERGENCY DRAWDOWN
        if pnl_pct <= self.emergency_drawdown_pct:
            result.exit = True
            result.direction = "EXIT"
            result.confidence = 1.0
            result.urgency = "emergency"
            result.suggested_action = "close_now"
            result.reasoning = f"EMERGENCY: PnL {pnl_pct:.1f}% exceeds max drawdown ({self.emergency_drawdown_pct:.1f}%)."
            logger.warning(f"  [EXIT] {symbol}: EMERGENCY DRAWDOWN ({pnl_pct:.1f}%)")
            return result

        # 2. TIME-BASED EXIT (negative only)
        if duration_hours >= self.max_hold_hours and pnl_pct < 0:
            result.exit = True
            result.direction = "EXIT"
            result.confidence = 0.85
            result.urgency = "high"
            result.suggested_action = "close_now"
            result.reasoning = f"TIME EXIT: Held {duration_hours:.1f}h with negative PnL {pnl_pct:.1f}%."
            logger.warning(f"  [EXIT] {symbol}: MAX HOLD TIME ({duration_hours:.1f}h, PnL {pnl_pct:.1f}%)")
            return result

        # 3. AI OPINION LAYER
        if opinion_layer_fn and len(bars) >= 10:
            try:
                ai_opinion = await opinion_layer_fn(
                    symbol=symbol, bars=bars, include_kronos=True,
                    include_personas=self.enable_personas,
                    metrics={
                        "pnl_pct": pnl_pct, "duration_hours": duration_hours,
                        "entry_price": entry_price, "current_price": current_price,
                        "direction": direction, "stop_loss": stop_loss, "take_profit": take_profit,
                    }
                )
                if hasattr(ai_opinion, 'direction') and ai_opinion.direction == "HOLD":
                    if pnl_pct < -8 and ai_opinion.confidence < 0.5:
                        result.exit = True
                        result.direction = "EXIT"
                        result.confidence = 0.7
                        result.urgency = "high"
                        result.suggested_action = "close_now"
                        result.reasoning = f"AI HOLD weak ({ai_opinion.confidence:.2f}) with deep loss ({pnl_pct:.1f}%)."
                        logger.warning(f"  [EXIT] {symbol}: AI HOLD weak, deep loss ({pnl_pct:.1f}%)")
                        return result

                elif hasattr(ai_opinion, 'direction') and ai_opinion.direction in ("SELL", "BUY"):
                    ai_dir = ai_opinion.direction
                    is_exit = (direction == "BUY" and ai_dir == "SELL") or (direction == "SELL" and ai_dir == "BUY")
                    if is_exit and ai_opinion.confidence >= self.exit_opinion_threshold:
                        result.exit = True
                        result.direction = "EXIT"
                        result.confidence = ai_opinion.confidence
                        result.urgency = "high" if pnl_pct < -5 else "medium"
                        result.suggested_action = "close_now"
                        result.reasoning = f"AI REVERSAL: {ai_dir} (conf={ai_opinion.confidence:.2f}) vs our {direction}. PnL={pnl_pct:.1f}%."
                        logger.warning(f"  [EXIT] {symbol}: AI reversal {ai_dir} (conf={ai_opinion.confidence:.2f}) vs {direction}")
                        return result
                    elif is_exit and ai_opinion.confidence >= 0.45:
                        result.exit = False
                        result.direction = "HOLD"
                        result.confidence = ai_opinion.confidence
                        result.urgency = "low"
                        result.suggested_action = "set_tighter_sl"
                        result.reasoning = f"AI weak reversal: {ai_dir} (conf={ai_opinion.confidence:.2f}). Tightening SL. PnL={pnl_pct:.1f}%"
                        logger.info(f"  [HOLD] {symbol}: AI weak reversal, tighten SL")
                        return result
            except Exception as e:
                logger.error(f"  Position AI analysis failed for {symbol}: {e}")

        # 4. TECHNICAL DETERIORATION
        if len(bars) >= 20:
            try:
                closes = [b["close"] for b in bars[-20:]]
                recent_avg = sum(closes[-5:]) / 5
                prev_avg = sum(closes[-10:-5]) / 5
                if direction == "BUY" and recent_avg < prev_avg * 0.985 and pnl_pct < -5:
                    result.exit = True; result.direction = "EXIT"; result.confidence = 0.6
                    result.urgency = "medium"; result.suggested_action = "close_now"
                    result.reasoning = f"TECHNICAL EXIT: Downtrend with loss {pnl_pct:.1f}%."
                    logger.warning(f"  [EXIT] {symbol}: Technical deterioration ({pnl_pct:.1f}%)")
                    return result
                elif direction == "SELL" and recent_avg > prev_avg * 1.015 and pnl_pct < -5:
                    result.exit = True; result.direction = "EXIT"; result.confidence = 0.6
                    result.urgency = "medium"; result.suggested_action = "close_now"
                    result.reasoning = f"TECHNICAL EXIT: Uptrend against SHORT with loss {pnl_pct:.1f}%."
                    logger.warning(f"  [EXIT] {symbol}: Technical deterioration ({pnl_pct:.1f}%)")
                    return result
            except Exception as e:
                logger.error(f"  Technical analysis error for {symbol}: {e}")

        result.exit = False
        result.direction = "HOLD"
        result.reasoning = f"HOLD: PnL={pnl_pct:.1f}%, {duration_hours:.1f}h held."
        return result

    async def should_exit(self, symbol: str, trade: dict, bars: list, current_price: float, opinion_layer_fn=None) -> bool:
        opinion = await self.analyze_open_position(symbol, trade, bars, current_price, opinion_layer_fn)
        return opinion.exit


_position_manager = None
def get_position_manager():
    global _position_manager
    if _position_manager is None:
        _position_manager = PositionManager()
    return _position_manager
