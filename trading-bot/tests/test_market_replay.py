"""Tests for replay_market_entry.market_entry_walk / summarize."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from replay_market_entry import market_entry_walk, summarize


def _c(rows):
    """(open, high, low) per 15m candle."""
    return pd.DataFrame([
        {"timestamp": pd.Timestamp("2026-10-01", tz="UTC") + pd.Timedelta(minutes=15 * i),
         "open": o, "high": h, "low": l, "close": o}
        for i, (o, h, l) in enumerate(rows)
    ])


def test_long_tp():
    # entry 100, sl 99.5 (0.5% risk) -> R:R 4; tp 102
    r = market_entry_walk(_c([(100, 101, 99.8), (101, 102.5, 100.5)]), "bullish", 99.5)
    assert r["status"] == "tp1_hit" and abs(r["r"] - 4.0) < 1e-9


def test_long_sl():
    r = market_entry_walk(_c([(100, 100.5, 99.4)]), "bullish", 99.5)
    assert r["status"] == "sl_hit" and r["r"] == -1.0


def test_same_candle_both_is_sl():
    r = market_entry_walk(_c([(100, 103, 99.0)]), "bullish", 99.5)
    assert r["status"] == "sl_hit"


def test_delay_uses_later_open():
    r = market_entry_walk(_c([(100, 100.2, 99.9), (101, 101.1, 100.9), (101, 103.5, 100.9)]),
                          "bullish", 100.5, delay=1)
    assert r["status"] == "tp1_hit"   # entry 101, tp 103.02


def test_invalid_when_price_through_stop():
    r = market_entry_walk(_c([(99, 99.5, 98.5)]), "bullish", 99.5)
    assert r["status"] == "invalid"


def test_low_rr_filtered():
    # risk 2% -> R:R 1 < 2
    r = market_entry_walk(_c([(100, 101, 99.9)]), "bullish", 98.0)
    assert r["status"] == "low_rr"


def test_bearish_tp():
    r = market_entry_walk(_c([(100, 100.2, 97.5)]), "bearish", 100.5)
    assert r["status"] == "tp1_hit" and abs(r["r"] - 4.0) < 1e-9


def test_pending_and_expired():
    assert market_entry_walk(_c([(100, 100.5, 99.8)]), "bullish", 99.5)["status"] == "pending"
    assert market_entry_walk(_c([(100, 100.5, 99.8)] * 3), "bullish", 99.5,
                             max_candles=3)["status"] == "expired"


def test_summarize_net_of_fee():
    res = [
        {"status": "tp1_hit", "r": 4.0, "risk_pct": 0.005},
        {"status": "sl_hit", "r": -1.0, "risk_pct": 0.005},
        {"status": "pending", "r": None, "risk_pct": None},
    ]
    s = summarize(res, fee_pct=0.08)   # fee = 0.16R at 0.5% risk
    assert s["n"] == 2 and s["win_rate"] == 50.0
    assert s["gross_R"] == 1.5 and s["net_R"] == 1.34 and s["pending"] == 1
