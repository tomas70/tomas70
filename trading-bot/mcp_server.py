"""
Claude Desktop MCP server — no API keys required.

Exposes SMC + Ross Hook analysis as tools that Claude Desktop calls natively.
Claude IS the AI analyst; it reads tool output and decides confidence / skip.

Setup in Claude Desktop (claude_desktop_config.json):
{
  "mcpServers": {
    "trading-bot": {
      "command": "python",
      "args": ["/absolute/path/to/trading-bot/mcp_server.py"],
      "env": {
        "TELEGRAM_BOT_TOKEN": "your_token_here",
        "TELEGRAM_CHAT_ID":   "your_chat_id_here"
      }
    }
  }
}

Then in Claude Desktop just say:
  "Skenuok rinkas, mano balansas $26"
  "Analizuok BTCUSDT, balansas $212"
  "Išsiųsk setup'ą į Telegram"
"""
import dataclasses
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# ── Path fix: ensure trading-bot/ is on sys.path regardless of CWD ────────────
# Claude Desktop runs the script with its own CWD, so local packages
# (analysis/, ai/, etc.) would not be found without this.
_ROOT = Path(__file__).parent.resolve()
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mcp.server.fastmcp import FastMCP

from analysis.market_data import get_all_timeframes, get_ohlcv, invalidate_cache
from analysis.multi_timeframe import get_full_analysis
from ai.claude_analyst import build_analysis_context
from notifications.telegram_bot import format_setup_message, send_telegram
from risk.position_sizer import get_current_level, get_position_summary
from config import PAIRS, MIN_CONFIDENCE_SCORE

logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
logger = logging.getLogger(__name__)

LOGS_DIR    = Path(__file__).parent / "logs"
TRADES_FILE = LOGS_DIR / "trades.json"

mcp = FastMCP(
    "SMC Ross Hook Trading Bot",
    instructions=(
        "Tu esi profesionalus crypto trader naudojantis SMC ir Ross Hook metodiką.\n"
        "Kai gauni rinkos analizę — įvertink setup'o kokybę ir priskirk confidence 1–10.\n"
        f"Siųsk į Telegram TIK jei confidence >= {MIN_CONFIDENCE_SCORE}.\n"
        "Grąžink atsakymą lietuviškai su visais parametrais ir pagrindimas vienu sakiniu."
    ),
)


# ─── Serialization ────────────────────────────────────────────────────────────

def _j(obj: Any) -> Any:
    """Recursively convert dataclasses to JSON-serializable dicts."""
    if obj is None:
        return None
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, dict):
        return {k: _j(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_j(i) for i in obj]
    return obj


# ─── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
def scan_markets(account_balance: float) -> str:
    """
    Scans all configured pairs (BTC, ETH, SOL, BNB, XRP) for valid
    SMC + Ross Hook setups. Returns all valid setups sorted by R:R ratio,
    plus the structured context for the best one.

    Claude should read the context, assign confidence, and decide whether
    to call send_setup_to_telegram.
    """
    results = []
    skipped = {}

    for pair in PAIRS:
        try:
            analysis = get_full_analysis(pair)
        except Exception as exc:
            skipped[pair] = str(exc)
            continue

        if not analysis.get("valid"):
            skipped[pair] = analysis.get("reason", "unknown")
            continue

        try:
            position = get_position_summary(account_balance, analysis["entry"], analysis["sl"])
        except Exception as exc:
            skipped[pair] = f"position sizing error: {exc}"
            continue

        results.append({**_j(analysis), **position})

    if not results:
        return json.dumps({
            "found":   False,
            "message": "Šiuo metu nėra tinkamų setup'ų. FLAT.",
            "skipped": skipped,
        }, ensure_ascii=False)

    results.sort(key=lambda r: r.get("rr_ratio", 0), reverse=True)
    best     = results[0]
    level    = get_current_level(account_balance)
    context  = build_analysis_context(best, best)

    return json.dumps({
        "found":        True,
        "total":        len(results),
        "best_setup":   best,
        "all_setups":   [{"pair": r["pair"], "bias": r["bias"], "rr_ratio": r["rr_ratio"]} for r in results],
        "level_info":   level,
        "context":      context,
        "skipped":      skipped,
    }, ensure_ascii=False)


@mcp.tool()
def analyze_pair(pair: str, account_balance: float) -> str:
    """
    Runs full SMC + Ross Hook multi-timeframe analysis on a single pair.
    Returns structured context for Claude to evaluate confidence.
    """
    pair = pair.upper().replace("/", "").replace("-", "")
    if "USDT" not in pair:
        pair += "USDT"

    if pair not in PAIRS:
        return json.dumps({"error": f"Nežinoma pora '{pair}'. Galimos: {PAIRS}"}, ensure_ascii=False)

    try:
        analysis = get_full_analysis(pair)
    except Exception as exc:
        return json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False)

    if not analysis.get("valid"):
        return json.dumps({
            "valid":  False,
            "pair":   pair,
            "reason": analysis.get("reason"),
        }, ensure_ascii=False)

    position = get_position_summary(account_balance, analysis["entry"], analysis["sl"])
    context  = build_analysis_context(analysis, position)
    level    = get_current_level(account_balance)

    return json.dumps({
        "valid":      True,
        "context":    context,
        "setup":      _j(analysis),
        "position":   position,
        "level_info": level,
    }, ensure_ascii=False)


@mcp.tool()
def send_setup_to_telegram(
    setup_json: str,
    confidence: int,
    reasoning:  str,
    account_balance: float,
) -> str:
    """
    Formats and sends a trading setup notification to Telegram.
    Only call this when confidence >= 7 and the setup is valid.

    Args:
        setup_json:      JSON string from scan_markets or analyze_pair (best_setup field)
        confidence:      Your confidence score 1–10
        reasoning:       One-sentence reasoning in Lithuanian
        account_balance: Current account balance in USDT
    """
    if confidence < MIN_CONFIDENCE_SCORE:
        return json.dumps({
            "sent":   False,
            "reason": f"Confidence {confidence} < {MIN_CONFIDENCE_SCORE} — setup praleistas",
        }, ensure_ascii=False)

    try:
        setup = json.loads(setup_json)
    except json.JSONDecodeError as exc:
        return json.dumps({"sent": False, "error": f"JSON klaida: {exc}"}, ensure_ascii=False)

    level_info = get_current_level(account_balance)
    message    = format_setup_message(setup, level_info, confidence, reasoning)
    sent       = send_telegram(message)

    return json.dumps({
        "sent":      sent,
        "message":   message,
        "confidence": confidence,
        "note":      "Telegram nesukonfikūruotas — pranešimas tik čia" if not sent else "Išsiųsta ✓",
    }, ensure_ascii=False)


@mcp.tool()
def log_trade_result(
    pair:          str,
    bias:          str,
    entry:         float,
    sl:            float,
    tp:            float,
    result:        str,
    pnl_usd:       float,
    balance_after: float,
) -> str:
    """
    Records a completed trade to logs/trades.json.

    Args:
        result: 'win' | 'loss' | 'be' (break-even)
    """
    LOGS_DIR.mkdir(exist_ok=True)

    if TRADES_FILE.exists():
        with open(TRADES_FILE) as f:
            data = json.load(f)
    else:
        data = {"current_level": 1, "current_balance": 0.0, "trades": []}

    level_info = get_current_level(balance_after)
    data["current_balance"] = balance_after
    data["current_level"]   = level_info["level"]
    data["trades"].append({
        "date":          datetime.now().isoformat(),
        "pair":          pair,
        "bias":          bias,
        "entry":         entry,
        "sl":            sl,
        "tp":            tp,
        "result":        result,
        "pnl_usd":       pnl_usd,
        "balance_after": balance_after,
        "level_after":   level_info["level"],
    })

    with open(TRADES_FILE, "w") as f:
        json.dump(data, f, indent=2)

    return json.dumps({
        "logged":  True,
        "level":   level_info["level"],
        "balance": balance_after,
        "remaining_to_next": level_info["remaining_profit"],
    }, ensure_ascii=False)


@mcp.tool()
def get_account_status(account_balance: float) -> str:
    """Returns current challenge level, progress, and target for next level."""
    info = get_current_level(account_balance)

    history: list[dict] = []
    if TRADES_FILE.exists():
        try:
            with open(TRADES_FILE) as f:
                data = json.load(f)
            trades = data.get("trades", [])
            wins   = sum(1 for t in trades if t.get("result") == "win")
            losses = sum(1 for t in trades if t.get("result") == "loss")
            history = [{"total": len(trades), "wins": wins, "losses": losses}]
        except Exception:
            pass

    return json.dumps({**info, "trade_history": history}, ensure_ascii=False)


@mcp.tool()
def refresh_cache(pair: str = "") -> str:
    """Forces refresh of cached market data. Leave pair empty to refresh all pairs."""
    invalidate_cache(pair or None)
    return json.dumps({"refreshed": pair or "all pairs"}, ensure_ascii=False)


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
