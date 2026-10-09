"""
BTC options premium (VRP) study + T50 filter. Hypotheses, definitions and pass
criteria are fixed in research/options_vrp_prereg.md (written before any IV
data was downloaded).

Usage (from trading-bot/):
    .venv/bin/python research_options_vrp.py [cache_dir] [binance_btc_pkl]
"""
import math
import pickle
import sys
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

DERIBIT = "https://www.deribit.com/api/v2/public/get_volatility_index_data"
HORIZON = 30
COST_FRAC = 0.03            # of premium
T_H1, T_H2, T_H3 = 3.0, 3.0, 2.5
MIN_POS_YEARS = 4


# ── data ──────────────────────────────────────────────────────────────────

def fetch_dvol(cache: Path) -> pd.Series:
    f = cache / "dvol_btc_1d.pkl"
    if f.exists():
        return pickle.loads(f.read_bytes())
    rows, start = {}, int(pd.Timestamp("2021-03-01", tz="UTC").timestamp() * 1000)
    end = int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)
    while start < end:
        r = httpx.get(DERIBIT, timeout=30, params={
            "currency": "BTC", "start_timestamp": start, "end_timestamp": end, "resolution": "1D"})
        r.raise_for_status()
        res = r.json()["result"]
        data = res["data"]
        if not data:
            break
        for ts, o, h, l, c in data:
            rows[ts] = c
        cont = res.get("continuation")
        if not cont:
            break
        end = int(cont)
    s = pd.Series(rows)
    s.index = pd.to_datetime(s.index, unit="ms", utc=True).normalize()
    s = s.sort_index()
    cache.mkdir(parents=True, exist_ok=True)
    f.write_bytes(pickle.dumps(s))
    return s


def load_btc(pkl: Path) -> pd.Series:
    _, df = pickle.loads(pkl.read_bytes())
    s = df["close"]
    s.index = pd.to_datetime(s.index, utc=True).normalize()
    return s[s.index < pd.Timestamp.now(tz="UTC").normalize()]


# ── option maths (Black-Scholes, r = 0, ATM) ──────────────────────────────

def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def atm_call_pct(iv: float, days: int = HORIZON) -> float:
    """ATM call (= ATM put at r=0) price as a fraction of spot."""
    return 2 * _ncdf(iv * math.sqrt(days / 365) / 2) - 1


def build_frame(dvol: pd.Series, px: pd.Series) -> pd.DataFrame:
    df = pd.DataFrame({"iv": dvol / 100, "px": px}).dropna()
    px = df["px"]
    logret = np.log(px).diff()
    fwd = logret[::-1].rolling(HORIZON).std(ddof=1)[::-1].shift(-1) * math.sqrt(365)   # t+1 .. t+30
    ret30 = px.shift(-HORIZON) / px - 1
    sma50 = px.rolling(50).mean()
    out = pd.DataFrame({
        "iv": df["iv"], "rv": fwd, "ret30": ret30, "long": (px > sma50).astype(float),
        "sma_ok": sma50.notna(),
    }, index=df.index)
    out["vrp"] = (out["iv"] - out["rv"]) * 100                                         # vol points
    call = out["iv"].map(atm_call_pct)
    out["prem_straddle"] = 2 * call
    out["prem_put"] = call
    out["pl_straddle"] = out["prem_straddle"] * (1 - COST_FRAC) - out["ret30"].abs()
    out["pl_put"] = out["prem_put"] * (1 - COST_FRAC) - (-out["ret30"]).clip(lower=0)
    return out.dropna(subset=["rv", "ret30"])


# ── HAC statistics ────────────────────────────────────────────────────────

def nw_mean_t(x: pd.Series, lag: int = HORIZON) -> tuple[float, float]:
    x = x.dropna().to_numpy(float)
    n = len(x)
    e = x - x.mean()
    s = e @ e / n
    for k in range(1, min(lag, n - 1) + 1):
        w = 1 - k / (lag + 1)
        s += 2 * w * (e[k:] @ e[:-k]) / n
    se = math.sqrt(s / n)
    return float(x.mean()), float(x.mean() / se) if se > 0 else float("nan")


def nw_diff_t(y: pd.Series, flag: pd.Series, lag: int = HORIZON) -> tuple[float, float]:
    """OLS y = a + b*flag with Newey-West SE for b."""
    d = pd.concat([y, flag], axis=1).dropna()
    Y, F = d.iloc[:, 0].to_numpy(float), d.iloc[:, 1].to_numpy(float)
    X = np.column_stack([np.ones(len(Y)), F])
    beta = np.linalg.lstsq(X, Y, rcond=None)[0]
    u = Y - X @ beta
    n = len(Y)
    Xu = X * u[:, None]
    S = Xu.T @ Xu / n
    for k in range(1, lag + 1):
        w = 1 - k / (lag + 1)
        G = Xu[k:].T @ Xu[:-k] / n
        S += w * (G + G.T)
    XtX_inv = np.linalg.inv(X.T @ X / n)
    V = XtX_inv @ S @ XtX_inv / n
    se = math.sqrt(V[1, 1])
    return float(beta[1]), float(beta[1] / se) if se > 0 else float("nan")


# ── report ────────────────────────────────────────────────────────────────

def main() -> None:
    cache = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs/vrp_cache")
    btc_pkl = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("logs/binance_cache/BTCUSDT.pkl")
    dvol = fetch_dvol(cache)
    df = build_frame(dvol, load_btc(btc_pkl))
    print(f"DVOL {dvol.index[0].date()} → {dvol.index[-1].date()} ({len(dvol)} d); "
          f"analizuojama {len(df)} d. su pilnu {HORIZON} d. langu: {df.index[0].date()} → {df.index[-1].date()}\n")

    m, t1 = nw_mean_t(df["vrp"])
    print(f"H1  VRP (IV − realizuotas, vol pt): vid. {m:+.2f}  NW t {t1:+.2f}   "
          f"(vid. IV {df['iv'].mean()*100:.1f}%, vid. RV {df['rv'].mean()*100:.1f}%)")
    h1 = bool(m > 0 and t1 > T_H1)

    ms, t2 = nw_mean_t(df["pl_straddle"])
    yearly = df.groupby(df.index.year)["pl_straddle"].agg(["mean", "count"])
    pos_years = int((yearly["mean"] > 0).sum())
    print(f"H2  Straddle pardavimas, net, % nuo S: vid. {ms*100:+.2f}%/30d  NW t {t2:+.2f}  "
          f"teigiamų metų {pos_years}/{len(yearly)}")
    print("    pagal metus: " + "  ".join(f"{y}: {r['mean']*100:+.2f}% (n={int(r['count'])})" for y, r in yearly.iterrows()))
    h2 = bool(ms > 0 and t2 > T_H2 and pos_years >= MIN_POS_YEARS)

    d, t3 = nw_diff_t(df["pl_put"], df["long"])
    lo = df[df["long"] == 1]["pl_put"]; sh = df[df["long"] == 0]["pl_put"]
    print(f"H3  ATM put pardavimas: T50=LONG vid. {lo.mean()*100:+.2f}% (n={len(lo)}, p5 {lo.quantile(.05)*100:+.1f}%, "
          f"blogiausias {lo.min()*100:+.1f}%) | FLAT vid. {sh.mean()*100:+.2f}% (n={len(sh)}, p5 {sh.quantile(.05)*100:+.1f}%, "
          f"blogiausias {sh.min()*100:+.1f}%)")
    print(f"    skirtumas LONG−FLAT {d*100:+.2f}%  NW t {t3:+.2f}")
    h3 = bool(d > 0 and t3 > T_H3)

    print("\n── Aprašomieji (ne sprendimui) ──")
    nonov = df.iloc[::HORIZON]
    mm = nonov["pl_straddle"]
    tt = mm.mean() / (mm.std() / math.sqrt(len(mm))) if len(mm) > 2 else float("nan")
    print(f"Ne persidengiantys 30 d. blokai: n={len(nonov)}, straddle vid. {mm.mean()*100:+.2f}%, t {tt:+.2f}, "
          f"teigiamų {int((mm > 0).sum())}/{len(mm)}; VRP vid. {nonov['vrp'].mean():+.2f}")
    for c in (0.0, 0.03, 0.10):
        pl = df["prem_straddle"] * (1 - c) - df["ret30"].abs()
        print(f"Kaštų jautrumas {c*100:.0f}% premijos: straddle vid. {pl.mean()*100:+.2f}%")
    df["iv_bucket"] = pd.qcut(df["iv"], 3, labels=["žemas IV", "vidutinis IV", "aukštas IV"])
    g = df.groupby("iv_bucket", observed=True).agg(vrp=("vrp", "mean"), strad=("pl_straddle", "mean"), n=("vrp", "size"))
    for k, r in g.iterrows():
        print(f"  {k:12} VRP {r['vrp']:+.2f} pt | straddle {r['strad']*100:+.2f}% | n={int(r['n'])}")
    print(f"\nVerdiktas pagal registraciją: H1 {'PRAEINA' if h1 else 'ne'}, H2 {'PRAEINA' if h2 else 'ne'}, "
          f"H3 {'PRAEINA' if h3 else 'ne'}  →  pasiūlymas „parduok BTC premiją“: "
          f"{'PRAEINA (H1 ir H2)' if h1 and h2 else 'NEPRAEINA'}")


if __name__ == "__main__":
    main()
