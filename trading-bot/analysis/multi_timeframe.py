import logging
from typing import Optional

from .market_data import get_all_timeframes
from .smc import (
    FVG, OrderBlock, SwingPoint,
    detect_market_structure, find_fvg, find_order_blocks, get_pd_zone,
)
from .ross_hook import detect_ross_hook
from config import MIN_RR_RATIO

logger = logging.getLogger(__name__)

OB_PROXIMITY_PCT = 0.02   # price must be within 2% of OB high/low to count as "in zone"
SL_BUFFER_PCT    = 0.003  # SL placed 0.3% beyond the OB boundary


# ─── Internal Helpers ─────────────────────────────────────────────────────────

def _find_nearest_ob(
    obs: list[OrderBlock],
    current_price: float,
    bias: str,
) -> Optional[OrderBlock]:
    """
    Returns the most recent unmitigated OB whose zone the current price is
    touching or approaching (within OB_PROXIMITY_PCT).

    Bullish OB is support → price approaches from above.
    Bearish OB is resistance → price approaches from below.
    """
    for ob in obs:  # already sorted most-recent first
        if bias == "bullish":
            in_zone = ob.low <= current_price <= ob.high * (1 + OB_PROXIMITY_PCT)
        else:
            in_zone = ob.low * (1 - OB_PROXIMITY_PCT) <= current_price <= ob.high
        if in_zone:
            return ob
    return None


def _find_fvg_near_ob(fvgs: list[FVG], ob: OrderBlock) -> Optional[FVG]:
    """Returns the first unfilled FVG that overlaps (even partially) the OB zone."""
    for fvg in fvgs:
        overlap = min(ob.high, fvg.top) - max(ob.low, fvg.bottom)
        if overlap > 0:
            return fvg
    return None


def _tp_targets(
    bias: str,
    entry: float,
    swing_highs: list[SwingPoint],
    swing_lows: list[SwingPoint],
) -> tuple[float, float]:
    """Derive TP1 / TP2 from the nearest 4H structural levels beyond entry."""
    if bias == "bullish":
        above = sorted(sp.price for sp in swing_highs if sp.price > entry)
        tp1 = above[0] if len(above) > 0 else round(entry * 1.03, 8)
        tp2 = above[1] if len(above) > 1 else round(entry * 1.06, 8)
    else:
        below = sorted((sp.price for sp in swing_lows if sp.price < entry), reverse=True)
        tp1 = below[0] if len(below) > 0 else round(entry * 0.97, 8)
        tp2 = below[1] if len(below) > 1 else round(entry * 0.94, 8)
    return tp1, tp2


def _build_levels(
    bias: str,
    hook: dict,
    ob: OrderBlock,
    swing_highs: list[SwingPoint],
    swing_lows: list[SwingPoint],
) -> dict:
    """Calculate entry, SL, TP1, TP2, R:R, and distance percentages."""
    entry = hook["hook_level"]

    sl = ob.low * (1 - SL_BUFFER_PCT) if bias == "bullish" else ob.high * (1 + SL_BUFFER_PCT)

    tp1, tp2 = _tp_targets(bias, entry, swing_highs, swing_lows)

    sl_dist  = abs(entry - sl)
    tp1_dist = abs(tp1 - entry)
    rr_ratio = round(tp1_dist / sl_dist, 2) if sl_dist > 0 else 0.0

    return {
        "entry":    round(entry, 8),
        "sl":       round(sl, 8),
        "tp1":      round(tp1, 8),
        "tp2":      round(tp2, 8),
        "rr_ratio": rr_ratio,
        "sl_pct":   round(sl_dist / entry * 100, 3),
        "tp1_pct":  round(tp1_dist / entry * 100, 3),
    }


# ─── Main Analysis Entry Point ────────────────────────────────────────────────

def get_full_analysis(pair: str) -> dict:
    """
    Multi-timeframe confluence analysis for a single trading pair.

    Timeframe roles:
      4H → HTF market structure bias (BOS / CHoCH)
      1H → Order Block + Fair Value Gap zone identification
      15m → Ross Hook entry confirmation

    ALL confluence conditions must be satisfied:
      ✓ 4H structure is bullish or bearish (not ranging)
      ✓ Active 1H OB aligned with 4H bias, with price in or near the zone
      ✓ Unfilled 1H FVG overlapping the OB zone
      ✓ 15m Ross Hook formed and aligned with bias
      ✓ Price in discount zone (long) or premium zone (short) on 4H range
      ✓ Calculated R:R >= MIN_RR_RATIO

    Returns:
        Full analysis dict with 'valid': True and all trade parameters, or
        {'valid': False, 'reason': str} when any condition fails.
    """
    # ── Data Fetch ────────────────────────────────────────────────────────────
    try:
        data = get_all_timeframes(pair)
    except Exception as exc:
        logger.error("Data fetch failed for %s: %s", pair, exc)
        return {"valid": False, "reason": f"Data fetch error: {exc}"}

    df_4h  = data["4h"]
    df_1h  = data["1h"]
    df_15m = data["15m"]

    current_price = float(df_15m["close"].iloc[-1])

    # ── 1. 4H Market Structure ────────────────────────────────────────────────
    structure_4h = detect_market_structure(df_4h)
    bias         = structure_4h["bias"]

    if bias == "ranging":
        return {"valid": False, "reason": "4H market structure is ranging"}

    # ── 2. 1H Order Blocks ────────────────────────────────────────────────────
    obs_1h     = find_order_blocks(df_1h, bias)
    active_obs = [ob for ob in obs_1h if not ob.mitigated]

    if not active_obs:
        return {"valid": False, "reason": f"No active {bias} OBs on 1H"}

    nearest_ob = _find_nearest_ob(active_obs, current_price, bias)
    if nearest_ob is None:
        return {"valid": False, "reason": "Price not in or near any active 1H OB zone"}

    # ── 3. 1H FVG Confluence ──────────────────────────────────────────────────
    fvgs_1h        = [f for f in find_fvg(df_1h) if not f.filled and f.kind == bias]
    confluence_fvg = _find_fvg_near_ob(fvgs_1h, nearest_ob)

    if confluence_fvg is None:
        return {"valid": False, "reason": "No unfilled 1H FVG overlapping the OB zone"}

    # ── 4. 15m Ross Hook ──────────────────────────────────────────────────────
    hook = detect_ross_hook(df_15m)

    if not hook["formation_complete"]:
        return {"valid": False, "reason": "No completed Ross Hook on 15m"}

    if hook["pattern"] != bias:
        return {
            "valid": False,
            "reason": f"15m Ross Hook is {hook['pattern']} but 4H bias is {bias}",
        }

    # ── 5. Premium / Discount Zone ────────────────────────────────────────────
    swing_highs = structure_4h.get("swing_highs", [])
    swing_lows  = structure_4h.get("swing_lows",  [])

    if swing_highs and swing_lows:
        pd_info = get_pd_zone(swing_highs[-1].price, swing_lows[-1].price, current_price)
        if bias == "bullish" and pd_info["zone"] == "premium":
            return {"valid": False, "reason": "Price in premium zone — need discount for long entry"}
        if bias == "bearish" and pd_info["zone"] == "discount":
            return {"valid": False, "reason": "Price in discount zone — need premium for short entry"}
    else:
        pd_info = {"zone": "unknown", "equilibrium": 0.0, "fib_pct": 0.0}

    # ── 6. Entry / SL / TP Levels ─────────────────────────────────────────────
    levels = _build_levels(bias, hook, nearest_ob, swing_highs, swing_lows)

    if levels["rr_ratio"] < MIN_RR_RATIO:
        return {
            "valid": False,
            "reason": f"R:R {levels['rr_ratio']:.1f} below minimum {MIN_RR_RATIO:.1f}",
        }

    logger.info(
        "Valid setup: %s %s | entry=%.5f SL=%.5f TP1=%.5f RR=%.2f",
        pair, bias.upper(), levels["entry"], levels["sl"], levels["tp1"], levels["rr_ratio"],
    )

    return {
        "valid":         True,
        "pair":          pair,
        "bias":          bias,
        "current_price": current_price,
        "structure_4h": {
            "bias":     bias,
            "last_bos": structure_4h["last_bos"],
            "is_choch": structure_4h["is_choch"],
        },
        "order_block":   nearest_ob,
        "fvg":           confluence_fvg,
        "ross_hook":     hook,
        "pd_zone":       pd_info,
        **levels,
    }
