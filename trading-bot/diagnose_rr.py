"""
Diagnostics: shows what RR_ratio each pair WOULD have if it passed all
other gates, without enforcing MIN_RR_RATIO. Helps decide whether to
lower MIN_RR_RATIO from 3.0, and identifies which gate is the real
bottleneck (Hook / 4H bias) for each pair. OB/FVG/PD-zone are reported
as confluence info only — they're bonus confirmation, not hard gates.

Usage (on the Mac where the bot runs):
    cd /Users/tomassipelis/trading-bot
    .venv/bin/python diagnose_rr.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from analysis.market_data import get_all_timeframes
from analysis.smc import calculate_atr, detect_market_structure, find_order_blocks, find_fvg, get_pd_zone
from analysis.ross_hook import detect_ross_hook, get_tte_entry
from analysis.multi_timeframe import (
    _find_nearest_ob, _find_fvg_near_ob, _build_levels, SL_BUFFER_PCT, OB_PROXIMITY_ATR_MULT,
    MAX_HOOK_AGE_FOR_ALERT, TP1_MAX_AGE_4H,
)
from config import PAIRS


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

    hook = detect_ross_hook(df_15m)
    if not hook["formation_complete"]:
        print(f"{pair:6} | ❌ no hook")
        return
    hook_bias = hook["pattern"]
    hook_age  = hook["candles_ago"]
    hook_age_label = f"hook_age={hook_age}c" + ("⚠️stale" if hook_age > MAX_HOOK_AGE_FOR_ALERT else "")

    structure_4h = detect_market_structure(df_4h)
    bias_4h = structure_4h["bias"]
    if bias_4h == "ranging":
        print(f"{pair:6} | hook={hook_bias:7} | ❌ 4H ranging")
        return
    if hook_bias != bias_4h:
        print(f"{pair:6} | hook={hook_bias:7} | ❌ 4H={bias_4h} mismatch")
        return
    bias = hook_bias

    # OB / FVG: confluence only, not a hard gate
    obs_1h     = find_order_blocks(df_1h, bias)
    active_obs = [ob for ob in obs_1h if not ob.mitigated]
    atr_1h     = calculate_atr(df_1h)
    nearest_ob = _find_nearest_ob(active_obs, current_price, bias, atr_1h) if active_obs else None

    fvgs_1h        = [f for f in find_fvg(df_1h) if not f.filled and f.kind == bias]
    confluence_fvg = _find_fvg_near_ob(fvgs_1h, nearest_ob) if nearest_ob else None

    ob_label  = "OB✓" if nearest_ob else "OB✗"
    fvg_label = "FVG✓" if confluence_fvg else "FVG✗"

    # PD zone: confluence info only, not a hard gate (conflicts with Ross
    # Hook's continuation nature — see multi_timeframe.py docstring)
    swing_highs = structure_4h.get("swing_highs", [])
    swing_lows = structure_4h.get("swing_lows", [])
    if swing_highs and swing_lows:
        pd_info = get_pd_zone(swing_highs[-1].price, swing_lows[-1].price, current_price)
    else:
        pd_info = {"zone": "unknown"}

    tte = get_tte_entry(df_15m, hook)
    if tte:
        entry, sl, entry_type = tte["tte_entry"], tte["tte_sl"], "TTE"
    elif nearest_ob is not None:
        entry = hook["hook_level"]
        sl = (nearest_ob.low * (1 - SL_BUFFER_PCT) if bias == "bullish"
              else nearest_ob.high * (1 + SL_BUFFER_PCT))
        entry_type = "HOOK(OB)"
    else:
        entry = hook["hook_level"]
        p2 = hook["p2"].price
        sl = p2 * (1 - SL_BUFFER_PCT) if bias == "bullish" else p2 * (1 + SL_BUFFER_PCT)
        entry_type = "HOOK(P2)"

    levels = _build_levels(bias, entry, sl, swing_highs, swing_lows, len(df_4h) - 1)
    rr = levels["rr_ratio"]

    if levels["tp1_structural"]:
        age = levels["tp1_age_4h"]
        tp1_label = f"tp1_age={age}c" + ("⚠️stale" if levels["tp1_stale"] else "")
    else:
        tp1_label = "tp1=fallback%⚠️"

    flag = "✅" if rr >= 3.0 else ("🟡" if rr >= 2.0 else "🔴")
    print(
        f"{pair:6} | hook={hook_bias:7} 4H={bias_4h:7} {ob_label} {fvg_label} pd={pd_info['zone']:11} "
        f"| {entry_type:8} entry={entry:.4f} sl={sl:.4f} | RR={rr:.2f} {flag} | {tp1_label} | {hook_age_label}"
    )


if __name__ == "__main__":
    print(f"{'PAIR':6} | gates...                                          | result")
    print("-" * 100)
    for pair in PAIRS:
        diagnose(pair)

    print("\nLegend: ✅ RR>=3.0  🟡 RR>=2.0  🔴 RR<2.0  |  OB/FVG/PD = bonus confluence, not a gate")
    print(f"        tp1_age = candles since the 4H swing used for TP1 (⚠️stale if > {TP1_MAX_AGE_4H} candles — HARD GATE in production)")
    print("        tp1=fallback% = no 4H swing beyond entry, TP1 is a flat % guess — HARD GATE in production")
    print(f"        hook_age = 15m candles since the Ross Hook formed (⚠️stale if > {MAX_HOOK_AGE_FOR_ALERT} candles — HARD GATE in production)")
    print("        NOTE: this script shows setups even if they'd fail the TP1/hook freshness gates, for tuning")
