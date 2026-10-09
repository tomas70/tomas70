"""
Telegram notifications via direct HTTP (no SDK, no extra API key).
TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env to enable sending.
"""
import logging

import httpx

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)


# ─── Sending ──────────────────────────────────────────────────────────────────

def send_telegram(
    message: str,
    token: str = "",
    chat_id: str = "",
) -> bool:
    """
    Sends a message to Telegram.
    Falls back to TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID from config if args omitted.
    Returns True on success, False if credentials missing or request fails.
    """
    token   = token   or TELEGRAM_BOT_TOKEN
    chat_id = chat_id or TELEGRAM_CHAT_ID

    if not token or not chat_id:
        logger.info("Telegram not configured — message not sent")
        return False

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        with httpx.Client(timeout=10) as client:
            resp = client.post(url, json={
                "chat_id":    chat_id,
                "text":       message,
                "parse_mode": "HTML",
            })
            if resp.is_success:
                logger.info("Telegram message sent")
                return True

            logger.warning("Telegram error %s: %s", resp.status_code, resp.text[:300])

            # An alert is worth delivering with visible tags rather than
            # not at all, so retry once without HTML parsing.
            if resp.status_code == 400:
                retry = client.post(url, json={"chat_id": chat_id, "text": message})
                if retry.is_success:
                    logger.info("Alert delivered as plain text (HTML rejected)")
                    return True
                logger.error("Plain-text retry also failed: %s", retry.text[:300])

        return False

    except Exception as exc:
        logger.error("Telegram send failed: %s", exc)
        return False
