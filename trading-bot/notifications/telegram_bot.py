"""
Telegram notifications via direct HTTP (no SDK, no extra API key).
TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env to enable sending.
If not configured, format_setup_message() still works for display in Claude Desktop.
"""
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

TIMEZONE = "Europe/Vilnius"


# ─── Formatting ───────────────────────────────────────────────────────────────

def format_setup_message(
    setup: dict,
    level_info: dict,
    confidence: int,
    reasoning: str,
) -> str:
    """
    Formats a trading setup as the Telegram message defined in the spec.
    Works even without Telegram credentials — used for Claude Desktop display too.
    """
    pair        = setup.get("pair", "?")
    bias        = setup.get("bias", "?").upper()
    entry       = setup.get("entry", 0)
    sl          = setup.get("sl", 0)
    tp1         = setup.get("tp1", 0)
    tp2         = setup.get("tp2", 0)
    rr          = setup.get("rr_ratio", 0)
    sl_pct      = setup.get("sl_pct", 0)
    tp1_pct     = setup.get("tp1_pct", 0)
    entry_type  = setup.get("entry_type", "HOOK")

    ob        = setup.get("order_block")
    fvg       = setup.get("fvg")
    hook      = setup.get("ross_hook") or {}
    tte       = setup.get("tte")
    struct    = setup.get("structure_4h") or {}
    risk_usd  = setup.get("risk_usd", 0)
    pos_usd   = setup.get("position_usd", 0)
    leverage  = setup.get("leverage", 1)

    level_num = level_info.get("level", 1)
    balance   = level_info.get("balance", 0)

    def _v(obj, key, default=None):
        if obj is None:
            return default
        return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)

    bias_arrow   = "⬆️" if bias == "LONG" else "⬇️"
    bos_label    = "CHoCH ✓" if struct.get("is_choch") else "BOS ✓"
    fvg_label    = "Neužpildytas ✓" if fvg else "nėra"
    hook_ago     = hook.get("candles_ago", 0)
    ob_range     = f"${_v(ob,'low',0):,.2f}–${_v(ob,'high',0):,.2f}" if ob else "N/A"
    entry_label  = "🎯 TTE" if entry_type == "TTE" else "📍 Hook"
    tte_note     = (
        f"\n  TTE SL:  <code>${_v(tte,'tte_sl',sl):,.2f}</code>  (korekcijos žemuma)"
        if tte else ""
    )

    sl_sign  = "-" if bias == "LONG" else "+"
    tp1_sign = "+" if bias == "LONG" else "-"

    now = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M %Z")

    return (
        f"🎯 <b>TRADING SETUP — {pair}/USDC  (Hyperliquid)</b>\n"
        f"\n"
        f"📊 <b>BIAS:</b> {bias} {bias_arrow}  |  {entry_label}\n"
        f"⏱ 15m Hook + TTE | 4H struktūra | 1H SMC OB\n"
        f"\n"
        f"💰 <b>POZICIJA:</b>\n"
        f"  Entry:  <code>${entry:,.4f}</code>\n"
        f"  SL:     <code>${sl:,.4f}</code>  ({sl_sign}{sl_pct:.2f}%){tte_note}\n"
        f"  TP1:    <code>${tp1:,.4f}</code>  ({tp1_sign}{tp1_pct:.2f}%)\n"
        f"  TP2:    <code>${tp2:,.4f}</code>\n"
        f"  R:R  =  1:{rr:.1f}\n"
        f"\n"
        f"💼 <b>RIZIKA</b> (Level {level_num} | ${balance}):\n"
        f"  Rizika:    ${risk_usd:.2f} (30%)\n"
        f"  Notional:  ~${pos_usd:.0f}\n"
        f"  Leverage:  {leverage:.0f}x\n"
        f"\n"
        f"🧠 <b>JOE ROSS + SMC:</b>\n"
        f"  Ross Hook: 15m ✓ (prieš {hook_ago} žvakių)\n"
        f"  4H {bos_label}\n"
        f"  OB zona:   {ob_range} ✓\n"
        f"  FVG:       {fvg_label}\n"
        f"\n"
        f"⚠️ <b>INVALIDATION:</b> {'žemiau' if bias == 'LONG' else 'virš'} "
        f"<code>${sl:,.4f}</code> (SL lygis)\n"
        f"\n"
        f"💬 <i>{reasoning}</i>\n"
        f"\n"
        f"📈 Confidence: {confidence}/10\n"
        f"⏰ {now}"
    )


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
        logger.warning("Telegram error %s: %s", resp.status_code, resp.text[:200])
        return False

    except Exception as exc:
        logger.error("Telegram send failed: %s", exc)
        return False
