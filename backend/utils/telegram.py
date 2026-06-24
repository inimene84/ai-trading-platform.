import os
import logging
import httpx

logger = logging.getLogger("telegram_util")

async def send_telegram_message(text: str, parse_mode: str = "Markdown") -> bool:
    """Send a text message via Telegram Bot API."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.warning("Telegram bot token or chat ID not configured. Skipping telegram broadcast.")
        return False
        
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(api_url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
            })
            if resp.is_success:
                logger.info("Message successfully broadcast to Telegram.")
                return True
            else:
                logger.error(f"Failed to send telegram message: {resp.text}")
                return False
    except Exception as e:
        logger.error(f"Error sending Telegram message: {e}")
        return False
