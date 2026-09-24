"""
Telegram notifications via direct HTTP (no SDK, no extra API key).
TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env to enable sending.
If not configured, format_setup_message() still works for display in Claude Desktop.
"""
import logging
import math
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

TIMEZONE = "Europe/Vilnius"


# ─── Formatting ───────────────────────────────────────────────────────────────

def _fmt_price(p: float) -> str:
    """
    Formats a price with enough decimals to stay readable for sub-cent
    instruments. A fixed 4dp worked fine on Hyperliquid's pair list (nothing
    much below DOGE's ~$0.08), but Evedex's low-price tokens (PEPE, BONK,
    ...) sit around $0.001-0.01, where 4dp isn't enough resolution to show
    an SL/entry gap of a few tenths of a percent — it silently rounds both
    to the same displayed number, which reads as "SL == entry" even though
    the underlying values differ.

    Keeps 4dp for anything >= $1 (unchanged from before), and otherwise
    scales decimals to keep ~4 significant figures.
    """
    if p == 0:
        return "$0"
    abs_p = abs(p)
    decimals = 4 if abs_p >= 1 else -math.floor(math.log10(abs_p)) + 3
    return f"${p:,.{decimals}f}"


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
    gt        = setup.get("global_trend") or {}
    sweep     = setup.get("sweep") or {}
    struct    = setup.get("structure_4h") or {}
    ict       = setup.get("ict") or {}
    perp      = setup.get("perp_context") or {}
    risk_usd  = setup.get("risk_usd", 0)
    pos_usd   = setup.get("position_usd", 0)
    leverage  = setup.get("leverage", 1)

    # TP1 freshness is informational only (not gated) — see multi_timeframe.py
    # module docstring for why an old 4H swing isn't treated as invalid.
    tp1_structural = setup.get("tp1_structural", True)
    tp1_stale      = setup.get("tp1_stale", False)
    tp1_age        = setup.get("tp1_age_4h")

    level_num = level_info.get("level", 1)
    balance   = level_info.get("balance", 0)

    # Cap the displayed confidence when TP1 isn't a fresh structural level —
    # a high R:R driven by a stale/fallback target overstates setup quality.
    if not tp1_structural or tp1_stale:
        confidence = min(confidence, 5)

    def _v(obj, key, default=None):
        if obj is None:
            return default
        return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)

    bias_arrow   = "⬆️" if bias == "BULLISH" else "⬇️"
    ob_range     = f"{_fmt_price(_v(ob,'low',0))}–{_fmt_price(_v(ob,'high',0))}" if ob else "N/A"
    entry_label  = "🧹 Sweep" if entry_type == "SWEEP" else "📍 Hook"
    daily_bias   = gt.get("daily_bias", "?")
    market_reg   = gt.get("market_regime", "?")

    # The two setups are read differently, so show what actually triggered
    if entry_type == "SWEEP":
        level_pretty = sweep.get("level_name", "?").replace("_", " ")

        # ICT confluence lines. Shown as measured facts, not as ticks that
        # imply the setup was validated by them — nothing here gates the
        # alert, and presenting an unproven factor as a checkmark is how a
        # recorded observation quietly turns into a belief.
        mss = ict.get("mss")
        ote = ict.get("ote") or {}
        mss_line = (
            f"prieš {mss['candles_ago']} žv. ({_fmt_price(mss['level'])})" if mss
            else "nėra (tik displacement)"
        )
        if ote:
            ote_line = (
                f"{ote['retracement']:.2f} "
                f"{'✓ OTE 0.62-0.79' if ote.get('in_ote') else '✗ už OTE'} "
                f"| 0.71 = {_fmt_price(ote['ote_price'])}"
            )
        else:
            ote_line = "n/a"
        kz = ict.get("killzone")
        ses_line = f"{ict.get('session', '?')}{f' ({kz})' if kz else ''}"

        setup_block = (
            f"🧹 <b>LIQUIDITY SWEEP:</b>\n"
            f"  Lygis:     {level_pretty} ({_fmt_price(sweep.get('level', 0))})\n"
            f"  Nušluota:  prieš {sweep.get('candles_ago', '?')} žvakių\n"
            f"  Displacement ✓  |  FVG: {'✓' if sweep.get('has_fvg') else 'nėra (įėjimas prie lygio)'}\n"
            f"  OB zona:   {ob_range}\n"
            f"\n"
            f"🔬 <b>ICT (tik stebima, negatuoja):</b>\n"
            f"  MSS:       {mss_line}\n"
            f"  Fibo:      {ote_line}\n"
            f"  Sesija:    {ses_line}\n"
        )
    else:
        setup_block = (
            f"🧠 <b>JOE ROSS + SMC:</b>\n"
            f"  Ross Hook: 15m ✓ (prieš {hook.get('candles_ago', 0)} žvakių)\n"
            f"  4H {'CHoCH ✓' if struct.get('is_choch') else 'BOS ✓'}\n"
            f"  OB zona:   {ob_range} ✓\n"
            f"  FVG:       {'Neužpildytas ✓' if fvg else 'nėra'}\n"
        )

    sl_sign  = "-" if bias == "BULLISH" else "+"
    tp1_sign = "+" if bias == "BULLISH" else "-"

    if not tp1_structural:
        tp1_quality = "⚠️ ne struktūrinis (fallback %, RR spekuliatyvus)"
    elif tp1_stale:
        tp1_quality = f"⚠️ senas 4H lygis (prieš {tp1_age} žvakių, ~{tp1_age * 4 / 24:.1f}d)"
    else:
        tp1_quality = f"✓ šviežias 4H lygis (prieš {tp1_age} žvakių)"

    # Funding over the 48h outcome window, in the same percent units as
    # TP1 — an extreme rate is a visible fraction of a 2% target, and
    # against the trade it is simply a worse trade than the R:R claims.
    carry = perp.get("funding_carry_pct_48h")
    if carry is None:
        funding_line = ""
    else:
        verdict = "gauni" if carry > 0 else "moki"
        funding_line = (
            f"  Funding:  {carry:+.3f}% / 48h ({verdict})  "
            f"| APR {perp.get('funding_apr_pct', 0):+.1f}%\n"
        )

    now = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M %Z")

    return (
        f"🎯 <b>TRADING SETUP — {pair}/USD  (Evedex)</b>\n"
        f"\n"
        f"📊 <b>BIAS:</b> {bias} {bias_arrow}  |  {entry_label}\n"
        f"⏱ 15m Hook | 4H struktūra | 1H SMC OB | 1D trendas\n"
        f"\n"
        f"💰 <b>POZICIJA:</b>\n"
        f"  Entry:  <code>{_fmt_price(entry)}</code>\n"
        f"  SL:     <code>{_fmt_price(sl)}</code>  ({sl_sign}{sl_pct:.2f}%)\n"
        f"  TP1:    <code>{_fmt_price(tp1)}</code>  ({tp1_sign}{tp1_pct:.2f}%)  {tp1_quality}\n"
        f"  TP2:    <code>{_fmt_price(tp2)}</code>\n"
        f"  R:R  =  1:{rr:.1f}\n"
        f"{funding_line}"
        f"\n"
        f"💼 <b>RIZIKA</b> (Level {level_num} | ${balance}):\n"
        f"  Rizika:    ${risk_usd:.2f} (30%)\n"
        f"  Notional:  ~${pos_usd:.0f}\n"
        f"  Leverage:  {leverage:.0f}x\n"
        f"\n"
        f"{setup_block}"
        f"\n"
        f"🌍 <b>GLOBALUS TRENDAS:</b>\n"
        f"  1D poros:  {daily_bias} ✓\n"
        f"  BTC:       {market_reg} ✓\n"
        f"\n"
        f"⚠️ <b>INVALIDATION:</b> {'žemiau' if bias == 'BULLISH' else 'virš'} "
        f"<code>{_fmt_price(sl)}</code> (SL lygis)\n"
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

            logger.warning("Telegram error %s: %s", resp.status_code, resp.text[:300])

            # A setup alert is worth delivering with visible tags rather than
            # not at all, so retry once without HTML parsing.
            if resp.status_code == 400:
                retry = client.post(url, json={"chat_id": chat_id, "text": message})
                if retry.is_success:
                    logger.info("Setup alert delivered as plain text (HTML rejected)")
                    return True
                logger.error("Plain-text retry also failed: %s", retry.text[:300])

        return False

    except Exception as exc:
        logger.error("Telegram send failed: %s", exc)
        return False
