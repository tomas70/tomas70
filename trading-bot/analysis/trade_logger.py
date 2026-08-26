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
from config import STRATEGY_EPOCH

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


def simulate_walk(
    df: pd.DataFrame,
    logged_at: datetime,
    bias: str,
    tp: float,
    sl: float,
    max_candles: int = MAX_OUTCOME_CANDLES,
) -> tuple[Optional[str], Optional[str], Optional[int]]:
    """
    Walks every 15m candle closed after `logged_at` and reports which of
    `tp` / `sl` it hit first — the core simulation shared by production
    outcome resolution (update_all_pending_outcomes) and offline replay
    (optimize_tp.py testing alternate TP distances against the same
    logged entry/SL).

    Stop and limit orders are triggered by price, not by close, so wicks
    are the correct comparison. When both levels appear on the same candle
    (gap/spike), TP is credited — standard backtesting convention when
    intra-candle order is unknown.

    Returns (status, outcome_at, candles). status is None when `df`
    doesn't yet contain enough history past `logged_at` to resolve either
    way — the caller should treat that as "not resolvable from this data"
    rather than as an expiry.
    """
    after = df[df["timestamp"] > pd.Timestamp(logged_at)].reset_index(drop=True)
    if after.empty:
        return None, None, None

    for idx, candle in after.iterrows():
        tp_hit = candle["high"] >= tp if bias == "bullish" else candle["low"] <= tp
        sl_hit = candle["low"]  <= sl if bias == "bullish" else candle["high"] >= sl

        if tp_hit or sl_hit:
            status = "tp1_hit" if tp_hit else "sl_hit"
            return status, str(candle["timestamp"]), int(idx) + 1

    if len(after) >= max_candles:
        return "expired", str(after.iloc[-1]["timestamp"]), len(after)

    return None, None, None  # not enough forward data yet


def update_all_pending_outcomes() -> int:
    """
    Resolves pending log entries against the latest 15m candle data.

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

        status, outcome_at, outcome_candles = simulate_walk(
            df,
            datetime.fromisoformat(row["logged_at"]),
            row["bias"],
            float(row["tp1"]),
            float(row["sl"]),
        )

        if status:
            row["status"]          = status
            row["outcome_at"]      = outcome_at
            row["outcome_candles"] = outcome_candles
            resolved += 1
            logger.info(
                "Outcome: %s %s → %s (%s bars)", row["pair"], row["bias"], status, outcome_candles
            )

    if resolved:
        _write_rows(rows)
    return resolved


def _rr_of(row: dict) -> float:
    try:
        return float(row["rr_ratio"] or 0)
    except ValueError:
        return 0.0


def _baseline_win_rate(subset: list[dict]) -> Optional[float]:
    """
    What win rate a coin-flip entry (no edge, pure random walk between the
    two levels) would produce at each trade's own R:R — the number actual
    win rate needs to beat before a setup can be called a real edge rather
    than noise. Approximates each trade as symmetric diffusion between its
    entry and SL/TP1 boundaries: P(hit TP1 first) ≈ 1/(1+R). This ignores
    drift and volatility clustering, so treat it as a sanity-check floor,
    not an exact model.

    Uses the same subset/denominator as win_rate() (expired trades
    included), so the two stay directly comparable as an edge in
    percentage points.
    """
    if not subset:
        return None
    probs = [1 / (1 + _rr_of(r)) for r in subset]
    return round(sum(probs) / len(probs) * 100, 1)


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


def get_stats(all_time: bool = False) -> dict:
    """
    Win rate AND expectancy, broken down by entry type, OB confluence, and
    R:R band. The R:R bands exist to answer one question directly: whether
    the low-R:R setups admitted by a lower MIN_RR_RATIO carry their weight
    or drag the average down.

    By default (all_time=False), only rows logged at/after
    config.STRATEGY_EPOCH count. trade_log.csv accumulates forever across
    every past version of the setup-detection logic, so an unfiltered
    view permanently blends the current rules with whatever came before —
    a real improvement (or regression) in the current system gets diluted
    into whatever the historical average already was. Pass all_time=True
    for the full unfiltered history (e.g. /log all).
    """
    rows = _read_rows()

    excluded_old = 0
    if not all_time:
        epoch = datetime.fromisoformat(STRATEGY_EPOCH)
        kept = []
        for r in rows:
            if datetime.fromisoformat(r["logged_at"]) >= epoch:
                kept.append(r)
            else:
                excluded_old += 1
        rows = kept

    resolved = [r for r in rows if r["status"] in ("tp1_hit", "sl_hit", "expired")]
    pending  = [r for r in rows if r["status"] == "pending"]

    def win_rate(subset: list[dict]) -> Optional[float]:
        if not subset:
            return None
        hits = sum(1 for r in subset if r["status"] == "tp1_hit")
        return round(hits / len(subset) * 100, 1)

    def summarize(subset: list[dict]) -> dict:
        wr       = win_rate(subset)
        baseline = _baseline_win_rate(subset)
        edge     = round(wr - baseline, 1) if wr is not None and baseline is not None else None
        return {
            "count":       len(subset),
            "win_rate":    wr,
            "baseline_wr": baseline,
            "edge_pp":     edge,
            "expectancy":  _expectancy(subset),
        }

    by_type: dict[str, dict] = {}
    for et in ("HOOK", "SWEEP", "TTE"):
        sub = [r for r in resolved if r["entry_type"] == et]
        if sub or et != "TTE":   # hide TTE once it has no history left
            by_type[et] = summarize(sub)

    by_rr = {
        "1.5-2":  summarize([r for r in resolved if 1.5 <= _rr_of(r) < 2.0]),
        "2-3":    summarize([r for r in resolved if 2.0 <= _rr_of(r) < 3.0]),
        "3+":     summarize([r for r in resolved if _rr_of(r) >= 3.0]),
    }

    ob_yes = [r for r in resolved if str(r.get("ob_confluence")) == "True"]
    ob_no  = [r for r in resolved if str(r.get("ob_confluence")) == "False"]
    by_ob  = {"with_ob": summarize(ob_yes), "without_ob": summarize(ob_no)}

    overall = summarize(resolved)

    return {
        "total":         len(rows),
        "pending":       len(pending),
        "resolved":      len(resolved),
        "tp1_hit":       sum(1 for r in resolved if r["status"] == "tp1_hit"),
        "sl_hit":        sum(1 for r in resolved if r["status"] == "sl_hit"),
        "expired":       sum(1 for r in resolved if r["status"] == "expired"),
        "win_rate":      overall["win_rate"],
        "baseline_wr":   overall["baseline_wr"],
        "edge_pp":       overall["edge_pp"],
        "expectancy":    overall["expectancy"],
        "by_type":       by_type,
        "by_rr":         by_rr,
        "by_ob":         by_ob,
        "all_time":      all_time,
        "epoch":         STRATEGY_EPOCH,
        "excluded_old":  excluded_old,
        "ob_yes_wr":     win_rate(ob_yes),
        "ob_no_wr":      win_rate(ob_no),
        "ob_yes_n":      len(ob_yes),
        "ob_no_n":       len(ob_no),
    }
