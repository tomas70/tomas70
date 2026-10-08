"""Tests for research_multi_cycle universe selection and weekly basket maths."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import research_multi_cycle as mc


def _panel(n=200, coins=24, start="2020-01-01"):
    idx = pd.date_range(start, periods=n, freq="D", tz="UTC")
    close = pd.DataFrame({f"C{i}": 100 * (1 + 0.001 * (i - 12)) ** np.arange(n) for i in range(coins)}, index=idx)
    qv = pd.DataFrame({f"C{i}": 1e6 * (i + 1) for i in range(coins)}, index=idx)
    return close, qv


def test_universe_requires_history_and_ranks_by_volume():
    close, qv = _panel()
    close.loc[close.index[:150], "C0"] = np.nan          # C0 only 50 days old at day 199
    t = close.index[199]
    uni = mc.eligible_universe(close, qv, t)
    assert "C0" not in uni and uni[0] == "C23"           # highest volume first
    assert len(uni) == 23


def test_universe_empty_when_too_few_coins():
    close, qv = _panel(coins=10)
    assert mc.eligible_universe(close, qv, close.index[199]) == []


def test_period_return_handles_delist():
    close, _ = _panel(coins=24)
    t = close.index[100]
    assert mc.period_return(close, "C20", t) > 0
    close.loc[close.index[103:], "C20"] = np.nan          # vanishes after 2 more days
    full = close["C20"].iloc[102] / close["C20"].iloc[100] - 1
    assert abs(mc.period_return(close, "C20", t) - full) < 1e-12
    close.loc[close.index[101:], "C20"] = np.nan          # nothing after entry
    assert mc.period_return(close, "C20", t) == 0.0


def test_momentum_wins_on_persistent_trends_and_reversal_loses():
    close, qv = _panel()
    mom = mc.xs_weekly(close, qv, 14, +1)
    rev = mc.xs_weekly(close, qv, 14, -1)
    assert len(mom) > 10
    assert (mom.iloc[1:] > 0).all() and (rev < 0).all()
    # costs: the same basket without any cost earns more
    free = mc.xs_weekly(close, qv, 14, +1, side_cost=0.0, short_drag=0.0)
    assert (free >= mom).all() and free.sum() > mom.sum()


def test_analyze_weekly_fails_short_sample():
    r = pd.Series(0.01, index=pd.date_range("2023-01-01", periods=50, freq="7D", tz="UTC"))
    assert not mc.analyze_weekly(r)["passes"]       # n < 200
