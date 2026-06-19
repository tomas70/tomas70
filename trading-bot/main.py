"""
Standalone scheduler + Telegram command bot.

Runs automatically every 15 min AND listens for iPhone commands via Telegram.

Commands (send to your bot from iPhone):
  /scan              → immediate market scan
  /status            → current level, balance, trade stats
  /balance <amount>  → update account balance
  /help              → command list

Usage:
    python main.py
    # or for auto-start on Mac:
    bash run_main.sh
"""
import json
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

TRADES_FILE = Path(__file__).parent / "logs" / "trades.json"
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


# ─── Balance persistence ──────────────────────────────────────────────────────

def _load_balance() -> float:
    if TRADES_FILE.exists():
        try:
            with open(TRADES_FILE) as f:
                return float(json.load(f).get("current_balance", 20.0))
        except Exception:
            pass
    return 20.0


def _save_balance(balance: float) -> None:
    LOGS_DIR.mkdir(exist_ok=True)
    data: dict = {}
    if TRADES_FILE.exists():
        try:
            with open(TRADES_FILE) as f:
                data = json.load(f)
        except Exception:
            pass
    data["current_balance"] = balance
    data["current_level"]   = get_current_level(balance)["level"]
    with open(TRADES_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ─── Market scanner ───────────────────────────────────────────────────────────

def scan_markets(silent: bool = False) -> str:
    """
    Scans all pairs. Sends best setup to Telegram if found.
    Returns a short status string (used by /scan command reply).
    """
    balance    = _load_balance()
    level_info = get_current_level(balance)
    now        = datetime.now().strftime("%Y-%m-%d %H:%M")

    logger.info("[%s] Skenuojamos rinkos | Level %d | $%.2f", now, level_info["level"], balance)

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
            decision = {
                "confidence": 0,
                "reasoning": f"Auto: Hook+4H+PD+RR ✓ ({ob_conf} {fvg_conf} bonus)",
            }

        rr = analysis.get("rr_ratio", 0)
        checked.append(f"{pair}: ✅ RR={rr:.1f}")
        if rr > best_rr:
            best_rr       = rr
            best_setup    = {**analysis, **position}
            best_decision = decision

    if best_setup and best_decision:
        confidence = best_decision.get("confidence", 0)
        reasoning  = best_decision.get("reasoning", "")
        message    = format_setup_message(best_setup, level_info, confidence, reasoning)
        sent       = send_telegram(message)
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
            "/status — balansas ir lygis\n"
            "/balance &lt;suma&gt; — nustatyti balansą\n"
            "  pvz: <code>/balance 26</code>\n"
            "/help — ši pagalba"
        )

    if command == "/scan":
        return "🔄 Skenuoju rinkas...\n\n" + scan_markets(silent=True)

    if command == "/status":
        balance    = _load_balance()
        level_info = get_current_level(balance)
        trades: list = []
        if TRADES_FILE.exists():
            try:
                with open(TRADES_FILE) as f:
                    trades = json.load(f).get("trades", [])
            except Exception:
                pass

        wins   = sum(1 for t in trades if t.get("result") == "win")
        losses = sum(1 for t in trades if t.get("result") == "loss")
        total  = len(trades)

        return (
            f"📊 <b>Account Status</b>\n\n"
            f"Level:     {level_info['level']} / 30\n"
            f"Balansas:  <b>${balance:.2f}</b>\n"
            f"Tikslas:   ${level_info['next_level_balance']:.2f} "
            f"(dar ${level_info['remaining_profit']:.2f})\n"
            f"Progress:  {level_info['progress_pct']:.0f}%\n\n"
            f"Trades: {total} iš viso | {wins}W / {losses}L"
        )

    if command == "/balance":
        if not args:
            return "⚠️ Nurodyk sumą. Pvz: <code>/balance 26</code>"
        try:
            new_balance = float(args[0])
            if new_balance <= 0:
                raise ValueError
        except ValueError:
            return "⚠️ Netinkama suma. Pvz: <code>/balance 26.50</code>"

        _save_balance(new_balance)
        level_info = get_current_level(new_balance)
        return (
            f"✅ Balansas atnaujintas: <b>${new_balance:.2f}</b>\n"
            f"Level: {level_info['level']} | "
            f"Tikslas: ${level_info['next_level_balance']:.2f}"
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
