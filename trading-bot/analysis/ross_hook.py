import logging
from dataclasses import dataclass
from typing import Literal, Optional

import pandas as pd

from .smc import SwingPoint, find_swing_highs, find_swing_lows

logger = logging.getLogger(__name__)

MAIN_SWING_WINDOW = 5   # window for 1-2-3 structure identification
HOOK_SWING_WINDOW = 3   # smaller window for post-breakout hook sensitivity
MAX_HOOK_AGE      = 30  # candles; older hooks treated as stale


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
    Scans for all valid 1-2-3 patterns in the DataFrame.

    Bullish:  swing_low(P1) → swing_high(P2) → swing_low(P3)  where P3 > P1
    Bearish:  swing_high(P1) → swing_low(P2) → swing_high(P3) where P3 < P1

    P3 > P1 (bullish) confirms price didn't fully retrace — pattern is intact.
    P3 < P1 (bearish) confirms price didn't fully retrace upward.
    """
    swing_highs = find_swing_highs(df, window=MAIN_SWING_WINDOW)
    swing_lows  = find_swing_lows(df, window=MAIN_SWING_WINDOW)

    all_swings = sorted(swing_highs + swing_lows, key=lambda s: s.index)
    patterns: list[Pattern123] = []

    for i in range(len(all_swings) - 2):
        s1, s2, s3 = all_swings[i], all_swings[i + 1], all_swings[i + 2]

        if s1.kind == "low" and s2.kind == "high" and s3.kind == "low":
            if s3.price > s1.price:
                patterns.append(Pattern123(kind="bullish", p1=s1, p2=s2, p3=s3))

        elif s1.kind == "high" and s2.kind == "low" and s3.kind == "high":
            if s3.price < s1.price:
                patterns.append(Pattern123(kind="bearish", p1=s1, p2=s2, p3=s3))

    return patterns


# ─── Ross Hook Detection ──────────────────────────────────────────────────────

def detect_ross_hook(df: pd.DataFrame) -> dict:
    """
    Detects the most recent valid Ross Hook on the given DataFrame.

    Sequence:
      1. Valid 1-2-3 pattern (confirms trend structure)
      2. Breakout: close above P2 (bullish) or below P2 (bearish)
      3. Post-breakout swing high (bullish) or swing low (bearish) = the Hook
      4. Hook valid only while P2 level has not been breached

    Bullish hook_level = swing high price → place buy stop above it
    Bearish hook_level = swing low price  → place sell stop below it

    Returns:
        {
            'pattern':            'bullish' | 'bearish' | None,
            'hook_level':         float | None,
            'hook_index':         int | None,
            'formation_complete': bool,
            'candles_ago':        int,
            'p1':                 SwingPoint | None,
            'p2':                 SwingPoint | None,
            'p3':                 SwingPoint | None,
            'breakout_index':     int | None,
        }
    """
    no_hook: dict = {
        "pattern": None,
        "hook_level": None,
        "hook_index": None,
        "formation_complete": False,
        "candles_ago": 0,
        "p1": None,
        "p2": None,
        "p3": None,
        "breakout_index": None,
    }

    patterns = detect_1_2_3_pattern(df)
    if not patterns:
        return no_hook

    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    last_i = len(df) - 1

    # Separate swing sets for post-breakout hook (more sensitive window)
    hook_swing_highs = find_swing_highs(df, window=HOOK_SWING_WINDOW)
    hook_swing_lows  = find_swing_lows(df, window=HOOK_SWING_WINDOW)

    for pat in sorted(patterns, key=lambda p: p.p3.index, reverse=True):
        p2_level = pat.p2.price

        if pat.kind == "bullish":
            # ── Step 1: first close above P2 after P3 ──
            breakout_i: Optional[int] = next(
                (i for i in range(pat.p3.index + 1, last_i + 1) if closes[i] > p2_level),
                None,
            )
            if breakout_i is None:
                continue

            # ── Step 2: post-breakout swing highs = hook candidates ──
            candidates = [sh for sh in hook_swing_highs if sh.index > breakout_i]
            if not candidates:
                continue

            hook = candidates[-1]  # most recent

            # ── Step 3: invalidation — low crossed back below P2 since hook ──
            if any(lows[k] < p2_level for k in range(hook.index, last_i + 1)):
                continue

            candles_ago = last_i - hook.index
            if candles_ago > MAX_HOOK_AGE:
                continue

            logger.debug(
                "Bullish Ross Hook found: hook=%.4f, candles_ago=%d", hook.price, candles_ago
            )
            return {
                "pattern": "bullish",
                "hook_level": hook.price,
                "hook_index": hook.index,
                "formation_complete": True,
                "candles_ago": candles_ago,
                "p1": pat.p1,
                "p2": pat.p2,
                "p3": pat.p3,
                "breakout_index": breakout_i,
            }

        else:  # bearish
            # ── Step 1: first close below P2 after P3 ──
            breakout_i = next(
                (i for i in range(pat.p3.index + 1, last_i + 1) if closes[i] < p2_level),
                None,
            )
            if breakout_i is None:
                continue

            # ── Step 2: post-breakout swing lows = hook candidates ──
            candidates = [sl for sl in hook_swing_lows if sl.index > breakout_i]
            if not candidates:
                continue

            hook = candidates[-1]

            # ── Step 3: invalidation — high crossed back above P2 since hook ──
            if any(highs[k] > p2_level for k in range(hook.index, last_i + 1)):
                continue

            candles_ago = last_i - hook.index
            if candles_ago > MAX_HOOK_AGE:
                continue

            logger.debug(
                "Bearish Ross Hook found: hook=%.4f, candles_ago=%d", hook.price, candles_ago
            )
            return {
                "pattern": "bearish",
                "hook_level": hook.price,
                "hook_index": hook.index,
                "formation_complete": True,
                "candles_ago": candles_ago,
                "p1": pat.p1,
                "p2": pat.p2,
                "p3": pat.p3,
                "breakout_index": breakout_i,
            }

    return no_hook
