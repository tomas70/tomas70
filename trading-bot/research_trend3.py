"""
TREND-3: BTC + ETH + gold (PAXG), per-asset SMA50 trend filter, inverse-volatility weights, perp funding paid on
the crypto legs. Rule, benchmarks and pass criteria are fixed in research/trend3_prereg.md.

Usage: .venv/bin/python research_trend3.py [cache_dir]
"""
import math
import pickle
import sys
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

import research_funding_carry as fc
from research_daily_factors import max_dd, sharpe
from research_t50_generalization import block_bootstrap_p

URL = "https://data-api.binance.vision/api/v3/klines"
SYMS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "GOLD": "PAXGUSDT"}
FETCH_START = pd.Timestamp("2020-06-01", tz="UTC")
WINDOW_START = pd.Timestamp("2020-08-28", tz="UTC")
SMA_N, VOL_N, WARM = 50, 60, 60
FEE, FEE_STRESS, GOLD_FUND_STRESS = 0.0010, 0.0025, 0.05
CRYPTO = ("BTC", "ETH")
P_LIMIT, DD_LIMIT = 0.05, 0.30


# ── data ──────────────────────────────────────────────────────────────────

def fetch_close(sym: str, cache: Path) -> pd.Series:
    f = cache / f"{sym}_1d.pkl"
    if f.exists():
        return pickle.loads(f.read_bytes())
    rows, t = [], FETCH_START
    while True:
        r = httpx.get(URL, timeout=30, params={"symbol": sym, "interval": "1d",
                                               "startTime": int(t.timestamp() * 1000), "limit": 1000})
        r.raise_for_status()
        b = r.json()
        if not b:
            break
        rows += [(pd.Timestamp(x[0], unit="ms", tz="UTC"), float(x[4])) for x in b]
        t = rows[-1][0] + pd.Timedelta(days=1)
        if len(b) < 1000:
            break
    s = pd.Series(dict(rows)).sort_index()
    s = s[s.index < pd.Timestamp.now(tz="UTC").normalize()]           # complete days only
    f.write_bytes(pickle.dumps(s))
    return s


def load_funding(cache: Path) -> pd.DataFrame:
    fc.START = FETCH_START
    out = {}
    for cur in CRYPTO:
        F, _ = fc.daily_funding(fc.fetch_funding(cur, cache))
        out[cur] = F
    return pd.DataFrame(out)


# ── portfolio ─────────────────────────────────────────────────────────────

def weights(close: pd.DataFrame, trend: bool) -> pd.DataFrame:
    """Position decided at close t (applies to t -> t+1). Inverse-vol over ALL assets, signal masks the invested ones."""
    ret = close.pct_change()
    vol = ret.rolling(VOL_N).std(ddof=1) * math.sqrt(365)
    inv = 1.0 / vol
    base = inv.div(inv.sum(axis=1), axis=0)
    sig = (close > close.rolling(SMA_N).mean()) if trend else pd.DataFrame(True, index=close.index, columns=close.columns)
    w = base.where(sig, 0.0)
    valid = close.rolling(SMA_N).mean().notna() & vol.notna() & (np.arange(len(close))[:, None] >= WARM)
    return w.where(valid.all(axis=1), np.nan)


def portfolio_returns(close: pd.DataFrame, w: pd.DataFrame, funding: pd.DataFrame, fee: float,
                      use_funding: bool, gold_fund: float = 0.0) -> pd.Series:
    ret = close.pct_change()
    held = w.shift(1)                                                    # weights chosen at t-1 earn the t return
    gross = (held * ret).sum(axis=1, min_count=1)
    cost = fee * w.diff().abs().sum(axis=1).shift(0)                      # cost charged on the day the weights change
    cost = cost.reindex(gross.index).fillna(0)
    fund = pd.Series(0.0, index=gross.index)
    if use_funding:
        for a in CRYPTO:
            fund = fund + held[a].fillna(0) * funding[a].reindex(gross.index).fillna(0)
    if gold_fund:
        fund = fund + held["GOLD"].fillna(0) * gold_fund / 365
    return (gross - cost - fund).where(held.notna().all(axis=1))


def ew_monthly(close: pd.DataFrame, fee: float, funding: pd.DataFrame, use_funding: bool) -> pd.Series:
    ret = close.pct_change().fillna(0)
    n = close.shape[1]
    wts = np.full(n, 1.0 / n)
    out, last_month = [], None
    for t in close.index:
        r = ret.loc[t].to_numpy()
        day = float((wts * r).sum())
        if use_funding:
            day -= sum(wts[list(close.columns).index(a)] * (funding[a].get(t, 0.0) if a in funding else 0.0)
                       for a in CRYPTO if not np.isnan(funding[a].get(t, 0.0)))
        wts = wts * (1 + r) / (1 + (wts * r).sum())
        if last_month is not None and t.month != last_month:
            day -= fee * np.abs(wts - 1.0 / n).sum()
            wts = np.full(n, 1.0 / n)
        last_month = t.month
        out.append(day)
    return pd.Series(out, index=close.index)


def single(close: pd.Series, funding: pd.Series, fee: float, use_funding: bool) -> pd.Series:
    sig = (close > close.rolling(SMA_N).mean()).astype(float).where(close.rolling(SMA_N).notna())
    held = sig.shift(1)
    r = held * close.pct_change() - fee * sig.diff().abs().fillna(0)
    if use_funding:
        r = r - held * funding.reindex(close.index).fillna(0)
    return r.where(held.notna())


def stats(name: str, r: pd.Series) -> dict:
    r = r.dropna()
    eq = (1 + r).cumprod()
    s = {"sharpe": sharpe(r, 365), "dd": max_dd(r), "cagr": float(eq.iloc[-1] ** (365 / len(r)) - 1), "n": len(r)}
    print(f"  {name:34} Sharpe {s['sharpe']:5.2f}  CAGR {s['cagr']*100:6.1f}%  maxDD {s['dd']*100:6.1f}%")
    return s


def main() -> None:
    cache = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("trend3_cache")
    cache.mkdir(parents=True, exist_ok=True)
    close = pd.DataFrame({k: fetch_close(v, cache) for k, v in SYMS.items()}).dropna()
    close = close[close.index >= WINDOW_START - pd.Timedelta(days=WARM + 5)]
    funding = load_funding(cache)
    print(f"Duomenys: {close.index[0].date()} → {close.index[-1].date()}  ({len(close)} d.)")
    ann = funding.mean() * 365 * 100
    print(f"Deribit funding vid. {ann.round(1).to_dict()} %/m. (visa imtis)\n")

    wt, wb = weights(close, True), weights(close, False)
    runs = {}
    for label, fee, uf, gf in (("PIRMINIS (perp funding)", FEE, True, 0.0), ("kaštų stresas 0.25%", FEE_STRESS, True, 0.0),
                               ("aukso funding 5%", FEE, True, GOLD_FUND_STRESS), ("spot (be funding)", FEE, False, 0.0)):
        runs[label] = {"T3": portfolio_returns(close, wt, funding, fee, uf, gf), "IVBH": portfolio_returns(close, wb, funding, fee, uf, gf),
                       "EW": ew_monthly(close, fee, funding, uf), "T50": single(close["BTC"], funding["BTC"], fee, uf)}
    common = runs["PIRMINIS (perp funding)"]["T3"].dropna().index
    common = common[common >= WINDOW_START + pd.Timedelta(days=WARM)]
    print(f"Lyginamas langas: {common[0].date()} → {common[-1].date()}  ({len(common)} d.)\n")

    for label, rr in runs.items():
        print(f"═══ {label} ═══")
        st = {k: stats(n, rr[k].reindex(common)) for k, n in (("T3", "TREND-3"), ("IVBH", "IV-B&H (be signalo)"),
                                                           ("EW", "EW-B&H mėn. rebalansas"), ("T50", "BTC T50"))}
        runs[label]["stats"] = st
        print()

    P = runs["PIRMINIS (perp funding)"]
    S = P["stats"]
    t3, bh = P["T3"].reindex(common), P["IVBH"].reindex(common)
    pb, p5 = block_bootstrap_p(t3.to_numpy(), bh.to_numpy())
    c1 = S["T3"]["sharpe"] > S["IVBH"]["sharpe"] and pb <= P_LIMIT
    c2 = S["T3"]["sharpe"] >= S["T50"]["sharpe"]
    c3 = S["T3"]["dd"] > S["IVBH"]["dd"] and S["T3"]["dd"] > -DD_LIMIT
    thirds = np.array_split(common, 3)
    won = [sharpe(t3.loc[th], 365) > sharpe(bh.loc[th], 365) for th in thirds]
    c4 = sum(won) >= 2
    sx = runs["kaštų stresas 0.25%"]["stats"]
    c5 = sx["T3"]["sharpe"] > sx["IVBH"]["sharpe"]
    gx = runs["aukso funding 5%"]["stats"]
    gb, _ = block_bootstrap_p(runs["aukso funding 5%"]["T3"].reindex(common).to_numpy(), runs["aukso funding 5%"]["IVBH"].reindex(common).to_numpy())
    c6 = gx["T3"]["sharpe"] > gx["IVBH"]["sharpe"] and gb <= P_LIMIT
    print("═══ KRITERIJAI (pirminis režimas) ═══")
    print(f"  1 Sharpe > IV-B&H ({S['T3']['sharpe']:.2f} vs {S['IVBH']['sharpe']:.2f}), bootstrap P={pb:.3f} (≤{P_LIMIT}): {c1}")
    print(f"  2 Sharpe ≥ BTC T50 ({S['T3']['sharpe']:.2f} vs {S['T50']['sharpe']:.2f}): {c2}")
    print(f"  3 maxDD {S['T3']['dd']*100:.1f}% vs IV-B&H {S['IVBH']['dd']*100:.1f}%, riba -{DD_LIMIT*100:.0f}%: {c3}")
    print(f"  4 trečdaliai (laimėta {won}): {c4}")
    print(f"  5 kaštų stresas Sharpe {sx['T3']['sharpe']:.2f} vs {sx['IVBH']['sharpe']:.2f}: {c5}")
    print(f"  6 aukso funding 5% (P={gb:.3f}): {c6}")
    ok = all((c1, c2, c3, c4, c5, c6))
    print(f"\nVerdiktas pagal registraciją: {'PRAEINA' if ok else 'NEPRAEINA'}")

    print("\n═══ APRAŠOMIEJI ═══")
    w = wt.reindex(common)
    print(f"  vid. bendra ekspozicija {w.sum(axis=1).mean()*100:.0f}%, dalis dienų visiškai grynuosiuose {(w.sum(axis=1) == 0).mean()*100:.0f}%, "
          f"vid. svoriai {w.mean().round(2).to_dict()}, apyvarta {w.diff().abs().sum(axis=1).mean()*365:.1f}×/m.")
    yr = pd.DataFrame({"T3": t3, "IVBH": bh, "T50": P["T50"].reindex(common)}).groupby(common.year).apply(lambda d: (1 + d).prod() - 1)
    print("  metinė grąža % :\n" + (yr * 100).round(1).to_string())
    print("  aukso T50 atskirai: Sharpe %.2f (aukso B&H %.2f)" % (
        sharpe(single(close["GOLD"], pd.Series(0.0, index=close.index), FEE, False).reindex(common), 365),
        sharpe(close["GOLD"].pct_change().reindex(common), 365)))
    print("  ETH T50 atskirai: Sharpe %.2f (ETH B&H %.2f)" % (
        sharpe(single(close["ETH"], funding["ETH"], FEE, True).reindex(common), 365), sharpe(close["ETH"].pct_change().reindex(common), 365)))


if __name__ == "__main__":
    main()
