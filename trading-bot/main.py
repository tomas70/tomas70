"""
Telegram command bot + forward paper tracker (BTC trend T50).

The SWEEP / HOOK / ICT signal scanner was removed after the research record
(research/*.md) showed no edge after costs. The full bot is archived on the
git branch archive/sweep-bot. What remains:

  * a paper portfolio that follows the one rule that passed its pre-registered
    tests (BTC long when the daily close is above the 50-day SMA), updated
    every 30 minutes and announced on Telegram when the state flips
  * read-only Evedex account views

Commands (send to your bot from iPhone):
  /paper     → paper tracker status (BTC T50, $100 paper account)
  /status    → live balance and 30-day trade stats (from Evedex)
  /positions → open positions (read-only)
  /help      → command list

Balance and trade history are read live from Evedex via EVEDEX_API_KEY
(see .env.example) — nothing is tracked manually. The bot never places orders.

Usage:
    python main.py
    # or for auto-start on Mac:
    bash run_main.sh
"""
import logging
import os
import sys
import threading
from pathlib import Path

import fcntl
import schedule
import time

from analysis.evedex_account import (
    AccountNotConfigured, get_account_balance, get_closed_positions,
    get_open_positions, summarize_trades,
)
from analysis.market_data import exchange_url, from_instrument
from analysis.paper_tracker import format_report as paper_report, update as paper_update
from notifications.bot_commands import listen_for_commands
from notifications.telegram_bot import send_telegram

PAPER_INTERVAL_MINUTES = 30

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


# ─── Paper tracker job ────────────────────────────────────────────────────────

def paper_job() -> None:
    """Advance the forward paper portfolio (daily data) and announce events."""
    try:
        for msg in paper_update():
            send_telegram(msg)
    except Exception as exc:
        logger.warning("paper tracker: %s", exc)


# ─── Telegram command handler ─────────────────────────────────────────────────

def _positions_text() -> str:
    positions = get_open_positions()
    if not positions:
        return "📭 <b>Pozicijos</b>\n\nAtvirų pozicijų nėra."
    lines = [f"📂 <b>Pozicijos</b>  <i>({len(positions)}, tik peržiūra)</i>\n"]
    for p in sorted(positions, key=lambda x: str(x.get("instrument", ""))):
        pair = from_instrument(str(p.get("instrument", "?")))
        side = "LONG" if str(p.get("side", "")).upper() == "BUY" else "SHORT"
        pnl = float(p.get("unRealizedPnL", 0) or 0)
        lines.append(
            f"<b>{pair} {side}</b>  (uPnL {pnl:+.2f}$)\n"
            f"  Entry: ${float(p.get('avgPrice', 0) or 0):,.6g}  |  {p.get('leverage', '?')}x  |  "
            f"kiekis {p.get('quantity', '?')}\n"
            f"  🔗 <a href=\"{exchange_url(pair)}\">Atidaryti {pair} Evedex</a>\n"
        )
    return "\n".join(lines)


def handle_command(command: str, args: list[str]) -> str:
    """Processes commands sent from iPhone via Telegram."""

    if command == "/help":
        return (
            "🤖 <b>Bot komandos:</b>\n\n"
            "/paper — paper portfelis: BTC trend T50 ($100)\n"
            "/status — balansas ir 30 d. sandorių statistika (iš Evedex)\n"
            "/positions — atviros pozicijos (tik peržiūra)\n"
            "/help — ši pagalba\n\n"
            "<i>SWEEP/HOOK/ICT signalai pašalinti: tyrimai neparodė edge po kaštų. "
            "Botas nesudaro orderių.</i>"
        )

    if command == "/paper":
        paper_job()
        return paper_report()

    if command == "/status":
        try:
            balance = get_account_balance()
        except AccountNotConfigured as exc:
            return f"⚠️ {exc}"
        except Exception as exc:
            return f"⚠️ Nepavyko gauti balanso iš Evedex: {exc}"

        try:
            summary = summarize_trades(get_closed_positions(lookback_days=30))
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
            f"📊 <b>Account Status</b>  <i>(gyvai iš Evedex)</i>\n\n"
            f"Balansas:  <b>${balance:.2f}</b>\n\n"
            f"{trade_line}"
        )

    if command == "/positions":
        try:
            return _positions_text()
        except AccountNotConfigured as exc:
            return f"⚠️ {exc}"
        except Exception as exc:
            logger.warning("Nepavyko gauti atvirų pozicijų: %s", exc)
            return f"⚠️ Nepavyko gauti pozicijų iš Evedex: {exc}"

    return f"❓ Nežinoma komanda: {command}\nRašyk /help norėdamas pamatyti sąrašą."


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    _acquire_lock()  # exit immediately if another instance is running

    logger.info("Bot paleistas | paper tracker kas %d min + Telegram komandos", PAPER_INTERVAL_MINUTES)

    paper_job()  # immediate first run

    # Scheduler runs in its own thread — schedule setup must happen inside
    # the same thread that calls run_pending() (thread safety).
    def _scheduler_loop() -> None:
        schedule.every(PAPER_INTERVAL_MINUTES).minutes.do(paper_job)
        while True:
            schedule.run_pending()
            time.sleep(60)

    threading.Thread(target=_scheduler_loop, daemon=True, name="scheduler").start()

    # Telegram command listener — blocks main thread (long polling)
    listen_for_commands(handle_command)


if __name__ == "__main__":
    main()
