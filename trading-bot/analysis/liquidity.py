"""
Liquidity primitives: daily levels, sweeps, and displacement.

These implement the "sweep → displacement → retest" model, which is the
mirror image of the Ross Hook continuation model already in the bot:

  Continuation (Ross Hook): price CLOSES beyond a level and keeps going.
      The level gave way — trade with the break.
  Reversal (sweep):         price WICKS beyond a level and closes back.
      The level held and the move beyond it was a stop hunt — trade the
      rejection.

The distinction is the close, not the touch. That single difference is
what separates the two setups, so both can share the same context, target
and risk machinery without contradicting each other.

Yesterday's high and low are the reference levels because they're where
resting stops actually cluster on a 24h market — every trader watching a
daily chart sees the same two lines, unlike a subjective trendline.
"""
import logging
from dataclasses import dataclass
from typing import Literal, Optional

import pandas as pd

from .smc import FVG, find_fvg

logger = logging.getLogger(__name__)

# How far back to look for a sweep, in 15m candles (24 = 6 hours).
#
# Widened from 12 after a live scan found 14 of 15 permitted directions
# rejected for having no sweep inside a 3-hour window — a rate of roughly
# 0.9 setups/day, which would have taken a month to produce enough trades
# to judge anything by. This does not change what counts as a sweep, only
# how long afterwards one stays actionable, and the reclaim-still-holds
# check already retires a setup whose premise has broken. The entry is a
# retrace into the displacement's FVG, which is often still unfilled hours
# later.
#
# Self-checking: sweep_candles_ago is logged per setup, so whether sweeps
# aged 3-6h actually perform worse than fresh ones is answerable from the
# data this change itself produces.
SWEEP_LOOKBACK = 24

# Displacement: the rejection candle's BODY must be at least this many
# 15m ATRs. Body (not range) because a long wick with a tiny body is
# indecision, while a large body is one side actually taking control.
DISPLACEMENT_ATR_MULT = 1.0


@dataclass
class Sweep:
    """A level that was wicked through and reclaimed."""
    kind: Literal["bullish", "bearish"]  # bullish = a LOW was swept
    level: float
    level_name: str   # e.g. "prev_day_low" — for alerts and logs
    index: int        # candle that did the sweeping
    candles_ago: int


# ─── Daily Levels ─────────────────────────────────────────────────────────────

def get_previous_day_range(df: pd.DataFrame) -> Optional[dict]:
    """
    Previous UTC day's high and low, derived from intraday candles.

    Returns None when the DataFrame doesn't reach back far enough to
    contain a complete prior day (CANDLES_LIMIT=200 on 15m is ~50h, so
    normally it does).
    """
    if df.empty or "timestamp" not in df.columns:
        return None

    days  = df["timestamp"].dt.date
    today = days.iloc[-1]

    prior = df[days < today]
    if prior.empty:
        return None

    prev_day  = days[days < today].iloc[-1]
    prev_bars = df[days == prev_day]

    return {
        "high": float(prev_bars["high"].max()),
        "low":  float(prev_bars["low"].min()),
        "date": str(prev_day),
    }


# ─── Sweep Detection ──────────────────────────────────────────────────────────

def detect_sweep(
    df: pd.DataFrame,
    level: float,
    kind: str,
    level_name: str,
    lookback: int = SWEEP_LOOKBACK,
) -> Optional[Sweep]:
    """
    Finds a recent sweep of `level` that has held since.

    Bullish sweep (kind="bullish"): a candle's LOW pierces below the level
    but it CLOSES back above — sell-side liquidity was taken and rejected.
    Bearish sweep mirrors this on the high side.

    The reclaim must still hold: if any later candle closed back through
    the level, the level genuinely broke and this is no longer a sweep —
    it's a breakout, which is the Ross Hook path, not this one.

    Returns the most recent qualifying sweep, or None.
    """
    if df.empty:
        return None

    highs  = df["high"].values
    lows   = df["low"].values
    closes = df["close"].values
    last_i = len(df) - 1
    start  = max(0, last_i - lookback + 1)

    # Newest first — the most recent sweep is the tradeable one
    for i in range(last_i, start - 1, -1):
        if kind == "bullish":
            swept = lows[i] < level and closes[i] > level
            # Reclaim broken if anything since closed back below
            held = all(closes[k] > level for k in range(i + 1, last_i + 1))
        else:
            swept = highs[i] > level and closes[i] < level
            held = all(closes[k] < level for k in range(i + 1, last_i + 1))

        if swept and held:
            return Sweep(
                kind=kind,
                level=level,
                level_name=level_name,
                index=i,
                candles_ago=last_i - i,
            )

    return None


# ─── Displacement ─────────────────────────────────────────────────────────────

def is_displacement(
    df: pd.DataFrame,
    index: int,
    atr: float,
    mult: float = DISPLACEMENT_ATR_MULT,
) -> bool:
    """
    True if the candle at `index` is a displacement candle — a decisive
    body of at least `mult` ATRs, in any direction.

    Guards against atr <= 0 (degenerate data), which would otherwise make
    every candle qualify.
    """
    if atr <= 0 or index < 0 or index >= len(df):
        return False

    body = abs(float(df["close"].iloc[index]) - float(df["open"].iloc[index]))
    return body >= atr * mult


def find_displacement_in_range(
    df: pd.DataFrame,
    start_index: int,
    bias: str,
    atr: float,
    mult: float = DISPLACEMENT_ATR_MULT,
) -> Optional[int]:
    """
    Index of the first displacement candle at/after `start_index` moving in
    `bias` direction — i.e. the impulse that confirms the sweep's rejection.

    Returns None if the move away from the level was never decisive, which
    is the common case and the main thing this filter is here to reject.
    """
    if atr <= 0:
        return None

    opens  = df["open"].values
    closes = df["close"].values

    for i in range(max(0, start_index), len(df)):
        body = abs(closes[i] - opens[i])
        if body < atr * mult:
            continue
        direction = "bullish" if closes[i] > opens[i] else "bearish"
        if direction == bias:
            return i

    return None


# ─── Execution Zone ───────────────────────────────────────────────────────────

def find_entry_fvg(
    df: pd.DataFrame,
    bias: str,
    after_index: int,
) -> Optional[FVG]:
    """
    The unfilled FVG left behind by the displacement leg — the execution
    zone to enter on a retrace, rather than chasing the impulse candle.

    Only gaps formed at/after `after_index` count: an older FVG belongs to
    a different move and says nothing about this one. Returns the most
    recent qualifying gap, or None when the impulse left no imbalance (in
    which case the caller falls back to a level-based entry).
    """
    candidates = [
        f for f in find_fvg(df)
        if f.kind == bias and not f.filled and f.index >= after_index
    ]
    return candidates[0] if candidates else None  # find_fvg returns newest first
