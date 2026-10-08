"""
4H-bias + 1h-pullback study on Binance BTCUSDT / ETHUSDT. Hypothesis,
parameters, exits, sizing and pass criteria are fixed in
research/bias_pullback_prereg.md (written before any return was computed).

Usage (from trading-bot/):
    .venv/bin/python research_bias_pullback.py [cache_dir=logs/binance_1h_cache]
"""
import math
import pickle
import sys
import time
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

BASE = "https://data-api.binance.vision/api/v3"
START_MS = int(pd.Timestamp("2017-08-01", tz="UTC").timestamp() * 1000)

EMA_4H, EMA_1H, ATR_N = 50, 20, 14
ATR_MULT, STOP_MIN, STOP_MAX = 2.5, 0.03, 0.05
TP_R = 2.0
MAX_HOLD = 336                      # 14 days of 1h bars
SIDE_COST = 0.0010
FUND_PER_YEAR = 0.10
RISK_PER_R = 0.01
TIER_W = {"A": 1.0, "B": 0.5, "C": 0.25}
MIN_TRADES = 300
T_PASS = 3.0
PERIODS = {
    "P1 2018-20": (pd.Timestamp("2018-01-01", tz="UTC"), pd.Timestamp("2020-12-31 23:59", tz="UTC")),
    "P2 2021-22": (pd.Timestamp("2021-01-01", tz="UTC"), pd.Timestamp("2022-12-31 23:59", tz="UTC")),
    "P3 2023-":   (pd.Timestamp("2023-01-01", tz="UTC"), pd.Timestamp("2100-01-01", tz="UTC")),
}


# ── data ──────────────────────────────────────────────────────────────────

def fetch_hourly(symbol: str, cache_dir: Path) -> pd.DataFrame:
    cache = cache_dir / f"{symbol}_1h.pkl"
    if cache.exists():
        return pickle.loads(cache.read_bytes())
    rows, start = [], START_MS
    while True:
        for attempt in range(6):
            r = httpx.get(f"{BASE}/klines", timeout=30,
                          params={"symbol": symbol, "interval": "1h", "startTime": start, "limit": 1000})
            if r.status_code in (418, 429):
                time.sleep(15 * (attempt + 1))
                continue
            r.raise_for_status()
            break
        page = r.json()
        if not page:
            break
        rows += page
        if len(page) < 1000:
            break
        start = page[-1][0] + 1
        time.sleep(0.1)
    df = pd.DataFrame(rows).iloc[:, [0, 1, 2, 3, 4]]
    df.columns = ["ts", "open", "high", "low", "close"]
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("ts").astype(float)
    df = df[~df.index.duplicated()].sort_index()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(pickle.dumps(df))
    return df


# ── features (no look-ahead: higher-timeframe values enter only once closed) ──

def _resample(df: pd.DataFrame, rule: str, bars: int) -> pd.DataFrame:
    g = df.resample(rule, label="left", closed="left")
    out = g.agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    out["n"] = g["close"].count()
    return out[out["n"] == bars].drop(columns="n")


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Per 1h bar: bias(+1/-1), dist, atr_pct, d1(+1/-1), cross_up, cross_dn, all known at that bar's CLOSE."""
    df = df.copy()
    ema20 = df["close"].ewm(span=EMA_1H, adjust=False).mean()
    prev_c, prev_e = df["close"].shift(1), ema20.shift(1)
    df["cross_up"] = (df["close"] > ema20) & (prev_c <= prev_e)
    df["cross_dn"] = (df["close"] < ema20) & (prev_c >= prev_e)

    h4 = _resample(df[["open", "high", "low", "close"]], "4h", 4)
    ema50 = h4["close"].ewm(span=EMA_4H, adjust=False).mean()
    tr = pd.concat([h4["high"] - h4["low"],
                    (h4["high"] - h4["close"].shift()).abs(),
                    (h4["low"] - h4["close"].shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(ATR_N).mean()
    f4 = pd.DataFrame({
        "bias": np.sign(h4["close"] - ema50),
        "dist": (h4["close"] - ema50).abs() / atr,
        "atr_pct": atr / h4["close"],
    })
    f4.index = f4.index + pd.Timedelta(hours=4)          # available once the 4H bar closes

    d1 = _resample(df[["open", "high", "low", "close"]], "1D", 24)
    sma = d1["close"].rolling(50).mean()
    fd = pd.DataFrame({"d1": np.sign(d1["close"] - sma)})
    fd.index = fd.index + pd.Timedelta(days=1)

    keys = pd.DataFrame({"T": df.index + pd.Timedelta(hours=1), "row": np.arange(len(df))})
    f4 = f4.assign(avail=f4.index).reset_index(drop=True).sort_values("avail")
    fd = fd.assign(avail_d=fd.index).reset_index(drop=True).sort_values("avail_d")
    m = pd.merge_asof(keys, f4, left_on="T", right_on="avail", direction="backward")
    m = pd.merge_asof(m, fd, left_on="T", right_on="avail_d", direction="backward")
    for c in ("bias", "dist", "atr_pct", "d1"):
        df[c] = m[c].to_numpy()
    return df


def tier_of(bias: float, dist: float, d1: float) -> str:
    score = int(dist >= 1.0) + int(d1 == bias)
    return {2: "A", 1: "B", 0: "C"}[score]


# ── simulation ────────────────────────────────────────────────────────────

def run_strategy(f: pd.DataFrame, exit_mode: str, use_trigger: bool = True) -> pd.DataFrame:
    """exit_mode 'E1' (TP 2R) or 'E2' (bias flip). One position at a time."""
    o, h, l, c = (f[k].to_numpy() for k in ("open", "high", "low", "close"))
    bias, dist, atrp, d1 = (f[k].to_numpy() for k in ("bias", "dist", "atr_pct", "d1"))
    up, dn = f["cross_up"].to_numpy(), f["cross_dn"].to_numpy()
    idx, n = f.index, len(f)
    trades, i = [], 0
    while i < n - 1:
        b = bias[i]
        if not (b == 1 or b == -1) or np.isnan(atrp[i]) or np.isnan(dist[i]) or np.isnan(d1[i]):
            i += 1
            continue
        trig = (up[i] if b == 1 else dn[i]) if use_trigger else True
        if not trig:
            i += 1
            continue

        e = i + 1
        entry = o[e]
        stop_pct = min(max(ATR_MULT * atrp[i], STOP_MIN), STOP_MAX)
        stop = entry * (1 - b * stop_pct)
        tp = entry * (1 + b * stop_pct * TP_R)
        tier = tier_of(b, dist[i], d1[i])

        exit_px, exit_i = None, None
        last = min(e + MAX_HOLD - 1, n - 1)
        for j in range(e, last + 1):
            if exit_mode == "E2" and j > e and bias[j - 1] == -b:
                exit_px, exit_i = o[j], j
                break
            stopped = l[j] <= stop if b == 1 else h[j] >= stop
            if stopped:
                gap = (o[j] <= stop) if b == 1 else (o[j] >= stop)
                exit_px, exit_i = (o[j] if gap and j > e else stop), j
                break
            if exit_mode == "E1" and ((h[j] >= tp) if b == 1 else (l[j] <= tp)):
                exit_px, exit_i = tp, j
                break
        if exit_px is None:
            exit_px, exit_i = c[last], last
            if last == n - 1 and last < e + MAX_HOLD - 1:
                break                                  # still open at end of data -> ignore
        hours = exit_i - e + 1
        r_gross = b * (exit_px - entry) / (entry * stop_pct)
        cost_r = (2 * SIDE_COST + hours * FUND_PER_YEAR / 8760) / stop_pct
        trades.append({"entry_t": idx[e], "exit_t": idx[exit_i], "dir": int(b), "tier": tier,
                       "stop_pct": stop_pct, "r_gross": r_gross, "r_net": r_gross - cost_r, "hours": hours})
        i = exit_i
    return pd.DataFrame(trades)


# ── analysis ──────────────────────────────────────────────────────────────

def _t(x: pd.Series) -> float:
    x = x.dropna()
    return float(x.mean() / (x.std() / math.sqrt(len(x)))) if len(x) > 2 and x.std() > 0 else float("nan")


def daily_sharpe(trades: pd.DataFrame, weighted: bool) -> float:
    if trades.empty:
        return float("nan")
    w = trades["tier"].map(TIER_W) if weighted else 1.0
    contrib = (w * trades["r_net"] * RISK_PER_R)
    daily = contrib.groupby(trades["exit_t"].dt.floor("D")).sum()
    full = daily.reindex(pd.date_range(daily.index.min(), daily.index.max(), freq="D", tz="UTC"), fill_value=0.0)
    return float(full.mean() / full.std() * math.sqrt(365)) if full.std() > 0 else float("nan")


def analyze(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"n": 0, "passes": False}
    per = {}
    for name, (a, b) in PERIODS.items():
        seg = trades[(trades["entry_t"] >= a) & (trades["entry_t"] <= b)]
        per[name] = (len(seg), float(seg["r_net"].mean()) if len(seg) else float("nan"))
    tiers = {k: (int((trades["tier"] == k).sum()),
                 float(trades.loc[trades["tier"] == k, "r_net"].mean()) if (trades["tier"] == k).any() else float("nan"))
             for k in "ABC"}
    sh_w, sh_u = daily_sharpe(trades, True), daily_sharpe(trades, False)
    t = _t(trades["r_net"])
    pos = sum(1 for n_, m in per.values() if n_ and m > 0)
    p3 = per["P3 2023-"]
    passes = bool(len(trades) >= MIN_TRADES and t > T_PASS and pos >= 2 and p3[0] and p3[1] > 0 and sh_w > 0.8)
    return {"n": len(trades), "mean": float(trades["r_net"].mean()), "gross": float(trades["r_gross"].mean()),
            "t": t, "win": float((trades["r_net"] > 0).mean()), "per": per, "tiers": tiers,
            "sharpe_w": sh_w, "sharpe_u": sh_u, "passes": passes}


def main() -> None:
    cache_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs/binance_1h_cache")
    today = pd.Timestamp.now(tz="UTC").floor("h")
    for sym in ("BTCUSDT", "ETHUSDT"):
        raw = fetch_hourly(sym, cache_dir)
        raw = raw[raw.index < today - pd.Timedelta(hours=1)]
        f = build_features(raw)
        d = raw["close"].resample("1D").last().dropna().pct_change().dropna()
        bh = float(d.mean() / d.std() * math.sqrt(365))
        print(f"═══ {sym}: {raw.index[0].date()} → {raw.index[-1].date()} ({len(raw)} 1h bars), "
              f"buy&hold Sharpe {bh:.2f} ═══")
        print(f"{'variantas':10} {'n':>5} {'win%':>5} {'bruto R':>8} {'net R':>7} {'t':>6} "
              f"{'P1':>7} {'P2':>7} {'P3':>7} {'ShW':>5} {'ShU':>5}  praeina?")
        for mode in ("E1", "E2"):
            tr = run_strategy(f, mode)
            a = analyze(tr)
            if not a["n"]:
                print(f"{sym[:3]}-{mode:6} nėra sandorių")
                continue
            p = a["per"]
            fm = lambda k: f"{p[k][1]:>7.3f}" if p[k][0] else "    n/a"
            print(f"{sym[:3]}-{mode:6} {a['n']:>5} {a['win']*100:>5.1f} {a['gross']:>8.3f} {a['mean']:>7.3f} "
                  f"{a['t']:>6.2f} {fm('P1 2018-20')} {fm('P2 2021-22')} {fm('P3 2023-')} "
                  f"{a['sharpe_w']:>5.2f} {a['sharpe_u']:>5.2f}  {'TAIP' if a['passes'] else 'ne'}")
            print("           tier A/B/C (n, vid. net R): " +
                  "  ".join(f"{k}: {a['tiers'][k][0]}, {a['tiers'][k][1]:.3f}" for k in "ABC"))
            ctl = analyze(run_strategy(f, mode, use_trigger=False))
            if ctl["n"]:
                print(f"           kontrolė be pullback trigerio: n {ctl['n']}, vid. net R {ctl['mean']:.3f}, t {ctl['t']:.2f}")
        print()
    print(f"Slenkstis: n≥{MIN_TRADES}, t>{T_PASS}, ≥2/3 laikotarpių teigiami ir P3 teigiamas, pasvertas Sharpe>0.8. "
          f"Kaštai {SIDE_COST*100:.2f}%/pusę + {FUND_PER_YEAR*100:.0f}%/m. laikymo kaina.")


if __name__ == "__main__":
    main()
