"""
ETH DVOL replication of the "sell premium only when IV is high" idea. Rule,
hypotheses and pass criteria are fixed in research/options_vrp_eth_prereg.md
(written before any ETH data was downloaded).

Usage (from trading-bot/):
    .venv/bin/python research_options_vrp_eth.py [cache_dir] [binance_cache_dir]
"""
import math
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import research_options_vrp as vr

PCT_WINDOW = 365
HIGH, LOW = 2 / 3, 1 / 3
T_PASS = 2.5
MIN_YEAR_DAYS = 60
MIN_POS_YEARS = 3


def iv_percentile(dvol: pd.Series, window: int = PCT_WINDOW) -> pd.Series:
    """Point-in-time share of the trailing `window` closes (incl. today) that are <= today's."""
    return dvol.rolling(window).apply(lambda w: float((w <= w[-1]).mean()), raw=True)


def hac_ols(y: np.ndarray, X: np.ndarray, lag: int = vr.HORIZON) -> tuple[np.ndarray, np.ndarray]:
    """OLS beta and Newey-West covariance, computed on the full (calendar-contiguous) series."""
    n = len(y)
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    u = y - X @ beta
    Xu = X * u[:, None]
    S = Xu.T @ Xu / n
    for k in range(1, lag + 1):
        G = Xu[k:].T @ Xu[:-k] / n
        S += (1 - k / (lag + 1)) * (G + G.T)
    A = np.linalg.inv(X.T @ X / n)
    return beta, A @ S @ A / n


def state_stats(df: pd.DataFrame, col: str = "pl_straddle") -> dict:
    d = df.dropna(subset=["pct", col]).copy()
    d["state"] = np.where(d["pct"] >= HIGH, "high", np.where(d["pct"] < LOW, "low", "mid"))
    y = d[col].to_numpy(float)
    X = np.column_stack([(d["state"] == s).to_numpy(float) for s in ("high", "mid", "low")])
    beta, V = hac_ols(y, X)
    se = np.sqrt(np.diag(V))
    c = np.array([1.0, 0.0, -1.0])
    diff = float(c @ beta)
    diff_t = diff / math.sqrt(float(c @ V @ c))
    res = {"n": {s: int((d["state"] == s).sum()) for s in ("high", "mid", "low")},
           "mean": dict(zip(("high", "mid", "low"), beta)),
           "t": dict(zip(("high", "mid", "low"), beta / se)),
           "diff": diff, "diff_t": diff_t, "d": d}
    hi = d[d["state"] == "high"]
    yr = hi.groupby(hi.index.year)[col].agg(["mean", "count"])
    yr = yr[yr["count"] >= MIN_YEAR_DAYS]
    res["years"] = yr
    res["pos_years"] = int((yr["mean"] > 0).sum())
    flags = (d["state"] == "high").astype(int)
    res["episodes"] = int(((flags == 1) & (flags.shift(1, fill_value=0) == 0)).sum())
    return res


def report(label: str, df: pd.DataFrame, primary: bool) -> bool:
    r = state_stats(df)
    n = r["n"]
    print(f"── {label} ──  dienų: {len(r['d'])}, {r['d'].index[0].date()} → {r['d'].index[-1].date()}")
    for s in ("high", "mid", "low"):
        print(f"  straddle, {s:4} IV (n={n[s]:4}): vid. {r['mean'][s]*100:+.2f}%/30d   NW t {r['t'][s]:+.2f}")
    print(f"  aukštas − žemas: {r['diff']*100:+.2f}%   NW t {r['diff_t']:+.2f}")
    yrs = "  ".join(f"{y}: {row['mean']*100:+.2f}% (n={int(row['count'])})" for y, row in r["years"].iterrows())
    print(f"  aukšto IV metai (≥{MIN_YEAR_DAYS} d.): {yrs}   teigiamų {r['pos_years']}/{len(r['years'])}")
    hi = r["d"][r["d"]["state"] == "high"]
    print(f"  aukšto IV epizodų (nepertraukiamų): {r['episodes']}; p5 {hi['pl_straddle'].quantile(.05)*100:+.1f}%, "
          f"blogiausias {hi['pl_straddle'].min()*100:+.1f}%")
    put = state_stats(df, "pl_put")
    print(f"  ATM put, aukštas IV: vid. {put['mean']['high']*100:+.2f}% (t {put['t']['high']:+.2f}); "
          f"žemas IV: {put['mean']['low']*100:+.2f}%")
    nonov = hi.iloc[::vr.HORIZON]
    if len(nonov) > 2:
        x = nonov["pl_straddle"]
        print(f"  ne persidengiantys aukšto IV įrašai (kas 30-a eilutė): n={len(x)}, vid. {x.mean()*100:+.2f}%")
    for c in (0.0, 0.03, 0.10):
        pl = hi["prem_straddle"] * (1 - c) - hi["ret30"].abs()
        print(f"  kaštų jautrumas {c*100:.0f}%: aukšto IV straddle vid. {pl.mean()*100:+.2f}%")
    h4a = bool(r["mean"]["high"] > 0 and r["t"]["high"] > T_PASS and r["pos_years"] >= MIN_POS_YEARS)
    h4b = bool(r["diff"] > 0 and r["diff_t"] > T_PASS)
    if primary:
        print(f"  H4a {'PRAEINA' if h4a else 'ne'}, H4b {'PRAEINA' if h4b else 'ne'}")
    print()
    return h4a and h4b


def prepare(currency: str, cache: Path, bcache: Path) -> pd.DataFrame:
    dvol = vr.fetch_dvol(cache, currency)
    px = vr.load_btc(bcache / f"{currency}USDT.pkl")
    df = vr.build_frame(dvol, px)
    df["pct"] = iv_percentile(dvol).reindex(df.index)
    return df


def main() -> None:
    cache = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs/vrp_cache")
    bcache = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("logs/binance_cache")
    eth = prepare("ETH", cache, bcache)
    m, t = vr.nw_mean_t(eth["vrp"])
    print(f"ETH unconditional VRP (replikacija): vid. {m:+.2f} vol pt, NW t {t:+.2f}\n")
    ok = report("ETH (PAGRINDINIS TESTAS)", eth, primary=True)
    report("BTC, point-in-time percentilis (aprašomasis)", prepare("BTC", cache, bcache), primary=False)
    print(f"Verdiktas pagal registraciją: {'PRAEINA (H4a ir H4b)' if ok else 'NEPRAEINA'}")


if __name__ == "__main__":
    main()
