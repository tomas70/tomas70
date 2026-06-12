"""
Standalone scheduler mode — runs without Claude Desktop.

Uses ANTHROPIC_API_KEY for AI analysis if set; otherwise skips the AI
confidence filter and sends all valid setups directly (not recommended
for live trading — use Claude Desktop MCP mode instead).

Usage:
    python main.py
"""
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import schedule
import time

from analysis.multi_timeframe import get_full_analysis
from ai.claude_analyst import build_analysis_context, generate_setup_standalone
from notifications.telegram_bot import format_setup_message, send_telegram
from risk.position_sizer import get_current_level, get_position_summary
from config import PAIRS, SCAN_INTERVAL_MINUTES, MIN_CONFIDENCE_SCORE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

TRADES_FILE = Path(__file__).parent / "logs" / "trades.json"


def _load_balance() -> float:
    """Reads current balance from trades.json, falls back to 20."""
    if TRADES_FILE.exists():
        try:
            with open(TRADES_FILE) as f:
                return float(json.load(f).get("current_balance", 20.0))
        except Exception:
            pass
    return 20.0


def scan_markets() -> None:
    balance     = _load_balance()
    level_info  = get_current_level(balance)
    now         = datetime.now().strftime("%Y-%m-%d %H:%M")

    logger.info("[%s] Skenuojamos rinkos | Level %d | Balansas $%.2f",
                now, level_info["level"], balance)

    best_setup    = None
    best_position = None
    best_decision = None
    best_rr       = 0.0

    for pair in PAIRS:
        try:
            analysis = get_full_analysis(pair)
        except Exception as exc:
            logger.warning("%s: duomenų klaida — %s", pair, exc)
            continue

        if not analysis.get("valid"):
            logger.debug("%s: %s", pair, analysis.get("reason"))
            continue

        try:
            position = get_position_summary(balance, analysis["entry"], analysis["sl"])
        except Exception as exc:
            logger.warning("%s: pozicijos klaida — %s", pair, exc)
            continue

        # Try AI decision (only if ANTHROPIC_API_KEY is set)
        decision = generate_setup_standalone(analysis, position)

        if decision:
            if decision.get("skip"):
                logger.info("%s: AI praleido (confidence %s)", pair, decision.get("confidence"))
                continue
            confidence = decision.get("confidence", 0)
            if confidence < MIN_CONFIDENCE_SCORE:
                logger.info("%s: confidence %d < %d", pair, confidence, MIN_CONFIDENCE_SCORE)
                continue
        else:
            # No ANTHROPIC_API_KEY — confluence filter (5 sąlygos + R:R ≥ 3) jau
            # pakankamai griežtas, siunčiame tiesiai į Telegram.
            confidence = 0   # bus rodoma "Auto" pranešime
            decision   = {"confidence": 0, "reasoning": "Auto: 5/5 confluence ✓"}

        rr = analysis.get("rr_ratio", 0)
        if rr > best_rr:
            best_rr       = rr
            best_setup    = {**analysis, **position}  # type: ignore[assignment]
            best_position = position
            best_decision = decision

    if best_setup and best_decision:
        confidence = best_decision.get("confidence", 0)
        reasoning  = best_decision.get("reasoning", "")
        message    = format_setup_message(best_setup, level_info, confidence, reasoning)
        sent       = send_telegram(message)
        logger.info("Setup išsiųstas: %s | sent=%s", best_setup.get("pair"), sent)
        if not sent:
            print("\n" + message + "\n")
    else:
        logger.info("Nėra tinkamų setup'ų. FLAT.")


def main() -> None:
    logger.info("Trading bot paleistas (standalone mode)")
    logger.info("Rekomenduojama: naudok mcp_server.py su Claude Desktop")

    scan_markets()  # immediate first run

    schedule.every(SCAN_INTERVAL_MINUTES).minutes.do(scan_markets)

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
