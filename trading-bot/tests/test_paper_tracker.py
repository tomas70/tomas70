"""Tests for analysis/paper_tracker.py (BTC T50 only) with injected closes."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analysis.paper_tracker as pt


def _closes(prices, start="2026-07-01"):
    idx = pd.date_range(start, periods=len(prices), freq="D", tz="UTC")
    return pd.DataFrame({"BTC": prices}, index=idx)


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_FILE", tmp_path / "paper.json")


def test_first_run_initialises_forward_only(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    df = _closes(list(100 * 1.003 ** np.arange(80)))
    msgs = pt.update(df)
    assert len(msgs) == 1 and "Paper tracker pradėtas" in msgs[0] and "LONG" in msgs[0]
    assert pt._load()["btc"]["equity"] == 100.0
    assert pt.update(df) == []                  # same data -> nothing new


def test_equity_compounds_while_long(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    full = _closes(list(100 * 1.003 ** np.arange(120)))
    pt.update(full.iloc[:70])
    pt.update(full)
    s = pt._load()["btc"]
    expected = 100 * (full["BTC"].iloc[-1] / full["BTC"].iloc[69])
    assert abs(s["equity"] - expected) < 1e-6 and s["pos"] == 1 and s["flips"] == 0


def test_flip_to_flat_costs_fee_and_stops_exposure(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    up = list(100 * 1.01 ** np.arange(70))
    down = list(up[-1] * 0.97 ** np.arange(1, 21))
    df = _closes(up + down)
    pt.update(df.iloc[:70])
    msgs = pt.update(df)
    s = pt._load()["btc"]
    assert s["pos"] == 0 and s["flips"] == 1 and any("FLAT" in m for m in msgs)
    eq_flat = s["equity"]
    # more falling days while flat must not change equity
    more = _closes(up + down + list(down[-1] * 0.97 ** np.arange(1, 6)))
    pt.update(more)
    assert abs(pt._load()["btc"]["equity"] - eq_flat) < 1e-9


def test_old_state_with_mom_sleeve_is_tolerated(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    df = _closes(list(100 * 1.003 ** np.arange(80)))
    pt.update(df)
    state = json.loads(pt.STATE_FILE.read_text())
    state["mom"] = {"equity": 99.0, "periods": []}
    pt.STATE_FILE.write_text(json.dumps(state))
    assert pt.update(df) == []
    assert "mom" not in json.loads(pt.STATE_FILE.read_text())


def test_report_before_and_after(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    assert "nepradėtas" in pt.format_report()
    pt.update(_closes(list(100 * 1.003 ** np.arange(80))))
    rep = pt.format_report()
    assert "BTC T50" in rep and "buy&hold" in rep and "MOM" not in rep
