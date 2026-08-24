"""
Multi-timeframe analysis over two setup types, sharing one context and
risk framework.

  HOOK  (continuation) — 15m Ross Hook: 1-2-3 → breakout → hook retest.
      Entry at the hook level, SL from the 1H order block boundary.
      Requires strict 4H agreement and 1H OB confluence.

  SWEEP (reversal) — yesterday's high/low is wicked and reclaimed, a
      displacement candle confirms the rejection, entry is the retest of
      the FVG that displacement left behind, SL beyond the sweep extreme.
      4H only has to not actively oppose the direction.

The two are opposite readings of the same event — price arriving at a
level. A CLOSE beyond it is continuation; a WICK beyond it that closes
back is a stop hunt. Requiring both at once would be near-contradictory,
so they're separate paths: HOOK is tried first (it has live win-rate data
behind it), SWEEP only when no hook qualifies.

Shared pipeline for whichever setup is found:
  4H   → Premium/Discount Fibonacci zone               [CONTEXT — bonus]
  4H   → TP1/TP2 = nearest UNSWEPT 4H swing beyond entry [TARGET — real liquidity only]
  4H   → TP1 swing age                                  [CONTEXT — bonus, see below]
  All  → R:R ≥ MIN_RR_RATIO                            [RISK FILTER]
  1D   → pair daily trend + BTC daily regime           [GLOBAL TREND — GATE]

Two live-data findings shaped the HOOK path in particular:

  1. TTE (Trader's Trick Entry, an early correction-bar entry) is disabled.
     Live trade log data showed a 7% TP1 win rate for TTE entries vs 37.5%+
     for the plain hook-level entry — TTE was firing before the correction
     had actually finished, catching false starts. Hook-level entry alone
     is simpler and, empirically, better.
  2. 1H OB confluence is now a hard gate, not bonus confirmation. Live data
     (94 resolved setups): 17.6% win rate with OB confluence vs 5.0%
     without. A Hook without a nearby OB is too often a false start to
     alert on.

The 4H bias gate keeps a setup aligned with its intermediate trend, but a
4H leg can still be a countertrend bounce inside a daily downtrend, and an
alt can look clean while BTC drags the whole market the other way. The
global trend gate (analysis/global_trend.py) rejects a setup that opposes
either the pair's own daily structure or BTC's — a "ranging" daily reading
is treated as neutral, not as a block.

These are empirical findings from logs/trade_log.csv (see analysis/
trade_logger.py), not fixed rules — revisit as more data accumulates.

TP1/TP2 target real, untapped liquidity: a 4H swing high/low whose level
hasn't already been closed through (filter_unswept_swings). A swing that
price has already closed beyond no longer has resting liquidity at it, so
even though it's still "beyond entry" by price, it isn't a meaningful
target — the move it would have implied already happened.

FVG/PD-zone/TP1-age are confirmation, not the primary signal — they raise
confidence in a setup but don't block a trade on their own. TP1 age in
particular was tried as a hard gate and reverted: an old 4H swing isn't a
fake level just because it's old (TA levels routinely hold for weeks), and
gating on it cost real setups (e.g. a same-day RR=6.5 BCH short rejected
purely because its TP1 swing was 49 candles old). It's reported so the
reader can judge for themselves whether a given high-R:R setup's target is
"near and real" or "far and speculative" — see tp1_age_4h/tp1_stale fields.

PD-zone is confirmation, not the primary signal. A Ross Hook is a
continuation pattern (it breaks out and keeps going), which conflicts with
PD zone theory's mean-reversion assumption (e.g. a bearish continuation
hook is almost always still in "discount", since it's making fresh new
lows) — so PD zone is reported for context, not enforced as a gate.
"""
import logging
from typing import Optional

from .market_data import get_all_timeframes
from .smc import (
    FVG, OrderBlock, SwingPoint,
    calculate_atr, detect_market_structure, filter_unswept_swings, find_fvg,
    find_order_blocks, get_pd_zone,
)
from .ross_hook import detect_ross_hook
from .global_trend import check_global_trend
from .liquidity import (
    detect_sweep, find_displacement_in_range, find_entry_fvg, get_previous_day_range,
)
from config import MIN_RR_RATIO

logger = logging.getLogger(__name__)

# OB zone: current price must be within this many 1H ATRs of the OB boundary.
# ATR-based (not a flat %) since volatility varies widely across pairs.
OB_PROXIMITY_ATR_MULT = 2.0
# Fallback SL buffer when using hook-level entry (no TTE)
SL_BUFFER_PCT    = 0.003
# Max age (in 4H candles) a TP1 swing may have before it's flagged "stale"
# in alerts/logs. Informational only — NOT a hard gate (tried and reverted;
# see module docstring). find_swing_highs/lows use window=5, so a swing
# can't be confirmed any sooner than 5 candles after it forms — 5 is the
# physical floor. 8 gives a small buffer above that floor.
TP1_MAX_AGE_4H = 8
# Max age (in 15m candles) a Ross Hook may have for the setup to still be
# worth alerting on. detect_ross_hook() itself allows hooks up to
# MAX_HOOK_AGE=30 candles old to remain "formed", but by then the early
# TTE entry opportunity has usually passed — this tighter cutoff keeps
# alerts limited to genuinely fresh, actionable signals.
MAX_HOOK_AGE_FOR_ALERT = 5


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
    last_index_4h: int,
) -> dict:
    """
    Derive TP1 / TP2 from the nearest 4H structural swing levels beyond entry.

    swing_highs/swing_lows must already be pre-filtered to unswept liquidity
    (see filter_unswept_swings) — an already-closed-through swing has no
    resting liquidity left at it and isn't a real target, even though it's
    still "beyond entry" by price alone.

    Falls back to a flat % target when no unswept swing lies beyond entry —
    flagged via tp1_structural=False so callers can warn that the level
    isn't backed by real 4H structure (and a high R:R off it is more
    speculative).

    TP2 is always pushed at least 2% beyond TP1, whichever path produced
    each. Without this, a lone real swing standing farther out than the
    flat-% TP2 fallback (e.g. TP1 at +6.4% with only one swing found, TP2
    fallback flat at +6%) would leave TP2 closer than TP1 — a target that
    isn't "further", contradicting what TP2 is supposed to mean.
    """
    if bias == "bullish":
        above  = sorted((sp for sp in swing_highs if sp.price > entry), key=lambda sp: sp.price)
        tp1_sp = above[0] if above else None
        tp1    = tp1_sp.price if tp1_sp else round(entry * 1.03, 8)

        further = above[1].price if len(above) > 1 else round(entry * 1.06, 8)
        tp2     = round(max(further, tp1 * 1.02), 8)
    else:
        below  = sorted((sp for sp in swing_lows if sp.price < entry), key=lambda sp: sp.price, reverse=True)
        tp1_sp = below[0] if below else None
        tp1    = tp1_sp.price if tp1_sp else round(entry * 0.97, 8)

        further = below[1].price if len(below) > 1 else round(entry * 0.94, 8)
        tp2     = round(min(further, tp1 * 0.98), 8)

    tp1_age = (last_index_4h - tp1_sp.index) if tp1_sp else None

    return {"tp1": tp1, "tp2": tp2, "tp1_age": tp1_age, "tp1_structural": tp1_sp is not None}


def _build_levels(
    bias: str,
    entry: float,
    sl: float,
    swing_highs: list[SwingPoint],
    swing_lows: list[SwingPoint],
    last_index_4h: int,
) -> dict:
    """Calculate TP1, TP2, R:R, and distance percentages from entry/SL."""
    tp_info = _tp_targets(bias, entry, swing_highs, swing_lows, last_index_4h)
    tp1, tp2 = tp_info["tp1"], tp_info["tp2"]

    sl_dist  = abs(entry - sl)
    tp1_dist = abs(tp1 - entry)
    rr_ratio = round(tp1_dist / sl_dist, 2) if sl_dist > 0 else 0.0

    return {
        "entry":          round(entry, 8),
        "sl":             round(sl, 8),
        "tp1":            round(tp1, 8),
        "tp2":            round(tp2, 8),
        "rr_ratio":       rr_ratio,
        "sl_pct":         round(sl_dist / entry * 100, 3),
        "tp1_pct":        round(tp1_dist / entry * 100, 3),
        "tp1_structural": tp_info["tp1_structural"],
        "tp1_age_4h":     tp_info["tp1_age"],
        "tp1_stale":      tp_info["tp1_age"] is not None and tp_info["tp1_age"] > TP1_MAX_AGE_4H,
    }


# ─── Setup Finders ────────────────────────────────────────────────────────────
#
# Two setups, deliberately kept separate. They are opposite readings of the
# same event — price arriving at a level — and requiring both at once would
# be near-contradictory:
#
#   HOOK  (continuation): price CLOSED beyond the level and kept going.
#   SWEEP (reversal):     price WICKED beyond the level and closed back.
#
# Each returns (setup_dict, reason). setup_dict is None when the setup
# doesn't apply, with `reason` explaining which condition failed.

def _try_hook_setup(
    df_15m,
    df_1h,
    bias_4h: str,
    current_price: float,
) -> tuple[Optional[dict], str]:
    """
    Ross Hook continuation setup: 1-2-3 → breakout → hook → retest.

    Entry at the hook level, SL from the 1H order block boundary. Requires
    strict 4H agreement (a continuation trade against the intermediate
    trend is just a countertrend trade) and OB confluence, which live data
    showed matters a lot here (17.6% vs 5.0% win rate).
    """
    hook = detect_ross_hook(df_15m)
    if not hook["formation_complete"]:
        return None, "No completed Ross Hook on 15m"

    bias = hook["pattern"]

    if hook["candles_ago"] > MAX_HOOK_AGE_FOR_ALERT:
        return None, f"Hook too old ({hook['candles_ago']} candles, max {MAX_HOOK_AGE_FOR_ALERT})"

    if bias_4h == "ranging":
        return None, "4H market structure is ranging"

    if bias != bias_4h:
        return None, f"15m Ross Hook is {bias} but 4H bias is {bias_4h}"

    obs_1h     = find_order_blocks(df_1h, bias)
    active_obs = [ob for ob in obs_1h if not ob.mitigated]
    atr_1h     = calculate_atr(df_1h)
    nearest_ob = _find_nearest_ob(active_obs, current_price, bias, atr_1h) if active_obs else None

    if nearest_ob is None:
        return None, "No 1H order block confluence near current price"

    fvgs_1h = [f for f in find_fvg(df_1h) if not f.filled and f.kind == bias]

    return {
        "bias":        bias,
        "entry":       hook["hook_level"],
        "sl": (
            nearest_ob.low  * (1 - SL_BUFFER_PCT) if bias == "bullish"
            else nearest_ob.high * (1 + SL_BUFFER_PCT)
        ),
        "entry_type":  "HOOK",
        "ross_hook":   hook,
        "order_block": nearest_ob,
        "fvg":         _find_fvg_near_ob(fvgs_1h, nearest_ob),
        "sweep":       None,
    }, ""


def _try_sweep_setup(
    df_15m,
    df_1h,
    bias_4h: str,
    current_price: float,
) -> tuple[Optional[dict], str]:
    """
    Liquidity sweep reversal: yesterday's level is wicked and reclaimed,
    a displacement candle confirms the rejection, and entry is the retest
    of the FVG that displacement left behind.

    The 4H filter here is deliberately looser than the hook setup's. A
    reversal off a swept level is often the first move against a stale 4H
    reading, so this only requires 4H to not be actively opposed — a
    "ranging" 4H is fine. Demanding strict agreement would reject the
    setup exactly when it's most useful.
    """
    prev_day = get_previous_day_range(df_15m)
    if prev_day is None:
        return None, "No complete previous day in 15m data"

    atr_15m = calculate_atr(df_15m)
    if atr_15m <= 0:
        return None, "15m ATR unavailable"

    # A swept LOW is bullish (sell-side liquidity taken, buyers stepped in)
    candidates = [
        ("bullish", prev_day["low"],  "prev_day_low"),
        ("bearish", prev_day["high"], "prev_day_high"),
    ]

    for bias, level, level_name in candidates:
        if bias_4h != "ranging" and bias_4h != bias:
            continue  # 4H actively opposes this direction

        sweep = detect_sweep(df_15m, level, bias, level_name)
        if sweep is None:
            continue

        disp_i = find_displacement_in_range(df_15m, sweep.index, bias, atr_15m)
        if disp_i is None:
            continue  # level was reclaimed but nobody committed to the move

        # Entry: retest of the imbalance the displacement left, at its
        # proximal edge (the side price reaches first on the retrace).
        # No FVG means the impulse was continuous — fall back to the
        # reclaimed level itself, which is the same idea one step wider.
        entry_fvg = find_entry_fvg(df_15m, bias, disp_i)
        if entry_fvg is not None:
            entry = entry_fvg.top if bias == "bullish" else entry_fvg.bottom
        else:
            entry = level

        # Invalidation is the sweep extreme: if price trades back through
        # it, the level did not hold and the premise is gone.
        sweep_low  = float(df_15m["low"].iloc[sweep.index])
        sweep_high = float(df_15m["high"].iloc[sweep.index])
        sl = (
            sweep_low  * (1 - SL_BUFFER_PCT) if bias == "bullish"
            else sweep_high * (1 + SL_BUFFER_PCT)
        )

        obs_1h     = find_order_blocks(df_1h, bias)
        active_obs = [ob for ob in obs_1h if not ob.mitigated]
        atr_1h     = calculate_atr(df_1h)
        nearest_ob = _find_nearest_ob(active_obs, current_price, bias, atr_1h) if active_obs else None

        return {
            "bias":        bias,
            "entry":       entry,
            "sl":          sl,
            "entry_type":  "SWEEP",
            "ross_hook":   None,
            "order_block": nearest_ob,   # info only for this setup, not a gate
            "fvg":         entry_fvg,
            "sweep": {
                "level":       sweep.level,
                "level_name":  sweep.level_name,
                "candles_ago": sweep.candles_ago,
                "displacement_index": disp_i,
                "has_fvg":     entry_fvg is not None,
            },
        }, ""

    return None, "No sweep setup (needs reclaimed daily level + displacement, 4H not opposing)"


# ─── Main Analysis Entry Point ────────────────────────────────────────────────

def get_full_analysis(pair: str) -> dict:
    """
    Multi-timeframe confluence analysis over two setup types.

    HOOK  — Ross Hook continuation: 1-2-3 → breakout → hook retest.
            Strict 4H agreement + 1H OB confluence required.
    SWEEP — Liquidity sweep reversal: yesterday's high/low is wicked and
            reclaimed, displacement confirms, entry on the FVG retest.
            Looser 4H filter (must not actively oppose).

    HOOK is tried first (it has live win-rate data behind it); SWEEP is
    only evaluated when no hook setup qualifies, so the two never compete
    for the same bar.

    Shared gates (both setups):
      ✓ R:R ≥ MIN_RR_RATIO
      ✓ Direction doesn't oppose the pair's daily trend or BTC's

    TP1 age is reported (tp1_age_4h/tp1_stale) but does not gate the setup —
    see module docstring for why.
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

    # ── Shared Context: 4H Structure ──────────────────────────────────────────
    structure_4h = detect_market_structure(df_4h)
    bias_4h      = structure_4h["bias"]

    # ── Setup Selection ───────────────────────────────────────────────────────
    setup, hook_reason = _try_hook_setup(df_15m, df_1h, bias_4h, current_price)
    if setup is None:
        setup, sweep_reason = _try_sweep_setup(df_15m, df_1h, bias_4h, current_price)
        if setup is None:
            return {"valid": False, "reason": f"{hook_reason}; {sweep_reason}"}

    bias           = setup["bias"]
    entry          = setup["entry"]
    sl             = setup["sl"]
    entry_type     = setup["entry_type"]
    hook           = setup["ross_hook"]
    nearest_ob     = setup["order_block"]
    confluence_fvg = setup["fvg"]

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

    tte = None  # TTE disabled — see module docstring

    # TP1/TP2 target real, untapped liquidity — a swing already closed
    # through doesn't have resting liquidity left at it, so it's not a
    # meaningful target even though it's still "beyond entry" by price.
    unswept_highs = filter_unswept_swings(df_4h, swing_highs)
    unswept_lows  = filter_unswept_swings(df_4h, swing_lows)

    levels = _build_levels(bias, entry, sl, unswept_highs, unswept_lows, len(df_4h) - 1)

    # ── Shared Gate: R:R Check ────────────────────────────────────────────────
    if levels["rr_ratio"] < MIN_RR_RATIO:
        return {
            "valid":  False,
            "reason": f"R:R {levels['rr_ratio']:.1f} below minimum {MIN_RR_RATIO:.1f}",
        }

    # ── Shared Gate: Global Trend ─────────────────────────────────────────────
    # Checked last on purpose: it's the only gate that fetches extra data
    # (daily candles for this pair + BTC), so it only runs for setups that
    # already cleared everything cheaper.
    global_trend = check_global_trend(pair, bias)
    if not global_trend["ok"]:
        return {"valid": False, "reason": global_trend["reason"]}

    logger.info(
        "Valid setup: %s %s [%s] | entry=%.5f SL=%.5f TP1=%.5f RR=%.2f | 1D=%s BTC=%s",
        pair, bias.upper(), entry_type,
        levels["entry"], levels["sl"], levels["tp1"], levels["rr_ratio"],
        global_trend["daily_bias"], global_trend["market_regime"],
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
        "global_trend":  global_trend,
        "sweep":         setup["sweep"],
        **levels,
    }
