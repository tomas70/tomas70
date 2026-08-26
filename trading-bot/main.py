"""
Standalone scheduler + Telegram command bot.

Runs automatically every 15 min AND listens for iPhone commands via Telegram.

Commands (send to your bot from iPhone):
  /scan    → immediate market scan
  /status  → current level, live balance, trade stats (from Hyperliquid)
  /help    → command list

Balance and trade history are read live from Hyperliquid via
HYPERLIQUID_ADDRESS (see .env.example) — nothing is tracked manually.

Usage:
    python main.py
    # or for auto-start on Mac:
    bash run_main.sh
"""
import logging
import os
import sys
import threading
from datetime import datetime
from pathlib import Path

import fcntl
import schedule
import time

from analysis.multi_timeframe import get_full_analysis
from analysis.trade_logger import get_stats, log_setup, update_all_pending_outcomes
from analysis.hyperliquid_account import (
    AccountNotConfigured, get_account_balance, get_recent_fills, summarize_trades,
)
from ai.claude_analyst import generate_setup_standalone
from notifications.telegram_bot import format_setup_message, send_telegram
from notifications.bot_commands import listen_for_commands
from risk.position_sizer import get_current_level, get_position_summary
from config import PAIRS, SCAN_INTERVAL_MINUTES, MIN_CONFIDENCE_SCORE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

LOGS_DIR    = Path(__file__).parent / "logs"
_LOCK_FILE  = LOGS_DIR / "main.lock"
_lock_fd    = None  # keep open to hold the lock


# ─── Single-instance lock ─────────────────────────────────────────────────────

def _acquire_lock() -> None:
    """Prevent two main.py instances from running simultaneously."""
    global _lock_fd
    LOGS_DIR.mkdir(exist_ok=True)
    _lock_fd = open(_LOCK_FILE, "w")
    try:
        fcntl.flock(_lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fd.write(str(os.getpid()))
        _lock_fd.flush()
    except BlockingIOError:
        print(
            "KLAIDA: main.py jau veikia (lock failas užrakintas).\n"
            "Nužudyk seną procesą:\n"
            "  pkill -9 -f main.py && sleep 35",
            file=sys.stderr,
        )
        sys.exit(1)


# ─── Market scanner ───────────────────────────────────────────────────────────

def scan_markets(silent: bool = False) -> str:
    """
    Scans all pairs. Sends best setup to Telegram if found.
    Returns a short status string (used by /scan command reply).
    """
    try:
        balance = get_account_balance()
    except AccountNotConfigured as exc:
        logger.error(str(exc))
        return f"⚠️ {exc}"
    except Exception as exc:
        logger.error("Nepavyko gauti balanso iš Hyperliquid: %s", exc)
        return f"⚠️ Nepavyko gauti balanso iš Hyperliquid: {exc}"

    level_info = get_current_level(balance)
    now        = datetime.now().strftime("%Y-%m-%d %H:%M")

    logger.info("[%s] Skenuojamos rinkos | Level %d | $%.2f", now, level_info["level"], balance)

    update_all_pending_outcomes()

    best_setup    = None
    best_decision = None
    best_rr       = 0.0
    checked       = []

    for pair in PAIRS:
        try:
            analysis = get_full_analysis(pair)
        except Exception as exc:
            logger.warning("%s: duomenų klaida — %s", pair, exc)
            continue

        if not analysis.get("valid"):
            reason = analysis.get("reason", "?")
            logger.debug("%s: %s", pair, reason)
            checked.append(f"{pair}: ❌ {reason}")
            continue

        try:
            position = get_position_summary(balance, analysis["entry"], analysis["sl"])
        except Exception as exc:
            logger.warning("%s: pozicijos klaida — %s", pair, exc)
            continue

        decision = generate_setup_standalone(analysis, position)

        if decision:
            if decision.get("skip") or decision.get("confidence", 0) < MIN_CONFIDENCE_SCORE:
                logger.info("%s: AI praleido", pair)
                continue
        else:
            ob_conf  = "OB✓" if analysis.get("order_block") else "OB✗"
            fvg_conf = "FVG✓" if analysis.get("fvg") else "FVG✗"
            tp1_warn = "" if analysis.get("tp1_structural") and not analysis.get("tp1_stale") else " ⚠️TP1 speculative"
            decision = {
                "confidence": 0,
                "reasoning": f"Auto: Hook+4H+PD+RR ✓ ({ob_conf} {fvg_conf} bonus){tp1_warn}",
            }

        rr = analysis.get("rr_ratio", 0)
        rr_flag = "⚠️" if not analysis.get("tp1_structural") or analysis.get("tp1_stale") else ""
        checked.append(f"{pair}: ✅ RR={rr:.1f}{rr_flag}")
        if rr > best_rr:
            best_rr       = rr
            best_setup    = {**analysis, **position}
            best_decision = decision

    if best_setup and best_decision:
        confidence = best_decision.get("confidence", 0)
        reasoning  = best_decision.get("reasoning", "")
        message    = format_setup_message(best_setup, level_info, confidence, reasoning)
        sent       = send_telegram(message)
        log_setup(best_setup)
        logger.info("Setup išsiųstas: %s | sent=%s", best_setup.get("pair"), sent)
        return f"✅ Setup rastas ir išsiųstas: <b>{best_setup['pair']}</b> RR=1:{best_rr:.1f}"
    else:
        logger.info("Nėra tinkamų setup'ų.")
        # Group reasons for compact display
        reasons: dict[str, list[str]] = {}
        for line in checked:
            pair_part, _, reason_part = line.partition(": ❌ ")
            reasons.setdefault(reason_part, []).append(pair_part)
        summary = "\n".join(
            f"❌ {reason}: {', '.join(pairs)}"
            for reason, pairs in reasons.items()
        )
        return f"🔍 Skenuota {len(PAIRS)} porų. Nėra setup'ų. FLAT.\n\n{summary}"


# ─── Telegram command handler ─────────────────────────────────────────────────

def handle_command(command: str, args: list[str]) -> str:
    """Processes commands sent from iPhone via Telegram."""

    if command == "/help":
        return (
            "🤖 <b>Trading Bot komandos:</b>\n\n"
            "/scan — skenuoti rinkas dabar\n"
            "/status — balansas, lygis ir sandorių statistika (iš Hyperliquid)\n"
            "/log — bot'o alertų statistika (skirtinga nuo /status — žr. žemiau)\n"
            "/help — ši pagalba\n\n"
            "<i>Balansas ir sandoriai imami tiesiogiai iš Hyperliquid — nieko "
            "įvesti rankiniu būdu nereikia.</i>"
        )

    if command == "/scan":
        return "🔄 Skenuoju rinkas...\n\n" + scan_markets(silent=True)

    if command == "/log":
        all_time = bool(args) and args[0].lower() == "all"
        s = get_stats(all_time=all_time)
        if s["total"] == 0:
            return "📋 <b>Trade Log</b>\n\nDar nėra užrašytų setup'ų."

        def fmt(info: dict) -> str:
            """'12 → 25.0% (baz. 30.0%, edge -5.0pp), +0.31R' — vs. random-walk baseline."""
            if not info["count"]:
                return "0 setup'ų"
            wr_s   = f"{info['win_rate']:.1f}%" if info["win_rate"] is not None else "—"
            bl_s   = f"{info['baseline_wr']:.1f}%" if info["baseline_wr"] is not None else "—"
            edge_s = f"{info['edge_pp']:+.1f}pp" if info["edge_pp"] is not None else "—"
            exp_s  = f"{info['expectancy']:+.2f}R" if info["expectancy"] is not None else "—"
            return f"{info['count']} → {wr_s} (baz. {bl_s}, edge {edge_s}), {exp_s}"

        wr    = f"{s['win_rate']:.1f}%" if s["win_rate"] is not None else "—"
        bl    = f"{s['baseline_wr']:.1f}%" if s["baseline_wr"] is not None else "—"
        edge  = f"{s['edge_pp']:+.1f}pp" if s["edge_pp"] is not None else "—"
        exp   = f"{s['expectancy']:+.2f}R" if s["expectancy"] is not None else "—"

        if all_time:
            scope_note = "<i>Visa istorija — maišo visas praeities strategijos versijas.</i>"
        else:
            epoch_date = s["epoch"][:10]
            scope_note = (
                f"<i>Tik nuo paskutinio strategijos pakeitimo ({epoch_date}) — "
                f"{s['excluded_old']} senesnių setup'ų neįskaičiuota. "
                f"Pilnai istorijai: <code>/log all</code></i>"
            )

        lines = [
            "📋 <b>Trade Log</b>",
            scope_note + "\n",
            f"Iš viso: {s['total']} setup'ų  |  Laukia: {s['pending']}",
            f"Išspręsta: {s['resolved']}  →  TP1: {s['tp1_hit']}  SL: {s['sl_hit']}  Expired: {s['expired']}",
            f"<b>Win rate: {wr}</b>  (atsitiktinumo riba: {bl}, edge: {edge})",
            f"<b>Expectancy: {exp}/sandoriui</b>",
            "<i>Atsitiktinumo riba = ką duotų grynas monetos metimas prie to paties R:R.\n"
            "Edge &gt; 0 = sistema geresnė už atsitiktinumą. Expectancy &gt; 0 = pelninga po visų sandorių.</i>\n",
            "<b>Pagal setup tipą:</b>",
        ]
        for et, info in s["by_type"].items():
            lines.append(f"  {et}: {fmt(info)}")

        lines.append("\n<b>Pagal R:R:</b>")
        for band, info in s["by_rr"].items():
            lines.append(f"  {band}: {fmt(info)}")

        by_ob = s.get("by_ob", {})
        if by_ob.get("with_ob", {}).get("count") or by_ob.get("without_ob", {}).get("count"):
            lines.append("\n<b>OB confluence:</b>")
            lines.append(f"  Su OB:  {fmt(by_ob['with_ob'])}")
            lines.append(f"  Be OB:  {fmt(by_ob['without_ob'])}")
        return "\n".join(lines)

    if command == "/status":
        try:
            balance = get_account_balance()
        except AccountNotConfigured as exc:
            return f"⚠️ {exc}"
        except Exception as exc:
            return f"⚠️ Nepavyko gauti balanso iš Hyperliquid: {exc}"

        level_info = get_current_level(balance)

        try:
            fills   = get_recent_fills(lookback_days=30)
            summary = summarize_trades(fills)
            trade_line = (
                f"Sandoriai (30d): {summary['closing_fills']} iš viso | "
                f"{summary['wins']}W / {summary['losses']}L"
                + (f" ({summary['win_rate']:.1f}%)" if summary["win_rate"] is not None else "")
                + f"\nPnL: ${summary['net_pnl']:+.2f} (po ${summary['total_fees']:.2f} fee)"
            )
        except Exception as exc:
            logger.warning("Nepavyko gauti sandorių istorijos: %s", exc)
            trade_line = "Sandorių istorija laikinai nepasiekiama."

        return (
            f"📊 <b>Account Status</b>  <i>(gyvai iš Hyperliquid)</i>\n\n"
            f"Level:     {level_info['level']} / 30\n"
            f"Balansas:  <b>${balance:.2f}</b>\n"
            f"Tikslas:   ${level_info['next_level_balance']:.2f} "
            f"(dar ${level_info['remaining_profit']:.2f})\n"
            f"Progress:  {level_info['progress_pct']:.0f}%\n\n"
            f"{trade_line}"
        )

    return f"❓ Nežinoma komanda: {command}\nRašyk /help norėdamas pamatyti sąrašą."


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    _acquire_lock()  # exit immediately if another instance is running

    logger.info("Trading bot paleistas | auto scan kas %d min + Telegram komandos",
                SCAN_INTERVAL_MINUTES)

    scan_markets()  # immediate first scan

    # Scheduler runs in its own thread — schedule setup must happen inside
    # the same thread that calls run_pending() (thread safety).
    def _scheduler_loop() -> None:
        schedule.every(SCAN_INTERVAL_MINUTES).minutes.do(scan_markets)
        while True:
            schedule.run_pending()
            time.sleep(60)

    threading.Thread(target=_scheduler_loop, daemon=True, name="scheduler").start()

    # Telegram command listener — blocks main thread (long polling)
    listen_for_commands(handle_command)


if __name__ == "__main__":
    main()
