"""Tests for research_t50_generalization: filter, bootstrap, alpha t, basket mechanics."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import research_t50_generalization as tg


def test_tradable_filter_excludes_audit_found_leaks():
    for bad in ("USDC", "U", "RLUSD", "USDSOLD", "BULL", "BEAR", "ETHUP", "BTCDOWN"):
        assert not tg.tradable(bad), bad
    for good in ("BTC", "ETH", "JUP", "SOL"):
        assert tg.tradable(good), good


def test_bootstrap_identical_series_is_not_significant_and_better_series_is():
    rng = np.random.default_rng(0)
    bh = rng.normal(0.0005, 0.03, 1500)
    p_same, _ = tg.block_bootstrap_p(bh, bh, n_boot=500)
    assert p_same == 1.0
    better = bh * 0.5 + 0.002                                           # lower vol, higher mean
    p_better, p5 = tg.block_bootstrap_p(better, bh, n_boot=500)
    assert p_better < 0.05 and p5 > 0


def test_alpha_t_recovers_planted_alpha_and_beta():
    rng = np.random.default_rng(2)
    x = pd.Series(rng.normal(0, 0.03, 2500))
    y = 0.0008 + 0.5 * x + pd.Series(rng.normal(0, 0.005, 2500))
    a, t, beta = tg.alpha_t(y, x)
    assert abs(beta - 0.5) < 0.05 and abs(a - 0.0008 * 365) < 0.1 and t > 3
    a0, t0, _ = tg.alpha_t(0.5 * x + pd.Series(rng.normal(0, 0.005, 2500)), x)
    assert abs(t0) < 3


def _panel(n_days=400, n_coins=13, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n_days, freq="D", tz="UTC")
    names = [f"C{i}" for i in range(n_coins)]
    close = pd.DataFrame({c: 100 * np.exp(np.cumsum(rng.normal(0.001, 0.02, n_days))) for c in names}, index=idx)
    qv = pd.DataFrame({c: np.full(n_days, 1e6 * (n_coins - i)) for i, c in enumerate(names)}, index=idx)
    return close, qv


def test_month_universe_uses_only_past_volume_and_top_n():
    close, qv = _panel()
    uni = tg.month_universes(close, qv)
    assert uni and all(len(v) == tg.BASKET_N for v in uni.values())
    first = min(uni)
    assert first >= close.index[tg.MIN_HISTORY]                         # needs 90 days of history
    assert uni[first] == [f"C{i}" for i in range(tg.BASKET_N)]          # highest volume first
    qv2 = qv.copy()
    qv2.loc[qv2.index >= first, "C12"] = 1e12                           # volume spike ON the rebalance day
    assert tg.month_universes(close, qv2)[first] == uni[first]          # cannot leak into that day's universe


def test_basket_returns_have_no_lookahead_and_fees_reduce_return():
    close, qv = _panel()
    t50, bh = tg.basket_returns(close, qv)
    t50_hi, _ = tg.basket_returns(close, qv, fee=0.01)
    assert len(t50) == len(bh) > 100 and (t50_hi.sum() < t50.sum())
    c2 = close.copy()
    c2.iloc[-1] *= 3                                                    # change only the last day's prices
    t50b, bhb = tg.basket_returns(c2, qv)
    assert np.allclose(t50.iloc[:-1], t50b.iloc[:-1]) and np.allclose(bh.iloc[:-1], bhb.iloc[:-1])
