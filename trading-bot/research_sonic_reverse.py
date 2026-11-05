"""
EXPLORATORY reverse-engineering of Sonic AI's entries and exits (hypothesis generation, NOT a test).
Uses the published trade list (times converted to UTC by analysis/sonic_follow.server_to_utc) and
Evedex XAUTUSD 1-minute opens as the price path. Anything found here must be pre-registered and
tested on data it was not found on (see research/sonic_replica_prereg.md).

Usage: .venv/bin/python research_sonic_reverse.py trades_raw.txt trades_new.csv day_bars_cache.pkl
"""
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import analysis.sonic_follow as sf
from research_sonic_follow import load_trades

LAUNCH = pd.Timestamp("2025-09-18", tz="UTC")
SAMPLE_START = pd.Timestamp("2026-04-07", tz="UTC")


def path_series(bars: dict) -> pd.Series:
    return pd.Series(bars).sort_index()


def main() -> None:
    trades = load_trades(Path(sys.argv[1]), Path(sys.argv[2]))
    df = pd.DataFrame(trades)
    df["hold"] = (df["close"] - df["open"]).dt.total_seconds() / 60
    df = df[df.hold <= 720].copy()
    post = df[df.open >= LAUNCH]
    print(f"sandorių {len(df)}, po starto {len(post)}\n")

    print("── 1. Dienos laikas (UTC), po starto ──")
    h = post.open.dt.hour
    print("įėjimų pagal valandą:", h.value_counts().sort_index().to_dict())
    print("minutė valandoje (kas 10):", (post.open.dt.minute // 10 * 10).value_counts().sort_index().to_dict())
    print("savaitės diena (0=pr):", post.open.dt.dayofweek.value_counts().sort_index().to_dict())
    gap = (post.open - post.close.shift(1)).dt.total_seconds() / 3600
    print("valandų nuo ankstesnio uždarymo: mediana %.1f, 25%% %.1f, 75%% %.1f; tą pačią dieną %.0f%%" % (
        gap.median(), gap.quantile(.25), gap.quantile(.75), (post.open.dt.date == post.close.shift(1).dt.date).mean() * 100))
    print("sandorių per dieną (aktyvios dienos):", post.groupby(post.open.dt.date).size().value_counts().sort_index().to_dict())

    print("\n── 2. Išėjimai (po starto) — judesys USD/oz ──")
    w, l = post[post.sonic_pts > 0].sonic_pts, post[post.sonic_pts <= 0].sonic_pts
    print("laimėjimų kvantiliai 5/25/50/75/95:", w.quantile([.05, .25, .5, .75, .95]).round(2).tolist())
    print("pralaimėjimų kvantiliai 5/25/50/75/95:", l.quantile([.05, .25, .5, .75, .95]).round(2).tolist())
    hist = np.histogram(post.sonic_pts, bins=[-8, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6, 8, 12, 40])
    print("histograma:", {f"{hist[1][i]:g}..{hist[1][i+1]:g}": int(c) for i, c in enumerate(hist[0])})
    print("laikymas min (po starto): laimėjimai med. %.0f, pralaimėjimai med. %.0f; p90 %.0f" % (
        post[post.sonic_pts > 0].hold.median(), post[post.sonic_pts <= 0].hold.median(), post.hold.quantile(.9)))

    # market context, only where we have Evedex 1m
    store = pickle.loads(Path(sys.argv[3]).read_bytes())
    allb = {}
    for b in store.values():
        allb.update(b)
    px = path_series(allb)
    smp = df[(df.open >= SAMPLE_START)]
    rows = []
    for _, t in smp.iterrows():
        o, c, s = t.open.floor("min"), t.close.floor("min"), t.side
        seg = px.loc[o:c]
        if len(seg) < 2 or o - pd.Timedelta(minutes=60) not in px.index or o not in px.index:
            continue
        e = px.loc[o]
        rec = {"open": t.open, "side": s, "hold": t.hold, "pts": t.sonic_pts, "e": e}
        for k in (1, 3, 5, 15, 30, 60):
            rec[f"pre{k}"] = s * (e - px.get(o - pd.Timedelta(minutes=k), np.nan))
        for k in (1, 2, 3, 5, 10, 15):
            rec[f"post{k}"] = s * (px.get(o + pd.Timedelta(minutes=k), np.nan) - e)
        seg_dir = s * (seg - e)
        rec["mfe"], rec["mae"] = seg_dir.max(), seg_dir.min()
        rec["range60"] = px.loc[o - pd.Timedelta(minutes=60):o].max() - px.loc[o - pd.Timedelta(minutes=60):o].min()
        hi = px.loc[o - pd.Timedelta(minutes=60):o]
        rec["pos60"] = (e - hi.min()) / (hi.max() - hi.min()) if hi.max() > hi.min() else 0.5
        rows.append(rec)
    m = pd.DataFrame(rows)
    print(f"\n── 3. Rinkos kontekstas (Evedex XAUT, {len(m)} sandorių nuo {SAMPLE_START.date()}) ──")
    print("PRIEŠ įėjimą, judesys sandorio kryptimi (USD/oz): vid. [t]")
    for k in (1, 3, 5, 15, 30, 60):
        v = m[f"pre{k}"].dropna()
        print(f"   {k:>3} min: {v.mean():+6.2f}  t={v.mean()/(v.std()/np.sqrt(len(v))):+5.1f}   medianos {v.median():+.2f}   dalis teigiamų {(v > 0).mean()*100:.0f}%")
    print("PO įėjimo, judesys sandorio kryptimi: vid. [t]")
    for k in (1, 2, 3, 5, 10, 15):
        v = m[f"post{k}"].dropna()
        print(f"   {k:>3} min: {v.mean():+6.2f}  t={v.mean()/(v.std()/np.sqrt(len(v))):+5.1f}   dalis teigiamų {(v > 0).mean()*100:.0f}%")
    print("MFE/MAE sandorio kryptimi (iki uždarymo), USD/oz:")
    print("   MAE kvantiliai 10/25/50/75/90:", m.mae.quantile([.1, .25, .5, .75, .9]).round(2).tolist())
    print("   MFE kvantiliai 10/25/50/75/90:", m.mfe.quantile([.1, .25, .5, .75, .9]).round(2).tolist())
    print("   trade'ai, kurių MAE ≥ -0.5 USD: %.0f%%  (įėjimas beveik be neigiamo judesio)" % ((m.mae >= -0.5).mean() * 100))
    win = m[m.pts > 0]
    print("   laimėtojų: (MFE - realizuotas) mediana %.2f, dalis išėjusių per 0.5 nuo MFE: %.0f%%" % (
        (win.mfe - win.pts).median(), ((win.mfe - win.pts) <= 0.5).mean() * 100))
    print("pozicija 60 min diapazone (0 = dugnas, 1 = viršus), LONG: vid. %.2f | SHORT: vid. %.2f" % (
        m[m.side == 1].pos60.mean(), m[m.side == -1].pos60.mean()))
    print("60 min diapazonas (USD): mediana %.1f" % m.range60.median())
    m.to_pickle(Path(sys.argv[3]).with_name("reverse_context.pkl"))


if __name__ == "__main__":
    main()
