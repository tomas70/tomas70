"""Tests for the signal parser and evaluator (research_kralov*.py)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import research_kralov as rk
import research_kralov_eval as ev

SIGNAL_TEXT = """📌
#DOGEUSD
.P ПРОДАЖА
Диапазон входа:
0.094054-0.093546
Тейк-профит 1: 0.092784
Тейк-профит 2: 0.092276
Тейк-профит 3: 0.09126
Тейк-профит 4: 0.089228
Тейк-профит 5: 0.085164
⚠️ Стоп-лосс: 0.09634
Открыть в EVEDEX"""


def test_parse_signal_and_template():
    s = rk.parse_signal(SIGNAL_TEXT)
    assert s["instrument"] == "DOGEUSD" and s["side"] == "short"
    assert s["zone_lo"] == 0.093546 and s["zone_hi"] == 0.094054 and s["sl"] == 0.09634
    mid = (s["zone_lo"] + s["zone_hi"]) / 2
    u = (s["zone_hi"] - s["zone_lo"]) / 2
    assert [round((mid - t) / u, 1) for t in s["tps"]] == [4.0, 6.0, 10.0, 18.0, 34.0]
    assert round((s["sl"] - mid) / u, 1) == 10.0


def test_parse_updates_and_aliases():
    tp = rk.parse_update("💵 \n#1000PEPEUSDT\n.P\nТП1 0.0039944\nПрибыль: 0.50%")
    assert tp == {"kind": "tp", "instrument": "1000PEPEUSD", "n": 1, "price": 0.0039944, "profit_pct": 0.5}
    cl = rk.parse_update("❗️\n#SUIUSDT\n.P Закрыто из-за обратного сигнала!\nЦена закрытия: 0.9195\nПрибыль: -0.65%")
    assert cl["kind"] == "close" and cl["price"] == 0.9195 and cl["profit_pct"] == -0.65
    assert rk._instrument("#PUMPUSDT") == "PUMPFUNUSD"
    assert rk.parse_signal("не сигнал") is None


def _candles(bars, start="2026-06-01 00:00"):
    ts = pd.date_range(start, periods=len(bars), freq="15min", tz="UTC")
    return ev.Candles(pd.DataFrame({"timestamp": ts, "open": [b[0] for b in bars],
                                    "high": [b[1] for b in bars], "low": [b[2] for b in bars],
                                    "close": [b[3] for b in bars]}))


def _sig(side="long", T="2026-06-01 00:15", T2=None):
    # long: entry ~100, SL 99 (1R), TPs at 0.4/0.6/1.0/1.8/3.4 R
    base = dict(instrument="X", side=side, zone_lo=99.9, zone_hi=100.1, utc=pd.Timestamp(T, tz="UTC"), T2=T2)
    if side == "long":
        return {**base, "sl": 99.0, "tps": [100.4, 100.6, 101.0, 101.8, 103.4]}
    return {**base, "sl": 101.0, "tps": [99.6, 99.4, 99.0, 98.2, 96.6]}


def _flat(n, p=100.0):
    return [(p, p + 0.05, p - 0.05, p)] * n


def test_market_entry_full_ladder_win():
    bars = _flat(2) + [(100, 103.5, 99.95, 103)] + _flat(700, 103)
    r = ev.simulate(_candles(bars), _sig(), "M0")
    assert isinstance(r, dict)
    assert abs(r["gross"] - 0.2 * (0.4 + 0.6 + 1.0 + 1.8 + 3.4)) < 1e-9
    assert r["net"] < r["gross"] and all(lv == 1 for lv in r["levels"])


def test_stop_first_when_same_bar_and_gap_through_stop():
    bars = _flat(2) + [(100, 103.5, 98.9, 100)] + _flat(700)
    r = ev.simulate(_candles(bars), _sig(), "M0")
    assert abs(r["gross"] - (-1.0)) < 1e-9 and all(lv == 0 for lv in r["levels"])
    gap = _flat(2) + [(98.0, 98.5, 97.5, 98.0)] + _flat(700, 98)
    r2 = ev.simulate(_candles(gap), _sig(), "M0")
    assert r2["gross"] < -1.9                       # filled at the gap open, not at the stop


def test_forced_exit_on_opposite_signal_and_short_side():
    bars = _flat(2) + [(100, 100.45, 99.9, 100.3)] + [(100.3, 100.4, 100.2, 100.3)] * 5 + _flat(700, 100.3)
    T2 = pd.Timestamp("2026-06-01 01:30", tz="UTC")       # bar index 6
    r = ev.simulate(_candles(bars), _sig(T2=T2), "M0")
    assert abs(r["gross"] - (0.2 * 0.4 + 0.8 * 0.3)) < 1e-9     # TP1 hit, rest closed at 100.3 open
    short = _flat(2) + [(100, 100.05, 96.5, 97)] + _flat(700, 97)
    rs = ev.simulate(_candles(short), _sig("short"), "M0")
    assert abs(rs["gross"] - 0.2 * (0.4 + 0.6 + 1.0 + 1.8 + 3.4)) < 1e-9


def test_limit_mode_missed_unfilled_and_fill():
    run_away = [(100.5, 100.5, 100.45, 100.5)] * 2 + [(100.5, 101.1, 100.45, 101.0)] + _flat(5, 101)
    assert ev.simulate(_candles(run_away), _sig(), "L") == "missed"
    far = [(100.3, 100.35, 100.2, 100.3)] * 20       # never reaches the limit (100.0) nor TP1 (100.4)
    assert ev.simulate(_candles(far), _sig(), "L") == "unfilled"
    dip = [(100.5, 100.55, 100.45, 100.5), (100.5, 100.6, 99.95, 100.4)] + [(100.4, 101.1, 100.3, 101)] + _flat(700, 101)
    r = ev.simulate(_candles(dip), _sig(), "L")
    assert isinstance(r, dict) and r["gross"] > 0


def test_skips_stale_and_invalid():
    stale = _flat(2) + [(100.5, 100.6, 100.4, 100.5)] + _flat(700, 100.5)    # open already beyond TP1 (100.4)
    assert ev.simulate(_candles(stale), _sig(T="2026-06-01 00:30"), "M0") == "stale"
    bad = _flat(2) + [(98.5, 98.6, 98.4, 98.5)] + _flat(700, 98.5)
    assert ev.simulate(_candles(bad), _sig(T="2026-06-01 00:30"), "M0") == "invalid_sl"
    short_data = _flat(5)
    assert ev.simulate(_candles(short_data), _sig(), "M0") == "open"


def test_z_skill_neutral_on_geometric_hit_rate():
    p = 10 / 14
    hits = [1] * 71 + [0] * 29
    df = pd.DataFrame({"levels": [[h, h, h, h, h] for h in hits], "p_geo": [[p] * 5 for _ in hits]})
    z, rate, n = ev.z_skill(df, 0)
    assert n == 100 and abs(rate - 0.71) < 1e-9 and abs(z) < 0.5
