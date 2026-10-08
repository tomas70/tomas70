"""
POC / VAH / VAL reaction study. Hypotheses, targets, rules and the pass
criterion are fixed in research/vp_reactions_prereg.md, written before this
was run. Do not change them to fit results.

Usage (from trading-bot/):
    .venv/bin/python research_vp_reactions.py [days=120] [cache_dir=logs/vp_cache]
"""
import math
import pickle
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from analysis.liquidity import compute_volume_profile

FEE_PCT      = 0.08      # % of notional, round trip
MIN_RISK_PCT = 0.25      # % — narrower stops are not tradable after fees
WINDOW       = 96        # candles after entry (24h)
BARS_PER_DAY = 96
T_PASS       = 2.6

TESTS = [   # (event, target)
    ("VAH_REJ", "POC"), ("VAH_REJ", "2R"),
    ("VAL_REJ", "POC"), ("VAL_REJ", "2R"),
    ("VAH_ACC", "2R"),  ("VAL_ACC", "2R"),
]


# ── data ──────────────────────────────────────────────────────────────────

def fetch_history(pair: str, days: int, cache_dir: Path) -> pd.DataFrame:
    from analysis import market_data as md

    cache = cache_dir / f"{pair}_{days}.pkl"
    if cache.exists():
        return pickle.loads(cache.read_bytes())

    ms     = md._GROUP_MS["15m"]
    end    = datetime.now(timezone.utc)
    oldest = end - timedelta(days=days)
    rows: dict[int, list] = {}
    while end > oldest:
        start = end - timedelta(milliseconds=1000 * ms)
        resp = md._get_with_retry(
            f"{md.MARKET_DATA_BASE_URL}/api/history/{md.to_instrument(pair)}/list",
            params={"group": "15m", "after": start.isoformat(), "before": end.isoformat()},
        )
        page = resp.json()
        if not page:
            break
        for c in page:
            rows[c[0]] = c
        first = min(c[0] for c in page)
        new_end = datetime.fromtimestamp(first / 1000, tz=timezone.utc)
        if new_end >= end:
            break
        end = new_end
    if not rows:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame([
        {"timestamp": c[0], "open": float(c[1]), "high": float(c[3]),
         "low": float(c[4]), "close": float(c[2]), "volume": float(c[6])}
        for c in rows.values()
    ])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(pickle.dumps(df))
    return df


# ── events ────────────────────────────────────────────────────────────────

def find_events(df: pd.DataFrame, prof: dict, lo: int, hi: int) -> list[dict]:
    """
    First event of each type within df.iloc[lo:hi] (the event day), given the
    previous day's profile. Returns dicts with: type, entry_idx (index of the
    bar whose OPEN is the entry), bias, sl, poc.
    """
    vah, val, poc = prof["vah"], prof["val"], prof["poc"]
    found: dict[str, dict] = {}
    c = df["close"].to_numpy(); h = df["high"].to_numpy(); l = df["low"].to_numpy()

    for i in range(max(lo, 1), hi):
        if "VAH_REJ" not in found and h[i] > vah and c[i] < vah and c[i - 1] < vah:
            found["VAH_REJ"] = dict(type="VAH_REJ", entry_idx=i + 1, bias="bearish", sl=float(h[i]), poc=poc)
        if "VAL_REJ" not in found and l[i] < val and c[i] > val and c[i - 1] > val:
            found["VAL_REJ"] = dict(type="VAL_REJ", entry_idx=i + 1, bias="bullish", sl=float(l[i]), poc=poc)
        if "VAH_ACC" not in found and i + 1 < hi and c[i] > vah and c[i + 1] > vah and c[i - 1] <= vah:
            found["VAH_ACC"] = dict(type="VAH_ACC", entry_idx=i + 2, bias="bullish", sl=float(vah), poc=poc)
        if "VAL_ACC" not in found and i + 1 < hi and c[i] < val and c[i + 1] < val and c[i - 1] >= val:
            found["VAL_ACC"] = dict(type="VAL_ACC", entry_idx=i + 2, bias="bearish", sl=float(val), poc=poc)
    return list(found.values())


def resolve(df: pd.DataFrame, ev: dict, target: str) -> Optional[dict]:
    """
    Walk one event to an outcome. Returns {r, risk_pct, win} or None when the
    event is not testable (stop too tight, geometry invalid, window incomplete).
    """
    e = ev["entry_idx"]
    if e + WINDOW > len(df):
        return None
    entry   = float(df["open"].iloc[e])
    sl      = ev["sl"]
    bullish = ev["bias"] == "bullish"
    risk    = (entry - sl) if bullish else (sl - entry)
    if entry <= 0 or risk <= 0:
        return None
    risk_pct = risk / entry * 100
    if risk_pct < MIN_RISK_PCT:
        return None

    if target == "POC":
        tp = ev["poc"]
        reward = (tp - entry) if bullish else (entry - tp)
        if reward <= 0:
            return None
    else:
        reward = 2 * risk
        tp = entry + reward if bullish else entry - reward
    rr = reward / risk

    hi = df["high"].to_numpy(); lo = df["low"].to_numpy()
    for j in range(e, e + WINDOW):
        sl_hit = lo[j] <= sl if bullish else hi[j] >= sl
        tp_hit = hi[j] >= tp if bullish else lo[j] <= tp
        if sl_hit:
            r = -1.0; win = False; break
        if tp_hit:
            r = rr; win = True; break
    else:
        last = float(df["close"].iloc[e + WINDOW - 1])
        r = ((last - entry) if bullish else (entry - last)) / risk
        win = r > 0
    net = r - (FEE_PCT / risk_pct)
    return {"gross": r, "net": net, "risk_pct": risk_pct, "win": win}


# ── aggregation ───────────────────────────────────────────────────────────

def analyze(records: list[dict]) -> dict:
    """records: {date, gross, net, win}. Day-clustered t-test on net R."""
    if not records:
        return {"events": 0}
    by_day: dict = {}
    for r in records:
        by_day.setdefault(r["date"], []).append(r["net"])
    days  = sorted(by_day)
    means = [sum(by_day[d]) / len(by_day[d]) for d in days]
    n     = len(means)
    mean  = sum(means) / n
    sd    = math.sqrt(sum((m - mean) ** 2 for m in means) / (n - 1)) if n > 1 else float("nan")
    t     = mean / (sd / math.sqrt(n)) if n > 1 and sd > 0 else float("nan")
    half  = n // 2
    h1 = sum(means[:half]) / half if half else float("nan")
    h2 = sum(means[half:]) / (n - half) if n - half else float("nan")
    ev = len(records)
    return {
        "events": ev, "days": n,
        "win_rate": round(sum(1 for r in records if r["win"]) / ev * 100, 1),
        "gross": round(sum(r["gross"] for r in records) / ev, 3),
        "net": round(sum(r["net"] for r in records) / ev, 3),
        "day_mean_net": round(mean, 3), "t": round(t, 2),
        "half1": round(h1, 3), "half2": round(h2, 3),
        "passes": bool(ev >= 100 and n >= 40 and t > T_PASS and h1 > 0 and h2 > 0),
    }


def main() -> None:
    days      = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    cache_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("logs/vp_cache")

    from analysis.pair_selection import get_liquid_pairs
    pairs = get_liquid_pairs()
    print(f"{len(pairs)} porų, {days}d istorija\n")

    records: dict[tuple, list[dict]] = {t: [] for t in TESTS}
    used = 0
    for pair in pairs:
        try:
            df = fetch_history(pair, days, cache_dir)
        except Exception as exc:
            print(f"! {pair}: {exc}")
            continue
        if len(df) < BARS_PER_DAY * 3:
            continue
        used += 1
        df = df.reset_index(drop=True)
        day = df["timestamp"].dt.floor("D")
        groups = {d: g.index for d, g in df.groupby(day)}
        dates = sorted(groups)
        for d_prev, d_cur in zip(dates, dates[1:]):
            if d_cur - d_prev != pd.Timedelta(days=1):
                continue
            gp, gc = groups[d_prev], groups[d_cur]
            if len(gp) < BARS_PER_DAY - 4 or len(gc) < BARS_PER_DAY - 4:
                continue
            prof = compute_volume_profile(df.loc[gp, ["open", "high", "low", "close", "volume"]])
            if not prof:
                continue
            for ev in find_events(df, prof, int(gc[0]), int(gc[-1]) + 1):
                for (etype, target) in TESTS:
                    if etype != ev["type"]:
                        continue
                    res = resolve(df, ev, target)
                    if res:
                        res["date"] = d_cur
                        records[(etype, target)].append(res)

    print(f"porų su istorija: {used}\n")
    print(f"{'įvykis':8} {'tikslas':6} {'n':>5} {'dienos':>6} {'win%':>6} {'bruto':>7} {'net':>7} "
          f"{'t':>6} {'1/2 pusė':>9} {'2/2 pusė':>9}  praeina?")
    for t in TESTS:
        a = analyze(records[t])
        if not a["events"]:
            print(f"{t[0]:8} {t[1]:6}  nėra įvykių")
            continue
        print(f"{t[0]:8} {t[1]:6} {a['events']:>5} {a['days']:>6} {a['win_rate']:>6} {a['gross']:>7} "
              f"{a['net']:>7} {a['t']:>6} {a['half1']:>9} {a['half2']:>9}  {'TAIP' if a['passes'] else 'ne'}")
    print(f"\nSlenkstis: t > {T_PASS}, abi pusės > 0, ≥100 įvykių, ≥40 dienų. Fee {FEE_PCT}% round-trip.")


if __name__ == "__main__":
    main()
