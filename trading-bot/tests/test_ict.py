"""
Deterministic tests for analysis/ict.py and the funding sign convention.

Synthetic candles, no network: the Hyperliquid endpoints are unreachable
from some environments, and these are checks of the arithmetic and the
structure rules, which do not need live data to be wrong.

Run:  .venv/bin/python -m pytest tests/test_ict.py -q
  or: .venv/bin/python tests/test_ict.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.ict import OTE_MAX, OTE_MIN, describe_ict_context, detect_mss, ote_position, session_of


def _df(rows: list[tuple[float, float, float, float]], start="2026-09-10T00:00:00Z") -> pd.DataFrame:
    """rows = [(open, high, low, close)] on a 15m grid."""
    ts = pd.date_range(start=start, periods=len(rows), freq="15min", tz="UTC")
    return pd.DataFrame({
        "timestamp": ts,
        "open":  [r[0] for r in rows],
        "high":  [r[1] for r in rows],
        "low":   [r[2] for r in rows],
        "close": [r[3] for r in rows],
        "volume": [1.0] * len(rows),
    })


def _bullish_sweep_scenario() -> tuple[pd.DataFrame, int]:
    """
    Sell-side sweep of 95.0 at index 20, displacement up, then a close
    above the pre-sweep swing high (101.0) = MSS, then a retrace.

    Returns (df, sweep_index).
    """
    rows: list[tuple[float, float, float, float]] = []

    # 0-7: drift down, no structure of note
    for i in range(8):
        p = 100.0 - i * 0.3
        rows.append((p, p + 0.2, p - 0.2, p - 0.1))

    # 8: the swing high MSS must later break. Needs 3 clear bars each side.
    rows.append((98.0, 101.0, 97.8, 100.5))

    # 9-19: decline into the level, strictly lower highs so idx 8 stays the
    # last swing high before the sweep
    for i in range(11):
        p = 99.0 - i * 0.32
        rows.append((p, p + 0.15, p - 0.25, p - 0.15))

    # 20: SWEEP — wicks below 95.0, closes back above it
    rows.append((95.5, 96.2, 93.0, 96.0))
    # 21: displacement up
    rows.append((96.0, 99.6, 95.9, 99.5))
    # 22-24: continuation through 101.0 -> MSS confirms here
    rows.append((99.5, 101.8, 99.4, 101.5))
    rows.append((101.5, 102.5, 101.0, 102.2))
    rows.append((102.2, 102.5, 101.5, 101.8))
    # 25-29: retrace back toward the OTE band
    for p in (101.0, 100.0, 99.0, 98.0, 97.5):
        rows.append((p + 0.3, p + 0.4, p - 0.3, p))

    return _df(rows), 20


def test_mss_detected_at_the_break_of_the_pre_sweep_swing_high():
    df, sweep_i = _bullish_sweep_scenario()
    mss = detect_mss(df, sweep_i, "bullish")

    assert mss is not None, "a close above the pre-sweep swing high must register"
    assert mss.level == 101.0, f"wrong reference swing: {mss.level}"
    assert mss.index == 22, f"MSS should confirm on the first close above 101.0, got {mss.index}"
    assert mss.candles_ago == len(df) - 1 - 22


def test_no_mss_when_price_never_closes_back_through_structure():
    """A sweep that bounces weakly is exactly the case MSS is meant to reject."""
    df, sweep_i = _bullish_sweep_scenario()
    weak = df.iloc[: sweep_i + 3].copy()          # sweep + displacement only
    weak.loc[sweep_i + 1, ["high", "close"]] = [97.0, 96.8]
    weak.loc[sweep_i + 2, ["high", "close"]] = [97.2, 96.9]

    assert detect_mss(weak, sweep_i, "bullish") is None


def test_ote_retracement_is_measured_from_the_impulse_extreme():
    df, sweep_i = _bullish_sweep_scenario()

    leg_start, leg_end = 93.0, 102.5              # sweep low -> impulse high
    leg = leg_end - leg_start

    at_071 = leg_end - 0.71 * leg
    ote = ote_position(df, sweep_i, "bullish", at_071)
    assert ote is not None
    assert abs(ote["retracement"] - 0.71) < 1e-6
    assert ote["in_ote"] is True
    assert abs(ote["ote_price"] - at_071) < 1e-6
    assert ote["leg_start"] == leg_start and ote["leg_end"] == leg_end

    # A shallow entry (chasing the impulse) must fall outside the band
    shallow = ote_position(df, sweep_i, "bullish", leg_end - 0.20 * leg)
    assert shallow["in_ote"] is False
    assert shallow["retracement"] < OTE_MIN

    # A deep entry (past the sweep's own 79%) likewise
    deep = ote_position(df, sweep_i, "bullish", leg_end - 0.95 * leg)
    assert deep["in_ote"] is False
    assert deep["retracement"] > OTE_MAX


def test_ote_bearish_mirrors_bullish():
    rows = [(100.0, 100.5, 99.5, 100.0)] * 10
    rows.append((100.0, 107.0, 99.8, 100.2))      # index 10: sweeps a high, closes back
    rows += [(100.0, 100.2, 93.0, 93.5)]          # impulse down to 93.0
    df = _df(rows)

    leg_start, leg_end = 107.0, 93.0              # sweep high -> impulse low
    leg = leg_start - leg_end
    ote = ote_position(df, 10, "bearish", leg_end + 0.71 * leg)

    assert abs(ote["retracement"] - 0.71) < 1e-6
    assert ote["in_ote"] is True


def test_ote_returns_none_on_a_leg_with_no_range():
    flat = _df([(100.0, 100.0, 100.0, 100.0)] * 5)
    assert ote_position(flat, 4, "bullish", 100.0) is None


def test_sessions_and_killzones_are_utc():
    assert session_of(pd.Timestamp("2026-09-10T03:00:00Z"))["session"] == "asia"
    assert session_of(pd.Timestamp("2026-09-10T08:00:00Z"))["session"] == "london"
    assert session_of(pd.Timestamp("2026-09-10T08:00:00Z"))["killzone"] == "london_kz"
    assert session_of(pd.Timestamp("2026-09-10T11:00:00Z"))["killzone"] is None
    assert session_of(pd.Timestamp("2026-09-10T13:00:00Z"))["killzone"] == "ny_kz"
    assert session_of(pd.Timestamp("2026-09-10T20:00:00Z"))["session"] == "off"


def test_describe_returns_every_field_even_when_factors_are_absent():
    """The logger distinguishes 'measured and absent' from 'not applicable'."""
    df, sweep_i = _bullish_sweep_scenario()
    ctx = describe_ict_context(df, sweep_i, "bullish", 97.0)

    assert set(ctx) == {"mss", "ote", "session", "killzone"}
    assert ctx["mss"]["level"] == 101.0
    assert ctx["ote"]["retracement"] > 0
    # 30 bars of 15m from 00:00 UTC ends at 07:15 — first hour of London
    assert ctx["session"] == "london"
    assert ctx["killzone"] == "london_kz"


def test_funding_carry_is_signed_from_the_trades_point_of_view():
    """A short into positive funding is paid; a long into it pays."""
    import analysis.market_context as mc

    mc._cache = ({"BTC": {"funding_hourly": 0.0001, "oi_usd": 1e9, "premium": 0.0, "mark": 100.0}}, 1e18)

    short = mc.get_market_context("BTC", "bearish")
    long_ = mc.get_market_context("BTC", "bullish")

    assert short["funding_carry_pct_48h"] > 0, "short receives positive funding"
    assert long_["funding_carry_pct_48h"] < 0, "long pays positive funding"
    assert abs(short["funding_carry_pct_48h"] + long_["funding_carry_pct_48h"]) < 1e-9
    assert abs(short["funding_carry_pct_48h"] - 0.48) < 1e-6   # 0.01%/h * 48h
    assert mc.get_market_context("DOGE", "bullish") is None    # unknown pair -> None, not 0

    mc._cache = None


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except AssertionError as exc:
                fails += 1
                print(f"FAIL  {name}: {exc}")
    print(f"\n{fails} failed")
    sys.exit(1 if fails else 0)
