"""Tests for analysis/sonic_follow: parsing, delayed-follower arithmetic, idempotent forward log."""
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analysis.sonic_follow as sf

NEW = """open,close,side,lots,price_open,price_close,profit
2026-11-04 08:00,2026-11-04 08:20,B,0.10,4000.00,4004.00,39.00
2026-11-04 10:00,2026-11-04 10:10,S,0.10,4010.00,4012.00,-21.00
2026-11-03 09:00,2026-11-03 09:10,B,0.10,4000.00,4001.00,9.00
"""


def _bars(base=4000.0, step=0.0, start="2026-11-04 07:50", minutes=200):
    idx = pd.date_range(start, periods=minutes, freq="min", tz="UTC")
    return {t: base + step * i for i, t in enumerate(idx)}


def test_parse_new_csv_and_raw():
    t = sf.parse_new_csv(NEW)
    assert len(t) == 3 and t[0]["side"] == 1 and abs(t[0]["sonic_pts"] - 4.0) < 1e-9
    assert t[1]["side"] == -1 and abs(t[1]["sonic_pts"] - (-2.0)) < 1e-9          # sold 4010, closed 4012
    raw = f"{int(pd.Timestamp('2026-11-04 08:00', tz='UTC').timestamp() // 60)},20,B,0.1,4000,4004,39"
    r = sf.parse_raw(raw)[0]
    assert r["close"] - r["open"] == pd.Timedelta(minutes=20) and r["sonic_pts"] == 4.0


def test_follow_trade_uses_delayed_candle_opens_and_costs():
    trade = sf.parse_new_csv(NEW)[0]
    bars = _bars(step=0.5)                                  # +0.5 per minute
    res0 = sf.follow_trade(trade, bars, 0, fee=0.0, half_spread=0.0)
    assert abs(res0["gross_pts"] - 0.5 * 20) < 1e-9          # 20 minutes held at +0.5/min
    res3 = sf.follow_trade(trade, bars, 3, fee=0.0, half_spread=0.0)
    assert abs(res3["gross_pts"] - 10.0) < 1e-9              # same hold, shifted by 3 min
    cost = sf.follow_trade(trade, bars, 0)
    expected = res0["gross"] - (2 * sf.FEE * 1e4 + 2 * sf.HALF_SPREAD / res0["entry"] * 1e4)
    assert abs(cost["net"] - expected) < 1e-9 and cost["net"] < res0["gross"]
    short = sf.parse_new_csv(NEW)[1]
    assert sf.follow_trade(short, bars, 0, fee=0, half_spread=0)["gross_pts"] < 0   # short loses on a rising tape


def test_missing_candle_falls_back_then_gives_up():
    trade = sf.parse_new_csv(NEW)[0]
    bars = _bars(step=0.0)
    del bars[pd.Timestamp("2026-11-04 08:00", tz="UTC")]
    assert sf.follow_trade(trade, bars, 0) is not None                    # next minute is used
    for k in range(4):
        bars.pop(pd.Timestamp("2026-11-04 08:00", tz="UTC") + pd.Timedelta(minutes=k), None)
    assert sf.follow_trade(trade, bars, 0) is None


def test_day_clustered_t_treats_days_as_observations():
    s = pd.Series([1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    days = pd.Series(["a", "a", "b", "b", "c", "c"])
    assert math.isnan(sf.day_clustered_t(s, days))                       # constant daily totals -> zero variance
    s2 = pd.Series([2.0, 1.0, 3.0, 2.0, 5.0, 4.0, 1.0, 1.0])
    d2 = pd.Series(["a", "a", "b", "b", "c", "c", "d", "d"])
    assert sf.day_clustered_t(s2, d2) > 2


def test_update_logs_new_trades_once_and_skips_recent_and_old(tmp_path):
    f = tmp_path / "s.csv"
    now = datetime(2026, 11, 4, 10, 5, tzinfo=timezone.utc)               # 10:00 trade not old enough yet
    calls = []

    def fake(trade):
        calls.append(trade["open"])
        return _bars(step=0.1)
    out = sf.update(now, f, NEW, fake)
    assert out == ["logged 2026-11-04T08:00"]                              # 11-03 trade predates FORWARD_START
    assert sf.update(now, f, NEW, fake) == [] and len(calls) == 1          # idempotent
    later = datetime(2026, 11, 4, 11, 0, tzinfo=timezone.utc)
    assert sf.update(later, f, NEW, fake) == ["logged 2026-11-04T10:00"]
    rows = sf.read_rows(f)
    assert len(rows) == 2 and float(rows[0]["net_3"]) < float(rows[0]["gross_3"])


def test_report_empty_and_filled(tmp_path):
    f = tmp_path / "s.csv"
    assert "Dar nėra" in sf.report(f)
    sf.update(datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc), f, NEW, lambda t: _bars(step=0.2))
    txt = sf.report(f)
    assert "d=3 min" in txt and "pirminis" in txt and "Sonic paskelbtas" in txt
