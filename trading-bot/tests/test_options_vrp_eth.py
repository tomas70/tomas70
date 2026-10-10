"""Tests for research_options_vrp_eth: point-in-time percentile, HAC contrast."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import research_options_vrp_eth as ve


def test_percentile_is_point_in_time():
    rng = np.random.default_rng(3)
    idx = pd.date_range("2022-01-01", periods=600, freq="D", tz="UTC")
    s = pd.Series(50 + rng.normal(0, 5, 600).cumsum() * 0.2, index=idx)
    full = ve.iv_percentile(s)
    cut = ve.iv_percentile(s.iloc[:450])
    assert np.allclose(full.iloc[:450].dropna(), cut.dropna())          # future data cannot change the past
    assert full.iloc[:364].isna().all() and full.iloc[364:].notna().all()
    top = pd.Series(np.arange(400.0), index=idx[:400])
    assert ve.iv_percentile(top).iloc[-1] == 1.0                         # new high -> percentile 1


def _frame(n=1500, effect=0.0, seed=4):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-03-01", periods=n, freq="D", tz="UTC")
    pct = pd.Series(((np.arange(n) // 120) % 3) / 2 + 0.01, index=idx)    # blocks of low / mid / high
    y = rng.normal(0, 0.05, n) + effect * (pct >= ve.HIGH).to_numpy()
    return pd.DataFrame({"pct": pct, "pl_straddle": y, "pl_put": y}, index=idx)


def test_state_stats_detects_planted_high_iv_effect():
    r = ve.state_stats(_frame(effect=0.05))
    assert r["mean"]["high"] > 0.03 and r["diff"] > 0.03 and r["diff_t"] > 2.5
    assert r["episodes"] >= 3


def test_state_stats_neutral_without_effect():
    r = ve.state_stats(_frame(effect=0.0))
    assert abs(r["diff_t"]) < 3
