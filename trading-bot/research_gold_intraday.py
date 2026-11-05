"""
Intraday gold search: 4 simple rules on PAXGUSDT 15m bars. Rules, splits, costs and pass criteria are fixed
in research/gold_intraday_prereg.md (committed before any PAXG bar was downloaded).

Usage: .venv/bin/python research_gold_intraday.py [cache.pkl] [ib_daily_closes.csv]
"""
import math
import pickle
import sys
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

URL = "https://data-api.binance.vision/api/v3/klines"
START = pd.Timestamp("2020-08-28", tz="UTC")
DEV_END = pd.Timestamp("2024-07-01", tz="UTC")
K1, K2 = 1.5, 4.0                      # round-trip cost, bps
T_DEV, T_TEST = 3.0, 2.0
MIN_DEV, MIN_TEST = 150, 80
STEP = pd.Timedelta(minutes=15)


# ── data ──────────────────────────────────────────────────────────────────

def fetch(cache: Path) -> pd.DataFrame:
    if cache.exists():
        return pickle.loads(cache.read_bytes())
    rows, t = [], START
    end = pd.Timestamp.now(tz="UTC")
    while t < end:
        r = httpx.get(URL, timeout=30, params={"symbol": "PAXGUSDT", "interval": "15m",
                                               "startTime": int(t.timestamp() * 1000), "limit": 1000})
        r.raise_for_status()
        b = r.json()
        if not b:
            break
        rows += [(x[0], float(x[1]), float(x[2]), float(x[3]), float(x[4])) for x in b]
        t = pd.Timestamp(b[-1][0], unit="ms", tz="UTC") + STEP
    df = pd.DataFrame(rows, columns=["t", "o", "h", "l", "c"])
    df["t"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    df = df.drop_duplicates("t").set_index("t").sort_index()
    cache.write_bytes(pickle.dumps(df))
    return df


# ── rules ─────────────────────────────────────────────────────────────────

def _bar(day: pd.DataFrame, hhmm: str):
    h, m = map(int, hhmm.split(":"))
    ts = day.index[0].normalize() + pd.Timedelta(hours=h, minutes=m)
    return day.loc[ts] if ts in day.index else None, ts


def _stop_exit(day: pd.DataFrame, entry_ts, exit_ts, side: int, stop: float, entry: float):
    """Walk bars in [entry_ts, exit_ts); return exit price (stop level / gap open) or None if not stopped."""
    for ts, b in day[(day.index >= entry_ts) & (day.index < exit_ts)].iterrows():
        if side == 1 and b.l <= stop:
            return min(stop, b.o)
        if side == -1 and b.h >= stop:
            return max(stop, b.o)
    return None


def rule_r1(day: pd.DataFrame):
    rng = day[(day.index.hour == 7)]
    if len(rng) < 4:
        return None
    hi, lo = rng.h.max(), rng.l.min()
    base = day.index[0].normalize()
    for ts, b in day[(day.index >= base + pd.Timedelta(hours=8)) & (day.index <= base + pd.Timedelta(hours=11, minutes=30))].iterrows():
        side = 1 if b.c > hi else -1 if b.c < lo else 0
        if side:
            entry_ts = ts + STEP
            e = day.loc[entry_ts].o if entry_ts in day.index else None
            xb, xts = _bar(day, "12:00")
            if e is None or xb is None:
                return None
            stop = lo if side == 1 else hi
            sx = _stop_exit(day, entry_ts, xts, side, stop, e)
            exit_px = sx if sx is not None else xb.o
            return side, e, exit_px
    return None


def _timed(day, sig_a, sig_b, enter, leave, contrarian):
    a, _ = _bar(day, sig_a)
    b, _ = _bar(day, sig_b)
    e, _ = _bar(day, enter)
    x, _ = _bar(day, leave)
    if a is None or b is None or e is None or x is None:
        return None
    r = b.o / a.o - 1
    if r == 0:
        return None
    side = int(np.sign(r)) * (-1 if contrarian else 1)
    return side, e.o, x.o


def rule_r2(day):
    return _timed(day, "00:00", "08:00", "08:00", "12:00", False)


def rule_r3(day):
    return _timed(day, "08:00", "12:00", "12:00", "16:00", True)


def rule_r4(day: pd.DataFrame):
    base = day.index[0].normalize()
    o, c = day.o.to_numpy(), day.c.to_numpy()
    idx = day.index
    for i in range(3, len(day) - 1):
        ts = idx[i]
        if not (base + pd.Timedelta(hours=3) <= ts <= base + pd.Timedelta(hours=13, minutes=30)):
            continue
        r = c[i] / o[i - 3] - 1
        if abs(r) >= 0.004:
            side = -int(np.sign(r))
            entry_ts = idx[i + 1]
            if entry_ts - ts != STEP:
                continue
            e = o[i + 1]
            exit_ts = entry_ts + 8 * STEP
            xrow = day.loc[exit_ts] if exit_ts in day.index else None
            stop = e * (1 - 0.004 * side)
            sx = _stop_exit(day, entry_ts, exit_ts, side, stop, e)
            if sx is not None:
                return side, e, sx
            if xrow is None:
                return None
            return side, e, xrow.o
    return None


RULES = {"R1 Londono proveržis": rule_r1, "R2 intraday momentum": rule_r2,
         "R3 sesijos atsisukimas": rule_r3, "R4 valandos impulso atsisukimas": rule_r4}


def run_rule(bars: pd.DataFrame, rule) -> pd.DataFrame:
    out = []
    for d, day in bars.groupby(bars.index.normalize()):
        if d.dayofweek >= 5 or len(day) < 90:
            continue
        r = rule(day)
        if r:
            side, e, x = r
            out.append({"date": d, "side": side, "gross": side * (x / e - 1) * 1e4})
    return pd.DataFrame(out).set_index("date") if out else pd.DataFrame(columns=["side", "gross"])


# ── stats ─────────────────────────────────────────────────────────────────

def tstat(x: pd.Series) -> float:
    return float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x)))) if len(x) > 2 and x.std() > 0 else float("nan")


def describe(label: str, t: pd.DataFrame, cost: float) -> dict:
    n = t.gross - cost
    pf = n[n > 0].sum() / -n[n <= 0].sum() if (n <= 0).any() else float("inf")
    r = {"n": len(n), "mean": float(n.mean()), "t": tstat(n), "win": float((n > 0).mean()), "pf": pf,
         "gross": float(t.gross.mean())}
    print(f"    {label:22} n={r['n']:4} gross {r['gross']:+6.1f} net {r['mean']:+6.1f} bps  t {r['t']:+5.2f}  "
          f"win {r['win']*100:3.0f}%  PF {pf:5.2f}")
    return r


def proxy_check(bars: pd.DataFrame, ib_csv: Path) -> float:
    ib = pd.read_csv(ib_csv, parse_dates=["time"])
    ib["time"] = pd.to_datetime(ib["time"], utc=True)
    px, ibp = [], []
    for _, r in ib.iterrows():
        ts = r["time"] + pd.Timedelta(hours=24)                   # bar closes 24h after its 22:00/23:00 UTC start
        ts = ts.floor("15min")
        if ts in bars.index:
            px.append(bars.loc[ts].o); ibp.append(r["close"])
    s, b = pd.Series(px).pct_change().dropna(), pd.Series(ibp).pct_change().dropna()
    return float(np.corrcoef(s, b)[0, 1]), len(s)


def main() -> None:
    bars = fetch(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("paxg15.pkl"))
    print(f"PAXG 15m: {bars.index[0]} → {bars.index[-1]}, {len(bars)} žvakių")
    if len(sys.argv) > 2:
        c, n = proxy_check(bars, Path(sys.argv[2]))
        print(f"Proxy sąlyga: dienos grąžų koreliacija PAXG vs IB XAUUSD (n={n}) = {c:.3f}  (reikia ≥ 0.95): {'OK' if c >= 0.95 else 'NEPASIEKTA'}")
    verdicts = {}
    for name, rule in RULES.items():
        t = run_rule(bars, rule)
        dev, test = t[(t.index >= "2021-01-01") & (t.index < DEV_END)], t[t.index >= DEV_END]
        print(f"\n═══ {name} ═══")
        d1 = describe("Dev  (K1)", dev, K1)
        t1 = describe("Test (K1)", test, K1)
        t2 = describe("Test (K2 stresas)", test, K2)
        yr = (t.gross - K1).groupby(t.index.year).agg(["mean", "count"])
        print("    pagal metus (K1): " + "  ".join(f"{y}: {r['mean']:+.1f} ({int(r['count'])})" for y, r in yr.iterrows()))
        ok = (d1["mean"] > 0 and d1["t"] > T_DEV and t1["mean"] > 0 and t1["t"] > T_TEST and t2["mean"] > 0
              and d1["n"] >= MIN_DEV and t1["n"] >= MIN_TEST)
        verdicts[name] = ok
        print(f"    → {'PRAEINA' if ok else 'NEPRAEINA'}")
    print("\nVerdiktas pagal registraciją:", ", ".join(f"{k.split()[0]} {'PRAEINA' if v else 'NE'}" for k, v in verdicts.items()))


if __name__ == "__main__":
    main()
