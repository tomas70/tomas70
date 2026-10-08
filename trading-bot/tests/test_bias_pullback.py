"""Tests for research_bias_pullback: no-look-ahead features, stop clamp, exits, costs."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import research_bias_pullback as bp


def _hourly(prices, start="2024-01-01"):
    idx = pd.date_range(start, periods=len(prices), freq="h", tz="UTC")
    p = np.asarray(prices, dtype=float)
    return pd.DataFrame({"open": p, "high": p * 1.001, "low": p * 0.999, "close": p}, index=idx)


def _trend(n=24 * 120, drift=0.0004, wobble=0.004):
    t = np.arange(n)
    return 100 * np.exp(drift * t + wobble * np.sin(t / 5.0) * 5)


def test_features_do_not_use_unclosed_higher_timeframe_bars():
    df = _hourly(_trend())
    f_full = bp.build_features(df)
    f_cut = bp.build_features(df.iloc[:-30])
    common = f_cut.index
    for col in ("bias", "dist", "atr_pct", "d1"):
        a, b = f_full.loc[common, col], f_cut[col]
        assert np.allclose(a.fillna(-9), b.fillna(-9)), col      # future data must not change the past


def test_bias_updates_only_after_4h_bar_closes():
    df = _hourly(_trend())
    f = bp.build_features(df)
    # 4H bar covering hours 00-03 completes at 04:00; its bias is first visible on the 03:00 bar's close,
    # i.e. the hourly bar labelled 03:00 sees bias from the PREVIOUS 4H bar, labelled 04:00 onwards sees the new one.
    day = f.loc["2024-03-01"]
    bias_seq = day["bias"].to_numpy()
    changes = np.where(np.diff(bias_seq) != 0)[0] + 1
    assert all(f.index[f.index.get_loc(day.index[k])].hour % 4 == 3 for k in changes) or len(changes) == 0


def test_stop_clamped_between_3_and_5_percent_and_long_trades_in_uptrend():
    f = bp.build_features(_hourly(_trend()))
    tr = bp.run_strategy(f, "E1")
    assert len(tr) > 5
    assert tr["stop_pct"].between(bp.STOP_MIN - 1e-12, bp.STOP_MAX + 1e-12).all()
    assert (tr["dir"] == 1).mean() > 0.7


def test_e1_take_profit_pays_two_r_minus_costs():
    f = bp.build_features(_hourly(_trend()))
    tr = bp.run_strategy(f, "E1")
    winners = tr[tr["r_gross"] > 1.99]
    assert len(winners) > 0
    assert np.allclose(winners["r_gross"], 2.0, atol=1e-6)
    assert (winners["r_net"] < winners["r_gross"]).all()


def test_cost_is_charged_per_hour_held():
    f = bp.build_features(_hourly(_trend()))
    tr = bp.run_strategy(f, "E2")
    assert len(tr) > 1
    expect = (2 * bp.SIDE_COST + tr["hours"] * bp.FUND_PER_YEAR / 8760) / tr["stop_pct"]
    assert np.allclose(tr["r_gross"] - tr["r_net"], expect)


def test_tiering_and_sizing_weights():
    assert bp.tier_of(1, 1.5, 1) == "A"
    assert bp.tier_of(1, 0.5, 1) == "B" and bp.tier_of(-1, 1.5, 1) == "B"
    assert bp.tier_of(-1, 0.2, 1) == "C"
    assert bp.TIER_W == {"A": 1.0, "B": 0.5, "C": 0.25}


def test_analyze_requires_enough_trades():
    f = bp.build_features(_hourly(_trend()))
    a = bp.analyze(bp.run_strategy(f, "E1"))
    assert a["n"] > 0 and a["passes"] is False         # far fewer than 300 trades on 120 days
