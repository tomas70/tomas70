"""
Multi-timeframe analysis: Ross Hook primary, SMC confirmation, TTE entry.

Logic flow:
  15m  → Ross Hook (1-2-3 pattern + breakout + hook)  [PRIMARY SIGNAL]
  15m  → TTE entry bar (Trader's Trick Entry)          [ENTRY LEVEL]
  4H   → BOS/CHoCH market structure bias               [DIRECTION FILTER]
  1H   → Order Block near the hook/TTE area            [SMC CONFLUENCE — bonus]
  1H   → Fair Value Gap overlap                        [SMC CONFLUENCE — bonus]
  4H   → Premium/Discount Fibonacci zone               [CONTEXT — bonus]
  All  → R:R ≥ MIN_RR_RATIO                            [RISK FILTER]

OB/FVG/PD-zone are confirmation, not the primary signal — they raise
confidence in a Hook+TTE setup but don't block a trade on their own. A Ross
Hook is a continuation pattern (it breaks out and keeps going), which
conflicts with PD zone theory's mean-reversion assumption (e.g. a bearish
continuation hook is almost always still in "discount", since it's making
fresh new lows) — so PD zone is reported for context, not enforced as a
gate. SL without a TTE signal falls back to the hook's own P2 invalidation
level (Joe Ross rule), not an OB boundary, so a valid Hook setup never
depends on SMC presence.
"""
import logging
from typing import Optional

from .market_data import get_all_timeframes
from .smc import (
    FVG, OrderBlock, SwingPoint,
    calculate_atr, detect_market_structure, find_fvg, find_order_blocks, get_pd_zone,
)
from .ross_hook import detect_ross_hook, get_tte_entry
from config import MIN_RR_RATIO

logger = logging.getLogger(__name__)

# OB zone: current price must be within this many 1H ATRs of the OB boundary.
# ATR-based (not a flat %) since volatility varies widely across pairs.
OB_PROXIMITY_ATR_MULT = 2.0
# Fallback SL buffer when using hook-level entry (no TTE)
SL_BUFFER_PCT    = 0.003


# ─── Internal Helpers ─────────────────────────────────────────────────────────

def _find_nearest_ob(
    obs: list[OrderBlock],
    current_price: float,
    bias: str,
    atr: float,
) -> Optional[OrderBlock]:
    """
    Returns the most recent unmitigated OB whose zone the current price
    is touching or approaching (within OB_PROXIMITY_ATR_MULT * atr).
    """
    proximity = atr * OB_PROXIMITY_ATR_MULT
    for ob in obs:
        if bias == "bullish":
            in_zone = ob.low <= current_price <= ob.high + proximity
        else:
            in_zone = ob.low - proximity <= current_price <= ob.high
        if in_zone:
            return ob
    return None


def _find_fvg_near_ob(fvgs: list[FVG], ob: OrderBlock) -> Optional[FVG]:
    """Returns the first unfilled FVG that overlaps (even partially) with the OB zone."""
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
    """Derive TP1 / TP2 from the nearest 4H structural swing levels beyond entry."""
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
    entry: float,
    sl: float,
    swing_highs: list[SwingPoint],
    swing_lows: list[SwingPoint],
) -> dict:
    """Calculate TP1, TP2, R:R, and distance percentages from entry/SL."""
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
    Multi-timeframe confluence analysis.

    Primary: 15m Ross Hook (Joe Ross 1-2-3 + breakout + hook).
    Entry:   TTE (Trader's Trick Entry) — first up/down bar in the correction,
             enter at that bar's high/low BEFORE the hook level is broken.
    SMC:     1H OB / FVG / 4H PD-zone are confluence — they raise confidence
             but are not required for a setup to be valid.

    Gates (all must pass):
      ✓ 15m Ross Hook formed and not stale
      ✓ 4H market structure bias matches hook direction
      ✓ R:R ≥ MIN_RR_RATIO

    Entry source:
      TTE signal found     → use TTE entry/SL (earlier, better R:R, Joe Ross rule)
      TTE pending, OB near → use hook_level as entry, OB-derived SL
      TTE pending, no OB   → use hook_level as entry, SL at hook's P2 level
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

    if df_4h.empty or df_1h.empty or df_15m.empty:
        return {"valid": False, "reason": "Empty candle data from Hyperliquid"}

    current_price = float(df_15m["close"].iloc[-1])

    # ── Gate 1: 15m Ross Hook ─────────────────────────────────────────────────
    hook = detect_ross_hook(df_15m)

    if not hook["formation_complete"]:
        return {"valid": False, "reason": "No completed Ross Hook on 15m"}

    hook_bias = hook["pattern"]  # "bullish" | "bearish"

    # ── Gate 2: 4H Structure Bias ─────────────────────────────────────────────
    structure_4h = detect_market_structure(df_4h)
    bias_4h      = structure_4h["bias"]

    if bias_4h == "ranging":
        return {"valid": False, "reason": "4H market structure is ranging"}

    if hook_bias != bias_4h:
        return {
            "valid":  False,
            "reason": f"15m Ross Hook is {hook_bias} but 4H bias is {bias_4h}",
        }

    bias = hook_bias  # confirmed direction

    # ── 1H Order Block + FVG (SMC confluence — bonus, not a hard gate) ────────
    obs_1h     = find_order_blocks(df_1h, bias)
    active_obs = [ob for ob in obs_1h if not ob.mitigated]

    atr_1h     = calculate_atr(df_1h)
    nearest_ob = _find_nearest_ob(active_obs, current_price, bias, atr_1h) if active_obs else None

    fvgs_1h        = [f for f in find_fvg(df_1h) if not f.filled and f.kind == bias]
    confluence_fvg = _find_fvg_near_ob(fvgs_1h, nearest_ob) if nearest_ob else None

    # ── Premium / Discount Zone (confluence info — bonus, not a hard gate) ────
    # PD theory assumes a mean-reversion retracement entry (short the premium
    # before a drop, buy the discount before a rally). A Ross Hook is a
    # continuation pattern — it breaks out and keeps going, so a real bearish
    # continuation hook is almost always still in "discount" (fresh new lows)
    # and would never satisfy a hard "need premium" requirement. Reported for
    # context only.
    swing_highs = structure_4h.get("swing_highs", [])
    swing_lows  = structure_4h.get("swing_lows",  [])

    if swing_highs and swing_lows:
        pd_info = get_pd_zone(swing_highs[-1].price, swing_lows[-1].price, current_price)
    else:
        pd_info = {"zone": "unknown", "equilibrium": 0.0, "fib_pct": 0.0}

    # ── TTE Entry Calculation ─────────────────────────────────────────────────
    tte = get_tte_entry(df_15m, hook)

    if tte:
        # Primary: TTE entry (better price, before the crowd)
        entry      = tte["tte_entry"]
        sl         = tte["tte_sl"]
        entry_type = "TTE"
    elif nearest_ob is not None:
        # Fallback: classic hook-level entry with OB-derived SL (when an OB
        # confluence zone is available)
        entry = hook["hook_level"]
        sl    = (
            nearest_ob.low  * (1 - SL_BUFFER_PCT) if bias == "bullish"
            else nearest_ob.high * (1 + SL_BUFFER_PCT)
        )
        entry_type = "HOOK"
    else:
        # Fallback: classic hook-level entry, SL at the hook's own P2
        # invalidation level (Joe Ross rule) — no OB confluence required
        entry = hook["hook_level"]
        p2    = hook["p2"].price
        sl    = p2 * (1 - SL_BUFFER_PCT) if bias == "bullish" else p2 * (1 + SL_BUFFER_PCT)
        entry_type = "HOOK"

    # ── Gate 5: R:R Check ─────────────────────────────────────────────────────
    levels = _build_levels(bias, entry, sl, swing_highs, swing_lows)

    if levels["rr_ratio"] < MIN_RR_RATIO:
        return {
            "valid":  False,
            "reason": f"R:R {levels['rr_ratio']:.1f} below minimum {MIN_RR_RATIO:.1f}",
        }

    logger.info(
        "Valid setup: %s %s [%s] | entry=%.5f SL=%.5f TP1=%.5f RR=%.2f",
        pair, bias.upper(), entry_type,
        levels["entry"], levels["sl"], levels["tp1"], levels["rr_ratio"],
    )

    return {
        "valid":         True,
        "pair":          pair,
        "bias":          bias,
        "entry_type":    entry_type,
        "current_price": current_price,
        "structure_4h": {
            "bias":     bias,
            "last_bos": structure_4h["last_bos"],
            "is_choch": structure_4h["is_choch"],
        },
        "order_block":   nearest_ob,
        "fvg":           confluence_fvg,
        "ross_hook":     hook,
        "tte":           tte,
        "pd_zone":       pd_info,
        **levels,
    }
