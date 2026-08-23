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
from typing import Optional

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
    "daily_bias", "market_regime",
    "sweep_level", "sweep_candles_ago", "sweep_has_fvg",
    "status", "outcome_at", "outcome_candles",
]


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _ensure_file() -> None:
    """
    Creates the log file if missing, and migrates it in place when COLUMNS
    has grown since the file was written. Without the migration, appending
    with the new field order to a file carrying the old header would write
    values into the wrong columns.
    """
    LOG_FILE.parent.mkdir(exist_ok=True)

    if not LOG_FILE.exists():
        with open(LOG_FILE, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=COLUMNS).writeheader()
        return

    with open(LOG_FILE, newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames == COLUMNS:
            return
        old_rows = list(reader)

    logger.info("trade_log.csv: migrating to %d columns", len(COLUMNS))
    with open(LOG_FILE, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, restval="")
        w.writeheader()
        for row in old_rows:
            w.writerow({k: row.get(k, "") for k in COLUMNS})


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

    hook  = result.get("ross_hook") or {}
    gt    = result.get("global_trend") or {}
    sweep = result.get("sweep") or {}
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
        "daily_bias":         gt.get("daily_bias", ""),
        "market_regime":      gt.get("market_regime", ""),
        "sweep_level":        sweep.get("level_name", ""),
        "sweep_candles_ago":  sweep.get("candles_ago", ""),
        "sweep_has_fvg":      sweep.get("has_fvg", ""),
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


def _expectancy(subset: list[dict]) -> Optional[float]:
    """
    Average R per trade — the number that actually decides whether a setup
    is worth trading, which win rate alone can't tell you.

    A TP1 hit pays the setup's own R:R; an SL hit costs exactly 1R (that's
    what R means); an expired trade is counted as 0R, since it was closed
    out flat-ish rather than resolved either way. Positive = the edge
    survives; negative = the setup loses money however good the win rate
    looks next to it.
    """
    if not subset:
        return None

    total = 0.0
    for r in subset:
        if r["status"] == "tp1_hit":
            try:
                total += float(r["rr_ratio"] or 0)
            except ValueError:
                pass
        elif r["status"] == "sl_hit":
            total -= 1.0
    return round(total / len(subset), 2)


def get_stats() -> dict:
    """
    Win rate AND expectancy, broken down by entry type, OB confluence, and
    R:R band. The R:R bands exist to answer one question directly: whether
    the low-R:R setups admitted by a lower MIN_RR_RATIO carry their weight
    or drag the average down.
    """
    rows     = _read_rows()
    resolved = [r for r in rows if r["status"] in ("tp1_hit", "sl_hit", "expired")]
    pending  = [r for r in rows if r["status"] == "pending"]

    def win_rate(subset: list[dict]) -> Optional[float]:
        if not subset:
            return None
        hits = sum(1 for r in subset if r["status"] == "tp1_hit")
        return round(hits / len(subset) * 100, 1)

    def summarize(subset: list[dict]) -> dict:
        return {
            "count":      len(subset),
            "win_rate":   win_rate(subset),
            "expectancy": _expectancy(subset),
        }

    by_type: dict[str, dict] = {}
    for et in ("HOOK", "SWEEP", "TTE"):
        sub = [r for r in resolved if r["entry_type"] == et]
        if sub or et != "TTE":   # hide TTE once it has no history left
            by_type[et] = summarize(sub)

    def rr_of(row: dict) -> float:
        try:
            return float(row["rr_ratio"] or 0)
        except ValueError:
            return 0.0

    by_rr = {
        "1.5-2":  summarize([r for r in resolved if 1.5 <= rr_of(r) < 2.0]),
        "2-3":    summarize([r for r in resolved if 2.0 <= rr_of(r) < 3.0]),
        "3+":     summarize([r for r in resolved if rr_of(r) >= 3.0]),
    }

    ob_yes = [r for r in resolved if str(r.get("ob_confluence")) == "True"]
    ob_no  = [r for r in resolved if str(r.get("ob_confluence")) == "False"]

    return {
        "total":       len(rows),
        "pending":     len(pending),
        "resolved":    len(resolved),
        "tp1_hit":     sum(1 for r in resolved if r["status"] == "tp1_hit"),
        "sl_hit":      sum(1 for r in resolved if r["status"] == "sl_hit"),
        "expired":     sum(1 for r in resolved if r["status"] == "expired"),
        "win_rate":    win_rate(resolved),
        "expectancy":  _expectancy(resolved),
        "by_type":     by_type,
        "by_rr":       by_rr,
        "ob_yes_wr":   win_rate(ob_yes),
        "ob_no_wr":    win_rate(ob_no),
        "ob_yes_n":    len(ob_yes),
        "ob_no_n":     len(ob_no),
    }
