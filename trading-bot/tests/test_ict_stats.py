"""
Tests for analysis/ict_stats.py against a synthetic log.

Checks the two things that would quietly produce wrong conclusions:
blanks leaking into a False bucket, and a planted effect either vanishing
or being invented by the significance test.
"""
import csv
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analysis.trade_logger as tl
import analysis.ict_stats as st


def _row(**kw) -> dict:
    base = {c: "" for c in tl.COLUMNS}
    base.update({
        "id": str(random.randint(0, 10**8)),
        "logged_at": "2026-09-13T12:00:00+00:00",
        "pair": "SOL", "bias": "bullish", "entry_type": "SWEEP",
        "entry": "100", "sl": "98", "tp1": "102", "tp2": "105", "rr_ratio": "2.0",
        "status": "sl_hit",
    })
    base.update(kw)
    return base


def _write(rows: list[dict]) -> None:
    path = Path(tempfile.mkdtemp()) / "trade_log.csv"
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=tl.COLUMNS)
        w.writeheader()
        w.writerows(rows)
    tl.LOG_FILE = path


def _ict_row(mss: bool, win: bool, session="london", killzone="london_kz",
             retr=0.70, carry=0.1) -> dict:
    return _row(
        status="tp1_hit" if win else "sl_hit",
        mss_confirmed=str(mss), mss_candles_ago="2" if mss else "",
        ote_retracement=str(retr), in_ote=str(0.62 <= retr <= 0.79),
        session=session, killzone=killzone,
        funding_carry_pct_48h=str(carry),
    )


def test_legacy_rows_without_ict_columns_are_excluded_not_counted_as_false():
    """The failure mode that would fabricate a finding out of old history."""
    legacy = [_row(status="sl_hit") for _ in range(200)]          # no ICT columns
    fresh  = [_ict_row(mss=True, win=i < 5) for i in range(20)]
    _write(legacy + fresh)

    s = st.get_ict_stats(all_time=True)

    assert s["resolved"] == 220, s["resolved"]
    assert s["scored"] == 20, "only rows carrying ICT data may be scored"
    assert s["by_mss"]["be MSS"]["count"] == 0, "blank must not land in the False bucket"
    assert s["by_mss"]["su MSS"]["count"] == 20


def test_a_planted_mss_effect_is_found_and_called_significant():
    rows  = [_ict_row(mss=True,  win=i < 24) for i in range(60)]   # 40%
    rows += [_ict_row(mss=False, win=i < 6)  for i in range(60)]   # 10%
    _write(rows)

    g = st.get_ict_stats(all_time=True)["by_mss"]

    assert g["su MSS"]["win_rate"] == 40.0
    assert g["be MSS"]["win_rate"] == 10.0
    assert g["p_value"] is not None and g["p_value"] < 0.05, g["p_value"]
    assert g["su MSS"]["reliable"] and g["be MSS"]["reliable"]


def test_a_small_lopsided_split_is_reported_but_not_called_significant():
    """3/8 vs 7/41 — the shape of the real SWEEP ob_confluence slice."""
    rows  = [_ict_row(mss=True,  win=i < 3) for i in range(8)]
    rows += [_ict_row(mss=False, win=i < 7) for i in range(41)]
    _write(rows)

    g = st.get_ict_stats(all_time=True)["by_mss"]

    assert g["su MSS"]["count"] == 8 and not g["su MSS"]["reliable"], "n=8 must be flagged"
    assert g["p_value"] > 0.05, f"p={g['p_value']} would call noise a finding"


def test_retracement_bands_are_disjoint_and_cover_the_scored_rows():
    rows = [
        _ict_row(mss=True, win=False, retr=r)
        for r in (0.10, 0.30, 0.50, 0.55, 0.65, 0.71, 0.79, 0.85, 1.20)
    ]
    _write(rows)

    bands = st.get_ict_stats(all_time=True)["by_retracement"]
    counts = {k: v["count"] for k, v in bands.items()}

    assert counts == {"seklu <0.4": 2, "0.4-0.62": 2, "OTE 0.62-0.79": 3, "gilu >0.79": 2}
    assert sum(counts.values()) == 9, "bands must partition the rows, not overlap"


def test_killzone_split_excludes_rows_with_no_session():
    rows  = [_ict_row(mss=True, win=True,  session="london", killzone="london_kz")]
    rows += [_ict_row(mss=True, win=False, session="asia",   killzone="")]
    rows += [_row(status="sl_hit")]                                 # legacy, no session
    _write(rows)

    g = st.get_ict_stats(all_time=True)["by_killzone"]
    assert g["killzone"]["count"] == 1
    assert g["ne killzone"]["count"] == 1


def test_format_renders_and_flags_unreliable_buckets():
    _write([_ict_row(mss=True, win=i < 2) for i in range(6)])
    out = st.format_ict_stats(all_time=True)

    assert "ICT statistika" in out
    assert "⚠️" in out, "an n=6 bucket must carry the unreliable marker"
    assert "MSS" in out and "Funding" in out


def test_empty_log_says_not_measured_rather_than_no_effect():
    _write([_row(status="sl_hit") for _ in range(10)])
    out = st.format_ict_stats(all_time=True)
    assert "nė vienas neturi ICT duomenų" in out


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
