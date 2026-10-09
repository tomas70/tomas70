"""Tests for research_options_vrp maths and frame construction."""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import research_options_vrp as vr


def test_atm_call_matches_closed_form_approximation():
    iv = 0.6
    approx = 0.3989 * iv * math.sqrt(30 / 365)            # S * 0.4 * sigma * sqrt(T)
    assert abs(vr.atm_call_pct(iv) - approx) < 5e-4
    assert vr.atm_call_pct(0.0) == 0.0


def test_nw_t_on_iid_noise_is_small_and_on_shifted_series_large():
    rng = np.random.default_rng(1)
    noise = pd.Series(rng.normal(0, 1, 3000))
    assert abs(vr.nw_mean_t(noise)[1]) < 3
    assert vr.nw_mean_t(noise + 0.3)[1] > 5


def test_diff_t_detects_group_effect():
    rng = np.random.default_rng(2)
    flag = pd.Series((np.arange(3000) // 100) % 2, dtype=float)
    y = pd.Series(rng.normal(0, 1, 3000)) + 0.5 * flag
    b, t = vr.nw_diff_t(y, flag)
    assert 0.2 < b < 0.8 and t > 2
    b0, t0 = vr.nw_diff_t(pd.Series(rng.normal(0, 1, 3000)), flag)
    assert abs(t0) < 3


def test_build_frame_alignment_and_pl_arithmetic():
    idx = pd.date_range("2022-01-01", periods=140, freq="D", tz="UTC")
    px = pd.Series(100 * np.exp(np.cumsum(np.full(140, 0.001))), index=idx)     # steady +0.1%/day
    dvol = pd.Series(50.0, index=idx)
    f = vr.build_frame(dvol, px)
    assert len(f) == 140 - vr.HORIZON
    row = f.iloc[0]
    assert abs(row["ret30"] - (px.iloc[30] / px.iloc[0] - 1)) < 1e-12
    assert row["rv"] < 1e-9                                                       # constant returns -> zero realised vol
    assert row["vrp"] > 49                                                         # IV 50 vs RV 0
    expect = row["prem_straddle"] * (1 - vr.COST_FRAC) - abs(row["ret30"])
    assert abs(row["pl_straddle"] - expect) < 1e-12
    assert row["pl_put"] == row["prem_put"] * (1 - vr.COST_FRAC)                  # price rose: put expires worthless
