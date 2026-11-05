"""Mechanics tests for research_trend3: weights, no look-ahead, funding sign, fee."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import research_trend3 as t3


def _close(n=300, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")
    vols = {"BTC": 0.03, "ETH": 0.04, "GOLD": 0.01}
    return pd.DataFrame({k: 100 * np.exp(np.cumsum(rng.normal(0.001, v, n))) for k, v in vols.items()}, index=idx)


def test_weights_never_exceed_one_and_low_vol_gets_more():
    c = _close()
    w = t3.weights(c, trend=False).dropna()
    assert (w.sum(axis=1) <= 1 + 1e-9).all() and abs(w.sum(axis=1).iloc[-1] - 1) < 1e-9
    assert w["GOLD"].iloc[-1] > w["BTC"].iloc[-1] > w["ETH"].iloc[-1]       # inverse volatility
    wt = t3.weights(c, trend=True).dropna()
    assert (wt.sum(axis=1) <= w.sum(axis=1) + 1e-9).all()


def test_no_lookahead_future_prices_do_not_change_past_returns():
    c = _close()
    f = pd.DataFrame({"BTC": 0.0, "ETH": 0.0}, index=c.index)
    r1 = t3.portfolio_returns(c, t3.weights(c, True), f, 0.001, False)
    c2 = c.copy(); c2.iloc[-1] *= 2
    r2 = t3.portfolio_returns(c2, t3.weights(c2, True), f, 0.001, False)
    assert np.allclose(r1.iloc[:-1].dropna(), r2.iloc[:-1].dropna())


def test_positive_funding_hurts_longs_and_fees_hurt():
    c = _close()
    f = pd.DataFrame({"BTC": 0.0005, "ETH": 0.0005}, index=c.index)
    w = t3.weights(c, False)
    base = t3.portfolio_returns(c, w, f, 0.0, False).dropna()
    with_f = t3.portfolio_returns(c, w, f, 0.0, True).dropna()
    with_fee = t3.portfolio_returns(c, w, f, 0.01, False).dropna()
    assert with_f.sum() < base.sum() and with_fee.sum() < base.sum()
    # funding cost is exactly held weight x rate on crypto legs
    held = w.shift(1).reindex(with_f.index)
    assert np.allclose((base - with_f), (held["BTC"] + held["ETH"]) * 0.0005)
