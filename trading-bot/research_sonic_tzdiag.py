"""
EXPLORATORY diagnostic (not pre-registered): the pre-registered Sonic follow test found a ~0 correlation
between Sonic's published move and the Evedex move over the same listed timestamps. This checks the
simplest innocent explanation: that the published times are broker SERVER time, not UTC.

For each whole-hour offset h, a listed time T is read as UTC = T - h. Correlation of the published move
with the market move over [open, close] is reported per h.

NOTE: run BEFORE analysis/sonic_follow.py applied server_to_utc (it then reads published times as UTC);
after the fix the correct offset is h = 0.

Usage: .venv/bin/python research_sonic_tzdiag.py trades_raw.txt trades_new.csv [day_bars_cache.pkl]
"""
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import analysis.sonic_follow as sf
from research_sonic_follow import SAMPLE_START, load_trades

OFFSETS = range(-4, 6)


def day_bars(days: list, cache: Path) -> dict:
    store = pickle.loads(cache.read_bytes()) if cache.exists() else {}
    for i, day in enumerate(days):
        if day in store:
            continue
        bars = {}
        t = pd.Timestamp(day, tz="UTC") - pd.Timedelta(hours=6)
        end = pd.Timestamp(day, tz="UTC") + pd.Timedelta(hours=30)
        while t < end:
            try:
                bars.update(sf.fetch_bars(t, t + pd.Timedelta(hours=6)))
            except Exception as exc:
                print("  fail", day, exc)
            t += pd.Timedelta(hours=6)
        store[day] = bars
        if i % 20 == 19:
            cache.write_bytes(pickle.dumps(store))
    cache.write_bytes(pickle.dumps(store))
    return store


def main() -> None:
    trades = [t for t in load_trades(Path(sys.argv[1]), Path(sys.argv[2]))
              if t["open"] >= SAMPLE_START and (t["close"] - t["open"]).total_seconds() <= 720 * 60]
    cache = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("sonic_daybars.pkl")
    days = sorted({t["open"].date().isoformat() for t in trades})
    store = day_bars(days, cache)
    allbars = {}
    for b in store.values():
        allbars.update(b)
    print(f"{len(trades)} sandorių, {len(allbars)} minučių žvakių\nofsetas h: listed time = UTC + h\n")
    print(f"{'h':>3} {'n':>4} {'corr':>6} {'sekėjo vid. pts':>16} {'Sonic vid. pts':>15}")
    for h in OFFSETS:
        a, b = [], []
        for t in trades:
            tt = dict(t, open=t["open"] - pd.Timedelta(hours=h), close=t["close"] - pd.Timedelta(hours=h))
            r = sf.follow_trade(tt, allbars, 0, fee=0, half_spread=0)
            if r:
                a.append(t["sonic_pts"]); b.append(r["gross_pts"])
        print(f"{h:>3} {len(a):>4} {np.corrcoef(a, b)[0, 1]:>6.2f} {np.mean(b):>16.2f} {np.mean(a):>15.2f}")


if __name__ == "__main__":
    main()
