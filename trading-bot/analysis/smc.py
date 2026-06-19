import logging
from dataclasses import dataclass, field
from typing import Literal, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MarketBias = Literal["bullish", "bearish", "ranging"]

ORDER_BLOCK_MAX_AGE = 50   # candles
FVG_MIN_GAP_PCT    = 0.001 # 0.1% minimum gap to filter noise


# ─── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class SwingPoint:
    index: int
    price: float
    kind: Literal["high", "low"]


@dataclass
class BosEvent:
    direction: Literal["bullish", "bearish"]
    index: int          # candle index where break occurred
    level: float        # the broken swing level
    swing_index: int    # index of the swing that was broken
    is_choch: bool = False


@dataclass
class OrderBlock:
    kind: Literal["bullish", "bearish"]
    high: float
    low: float
    index: int          # candle index of the OB candle
    mitigated: bool = False


@dataclass
class FVG:
    kind: Literal["bullish", "bearish"]
    top: float
    bottom: float
    index: int          # candle index of the middle candle (i)
    filled: bool = False


# ─── Swing Point Detection ────────────────────────────────────────────────────

def find_swing_highs(df: pd.DataFrame, window: int = 5) -> list[SwingPoint]:
    highs = df["high"].values
    result: list[SwingPoint] = []
    for i in range(window, len(highs) - window):
        if highs[i] >= highs[i - window : i].max() and highs[i] > highs[i + 1 : i + window + 1].max():
            result.append(SwingPoint(index=i, price=highs[i], kind="high"))
    return result


def find_swing_lows(df: pd.DataFrame, window: int = 5) -> list[SwingPoint]:
    lows = df["low"].values
    result: list[SwingPoint] = []
    for i in range(window, len(lows) - window):
        if lows[i] <= lows[i - window : i].min() and lows[i] < lows[i + 1 : i + window + 1].min():
            result.append(SwingPoint(index=i, price=lows[i], kind="low"))
    return result


def find_swing_points(df: pd.DataFrame, window: int = 5) -> list[SwingPoint]:
    points = find_swing_highs(df, window) + find_swing_lows(df, window)
    return sorted(points, key=lambda p: p.index)


# ─── BOS / CHoCH Detection ────────────────────────────────────────────────────

def _detect_bos_events(
    df: pd.DataFrame,
    swing_highs: list[SwingPoint],
    swing_lows: list[SwingPoint],
) -> list[BosEvent]:
    """
    Walks forward in time. Each swing level is consumed after first break —
    prevents the same level from generating multiple BOS events.
    """
    closes = df["close"].values
    events: list[BosEvent] = []

    # Build time-ordered list of (index, swing) for each type
    highs_queue = list(swing_highs)  # already sorted by index
    lows_queue  = list(swing_lows)

    active_high: Optional[SwingPoint] = None
    active_low:  Optional[SwingPoint] = None

    high_ptr = 0
    low_ptr  = 0

    for i in range(len(df)):
        # Advance pointers: register swings confirmed at or before candle i
        while high_ptr < len(highs_queue) and highs_queue[high_ptr].index <= i:
            active_high = highs_queue[high_ptr]
            high_ptr += 1

        while low_ptr < len(lows_queue) and lows_queue[low_ptr].index <= i:
            active_low = lows_queue[low_ptr]
            low_ptr += 1

        # Bullish BOS: close above last swing high
        if active_high is not None and closes[i] > active_high.price:
            events.append(BosEvent(
                direction="bullish",
                index=i,
                level=active_high.price,
                swing_index=active_high.index,
            ))
            active_high = None  # level consumed

        # Bearish BOS: close below last swing low
        if active_low is not None and closes[i] < active_low.price:
            events.append(BosEvent(
                direction="bearish",
                index=i,
                level=active_low.price,
                swing_index=active_low.index,
            ))
            active_low = None  # level consumed

    # Mark CHoCH: direction change vs previous BOS
    for i in range(1, len(events)):
        if events[i].direction != events[i - 1].direction:
            events[i].is_choch = True

    return events


def detect_market_structure(df: pd.DataFrame) -> dict:
    """
    Analyzes market structure (designed for 4H DataFrame).

    Returns:
        {
            'bias':         'bullish' | 'bearish' | 'ranging',
            'last_bos':     BosEvent | None,
            'is_choch':     bool,
            'swing_highs':  list[SwingPoint],
            'swing_lows':   list[SwingPoint],
            'bos_events':   list[BosEvent],
        }
    """
    swing_highs = find_swing_highs(df, window=5)
    swing_lows  = find_swing_lows(df, window=5)

    empty = {
        "bias": "ranging",
        "last_bos": None,
        "is_choch": False,
        "swing_highs": swing_highs,
        "swing_lows": swing_lows,
        "bos_events": [],
    }

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return empty

    bos_events = _detect_bos_events(df, swing_highs, swing_lows)

    # Bias = direction of the most recent break of structure (BOS/CHoCH).
    # More forgiving than requiring a strict HH+HL / LH+LL pair from only
    # the last two swing points, which misclassifies normal pullback-driven
    # trends (e.g. a fresh higher high with a lower low) as "ranging".
    last_bos = bos_events[-1] if bos_events else None
    bias: MarketBias = last_bos.direction if last_bos else "ranging"

    return {
        "bias": bias,
        "last_bos": last_bos,
        "is_choch": last_bos.is_choch if last_bos else False,
        "swing_highs": swing_highs,
        "swing_lows": swing_lows,
        "bos_events": bos_events,
    }


# ─── Order Blocks ─────────────────────────────────────────────────────────────

def find_order_blocks(
    df: pd.DataFrame,
    structure_bias: MarketBias,
) -> list[OrderBlock]:
    """
    Identifies valid, unmitigated order blocks aligned with structure_bias.

    Bullish OB: last bearish candle (close < open) before a bullish BOS.
    Bearish OB: last bullish candle (close > open) before a bearish BOS.
    Max age: ORDER_BLOCK_MAX_AGE candles from current bar.
    """
    if structure_bias == "ranging":
        return []

    swing_highs = find_swing_highs(df, window=5)
    swing_lows  = find_swing_lows(df, window=5)
    bos_events  = _detect_bos_events(df, swing_highs, swing_lows)

    opens  = df["open"].values
    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    last_i = len(df) - 1

    target_direction = "bullish" if structure_bias == "bullish" else "bearish"
    relevant_bos = [e for e in bos_events if e.direction == target_direction]

    blocks: list[OrderBlock] = []
    seen_indices: set[int] = set()

    for bos in relevant_bos:
        # Search backwards from the BOS candle for the last opposing candle
        start = bos.index - 1
        ob_index: Optional[int] = None

        for j in range(start, max(start - 20, -1), -1):
            if structure_bias == "bullish" and closes[j] < opens[j]:
                # Last bearish candle before bullish BOS
                ob_index = j
                break
            elif structure_bias == "bearish" and closes[j] > opens[j]:
                # Last bullish candle before bearish BOS
                ob_index = j
                break

        if ob_index is None or ob_index in seen_indices:
            continue

        age = last_i - ob_index
        if age > ORDER_BLOCK_MAX_AGE:
            continue

        ob_high = highs[ob_index]
        ob_low  = lows[ob_index]

        # Check mitigation: has price entered the OB zone after its formation?
        mitigated = False
        for k in range(ob_index + 1, last_i + 1):
            if structure_bias == "bullish" and lows[k] <= ob_high and highs[k] >= ob_low:
                mitigated = True
                break
            elif structure_bias == "bearish" and highs[k] >= ob_low and lows[k] <= ob_high:
                mitigated = True
                break

        seen_indices.add(ob_index)
        blocks.append(OrderBlock(
            kind=structure_bias,
            high=ob_high,
            low=ob_low,
            index=ob_index,
            mitigated=mitigated,
        ))

    # Most recent first
    return sorted(blocks, key=lambda b: b.index, reverse=True)


# ─── Fair Value Gaps ──────────────────────────────────────────────────────────

def find_fvg(df: pd.DataFrame) -> list[FVG]:
    """
    Detects Fair Value Gaps (imbalances) in the DataFrame.

    Bullish FVG: df[i-1].high < df[i+1].low  (gap above candle i-1)
    Bearish FVG: df[i-1].low  > df[i+1].high (gap below candle i-1)

    Marks as filled if price re-entered the gap zone after formation.
    """
    highs  = df["high"].values
    lows   = df["low"].values
    n      = len(df)
    gaps:  list[FVG] = []

    for i in range(1, n - 1):
        # Bullish FVG
        gap_bottom = highs[i - 1]
        gap_top    = lows[i + 1]
        if gap_top > gap_bottom and gap_bottom > 0:
            size_pct = (gap_top - gap_bottom) / gap_bottom
            if size_pct >= FVG_MIN_GAP_PCT:
                filled = any(
                    lows[k] <= gap_top and highs[k] >= gap_bottom
                    for k in range(i + 2, n)
                )
                gaps.append(FVG(kind="bullish", top=gap_top, bottom=gap_bottom, index=i, filled=filled))

        # Bearish FVG
        gap_top    = lows[i - 1]
        gap_bottom = highs[i + 1]
        if gap_top > gap_bottom and gap_bottom > 0:
            size_pct = (gap_top - gap_bottom) / gap_bottom
            if size_pct >= FVG_MIN_GAP_PCT:
                filled = any(
                    lows[k] <= gap_top and highs[k] >= gap_bottom
                    for k in range(i + 2, n)
                )
                gaps.append(FVG(kind="bearish", top=gap_top, bottom=gap_bottom, index=i, filled=filled))

    # Most recent first
    return sorted(gaps, key=lambda g: g.index, reverse=True)


# ─── Premium / Discount Zones ─────────────────────────────────────────────────

def get_pd_zone(
    swing_high: float,
    swing_low: float,
    current_price: float,
) -> dict:
    """
    Classifies current price relative to the swing range using Fibonacci 50%.

    Returns:
        {
            'zone':        'premium' | 'discount' | 'equilibrium',
            'equilibrium': float,
            'fib_pct':     float,   # 0.0 = at swing_low, 1.0 = at swing_high
        }
    """
    if swing_high <= swing_low:
        raise ValueError("swing_high must be greater than swing_low")

    rng          = swing_high - swing_low
    equilibrium  = swing_low + rng * 0.5
    fib_pct      = (current_price - swing_low) / rng

    if current_price > equilibrium:
        zone = "premium"
    elif current_price < equilibrium:
        zone = "discount"
    else:
        zone = "equilibrium"

    return {
        "zone": zone,
        "equilibrium": round(equilibrium, 8),
        "fib_pct": round(fib_pct, 4),
        "swing_high": swing_high,
        "swing_low": swing_low,
    }
