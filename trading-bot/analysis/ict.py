"""
ICT confluence factors for the SWEEP setup: market structure shift (MSS),
optimal trade entry (OTE) Fibonacci position, and session/killzone.

None of these gate a setup. They are recorded per alert so the question
"do setups with an MSS / inside OTE / in a killzone actually perform
better?" becomes answerable from logs/trade_log.csv instead of from
belief. This mirrors how liquidity_walls.py is wired in — observe first,
gate only once the logged data says it is worth gating on.

Why each one is worth measuring:

  MSS   The bot currently treats a displacement candle as proof the sweep
        was rejected. A displacement is a big body; it says someone moved
        price, not that structure changed hands. MSS is the stricter
        reading — price must CLOSE beyond the last opposing short-term
        swing, i.e. the previous leg actually broke. If displacement alone
        is enough, MSS-flagged setups will not outperform, and that is a
        useful answer too.

  OTE   Entry today is the proximal edge of the displacement FVG, wherever
        that happens to sit on the leg. ICT's claim is that the 0.62-0.79
        retracement of the impulse is where the reversal continues from.
        Recording where the entry actually falls on that leg tests whether
        FVG-edge entries that happen to land in OTE resolve better than
        ones that land shallow (0.2-0.4) or deep (past 0.79).

  Session  A 24h market still has concentrated participation windows. The
        sweeps this model is built on are, in theory, manufactured around
        the London and New York opens. Timestamped sessions say whether
        that holds on Hyperliquid or is inherited folklore from FX.

All times are computed in UTC. Vilnius (EEST, UTC+3) equivalents are in
the comments for reading alerts, not used in logic — a fixed offset would
silently drift when Lithuania leaves DST.
"""
import logging
from dataclasses import dataclass
from typing import Literal, Optional

import pandas as pd

from .smc import find_swing_highs, find_swing_lows

logger = logging.getLogger(__name__)

# Short-term structure window for MSS. Deliberately tighter than the
# window=5 used for 4H structure: an MSS is an intraday event on 15m, and
# a 5-bar window needs 5 bars of confirmation on each side, which would
# only ever confirm an MSS more than an hour after it happened.
MSS_SWING_WINDOW = 3

# OTE band. ICT's own "0.705" and the 0.71 in the reference material are
# the midpoint of this range, not a line price respects to the tick —
# a band is the honest version of the same idea.
OTE_MIN = 0.62
OTE_MAX = 0.79

# Sessions, in UTC. EEST (UTC+3) in brackets.
# Killzones are the first hours of London and New York, where the sweep
# model claims the manufactured moves happen.
SESSIONS: list[tuple[str, int, int]] = [
    ("asia",   0,  7),    # 03:00-10:00 EEST
    ("london", 7,  12),   # 10:00-15:00 EEST
    ("ny",     12, 17),   # 15:00-20:00 EEST
]
KILLZONES: list[tuple[str, int, int]] = [
    ("london_kz", 7,  10),   # 10:00-13:00 EEST
    ("ny_kz",     12, 15),   # 15:00-18:00 EEST
]


@dataclass
class MssEvent:
    """A confirmed break of the last opposing short-term swing."""
    direction: Literal["bullish", "bearish"]
    level: float        # the swing level that was closed through
    index: int          # candle that closed beyond it
    candles_ago: int


# ─── Market Structure Shift ───────────────────────────────────────────────────

def detect_mss(
    df: pd.DataFrame,
    sweep_index: int,
    bias: str,
    window: int = MSS_SWING_WINDOW,
) -> Optional[MssEvent]:
    """
    The first candle after `sweep_index` that CLOSES beyond the last
    opposing short-term swing formed before the sweep.

    Bullish (a low was swept): the reference is the most recent swing HIGH
    that formed before the sweep candle; MSS is the first later close
    above it. Bearish mirrors this.

    Returns None when no such swing exists in the data (too little history
    before the sweep) or when nothing has closed through it yet — the
    common case, and the whole point of recording it.
    """
    if df.empty or sweep_index < 0 or sweep_index >= len(df):
        return None

    closes = df["close"].values
    last_i = len(df) - 1

    if bias == "bullish":
        prior = [sp for sp in find_swing_highs(df, window) if sp.index < sweep_index]
    else:
        prior = [sp for sp in find_swing_lows(df, window) if sp.index < sweep_index]

    if not prior:
        return None

    ref = prior[-1]

    for i in range(sweep_index + 1, last_i + 1):
        broke = closes[i] > ref.price if bias == "bullish" else closes[i] < ref.price
        if broke:
            return MssEvent(
                direction=bias,
                level=float(ref.price),
                index=i,
                candles_ago=last_i - i,
            )

    return None


# ─── Optimal Trade Entry (Fibonacci) ──────────────────────────────────────────

def ote_position(
    df: pd.DataFrame,
    sweep_index: int,
    bias: str,
    entry: float,
) -> Optional[dict]:
    """
    Where `entry` sits on the impulse leg, measured from the impulse
    extreme back toward the sweep extreme.

    The leg runs from the sweep wick (0.0 = the extreme the hunt reached)
    to the furthest point price has since travelled in the setup's
    direction (1.0 = the impulse extreme). `retracement` is expressed the
    way ICT reads it: 0.0 at the impulse extreme, 1.0 back at the sweep
    extreme, so 0.71 means "71% of the impulse given back".

    Returns None on a degenerate leg (zero range), which is not an error —
    it means the impulse has not travelled yet.
    """
    if df.empty or sweep_index < 0 or sweep_index >= len(df):
        return None

    highs = df["high"].values
    lows  = df["low"].values
    last_i = len(df) - 1

    if bias == "bullish":
        leg_start = float(lows[sweep_index])                       # sweep extreme
        leg_end   = float(highs[sweep_index : last_i + 1].max())   # impulse extreme
    else:
        leg_start = float(highs[sweep_index])
        leg_end   = float(lows[sweep_index : last_i + 1].min())

    leg = abs(leg_end - leg_start)
    if leg <= 0:
        return None

    retracement = abs(leg_end - entry) / leg
    in_ote = OTE_MIN <= retracement <= OTE_MAX

    return {
        "leg_start":    round(leg_start, 8),   # sweep extreme
        "leg_end":      round(leg_end, 8),     # impulse extreme
        "leg_pct":      round(leg / leg_start * 100, 3) if leg_start else None,
        "retracement":  round(retracement, 4),
        "in_ote":       in_ote,
        # Where a strict 0.71 entry would have been, for comparison against
        # the FVG-edge entry actually used.
        "ote_price":    round(leg_end + (leg_start - leg_end) * 0.71, 8),
    }


# ─── Session ──────────────────────────────────────────────────────────────────

def session_of(ts: pd.Timestamp) -> dict:
    """
    Which trading session a UTC timestamp falls in, and whether it is
    inside a killzone. Naive timestamps are assumed UTC — every candle
    from market_data carries tz-aware UTC, so this is a guard, not a path.
    """
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")

    hour = ts.hour

    name = "off"
    for label, start, end in SESSIONS:
        if start <= hour < end:
            name = label
            break

    kz = None
    for label, start, end in KILLZONES:
        if start <= hour < end:
            kz = label
            break

    return {"session": name, "killzone": kz, "hour_utc": hour}


# ─── Aggregate ────────────────────────────────────────────────────────────────

def describe_ict_context(
    df: pd.DataFrame,
    sweep_index: int,
    bias: str,
    entry: float,
) -> dict:
    """
    All three factors for one SWEEP setup, in the shape the result dict
    and trade logger expect. Any factor that cannot be computed comes back
    as None rather than a default — a missing MSS and an MSS at 0 candles
    ago are different facts and must not log identically.
    """
    mss = detect_mss(df, sweep_index, bias)
    ote = ote_position(df, sweep_index, bias, entry)
    ses = session_of(df["timestamp"].iloc[-1]) if "timestamp" in df.columns else {}

    return {
        "mss": (
            {
                "level":       mss.level,
                "index":       mss.index,
                "candles_ago": mss.candles_ago,
            }
            if mss else None
        ),
        "ote":     ote,
        "session": ses.get("session"),
        "killzone": ses.get("killzone"),
    }
