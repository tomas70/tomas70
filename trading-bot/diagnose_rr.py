"""
Diagnostics: shows what RR_ratio each pair WOULD have if it passed all
other gates, without enforcing MIN_RR_RATIO. Helps decide whether to
lower MIN_RR_RATIO from 3.0, and identifies which gate is the real
bottleneck (Hook / 4H bias / OB / PD zone) for each pair.

Usage (on the Mac where the bot runs):
    cd /Users/tomassipelis/trading-bot
    .venv/bin/python diagnose_rr.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from analysis.market_data import get_all_timeframes
from analysis.smc import detect_market_structure, find_order_blocks, get_pd_zone
from analysis.ross_hook import detect_ross_hook, get_tte_entry
from analysis.multi_timeframe import _find_nearest_ob, _build_levels, SL_BUFFER_PCT
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

    structure_4h = detect_market_structure(df_4h)
    bias_4h = structure_4h["bias"]
    if bias_4h == "ranging":
        print(f"{pair:6} | hook={hook_bias:7} | ❌ 4H ranging")
        return
    if hook_bias != bias_4h:
        print(f"{pair:6} | hook={hook_bias:7} | ❌ 4H={bias_4h} mismatch")
        return
    bias = hook_bias

    obs_1h = find_order_blocks(df_1h, bias)
    active_obs = [ob for ob in obs_1h if not ob.mitigated]
    if not active_obs:
        print(f"{pair:6} | hook={hook_bias:7} 4H={bias_4h:7} | ❌ no active OB")
        return
    nearest_ob = _find_nearest_ob(active_obs, current_price, bias)
    if nearest_ob is None:
        closest_dist_pct = min(
            (
                (ob.low - current_price) / current_price * 100 if current_price < ob.low
                else (current_price - ob.high) / current_price * 100 if current_price > ob.high
                else 0.0
            )
            for ob in active_obs
        )
        print(
            f"{pair:6} | hook={hook_bias:7} 4H={bias_4h:7} | ❌ price not near OB "
            f"({len(active_obs)} OBs exist, closest is {closest_dist_pct:.2f}% away, need <=2%)"
        )
        return

    swing_highs = structure_4h.get("swing_highs", [])
    swing_lows = structure_4h.get("swing_lows", [])
    if swing_highs and swing_lows:
        pd_info = get_pd_zone(swing_highs[-1].price, swing_lows[-1].price, current_price)
        if bias == "bullish" and pd_info["zone"] == "premium":
            print(f"{pair:6} | hook={hook_bias:7} 4H={bias_4h:7} OB=✓ | ❌ premium zone (need discount)")
            return
        if bias == "bearish" and pd_info["zone"] == "discount":
            print(f"{pair:6} | hook={hook_bias:7} 4H={bias_4h:7} OB=✓ | ❌ discount zone (need premium)")
            return
    else:
        pd_info = {"zone": "unknown"}

    tte = get_tte_entry(df_15m, hook)
    if tte:
        entry, sl, entry_type = tte["tte_entry"], tte["tte_sl"], "TTE"
    else:
        entry = hook["hook_level"]
        sl = (nearest_ob.low * (1 - SL_BUFFER_PCT) if bias == "bullish"
              else nearest_ob.high * (1 + SL_BUFFER_PCT))
        entry_type = "HOOK"

    levels = _build_levels(bias, entry, sl, swing_highs, swing_lows)
    rr = levels["rr_ratio"]

    flag = "✅" if rr >= 3.0 else ("🟡" if rr >= 2.0 else "🔴")
    print(
        f"{pair:6} | hook={hook_bias:7} 4H={bias_4h:7} OB=✓ pd={pd_info['zone']:11} "
        f"| {entry_type:4} entry={entry:.4f} sl={sl:.4f} | RR={rr:.2f} {flag}"
    )


if __name__ == "__main__":
    print(f"{'PAIR':6} | gates...                                          | result")
    print("-" * 100)
    for pair in PAIRS:
        diagnose(pair)

    print("\nLegend: ✅ RR>=3.0  🟡 RR>=2.0  🔴 RR<2.0")
