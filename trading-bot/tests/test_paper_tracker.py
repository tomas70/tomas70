"""Tests for analysis/paper_tracker.py (BTC T50 only) with injected closes."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import analysis.paper_tracker as pt


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Funding lookups must never hit Deribit from tests; individual tests inject their own."""
    monkeypatch.setattr(pt, "_funding_daily", lambda cur, a, b: {})


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


# ── funding (perp equity) and TREND-3 ─────────────────────────────────────

def _const_funding(rate):
    def fn(cur, start, end):
        return {d.strftime("%Y-%m-%d"): rate for d in pd.date_range(start, end, freq="D")}
    return fn


def test_perp_equity_pays_funding_only_while_long(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    up = list(100 * 1.01 ** np.arange(70))
    down = list(up[-1] * 0.97 ** np.arange(1, 21))
    df = _closes(up + down)
    pt.update(df.iloc[:70], funding=_const_funding(0.001))
    pt.update(df, funding=_const_funding(0.001))
    s = pt._load()["btc"]
    assert s["equity_perp"] < s["equity"]                                  # funding paid while long
    assert s["funding_paid"] > 0 and s["funding_missing"] == 0
    paid = s["funding_paid"]
    eq_perp = s["equity_perp"]
    more = _closes(up + down + list(down[-1] * 0.97 ** np.arange(1, 6)))
    pt.update(more, funding=_const_funding(0.001))                         # flat days: no funding, no exposure
    s2 = pt._load()["btc"]
    assert abs(s2["funding_paid"] - paid) < 1e-12 and abs(s2["equity_perp"] - eq_perp) < 1e-9


def test_old_state_without_perp_fields_is_upgraded(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    full = _closes(list(100 * 1.003 ** np.arange(120)))
    pt.update(full.iloc[:70])
    state = json.loads(pt.STATE_FILE.read_text())
    for k in ("equity_perp", "funding_paid", "funding_missing", "funding_since"):
        state["btc"].pop(k, None)
    pt.STATE_FILE.write_text(json.dumps(state))
    pt.update(full, funding=_const_funding(0.0005))
    s = pt._load()["btc"]
    assert s["equity_perp"] < s["equity"] and s["funding_since"] == "2026-09-08"       # baseline = day before upgrade


def test_unknown_funding_is_counted_not_invented(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    full = _closes(list(100 * 1.003 ** np.arange(100)))
    pt.update(full.iloc[:70])
    pt.update(full)                                                         # autouse stub returns {} -> unknown
    s = pt._load()["btc"]
    assert s["funding_missing"] == 30 and s["funding_paid"] == 0 and abs(s["equity_perp"] - s["equity"]) < 1e-9


def _three(n, gold_trend=0.0005, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-03-01", periods=n, freq="D", tz="UTC")
    vols = {"BTC": 0.03, "ETH": 0.04, "GOLD": 0.01}
    drift = {"BTC": 0.002, "ETH": 0.002, "GOLD": gold_trend}
    return pd.DataFrame({k: 100 * np.exp(np.cumsum(rng.normal(drift[k], v, n))) for k, v in vols.items()}, index=idx)


def test_trend3_needs_three_assets_and_history(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    btc_only = _closes(list(100 * 1.003 ** np.arange(80)))
    pt.update(btc_only)
    assert "t3" not in pt._load()
    short = _three(40)
    pt.update(short)
    assert "t3" not in pt._load()


def test_trend3_init_weights_and_report(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    df = _three(100)
    msgs = pt.update(df.iloc[:80])
    assert any("TREND-3 paper pradėtas" in m for m in msgs)
    t3 = pt._load()["t3"]
    assert sum(t3["w"].values()) <= 1 + 1e-9 and sum(t3["w_bh"].values()) > 0.999     # IV-B&H fully invested
    assert t3["equity"] == 100.0 and t3["flips"] == 0
    rep = pt.format_report()
    assert "TREND-3" in rep and "IV-B&H" in rep and "perp" in rep


def test_trend3_advances_with_funding_on_crypto_legs_and_is_idempotent(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    df = _three(130)
    pt.update(df.iloc[:80], funding=_const_funding(0.0))
    pt.update(df, funding=_const_funding(0.002))
    t3 = pt._load()["t3"]
    assert t3["last_day"] == df.index[-1].strftime("%Y-%m-%d")
    assert t3["equity_perp"] < t3["equity"]                                  # crypto longs paid funding
    saved = dict(t3)
    assert pt.update(df, funding=_const_funding(0.002)) == [] and pt._load()["t3"]["equity"] == saved["equity"]
    # no funding on a gold-only book: if only gold has weight, perp == spot
    d2 = df.copy()
    d2["BTC"] = 100 * np.exp(-0.01 * np.arange(len(d2)))                   # BTC and ETH in a downtrend -> flat
    d2["ETH"] = 100 * np.exp(-0.01 * np.arange(len(d2)))
    _fresh(tmp_path, monkeypatch)
    monkeypatch.setattr(pt, "STATE_FILE", tmp_path / "paper2.json")
    pt.update(d2.iloc[:80], funding=_const_funding(0.0))
    pt.update(d2, funding=_const_funding(0.01))
    t = pt._load()["t3"]
    assert t["w"]["BTC"] == 0 and t["w"]["ETH"] == 0 and abs(t["equity_perp"] - t["equity"]) < 1e-9


def test_trend3_signal_flip_sends_message_and_costs_fee(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    n = np.arange(80)
    idx = pd.date_range("2026-03-01", periods=80, freq="D", tz="UTC")
    amp = {"BTC": 0.02, "ETH": 0.03, "GOLD": 0.005}
    up = pd.DataFrame({k: 100 * 1.004 ** n * (1 + a * np.sin(n)) for k, a in amp.items()}, index=idx)   # deterministic uptrend
    pt.update(up)
    t0 = pt._load()["t3"]
    assert all(t0["sig"].values()) and t0["flips"] == 0
    ext_idx = pd.date_range(idx[-1] + pd.Timedelta(days=1), periods=29, freq="D", tz="UTC")
    ext = pd.DataFrame({k: up[k].iloc[-1] * 0.96 ** np.arange(1, 30) for k in amp}, index=ext_idx)   # sustained crash
    msgs = pt.update(pd.concat([up, ext]))
    assert any("signalai pasikeitė" in m for m in msgs)
    t3 = pt._load()["t3"]
    assert t3["flips"] >= 1 and sum(t3["w"].values()) < 0.5
    assert t3["equity"] < 100 * 3 and t3["equity"] > 0


def test_funding_daily_keeps_only_complete_days(monkeypatch):
    rows = []
    day1 = pd.Timestamp("2026-10-01", tz="UTC")
    for h in range(24):
        rows.append({"timestamp": int((day1 + pd.Timedelta(hours=h)).timestamp() * 1000), "interest_1h": 0.00001})
    for h in range(10):                                                    # incomplete day
        rows.append({"timestamp": int((day1 + pd.Timedelta(days=1, hours=h)).timestamp() * 1000), "interest_1h": 0.00002})

    class R:
        def raise_for_status(self): pass
        def json(self): return {"result": rows}
    monkeypatch.undo()                                                     # restore real _funding_daily, stub only the HTTP call
    import analysis.paper_tracker as real
    monkeypatch.setattr(real.httpx, "get", lambda *a, **k: R())
    out = real._funding_daily("BTC", pd.Timestamp("2026-10-01", tz="UTC"), pd.Timestamp("2026-10-02", tz="UTC"))
    assert list(out) == ["2026-10-01"] and abs(out["2026-10-01"] - 24e-5) < 1e-12


def test_funding_daily_chunk_boundaries_do_not_lose_the_first_hour(monkeypatch):
    stamps = pd.date_range("2026-09-30 01:00", "2026-10-26 00:00", freq="h", tz="UTC")       # Deribit-style hourly stamps
    data = [{"timestamp": int(x.timestamp() * 1000), "interest_1h": 1e-5} for x in stamps]

    class R:
        def __init__(self, rows): self.rows = rows
        def raise_for_status(self): pass
        def json(self): return {"result": self.rows}

    def fake_get(url, params=None, timeout=None):
        a, b = params["start_timestamp"], params["end_timestamp"]
        return R([r for r in data if a < r["timestamp"] <= b])            # start exclusive, end inclusive
    monkeypatch.undo()
    import analysis.paper_tracker as real
    monkeypatch.setattr(real.httpx, "get", fake_get)
    out = real._funding_daily("BTC", pd.Timestamp("2026-10-01", tz="UTC"), pd.Timestamp("2026-10-25", tz="UTC"))
    assert len(out) == 25 and all(abs(v - 24e-5) < 1e-12 for v in out.values())
