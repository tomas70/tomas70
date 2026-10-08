"""Tests for research_vp_reactions event detection and resolution."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import research_vp_reactions as rv

PROF = {"poc": 100.0, "vah": 102.0, "val": 98.0}


def _df(bars):
    """(open, high, low, close) per bar, padded so a full window exists."""
    rows = [dict(timestamp=pd.Timestamp("2026-10-01", tz="UTC") + pd.Timedelta(minutes=15 * i),
                 open=o, high=h, low=l, close=c, volume=1.0) for i, (o, h, l, c) in enumerate(bars)]
    return pd.DataFrame(rows)


def _pad(bars, price):
    return bars + [(price, price + 0.01, price - 0.01, price)] * (rv.WINDOW + 2)


def test_vah_rejection_short_hits_poc():
    # bar1 pokes above VAH and closes back inside; entry bar2 open 101.9; SL 102.6
    bars = [(101, 101.5, 100.9, 101.4), (101.4, 102.6, 101.3, 101.9),
            (101.9, 101.95, 100.0, 100.1)]
    df = _df(_pad(bars, 100.1))
    evs = rv.find_events(df, PROF, 0, 3)
    ev = [e for e in evs if e["type"] == "VAH_REJ"][0]
    assert ev["bias"] == "bearish" and ev["entry_idx"] == 2 and ev["sl"] == 102.6
    res = rv.resolve(df, ev, "POC")
    assert res["win"] and res["gross"] > 0


def test_val_rejection_long_stopped():
    bars = [(99, 99.2, 98.5, 98.9), (98.9, 99.0, 97.4, 98.2), (98.2, 98.3, 97.3, 97.5)]
    df = _df(_pad(bars, 97.5))
    ev = [e for e in rv.find_events(df, PROF, 0, 3) if e["type"] == "VAL_REJ"][0]
    res = rv.resolve(df, ev, "2R")
    assert res["gross"] == -1.0 and not res["win"]


def test_acceptance_needs_two_closes():
    bars = [(101.5, 101.9, 101.4, 101.8), (101.8, 102.6, 101.7, 102.4),
            (102.4, 102.9, 102.3, 102.7), (102.7, 103.0, 102.6, 102.9)]
    df = _df(_pad(bars, 102.9))
    ev = [e for e in rv.find_events(df, PROF, 0, 4) if e["type"] == "VAH_ACC"]
    assert ev and ev[0]["entry_idx"] == 3 and ev[0]["sl"] == 102.0


def test_tight_stop_and_short_window_rejected():
    bars = [(101, 101.5, 100.9, 101.4), (101.4, 102.05, 101.3, 101.9), (101.9, 102.0, 101.8, 101.9)]
    df = _df(_pad(bars, 101.9))
    ev = [e for e in rv.find_events(df, PROF, 0, 3) if e["type"] == "VAH_REJ"][0]
    assert rv.resolve(df, ev, "2R") is None          # stop 0.15% < 0.25%
    short = _df(bars)
    assert rv.resolve(short, dict(ev, sl=103.0), "2R") is None   # no full window


def test_analyze_day_clustering():
    d = pd.Timestamp
    recs = [dict(date=d("2026-01-%02d" % (i % 28 + 1)), gross=0.5, net=0.4, win=True) for i in range(100)]
    a = rv.analyze(recs)
    assert a["events"] == 100 and a["days"] == 28 and a["net"] == 0.4
    assert not a["passes"]            # 28 days < 40
