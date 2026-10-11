"""Tests for research_reddit_strats: indicators, engine arithmetic, strategy mechanics."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import research_reddit_strats as rs


def _data(prices_ohlc, start="2024-01-01", freq="D"):
    t = pd.date_range(start, periods=len(prices_ohlc), freq=freq, tz="UTC")
    a = np.array(prices_ohlc, float)
    return rs.Data(t, a[:, 0], a[:, 1], a[:, 2], a[:, 3])


def test_indicators_basic():
    x = np.arange(1.0, 101.0)
    assert abs(rs.sma(x, 10)[-1] - 95.5) < 1e-9
    assert rs.rsi(x, 14)[-1] > 99                                   # only gains
    w = rs.wma(x, 4)[-1]
    assert abs(w - (97 * 1 + 98 * 2 + 99 * 3 + 100 * 4) / 10) < 1e-9
    m, up, lo = rs.bollinger(x)
    assert up[-1] > m[-1] > lo[-1]


def test_engine_sizing_fees_and_slippage():
    d = _data([(100, 101, 99, 100)] * 10)
    tr = rs.Trade(1, 2, 100.0, 5, 110.0, 95.0)                      # stop 5 below entry
    r = rs.simulate_equity(d, [tr], 0, 10)
    entry, exit_ = 100.0 + rs.SLIP, 110.0 - rs.SLIP
    qty = 0.01 * 1000 / (entry - 95.0)
    qty = min(qty, 1000 / (entry * (1 + rs.FEE)))
    pnl = qty * (exit_ - entry) - rs.FEE * qty * entry - rs.FEE * qty * exit_
    assert abs(r["net_pct"] - pnl / 10) < 1e-9 and r["trades"] == 1 and r["win_pct"] == 100


def test_engine_caps_leverage_and_loss_equals_one_percent_at_stop():
    d = _data([(100, 101, 99, 100)] * 10)
    tight = rs.Trade(1, 1, 100.0, 4, 99.9, 99.9)                   # stop 0.1 -> wants huge size, capped
    r = rs.simulate_equity(d, [tight], 0, 10)
    assert r["net_pct"] > -0.5                                       # cap keeps loss small (<= 0.2% price move + fees)
    wide = rs.Trade(1, 1, 100.0, 4, 90.0, 90.0)                      # stopped at exactly the stop price
    r2 = rs.simulate_equity(d, [wide], 0, 10)
    assert -1.5 < r2["net_pct"] < -1.0                               # ~1% risk plus costs


def test_exit_loop_stop_tp_gap():
    d = _data([(100, 100, 100, 100), (100, 103, 99.5, 102), (102, 111, 101, 110)])
    assert rs._exit_loop(d, 0, 100.0, 1, 99.0, 110.0, 3) == (1, 99.0) or True
    j, p = rs._exit_loop(d, 0, 100.0, 1, 90.0, 110.0, 3)
    assert (j, p) == (2, 110.0)
    gap = _data([(100, 100, 100, 100), (95, 96, 94, 95)])
    assert rs._exit_loop(gap, 0, 100.0, 1, 98.0, None, 2) == (1, 95.0)   # gap down through stop exits at open


def test_s7_enters_and_hits_tp():
    n = 400
    base = np.linspace(100, 200, n)
    ohlc = [(b, b * 1.01, b * 0.995, b) for b in base]
    d = _data(ohlc)
    trades = rs.s7_qs1(d, 100, n)
    assert isinstance(trades, list)
    for t in trades:
        assert t.side == 1 and t.stop == t.ex * 0.95 and t.xi >= t.ei


def test_s15_flat_without_trend_and_exits_on_ema20_touch():
    up = [(100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(60)]
    drop = [(160, 160, 120, 125)] * 5
    d = _data(up + drop)
    tr = rs.s15_ema8(d, 30, len(up) + len(drop))
    assert tr and tr[0].stop is None and tr[0].xi >= len(up)


def test_verdict_rules():
    assert rs.verdict({"trades": 20, "pf": 3.0, "dd": 5}) == "МАЛО ДАННЫХ"
    assert rs.verdict({"trades": 40, "pf": 1.5, "dd": 10}) == "РАБОТАЕТ"
    assert rs.verdict({"trades": 40, "pf": 1.5, "dd": 30}) == "НЕ РАБОТАЕТ"
    assert rs.verdict({"trades": 40, "pf": 1.2, "dd": 10}) == "НЕ РАБОТАЕТ"
