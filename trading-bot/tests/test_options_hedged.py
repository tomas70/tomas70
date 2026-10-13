"""Tests for research_options_hedged: BS maths, fair-game property of the hedged straddle, wing bound."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import research_options_hedged as oh

ZERO = oh.Params(c_opt=0.0, c_hedge=0.0, wing_mult=1.0)


def test_bs_put_call_parity_and_atm_formula():
    c = oh.bs_call(100.0, 100.0, 0.6, 30 / 365)
    p = oh.bs_put(100.0, 100.0, 0.6, 30 / 365)
    assert abs(c - p) < 1e-9
    assert abs(c / 100 - 0.3989 * 0.6 * np.sqrt(30 / 365)) < 5e-4
    assert float(oh.bs_call(120.0, 100.0, 0.5, 0.0)) == 20.0


def test_flat_price_hedged_straddle_collects_full_premium():
    n = 80
    S, IV = np.full(n, 100.0), np.full(n, 0.5)
    r = oh.simulate(S, IV, ZERO)
    prem = 2 * float(oh.bs_call(100.0, 100.0, 0.5, 30 / 365)) / 100
    assert np.allclose(r["A"], prem) and np.allclose(r["A_hedge"], 0)
    assert np.allclose(r["B"], r["B_prem"])                                   # nothing pays out


def test_hedged_straddle_is_fair_game_under_gbm_and_less_volatile_than_naked():
    rng = np.random.default_rng(11)
    n = 8000
    S = 100 * np.exp(np.cumsum(rng.normal(-0.5 * 0.6 ** 2 / 365, 0.6 / np.sqrt(365), n)))
    r = oh.simulate(S, np.full(n, 0.6), ZERO)
    naked = r["A_prem"] + r["A_settle"]
    assert abs(r["A"].mean()) < 0.012                                          # fair price -> ~0 P&L
    assert r["A"].std() < naked.std()


def test_costs_reduce_pnl_and_wing_loss_is_bounded():
    rng = np.random.default_rng(5)
    S = 100 * np.exp(np.cumsum(rng.normal(0, 0.03, 400)))
    S[200:] *= 0.5                                                            # crash
    IV = np.full(400, 0.6)
    free, costly = oh.simulate(S, IV, ZERO), oh.simulate(S, IV, oh.MAIN)
    assert (costly["A"] < free["A"]).all()
    assert free["B"].min() > -oh.WING - 1e-9                                  # iron butterfly: max loss = wing width


def test_max_drawdown():
    assert abs(oh.max_drawdown(np.array([0.1, -0.5, 0.2])) - 0.5) < 1e-12
    assert oh.max_drawdown(np.array([0.1, 0.1])) == 0.0
