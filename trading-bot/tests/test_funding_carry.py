"""Tests for research_funding_carry: daily aggregation, carry variants, costs, no-look-ahead."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import research_funding_carry as fc


def _hourly(days=5, rate=1e-5, drop=()):
    idx = pd.date_range("2021-01-01", periods=days * 24, freq="h", tz="UTC")
    s = pd.Series(rate, index=idx)
    return s.drop(s.index[list(drop)]).rename("interest_1h").to_frame()


def test_daily_funding_sums_hours_and_drops_incomplete_days():
    h = _hourly(days=5, drop=[30])                                  # one hour missing on day 2
    F, info = fc.daily_funding(h)
    assert info["incomplete"] >= 1 and np.isnan(F.iloc[1])
    assert abs(F.iloc[0] - 24e-5) < 1e-12
    assert F.index[-1] == pd.Timestamp("2021-01-04", tz="UTC")     # last (possibly partial) day excluded


def _F(vals):
    return pd.Series(vals, index=pd.date_range("2022-01-01", periods=len(vals), freq="D", tz="UTC"))


def test_c0_positive_funding_nets_funding_minus_round_trip():
    F = _F(np.full(100, 1e-4))
    pnl = fc.carry_returns(F, "C0")
    assert abs(pnl.sum() - (100 * 1e-4 - 2 * fc.ENTRY_COST)) < 1e-12


def test_c1_stays_off_when_funding_negative_and_costs_nothing():
    pnl = fc.carry_returns(_F(np.full(60, -1e-4)), "C1")
    assert (pnl == 0).all()


def test_c1_waits_for_trailing_window_and_uses_only_past_data():
    base = np.full(40, 1e-4)
    F = _F(base)
    pnl = fc.carry_returns(F, "C1")
    assert pnl.index[0] == F.index[fc.LAG]                                 # first trailing mean needs 7 days
    spike = base.copy()
    spike[20] = -0.05                                                      # shock ON day 20
    p2 = fc.carry_returns(_F(spike), "C1")
    d20 = F.index[20]
    assert (pnl.loc[d20] != 0) and (p2.loc[d20] != 0)                      # same on-state: decision ignores day-20 funding
    assert abs(p2.loc[d20] - (-0.05)) < 1e-12


def test_stress_lowers_return_and_capital_scaling():
    F = _F(np.full(200, 1e-4))
    assert fc.annual(fc.carry_returns(F, "C0", funding_mult=0.7, cost_mult=2.0)) < fc.annual(fc.carry_returns(F, "C0"))
    pnl = fc.carry_returns(F, "C0")
    assert abs(fc.annual(pnl) - pnl.mean() / fc.CAPITAL_MULT * 365) < 1e-12
