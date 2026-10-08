"""
Tests for limit-order fill modeling: simulate_limit_walk, the fill-rate
fields in get_stats, and the one-time restate_fills repair.
"""
import csv
import random
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analysis.trade_logger as tl

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _df(candles: list[tuple[float, float]], start=T0) -> pd.DataFrame:
    """(high, low) pairs, one 15m candle each, first one after `start`."""
    rows = []
    for i, (hi, lo) in enumerate(candles):
        rows.append({
            "timestamp": pd.Timestamp(start + timedelta(minutes=15 * (i + 1))),
            "open": lo, "high": hi, "low": lo, "close": hi, "volume": 1.0,
        })
    return pd.DataFrame(rows)


def _long(candles, max_candles=192):
    return tl.simulate_limit_walk(_df(candles), T0, "bullish", 100, 102, 99, max_candles)


def test_fill_then_tp():
    # c1 away, c2 touches entry, c3 reaches TP
    assert _long([(101, 100.5), (101, 99.9), (102.5, 101)]) == ("tp1_hit", str(_df([(0, 0)] * 3).iloc[2]["timestamp"]), 3, 2)


def test_tp_before_touch_is_missed():
    status, _, candles, fill = _long([(101, 100.5), (102.5, 101)])
    assert (status, candles, fill) == ("missed", 2, None)


def test_unfilled_at_window_end():
    status, _, candles, fill = _long([(101, 100.5)] * 3, max_candles=3)
    assert (status, candles, fill) == ("unfilled", 3, None)


def test_pending_when_window_not_over():
    assert _long([(101, 100.5)] * 3, max_candles=10) == (None, None, None, None)


def test_no_forward_data():
    empty = pd.DataFrame({"timestamp": pd.to_datetime([], utc=True), "high": [], "low": []})
    assert tl.simulate_limit_walk(empty, T0, "bullish", 100, 102, 99) == (None, None, None, None)


def test_fill_candle_can_stop_out():
    status, _, candles, fill = _long([(101, 98.5)])
    assert (status, candles, fill) == ("sl_hit", 1, 1)


def test_tp_on_fill_candle_not_credited():
    # fill candle touches entry and TP; next candle is quiet -> expired
    status, _, candles, fill = _long([(103, 99.5), (101, 100)], max_candles=2)
    assert (status, candles, fill) == ("expired", 2, 1)


def test_tp_and_sl_same_candle_after_fill_credits_tp():
    status, _, _, fill = _long([(101, 99.5), (103, 98.5)])
    assert (status, fill) == ("tp1_hit", 1)


def test_bearish_mirror():
    df = _df([(99.5, 98.5), (100.2, 99.0), (99.0, 97.5)])
    status, _, candles, fill = tl.simulate_limit_walk(df, T0, "bearish", 100, 98, 101)
    assert (status, candles, fill) == ("tp1_hit", 3, 2)

    df = _df([(99.5, 97.5)])
    status, _, candles, fill = tl.simulate_limit_walk(df, T0, "bearish", 100, 98, 101)
    assert (status, candles, fill) == ("missed", 1, None)


# ── helpers for log-backed tests ──────────────────────────────────────────

def _row(**kw) -> dict:
    base = {c: "" for c in tl.COLUMNS}
    base.update({
        "id": str(random.randint(0, 10**8)),
        "logged_at": T0.isoformat(),
        "pair": "SOL", "bias": "bullish", "entry_type": "SWEEP",
        "entry": "100", "sl": "99", "tp1": "102", "tp2": "105", "rr_ratio": "2.0",
        "status": "pending",
    })
    base.update(kw)
    return base


def _write(rows: list[dict]) -> Path:
    path = Path(tempfile.mkdtemp()) / "trade_log.csv"
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=tl.COLUMNS)
        w.writeheader()
        w.writerows(rows)
    tl.LOG_FILE = path
    return path


def test_stats_fill_rate():
    tl.STRATEGY_EPOCH = "2020-01-01T00:00:00+00:00"
    statuses = ["tp1_hit"] * 3 + ["sl_hit"] + ["missed"] * 2 + ["unfilled"] + ["pending"]
    _write([_row(status=s) for s in statuses])
    s = tl.get_stats(all_time=True)
    assert (s["filled"], s["missed"], s["unfilled"], s["pending"]) == (4, 2, 1, 1)
    assert s["fill_rate"] == 57.1
    assert s["win_rate"] == 75.0   # filled trades only


def test_restate_fills(monkeypatch):
    monkeypatch.setattr(tl, "STRATEGY_EPOCH", "2020-01-01T00:00:00+00:00")
    logged = datetime.now(timezone.utc) - timedelta(days=1)
    # legacy tp1_hit whose entry never traded, plus a genuine winner
    missed_row = _row(id="m", logged_at=logged.isoformat(), pair="AAA", status="tp1_hit")
    real_row   = _row(id="r", logged_at=logged.isoformat(), pair="BBB", status="tp1_hit")
    path = _write([missed_row, real_row])

    candles = {
        "AAA": _df([(101, 100.5), (102.5, 101)], start=logged),
        "BBB": _df([(101, 99.9), (102.5, 101)], start=logged),
    }
    monkeypatch.setattr(tl, "get_candles_since", lambda pair, tf, since: candles[pair])

    assert tl.restate_fills() == 2
    rows = {r["id"]: r for r in tl._read_rows()}
    assert rows["m"]["status"] == "missed" and rows["m"]["fill_candles"] == ""
    assert rows["r"]["status"] == "tp1_hit" and rows["r"]["fill_candles"] == "1"
    assert path.with_name("trade_log.pre-fill-restate.csv").exists()

    # idempotent: rows with fill_candles or non-filled status are skipped.
    # The missed row has a blank fill_candles but status "missed" -> not a target.
    assert tl.restate_fills() == 0
