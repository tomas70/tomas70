"""
Joe Ross methodology: 1-2-3 Pattern → Ross Hook → Trader's Trick Entry (TTE)

Flow:
  1. 1-2-3 pattern forms on 15m
  2. Price breaks out beyond P2 — trend confirmed
  3. Post-breakout swing = Ross Hook level
  4. Price pulls back to/toward the hook (correction)
  5. TTE: violation of the high (bullish) / low (bearish) of any of the
     first TTE_MAX_CORRECTION_BARS bars after the hook — enter at that
     bar's high/low (before the crowd). A double/triple top or bottom
     within the window doesn't consume the bar budget (no real progress
     was made), so the window can extend slightly in that case. Beyond
     the window the odds favor consolidation, so TTE is abandoned.
  6. SL = the opposite side of the signal bar itself (Ross's rule),
     not the full pullback/bounce extreme.
"""
import logging
from dataclasses import dataclass
from typing import Literal, Optional

import pandas as pd

from .smc import SwingPoint, find_swing_highs, find_swing_lows

logger = logging.getLogger(__name__)

MAIN_SWING_WINDOW = 5   # window for 1-2-3 structure
HOOK_SWING_WINDOW = 3   # smaller window for post-breakout hook sensitivity
MAX_HOOK_AGE      = 30  # candles; older hooks are stale

# TTE only watches the first N bars of the correction — "beyond 3 bars of
# correction, the odds begin to favor consolidation, so there's no reason
# to attempt the TTE" (Ross). Window can extend within that budget when a
# double/triple top forms (see TTE_TIE_TOLERANCE_PCT below).
TTE_MAX_CORRECTION_BARS = 3
# A bar whose high/low is within this tolerance of the reference level is
# treated as a tie (double/triple top or bottom) rather than a genuinely
# lower/higher bar — it doesn't consume the 3-bar budget, since price
# hasn't made real progress away from that level. Real OHLCV data rarely
# produces an exact tie, hence the small tolerance.
TTE_TIE_TOLERANCE_PCT = 0.0005


# ─── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Pattern123:
    kind: Literal["bullish", "bearish"]
    p1: SwingPoint   # origin  (low for bullish, high for bearish)
    p2: SwingPoint   # impulse extreme (high for bullish, low for bearish)
    p3: SwingPoint   # retracement (higher low for bullish, lower high for bearish)


# ─── 1-2-3 Pattern Detection ──────────────────────────────────────────────────

def detect_1_2_3_pattern(df: pd.DataFrame) -> list[Pattern123]:
    """
    Scans for all valid Joe Ross 1-2-3 patterns.

    Bullish bottom: swing_low(P1) → swing_high(P2) → swing_low(P3), P3 > P1
      → breakout ABOVE P2 signals trend resumption
    Bearish top:   swing_high(P1) → swing_low(P2) → swing_high(P3), P3 < P1
      → breakout BELOW P2 signals trend resumption
    """
    swing_highs = find_swing_highs(df, window=MAIN_SWING_WINDOW)
    swing_lows  = find_swing_lows(df, window=MAIN_SWING_WINDOW)
    all_swings  = sorted(swing_highs + swing_lows, key=lambda s: s.index)

    patterns: list[Pattern123] = []
    for i in range(len(all_swings) - 2):
        s1, s2, s3 = all_swings[i], all_swings[i + 1], all_swings[i + 2]

        if s1.kind == "low" and s2.kind == "high" and s3.kind == "low":
            if s3.price > s1.price:  # higher low = intact bullish structure
                patterns.append(Pattern123(kind="bullish", p1=s1, p2=s2, p3=s3))

        elif s1.kind == "high" and s2.kind == "low" and s3.kind == "high":
            if s3.price < s1.price:  # lower high = intact bearish structure
                patterns.append(Pattern123(kind="bearish", p1=s1, p2=s2, p3=s3))

    return patterns


# ─── Ross Hook Detection ──────────────────────────────────────────────────────

def detect_ross_hook(df: pd.DataFrame) -> dict:
    """
    Detects the most recent valid Ross Hook on the given DataFrame.

    Sequence:
      1. Valid 1-2-3 pattern
      2. Breakout: close above P2 (bullish) or below P2 (bearish)
      3. Post-breakout swing high (bullish) or swing low (bearish) = the Hook
      4. Hook is valid while P2 level has not been violated since

    Returns dict with formation_complete=True/False and all key levels.
    """
    no_hook: dict = {
        "pattern":            None,
        "hook_level":         None,
        "hook_index":         None,
        "formation_complete": False,
        "candles_ago":        0,
        "p1":                 None,
        "p2":                 None,
        "p3":                 None,
        "breakout_index":     None,
    }

    patterns = detect_1_2_3_pattern(df)
    if not patterns:
        return no_hook

    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    last_i = len(df) - 1

    hook_swing_highs = find_swing_highs(df, window=HOOK_SWING_WINDOW)
    hook_swing_lows  = find_swing_lows(df, window=HOOK_SWING_WINDOW)

    for pat in sorted(patterns, key=lambda p: p.p3.index, reverse=True):
        p2_level = pat.p2.price

        if pat.kind == "bullish":
            breakout_i: Optional[int] = next(
                (i for i in range(pat.p3.index + 1, last_i + 1) if closes[i] > p2_level),
                None,
            )
            if breakout_i is None:
                continue

            candidates = [sh for sh in hook_swing_highs if sh.index > breakout_i]
            if not candidates:
                continue

            hook = candidates[-1]

            # Invalidated if price closed back below P2 since hook formed
            if any(lows[k] < p2_level for k in range(hook.index, last_i + 1)):
                continue

            candles_ago = last_i - hook.index
            if candles_ago > MAX_HOOK_AGE:
                continue

            return {
                "pattern":            "bullish",
                "hook_level":         hook.price,
                "hook_index":         hook.index,
                "formation_complete": True,
                "candles_ago":        candles_ago,
                "p1": pat.p1,
                "p2": pat.p2,
                "p3": pat.p3,
                "breakout_index":     breakout_i,
            }

        else:  # bearish
            breakout_i = next(
                (i for i in range(pat.p3.index + 1, last_i + 1) if closes[i] < p2_level),
                None,
            )
            if breakout_i is None:
                continue

            candidates = [sl for sl in hook_swing_lows if sl.index > breakout_i]
            if not candidates:
                continue

            hook = candidates[-1]

            if any(highs[k] > p2_level for k in range(hook.index, last_i + 1)):
                continue

            candles_ago = last_i - hook.index
            if candles_ago > MAX_HOOK_AGE:
                continue

            return {
                "pattern":            "bearish",
                "hook_level":         hook.price,
                "hook_index":         hook.index,
                "formation_complete": True,
                "candles_ago":        candles_ago,
                "p1": pat.p1,
                "p2": pat.p2,
                "p3": pat.p3,
                "breakout_index":     breakout_i,
            }

    return no_hook


# ─── Trader's Trick Entry (TTE) ───────────────────────────────────────────────

def get_tte_entry(df: pd.DataFrame, hook: dict) -> Optional[dict]:
    """
    Joe Ross Trader's Trick Entry (TTE) — enter BEFORE the crowd.

    Rule: "we watch the correction and want to buy [sell] a violation of
    the high [low] of any of the first three [bars] after the Ross Hook"
    (Ross, Trading Educators). Each correction bar's high/low becomes a
    reference level; the first subsequent bar to break it is the signal
    bar. A double/triple top or bottom (a bar tying the reference within
    TTE_TIE_TOLERANCE_PCT) doesn't consume the bar budget — no real
    progress was made. Beyond TTE_MAX_CORRECTION_BARS, abandon the TTE.

    Bullish TTE:
      1. Watch up to the first TTE_MAX_CORRECTION_BARS bars after the hook.
      2. Entry  = the high of the first bar that exceeds the running
         reference high (place buy-stop just above).
      3. SL     = the LOW of that same signal bar — opposite side of the
         bar, per Ross's rule, not the wider pullback extreme.

    Bearish TTE mirrors this on lows/highs.

    Returns None if no violation occurs within the window (TTE abandoned)
    or the correction hasn't produced enough bars yet.
    """
    if not hook or not hook.get("formation_complete"):
        return None

    hook_index: int = hook["hook_index"]
    pattern:    str = hook["pattern"]

    highs  = df["high"].values
    lows   = df["low"].values
    last_i = len(df) - 1

    # Need at least 1 bar after the hook to set the reference level
    if hook_index + 1 > last_i:
        return None

    if pattern == "bullish":
        ref_high     = highs[hook_index + 1]
        bars_counted = 1
        i = hook_index + 2
        while i <= last_i and bars_counted <= TTE_MAX_CORRECTION_BARS:
            if highs[i] > ref_high:
                logger.debug(
                    "Bullish TTE signal bar at %d: entry=%.4f, sl=%.4f",
                    i, highs[i], lows[i],
                )
                return {
                    "tte_entry":     round(highs[i], 8),
                    "tte_sl":        round(lows[i], 8),
                    "tte_bar_index": i,
                    "entry_type":    "TTE",
                }
            is_tie = highs[i] >= ref_high * (1 - TTE_TIE_TOLERANCE_PCT)
            if not is_tie:
                bars_counted += 1
            ref_high = max(ref_high, highs[i])
            i += 1

    else:  # bearish
        ref_low      = lows[hook_index + 1]
        bars_counted = 1
        i = hook_index + 2
        while i <= last_i and bars_counted <= TTE_MAX_CORRECTION_BARS:
            if lows[i] < ref_low:
                logger.debug(
                    "Bearish TTE signal bar at %d: entry=%.4f, sl=%.4f",
                    i, lows[i], highs[i],
                )
                return {
                    "tte_entry":     round(lows[i], 8),
                    "tte_sl":        round(highs[i], 8),
                    "tte_bar_index": i,
                    "entry_type":    "TTE",
                }
            is_tie = lows[i] <= ref_low * (1 + TTE_TIE_TOLERANCE_PCT)
            if not is_tie:
                bars_counted += 1
            ref_low = min(ref_low, lows[i])
            i += 1

    # No violation within the window — TTE abandoned (or still pending if
    # the correction hasn't produced enough bars yet to exhaust the budget)
    return None
