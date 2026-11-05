"""Mechanics tests for research_gold_intraday rules on synthetic 15m bars."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import research_gold_intraday as gi


def _day(prices_by_hhmm: dict, date="2024-03-05", base=2000.0):
    idx = pd.date_range(f"{date} 00:00", periods=96, freq="15min", tz="UTC")
    o = np.full(96, base)
    for hhmm, p in prices_by_hhmm.items():
        h, m = map(int, hhmm.split(":"))
        o[h * 4 + m // 15:] = p
    df = pd.DataFrame({"o": o, "c": o, "h": o + 0.5, "l": o - 0.5}, index=idx)
    return df


def test_r2_follows_morning_move_and_r3_fades_it():
    day = _day({"00:00": 2000, "08:00": 2010, "12:00": 2020, "16:00": 2015})
    side, e, x = gi.rule_r2(day)
    assert side == 1 and e == 2010 and x == 2020                          # up morning -> long 08:00..12:00
    side, e, x = gi.rule_r3(day)
    assert side == -1 and e == 2020 and x == 2015                        # up 08-12 -> fade to 16:00


def test_r1_breaks_range_and_stops_at_opposite_side():
    day = _day({})
    base = day.index[0]
    for i in range(28, 32):                                              # 07:00-07:45 range: h 2000.5 l 1999.5
        pass
    day.loc[base + pd.Timedelta(hours=8, minutes=15), ["o", "c", "h", "l"]] = [2000, 2003, 2003.5, 2000]   # closes above range
    day.loc[day.index >= base + pd.Timedelta(hours=8, minutes=30), ["o", "c", "h", "l"]] = [2003, 2003, 2003.5, 2002.5]
    side, e, x = gi.rule_r1(day)
    assert side == 1 and e == 2003 and x == 2003                         # no stop, exits 12:00 open
    stopped = day.copy()
    stopped.loc[stopped.index == base + pd.Timedelta(hours=9), ["o", "c", "h", "l"]] = [2003, 1990, 2003, 1989]
    side, e, x = gi.rule_r1(stopped)
    assert side == 1 and x == 1999.5                                     # stopped at the range low


def test_r4_fades_hour_impulse_with_stop_and_timeout():
    day = _day({"05:00": 2000})
    base = day.index[0]
    for k in range(4):                                                   # +0.5% in the 05:00-05:45 hour
        ts = base + pd.Timedelta(hours=5, minutes=15 * k)
        day.loc[ts, ["o", "c", "h", "l"]] = [2000 + 2.5 * k, 2000 + 2.5 * (k + 1), 2000 + 2.5 * (k + 1), 2000 + 2.5 * k]
    day.loc[day.index >= base + pd.Timedelta(hours=6), ["o", "c", "h", "l"]] = [2010, 2010, 2010.5, 2009.5]
    side, e, x = gi.rule_r4(day)
    assert side == -1 and e == 2010 and x == 2010                        # faded the up-impulse, timed exit 2 h later


def test_tstat_and_describe_costs():
    t = pd.DataFrame({"gross": [10.0, 12.0, 8.0, 11.0, 9.0] * 20, "side": 1}, index=pd.date_range("2022-01-03", periods=100, freq="B"))
    r = gi.describe("x", t, 1.5)
    assert abs(r["mean"] - 8.5) < 1e-9 and r["t"] > 10
