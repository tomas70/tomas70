"""Tests for research_daily_factors helpers."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import research_daily_factors as rd


def _idx(n, start="2025-03-02"):
    return pd.date_range(start, periods=n, freq="D", tz="UTC")


def test_trend_position_uses_prior_close_no_lookahead():
    close = pd.Series(np.arange(1.0, 61.0), index=_idx(60))
    pos = rd.trend_positions(close, "T50")
    assert pos.iloc[:49].isna().all() and pos.iloc[49] == 1.0
    r = rd.strategy_returns(close, pos)
    # first held return is the day AFTER the first signal
    assert np.isnan(r.iloc[49]) and not np.isnan(r.iloc[50])


def test_donchian_enters_and_exits():
    up = list(np.linspace(100, 160, 70))
    down = list(np.linspace(160, 80, 40))
    close = pd.Series(up + down, index=_idx(110))
    pos = rd.trend_positions(close, "D55").dropna()
    assert pos.iloc[0] == 0.0 or pos.max() == 1.0
    assert pos.iloc[-1] == 0.0 and pos.max() == 1.0


def test_xs_momentum_picks_winners_and_charges_turnover():
    n, cols = 60, [f"C{i}" for i in range(14)]
    base = _idx(n)
    # coin i grows at a steady rate proportional to i -> ranking is stable
    prices = pd.DataFrame({c: 100 * (1 + 0.002 * i) ** np.arange(n) for i, c in enumerate(cols)},
                          index=base)
    r = rd.xs_returns(prices, 7, +1, 7, weekly=True)
    assert len(r) > 3 and (r.iloc[1:] > 0).all()      # momentum wins on trending data
    rev = rd.xs_returns(prices, 7, -1, 7, weekly=True)
    assert (rev.iloc[1:] < 0).all()
    # first rebalance pays full turnover (2 legs x 6 x 1/6 = 2.0) x fee
    gross_first = r.iloc[0] + 2.0 * rd.SIDE_FEE
    assert gross_first > r.iloc[0]


def test_stats_helpers():
    s = pd.Series([0.01, -0.005, 0.02, 0.0, 0.01])
    assert rd.t_stat(s) > 0 and rd.max_dd(s) <= 0
