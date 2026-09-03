"""
Diagnostics: shows which setup (if any) each pair currently offers and what
R:R it would carry, WITHOUT enforcing MIN_RR_RATIO — so you can see the R:R
distribution and decide whether the threshold is set sensibly.

Calls the same _try_hook_setup / _try_sweep_setup / check_global_trend used
in production rather than reimplementing them, so this can't drift out of
sync with what the bot actually does. The only gate it skips is MIN_RR_RATIO
(reported instead of enforced).

Usage (on the Mac where the bot runs):
    cd /Users/tomassipelis/trading-bot
    .venv/bin/python diagnose_rr.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from analysis.market_data import get_all_timeframes
from analysis.smc import detect_market_structure, filter_unswept_swings, get_pd_zone
from analysis.global_trend import check_global_trend
from analysis.multi_timeframe import (
    _try_hook_setup, _try_sweep_setup, _build_levels, TP1_MAX_AGE_4H,
)
from config import HOOK_SETUP_ENABLED, MIN_RR_RATIO, PAIRS


def diagnose(pair: str) -> None:
    try:
        data = get_all_timeframes(pair)
    except Exception as exc:
        print(f"{pair:6} | DATA ERROR: {exc}")
        return

    df_4h, df_1h, df_15m = data["4h"], data["1h"], data["15m"]
    if df_4h.empty or df_1h.empty or df_15m.empty:
        print(f"{pair:6} | EMPTY DATA")
        return

    current_price = float(df_15m["close"].iloc[-1])

    structure_4h = detect_market_structure(df_4h)
    bias_4h      = structure_4h["bias"]

    # Mirror production: HOOK only runs when enabled (see config.HOOK_SETUP_ENABLED)
    if HOOK_SETUP_ENABLED:
        setup, hook_reason = _try_hook_setup(df_15m, df_1h, bias_4h, current_price)
    else:
        setup, hook_reason = None, "HOOK disabled"

    sweep_reason = ""
    if setup is None:
        setup, sweep_reason = _try_sweep_setup(df_15m, df_1h, bias_4h, current_price)

    if setup is None:
        print(f"{pair:6} | 4H={bias_4h:7} | ❌ HOOK: {hook_reason}")
        print(f"{'':6} | {'':11} | ❌ SWEEP: {sweep_reason}")
        return

    bias = setup["bias"]

    swing_highs = structure_4h.get("swing_highs", [])
    swing_lows  = structure_4h.get("swing_lows",  [])
    pd_zone = (
        get_pd_zone(swing_highs[-1].price, swing_lows[-1].price, current_price)["zone"]
        if swing_highs and swing_lows else "unknown"
    )

    levels = _build_levels(
        bias, setup["entry"], setup["sl"],
        filter_unswept_swings(df_4h, swing_highs),
        filter_unswept_swings(df_4h, swing_lows),
        len(df_4h) - 1,
    )
    rr = levels["rr_ratio"]

    if levels["tp1_structural"]:
        tp1_label = f"tp1_age={levels['tp1_age_4h']}c" + ("⚠️stale" if levels["tp1_stale"] else "")
    else:
        tp1_label = "tp1=fallback%⚠️"

    gt = check_global_trend(pair, bias)
    gt_label = f"1D={gt['daily_bias'][:4]}/BTC={gt['market_regime'][:4]}" + ("" if gt["ok"] else " ❌GLOBAL")

    # What actually triggered — hook age, or which level was swept
    if setup["entry_type"] == "HOOK":
        trigger = f"hook_age={setup['ross_hook']['candles_ago']}c"
    else:
        sw = setup["sweep"]
        trigger = f"{sw['level_name']} {sw['candles_ago']}c ago" + (" +FVG" if sw["has_fvg"] else "")

    flag = "✅" if rr >= MIN_RR_RATIO else ("🟡" if rr >= 2.0 else "🔴")
    print(
        f"{pair:6} | {setup['entry_type']:5} {bias:7} 4H={bias_4h:7} pd={pd_zone:11} "
        f"| entry={setup['entry']:.4f} sl={setup['sl']:.4f} | RR={rr:.2f} {flag} "
        f"| {tp1_label} | {trigger} | {gt_label}"
    )


if __name__ == "__main__":
    print(f"{'PAIR':6} | setup / gates ...")
    print("-" * 120)
    for pair in PAIRS:
        diagnose(pair)

    print(f"\nLegend: ✅ RR>={MIN_RR_RATIO}  🟡 RR>=2.0  🔴 RR<2.0")
    print("        HOOK  = Ross Hook continuation (needs strict 4H match + 1H OB confluence)")
    print("        SWEEP = daily level swept & reclaimed + displacement (4H only must not oppose)")
    print("        HOOK is tried first; SWEEP only when no hook qualifies")
    print(f"        tp1_age = 4H candles since the swing used for TP1 (⚠️stale if > {TP1_MAX_AGE_4H} — info only, not a gate)")
    print("        1D/BTC = pair daily trend / BTC daily regime; ❌GLOBAL = opposes one (HARD GATE in production)")
    print(f"        NOTE: MIN_RR_RATIO={MIN_RR_RATIO} is reported here, not enforced — everything else matches production")
