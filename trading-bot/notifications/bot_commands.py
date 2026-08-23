"""
Telegram command listener — lets you control the bot from iPhone.

Supported commands (send to your bot in Telegram):
  /scan              → runs market scan immediately
  /status            → shows current level, balance, trade history
  /balance <amount>  → updates account balance (e.g. /balance 26)
  /help              → lists all commands

Only accepts commands from the configured TELEGRAM_CHAT_ID (security).
Uses long polling — no webhook or open ports needed.
"""
import json
import logging
import time
from typing import Callable

import httpx

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

POLL_TIMEOUT = 30   # seconds — long polling keeps connection open, saves battery
RETRY_DELAY  = 5    # seconds to wait after a failed poll


def _get_updates(token: str, offset: int) -> list[dict]:
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        with httpx.Client(timeout=POLL_TIMEOUT + 5) as client:
            resp = client.get(url, params={"offset": offset, "timeout": POLL_TIMEOUT})
        if resp.status_code == 409:
            # Another bot instance is polling — wait for it to release the connection
            logger.warning("Telegram 409 Conflict: kitas main.py procesas veikia. Laukiama 60s...")
            time.sleep(60)
            return []
        if resp.is_success:
            return resp.json().get("result", [])
        logger.debug("Poll HTTP %s", resp.status_code)
    except Exception as exc:
        logger.debug("Poll error: %s", exc)
    return []


def _send(token: str, chat_id: str, text: str) -> None:
    """
    Sends a reply, falling back to plain text if Telegram rejects the HTML.

    Telegram answers a malformed-HTML message with HTTP 400 rather than an
    exception, so an unchecked post() looks like success while the user
    sees nothing at all. Any non-2xx is logged, and a parse failure is
    retried without parse_mode so the reply still arrives — a message with
    visible tags beats a silently missing one.
    """
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(url, json=payload)
            if resp.is_success:
                return

            body = resp.text[:300]
            logger.warning("Telegram rejected reply (HTTP %s): %s", resp.status_code, body)

            if resp.status_code == 400:
                retry = client.post(url, json={"chat_id": chat_id, "text": text})
                if retry.is_success:
                    logger.info("Reply delivered as plain text after HTML was rejected")
                else:
                    logger.error("Plain-text retry also failed: %s", retry.text[:300])

    except Exception as exc:
        logger.warning("Reply send failed: %s", exc)


def listen_for_commands(on_command: Callable[[str, list[str]], str]) -> None:
    """
    Blocks forever, polling Telegram for commands from TELEGRAM_CHAT_ID.

    on_command(command, args) → str reply text
    Should be run in a background thread from main.py.

    Args:
        on_command: callback that receives (command_str, args_list) and
                    returns a reply string to send back to the user.
    """
    token   = TELEGRAM_BOT_TOKEN
    chat_id = TELEGRAM_CHAT_ID

    if not token or not chat_id:
        logger.warning("Telegram credentials not set — command listener disabled")
        return

    logger.info("Telegram command listener started (chat_id=%s)", chat_id)
    _send(token, chat_id, "🤖 <b>Trading bot paleistas.</b>\nRašyk /help norėdamas pamatyti komandas.")

    offset = 0
    while True:
        updates = _get_updates(token, offset)

        for upd in updates:
            offset = upd["update_id"] + 1
            msg    = upd.get("message") or upd.get("channel_post") or {}
            text   = (msg.get("text") or "").strip()

            # Security: ignore messages not from configured chat
            sender_id = str(msg.get("chat", {}).get("id", ""))
            if sender_id != str(chat_id):
                continue

            if not text.startswith("/"):
                continue

            parts   = text.split()
            command = parts[0].lower().split("@")[0]  # strip @botname suffix
            args    = parts[1:]

            logger.info("Command received: %s %s", command, args)
            try:
                reply = on_command(command, args)
            except Exception as exc:
                reply = f"⚠️ Klaida: {exc}"

            if reply:
                _send(token, chat_id, reply)

        if not updates:
            time.sleep(1)
