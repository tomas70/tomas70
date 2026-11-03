"""
Forward test of FOLLOWING the published trades of "Sonic AI" (XAUUSD) on Evedex XAUTUSD.

The monitor repo sonicdesk-live/sonicdesk publishes Sonic's closed trades (open time, close time,
side, prices). Whether that account is real is unknown and NOT tested here. The question is
narrower: if the trades are real, could a follower on Evedex make money after fees and delay?
Rules are fixed in research/sonic_follow_prereg.md.

For each new trade, for each delay d in DELAYS, the follower enters at the open of the 1-minute
Evedex XAUTUSD candle containing (open_time + d) and exits at the open of the candle containing
(close_time + d), paying FEE per side plus half the spread per side. Nothing here places orders.

The feed shows trades only after they close, so this is a forward report on whether following
would have worked, not a live copy.

Log: logs/sonic_follow.csv (one row per Sonic trade, wide by delay).
"""
import csv
import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

FEED_URL = "https://raw.githubusercontent.com/sonicdesk-live/sonicdesk/main/trades_new.csv"
CSV_FILE = Path(__file__).resolve().parent.parent / "logs" / "sonic_follow.csv"
PAIR = "XAUT"
DELAYS = (0, 1, 3, 5)
PRIMARY_DELAY = 3
FEE = 0.0004                 # per side, Evedex taker (as analysis/paper_tracker.py)
HALF_SPREAD = 0.20           # USD/oz per side, measured on the live book 2026-11-03
MAX_HOLD_MIN = 720
FORWARD_START = pd.Timestamp("2026-11-04", tz="UTC")   # compared with UTC-converted trade times
FALLBACK_MIN = 3             # missing minute -> first candle within this many minutes after

COLUMNS = ["open", "close", "side", "sonic_pts"] + [
    f"{k}_{d}" for d in DELAYS for k in ("entry", "exit", "gross", "net")]


# ── parsing ───────────────────────────────────────────────────────────────

_NY = ZoneInfo("America/New_York")


def server_to_utc(t: pd.Timestamp) -> pd.Timestamp:
    """Published times are the broker SERVER clock: UTC+3 while New York is on daylight time, else UTC+2.

    Found by a time-offset diagnostic (research/sonic_follow_prereg.md, addendum): with +3 the published
    move matches the Evedex move (corr 0.83 over Apr-Oct 2026, 0.99 on Oct 26-30 when Europe was already
    on winter time), with any other whole-hour offset corr is ~0.
    """
    naive = t.tz_localize(None) if t.tzinfo else t
    probe = naive.tz_localize("UTC").to_pydatetime().astimezone(_NY)
    offset = 3 if probe.dst() else 2
    return (naive - pd.Timedelta(hours=offset)).tz_localize("UTC")


def parse_new_csv(text: str) -> list[dict]:
    """trades_new.csv: open,close,side,lots,price_open,price_close,profit (UTC 'YYYY-MM-DD HH:MM')."""
    out = []
    for r in csv.DictReader(text.strip().splitlines()):
        side = 1 if r["side"] == "B" else -1
        out.append({"open": server_to_utc(pd.Timestamp(r["open"])), "close": server_to_utc(pd.Timestamp(r["close"])),
                    "side": side, "sonic_pts": side * (float(r["price_close"]) - float(r["price_open"]))})
    return out


def parse_raw(text: str) -> list[dict]:
    """trades_raw.txt: open (minutes since epoch), duration (min), B/S, lots, price_open, price_close, profit."""
    out = []
    for row in csv.reader(text.strip().splitlines()):
        o = server_to_utc(pd.Timestamp(int(float(row[0])) * 60, unit="s"))
        side = 1 if row[2] == "B" else -1
        out.append({"open": o, "close": o + pd.Timedelta(minutes=int(row[1])), "side": side,
                    "sonic_pts": side * (float(row[5]) - float(row[4]))})
    return out


# ── execution model ───────────────────────────────────────────────────────

def bar_open(bars: dict, t: pd.Timestamp) -> Optional[float]:
    """Open of the 1m candle containing t; if that minute has no candle, the first one within FALLBACK_MIN."""
    m = t.floor("min")
    for k in range(FALLBACK_MIN + 1):
        v = bars.get(m + pd.Timedelta(minutes=k))
        if v is not None:
            return v
    return None


def follow_trade(trade: dict, bars: dict, delay: int, fee: float = FEE, half_spread: float = HALF_SPREAD) -> Optional[dict]:
    """Follower result for one Sonic trade at one delay; None when a price is missing."""
    d = pd.Timedelta(minutes=delay)
    entry, exit_ = bar_open(bars, trade["open"] + d), bar_open(bars, trade["close"] + d)
    if entry is None or exit_ is None:
        return None
    gross_pts = trade["side"] * (exit_ - entry)
    gross_bps = gross_pts / entry * 1e4
    cost_bps = 2 * fee * 1e4 + 2 * half_spread / entry * 1e4
    return {"entry": entry, "exit": exit_, "gross_pts": gross_pts, "gross": gross_bps, "net": gross_bps - cost_bps}


def day_clustered_t(net: pd.Series, days: pd.Series) -> float:
    """t-statistic of the mean of per-day totals (each trading day is one observation)."""
    g = net.groupby(days.values).sum()
    if len(g) < 3 or g.std() == 0:
        return float("nan")
    return float(g.mean() / (g.std() / math.sqrt(len(g))))


# ── data ──────────────────────────────────────────────────────────────────

def fetch_bars(a: pd.Timestamp, b: pd.Timestamp) -> dict:
    """1m XAUTUSD candles (open prices keyed by minute start) between a and b from Evedex."""
    from analysis.market_data import MARKET_DATA_BASE_URL, _get_with_retry, to_instrument
    resp = _get_with_retry(f"{MARKET_DATA_BASE_URL}/api/history/{to_instrument(PAIR)}/list",
                           params={"group": "1m", "after": a.isoformat(), "before": b.isoformat()})
    return {pd.Timestamp(c[0], unit="ms", tz="UTC"): float(c[1]) for c in resp.json()}


def bars_for(trade: dict) -> dict:
    """Candles around a trade's entry and exit, wide enough for every delay."""
    pad_a, pad_b = timedelta(minutes=2), timedelta(minutes=max(DELAYS) + FALLBACK_MIN + 3)
    bars = {}
    for t in (trade["open"], trade["close"]):
        bars.update(fetch_bars(t - pad_a, t + pad_b))
    return bars


# ── forward log ───────────────────────────────────────────────────────────

def read_rows(path: Optional[Path] = None) -> list[dict]:
    path = path or CSV_FILE
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def _append(row: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if new:
            w.writeheader()
        w.writerow({k: ("" if row.get(k) is None else row[k]) for k in COLUMNS})


def update(now: Optional[datetime] = None, path: Optional[Path] = None, feed_text: Optional[str] = None,
           bars_fn: Callable[[dict], dict] = bars_for) -> list[str]:
    """Log every new Sonic trade (opened on/after FORWARD_START, old enough to have candles). Returns log lines."""
    path = path or CSV_FILE
    now_ts = pd.Timestamp(now or datetime.now(timezone.utc))
    if feed_text is None:
        r = httpx.get(FEED_URL, timeout=20)
        r.raise_for_status()
        feed_text = r.text
    done = {r["open"] for r in read_rows(path)}
    out = []
    for t in sorted(parse_new_csv(feed_text), key=lambda x: x["open"]):
        key = t["open"].strftime("%Y-%m-%dT%H:%M")
        hold = (t["close"] - t["open"]).total_seconds() / 60
        if (key in done or t["open"] < FORWARD_START or hold > MAX_HOLD_MIN
                or t["close"] + timedelta(minutes=max(DELAYS) + 10) > now_ts):
            continue
        try:
            bars = bars_fn(t)
        except Exception as exc:
            logger.warning("sonic follow: candles for %s failed: %s", key, exc)
            continue
        row = {"open": key, "close": t["close"].strftime("%Y-%m-%dT%H:%M"), "side": t["side"],
               "sonic_pts": round(t["sonic_pts"], 2)}
        for d in DELAYS:
            res = follow_trade(t, bars, d)
            if res:
                row.update({f"entry_{d}": res["entry"], f"exit_{d}": res["exit"],
                            f"gross_{d}": round(res["gross"], 2), f"net_{d}": round(res["net"], 2)})
        _append(row, path)
        out.append(f"logged {key}")
    return out


SYNC_WINDOW, SYNC_MIN = 20, 0.5


def sync_check(rows: list[dict]) -> Optional[float]:
    """Correlation of Sonic's published move with the follower's d=0 move over the latest trades."""
    pairs = [(float(r["sonic_pts"]), float(r["gross_0"]) * float(r["entry_0"]) / 1e4)
             for r in rows[-SYNC_WINDOW:] if r.get("gross_0") and r.get("entry_0")]
    if len(pairs) < 8:
        return None
    a, b = zip(*pairs)
    if len(set(a)) < 2 or len(set(b)) < 2:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def report(path: Optional[Path] = None) -> str:
    rows = read_rows(path)
    if not rows:
        return ("📭 <b>Sonic sekimas</b>\n\nDar nėra įrašų (loguojami nauji Sonic sandoriai nuo "
                f"{FORWARD_START.date()}).")
    lines = [f"🦔 <b>Sonic sekimas (Evedex XAUT)</b>  <i>{len(rows)} sandorių, {rows[0]['open'][:10]} → {rows[-1]['open'][:10]}</i>\n",
             f"<i>Kaštai: {FEE*100:.2f}%/pusę + {HALF_SPREAD:.2f} USD/oz/pusę. Vėlavimas = min po Sonic įėjimo/išėjimo.</i>\n"]
    for d in DELAYS:
        v = [float(r[f"net_{d}"]) for r in rows if r.get(f"net_{d}")]
        if not v:
            continue
        mark = "  ← pirminis" if d == PRIMARY_DELAY else ""
        lines.append(f"d={d} min: vid. {sum(v)/len(v):+.1f} bps, laimi {sum(x > 0 for x in v)/len(v)*100:.0f}%, "
                     f"suma {sum(v):+.0f} bps ({len(v)} sand.){mark}")
    sp = [float(r["sonic_pts"]) for r in rows]
    lines.append(f"\nSonic paskelbtas vid. judesys: {sum(sp)/len(sp):+.2f} USD/oz")
    c = sync_check(rows)
    if c is not None:
        lines.append(f"Tikrumo patikra (paskutiniai {min(len(rows), SYNC_WINDOW)}): Sonic vs rinkos judesio koreliacija {c:.2f}"
                     + ("  ⚠️ <b>per maža — laiko konvertavimas ar šaltinis gali nebetikti</b>" if c < SYNC_MIN else "  ✓"))
    lines.append("<i>Ar paskyra tikra — nežinoma. Tik ar sekimas ant Evedex apsimokėtų. Verdiktui reikia ≥100 sandorių.</i>")
    return "\n".join(lines)
