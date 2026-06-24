import os
import json
import logging
import httpx
from datetime import datetime, timezone, timedelta
from backend.database.connection import SessionLocal
from backend.database.models import Trade, TradingSignal, PortfolioSnapshot
from backend.llm.router import pick_model, get_api_key

logger = logging.getLogger("daily_digest")

def get_daily_stats() -> dict:
    """Query database for trade performance and signal stats in the last 24h."""
    db = SessionLocal()
    try:
        since = datetime.now(timezone.utc) - timedelta(days=1)
        
        # 1. Closed trades in last 24h
        closed_trades = db.query(Trade).filter(
            Trade.status == "closed",
            Trade.closed_at >= since
        ).all()
        
        total_pnl = sum(t.pnl for t in closed_trades if t.pnl is not None)
        wins = sum(1 for t in closed_trades if t.pnl is not None and t.pnl > 0)
        losses = sum(1 for t in closed_trades if t.pnl is not None and t.pnl <= 0)
        
        # 2. Open trades right now
        open_trades = db.query(Trade).filter(
            Trade.status.in_(["open", "filled"])
        ).all()
        open_positions_count = len(open_trades)
        open_exposure = sum(t.quantity * t.entry_price for t in open_trades if t.quantity and t.entry_price)
        
        # 3. Signals in last 24h
        total_signals = db.query(TradingSignal).filter(
            TradingSignal.timestamp >= since
        ).count()
        
        # Count vetoed signals
        vetoed_signals = db.query(TradingSignal).filter(
            TradingSignal.timestamp >= since,
            (TradingSignal.reasoning.like("%veto%") | TradingSignal.status.like("%reject%"))
        ).count()
        
        # 4. Latest portfolio snapshot for margin health
        latest_snapshot = db.query(PortfolioSnapshot).order_by(PortfolioSnapshot.timestamp.desc()).first()
        
        return {
            "total_pnl": total_pnl,
            "wins": wins,
            "losses": losses,
            "closed_count": len(closed_trades),
            "open_positions_count": open_positions_count,
            "open_exposure": open_exposure,
            "total_signals": total_signals,
            "vetoed_signals": vetoed_signals,
            "equity": latest_snapshot.total_value if latest_snapshot else 0.0,
            "cash": latest_snapshot.cash if latest_snapshot else 0.0,
        }
    finally:
        db.close()

async def generate_digest_text(stats: dict) -> str:
    """Generate a concise 5-line digest using the general LLM."""
    system_prompt = (
        "You are an expert AI trading analyst. "
        "Your task is to write a concise 5-line daily trading performance report for a Telegram channel. "
        "The report must be highly professional, structured, and easy to read. "
        "Format it using Markdown. Keep it strictly to 5 lines (excluding the header)."
    )
    
    user_prompt = (
        f"Generate a 5-line daily digest based on these stats:\n"
        f"- Net PnL: ${stats['total_pnl']:.2f} ({stats['wins']} Wins / {stats['losses']} Losses)\n"
        f"- Closed Trades: {stats['closed_count']}\n"
        f"- Open Positions: {stats['open_positions_count']} (Exposure: ${stats['open_exposure']:.2f})\n"
        f"- Signals Processed: {stats['total_signals']} (Vetoed: {stats['vetoed_signals']})\n"
        f"- Account Equity: ${stats['equity']:.2f} (Available Balance: ${stats['cash']:.2f})\n\n"
        f"Lines structure suggestion:\n"
        f"1. 📊 Daily PnL and Win/Loss metrics\n"
        f"2. 🔄 Trade activity overview (closed/open position counts)\n"
        f"3. 🛡️ Risk/Veto highlights (signals processed vs vetoed)\n"
        f"4. 💼 Portfolio & Margin Health (equity vs available cash)\n"
        f"5. 💡 Brief analytical takeaway/advice"
    )
    
    try:
        from backend.llm.router import call_llm_resilient
        content = await call_llm_resilient(
            task_type="general",
            prompt=user_prompt,
            system=system_prompt,
            temperature=0.3,
            max_tokens=512,
        )
        return f"🔍 *Trading Daily Digest*\n{content.strip()}"
            
    except Exception as e:
        logger.error(f"Error generating LLM summary: {e}. Falling back to default format.")
        return (
            f"🔍 *Trading Daily Digest*\n"
            f"📊 *Net PnL:* ${stats['total_pnl']:.2f} ({stats['wins']}W / {stats['losses']}L)\n"
            f"🔄 *Activity:* {stats['closed_count']} closed, {stats['open_positions_count']} open (Exp: ${stats['open_exposure']:.2f})\n"
            f"🛡️ *Signals:* {stats['total_signals']} processed ({stats['vetoed_signals']} vetoed)\n"
            f"💼 *Equity:* ${stats['equity']:.2f} (Available: ${stats['cash']:.2f})\n"
            f"💡 *takeaway:* Maintain strict risk control. System operating normally."
        )


async def send_telegram_message(text: str) -> bool:
    """Send a text message via Telegram Bot API."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.warning("Telegram bot token or chat ID not configured. Skipping daily digest broadcast.")
        return False
        
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(api_url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
            })
            if resp.is_success:
                logger.info("Daily digest successfully broadcast to Telegram.")
                return True
            else:
                logger.error(f"Failed to send daily digest: {resp.text}")
                return False
    except Exception as e:
        logger.error(f"Error sending Telegram message: {e}")
        return False

async def run_daily_digest():
    """Main entrypoint to run the daily stats extraction, LLM summary generation, and broadcasting."""
    logger.info("Starting automated daily trading digest execution...")
    stats = get_daily_stats()
    digest_text = await generate_digest_text(stats)
    await send_telegram_message(digest_text)
