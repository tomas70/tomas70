"""Tests for analysis/paper_tracker.py with injected daily closes."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analysis.paper_tracker as pt


def _closes(n=80, start="2026-07-01", btc_slope=0.003, n_coins=14):
    idx = pd.date_range(start, periods=n, freq="D", tz="UTC")
    cols = {"BTC": 100 * (1 + btc_slope) ** np.arange(n)}
    for i in range(n_coins - 1):
        cols[f"C{i}"] = 100 * (1 + 0.001 * (i + 1)) ** np.arange(n)
    return pd.DataFrame(cols, index=idx)


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_FILE", tmp_path / "paper.json")


def test_first_run_initialises_forward_only(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    msgs = pt.update(_closes())
    assert len(msgs) == 1 and "Paper tracker pradėtas" in msgs[0]
    s = pt._load()
    assert s["btc"]["equity"] == 100.0 and s["mom"]["positions"] is None
    assert pt.update(_closes()) == []            # same data -> nothing new


def test_forward_days_btc_compounds_and_mom_rebalances_on_sunday(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    full = _closes(n=120)
    pt.update(full.iloc[:70])                    # start at day 70
    msgs = pt.update(full)                       # 50 more days arrive
    s = pt._load()
    assert s["btc"]["equity"] > 100 and s["btc"]["pos"] == 1
    assert s["mom"]["positions"] is not None
    assert len(s["mom"]["periods"]) >= 5         # ~7 Sundays, first has no prior period
    assert any("MOM14" in m for m in msgs)
    assert s["mom"]["last_day"] == s["btc"]["last_day"] == pt._day(full.index[-1])


def test_momentum_basket_earns_on_trending_data(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    full = _closes(n=120)
    pt.update(full.iloc[:70])
    pt.update(full)
    s = pt._load()
    assert all(p["ret"] > 0 for p in s["mom"]["periods"])   # winners keep winning here
    top = s["mom"]["positions"]["longs"]
    assert "C12" in top and "C0" not in top


def test_btc_flip_costs_fee_and_messages(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    up = list(100 * 1.01 ** np.arange(70))
    down = list(up[-1] * 0.97 ** np.arange(1, 21))
    idx = pd.date_range("2026-07-01", periods=90, freq="D", tz="UTC")
    df = pd.DataFrame({"BTC": up + down}, index=idx)
    for i in range(13):
        df[f"C{i}"] = 100.0
    pt.update(df.iloc[:70])
    msgs = pt.update(df)
    s = pt._load()
    assert s["btc"]["pos"] == 0 and s["btc"]["flips"] >= 1
    assert any("FLAT" in m for m in msgs)


def test_report_before_and_after(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    assert "nepradėtas" in pt.format_report()
    full = _closes(n=120)
    pt.update(full.iloc[:70]); pt.update(full)
    rep = pt.format_report()
    assert "BTC T50" in rep and "MOM14" in rep and "buy&hold" in rep
