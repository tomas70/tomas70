"""
Trade outcome logger.

Records every bot-alerted setup to logs/trade_log.csv and auto-detects
whether price subsequently hit TP1, SL, or neither (expired) within a
48-hour window.  Outcomes are resolved on each market scan using the
cached 15m OHLCV data — no extra API calls.

Useful for empirical tuning: which entry types, confluence factors, or
RR bands actually correlate with TP1 hits in live market conditions.
"""
import csv
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .market_data import get_ohlcv

logger = logging.getLogger(__name__)

LOG_FILE            = Path(__file__).parent.parent / "logs" / "trade_log.csv"
MAX_OUTCOME_CANDLES = 192   # 48 h at 15-minute intervals

COLUMNS = [
    "id", "logged_at", "pair", "bias", "entry_type",
    "entry", "sl", "tp1", "tp2", "rr_ratio",
    "ob_confluence", "fvg_confluence", "pd_zone",
    "tp1_structural", "tp1_age_4h", "hook_candles_ago",
    "status", "outcome_at", "outcome_candles",
]


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _ensure_file() -> None:
    LOG_FILE.parent.mkdir(exist_ok=True)
    if not LOG_FILE.exists():
        with open(LOG_FILE, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=COLUMNS).writeheader()


def _read_rows() -> list[dict]:
    _ensure_file()
    with open(LOG_FILE, newline="") as f:
        return list(csv.DictReader(f))


def _write_rows(rows: list[dict]) -> None:
    with open(LOG_FILE, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)


# ─── Public API ───────────────────────────────────────────────────────────────

def _has_pending(pair: str, bias: str) -> bool:
    """True if a pending entry already exists for this pair + direction."""
    rows = _read_rows()
    return any(r["pair"] == pair and r["bias"] == bias and r["status"] == "pending" for r in rows)


def log_setup(result: dict) -> None:
    """Appends a new pending row when the bot fires an alert.

    Skips if an identical pending entry (same pair + direction) already
    exists — prevents duplicate logging when the same setup persists
    across consecutive 15-min scans.
    """
    _ensure_file()
    pair = result.get("pair", "")
    bias = result.get("bias", "")

    if _has_pending(pair, bias):
        logger.debug("trade_logger: skipping duplicate — %s %s already pending", pair, bias)
        return

    hook = result.get("ross_hook") or {}
    row = {
        "id":               str(uuid.uuid4())[:8],
        "logged_at":        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pair":             result.get("pair", ""),
        "bias":             result.get("bias", ""),
        "entry_type":       result.get("entry_type", ""),
        "entry":            result.get("entry", ""),
        "sl":               result.get("sl", ""),
        "tp1":              result.get("tp1", ""),
        "tp2":              result.get("tp2", ""),
        "rr_ratio":         result.get("rr_ratio", ""),
        "ob_confluence":    bool(result.get("order_block")),
        "fvg_confluence":   bool(result.get("fvg")),
        "pd_zone":          (result.get("pd_zone") or {}).get("zone", ""),
        "tp1_structural":   result.get("tp1_structural", ""),
        "tp1_age_4h":       result.get("tp1_age_4h", ""),
        "hook_candles_ago": hook.get("candles_ago", ""),
        "status":           "pending",
        "outcome_at":       "",
        "outcome_candles":  "",
    }
    with open(LOG_FILE, "a", newline="") as f:
        csv.DictWriter(f, fieldnames=COLUMNS).writerow(row)
    logger.info("Setup logged: %s %s RR=%.2f", row["pair"], row["bias"], float(row["rr_ratio"] or 0))


def update_all_pending_outcomes() -> int:
    """
    Resolves pending log entries against the latest 15m candle data.

    For each pending setup, walks every 15m candle that closed after the
    alert timestamp and checks whether the HIGH (bullish) or LOW (bearish)
    reached TP1, or the LOW (bullish) / HIGH (bearish) reached SL.
    Stop and limit orders are triggered by price, not by close, so wicks
    are the correct comparison.  When both levels appear on the same candle
    (gap/spike), TP1 is credited — standard backtesting convention when
    intra-candle order is unknown.

    Returns the number of rows resolved in this call.
    """
    rows = _read_rows()
    pending = [r for r in rows if r["status"] == "pending"]
    if not pending:
        return 0

    pairs = {r["pair"] for r in pending}
    df_by_pair: dict[str, pd.DataFrame] = {}
    for pair in pairs:
        try:
            df_by_pair[pair] = get_ohlcv(pair, "15m")
        except Exception as exc:
            logger.warning("trade_logger: cannot fetch %s 15m — %s", pair, exc)

    resolved = 0
    for row in rows:
        if row["status"] != "pending":
            continue
        df = df_by_pair.get(row["pair"])
        if df is None or df.empty:
            continue

        logged_at = datetime.fromisoformat(row["logged_at"])
        tp1  = float(row["tp1"])
        sl   = float(row["sl"])
        bias = row["bias"]

        after = df[df["timestamp"] > pd.Timestamp(logged_at)].reset_index(drop=True)
        if after.empty:
            continue

        outcome = outcome_at = outcome_candles = None

        for idx, candle in after.iterrows():
            tp_hit = candle["high"] >= tp1 if bias == "bullish" else candle["low"] <= tp1
            sl_hit = candle["low"]  <= sl  if bias == "bullish" else candle["high"] >= sl

            if tp_hit or sl_hit:
                outcome          = "tp1_hit" if tp_hit else "sl_hit"
                outcome_at       = str(candle["timestamp"])
                outcome_candles  = int(idx) + 1
                break

        if outcome is None and len(after) >= MAX_OUTCOME_CANDLES:
            outcome         = "expired"
            outcome_at      = str(after.iloc[-1]["timestamp"])
            outcome_candles = len(after)

        if outcome:
            row["status"]          = outcome
            row["outcome_at"]      = outcome_at
            row["outcome_candles"] = outcome_candles
            resolved += 1
            logger.info(
                "Outcome: %s %s → %s (%s bars)", row["pair"], row["bias"], outcome, outcome_candles
            )

    if resolved:
        _write_rows(rows)
    return resolved


def get_stats() -> dict:
    """Returns win-rate summary broken down by entry type and OB confluence."""
    rows     = _read_rows()
    resolved = [r for r in rows if r["status"] in ("tp1_hit", "sl_hit", "expired")]
    pending  = [r for r in rows if r["status"] == "pending"]

    def win_rate(subset: list[dict]) -> float | None:
        if not subset:
            return None
        hits = sum(1 for r in subset if r["status"] == "tp1_hit")
        return round(hits / len(subset) * 100, 1)

    by_type: dict[str, dict] = {}
    for et in ("TTE", "HOOK"):
        sub = [r for r in resolved if r["entry_type"] == et]
        by_type[et] = {"count": len(sub), "win_rate": win_rate(sub)}

    ob_yes = [r for r in resolved if str(r.get("ob_confluence")) == "True"]
    ob_no  = [r for r in resolved if str(r.get("ob_confluence")) == "False"]

    return {
        "total":     len(rows),
        "pending":   len(pending),
        "resolved":  len(resolved),
        "tp1_hit":   sum(1 for r in resolved if r["status"] == "tp1_hit"),
        "sl_hit":    sum(1 for r in resolved if r["status"] == "sl_hit"),
        "expired":   sum(1 for r in resolved if r["status"] == "expired"),
        "win_rate":  win_rate(resolved),
        "by_type":   by_type,
        "ob_yes_wr": win_rate(ob_yes),
        "ob_no_wr":  win_rate(ob_no),
        "ob_yes_n":  len(ob_yes),
        "ob_no_n":   len(ob_no),
    }
