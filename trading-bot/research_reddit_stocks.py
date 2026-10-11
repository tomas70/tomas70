"""
Reddit strategies on stocks (IB daily data in research/reddit/data). Rules and
mapping fixed in research/reddit/reddit_strategies_prereg.md (addendum).

Usage (from trading-bot/):
    .venv/bin/python research_reddit_stocks.py [data_dir=research/reddit/data]
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import research_reddit_strats as rs

SPECIFIED = {("S3", "SPY"), ("S8", "NVDA"), ("S8", "SAP")}      # instruments named in the table
SYMBOLS = ["SPY", "NVDA", "SAP"]
STRATS = {name.split()[0]: (name, fn) for name, tf, fn in rs.STRATS if tf == "1d"}


def load_csv(path: Path) -> rs.Data:
    df = pd.read_csv(path)
    df.index = pd.to_datetime(df["date"], utc=True)
    return rs.Data.from_df(df)


def buy_hold(d: rs.Data, a, b) -> tuple[float, float]:
    s, e = d.window(a, b)
    c = d.c[s:e]
    peak = np.maximum.accumulate(c)
    return (c[-1] / d.o[s] - 1) * 100, float(((peak - c) / peak).max() * 100)


def main() -> None:
    ddir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("research/reddit/data")
    rows = []
    for sym in SYMBOLS:
        d = load_csv(ddir / f"{sym}_1d_ib.csv")
        bh_main, bh_dd = buy_hold(d, rs.MAIN_START, rs.SPLIT)
        bh_hold, _ = buy_hold(d, rs.SPLIT, rs.END)
        print(f"{sym}: buy&hold pagrindinis {bh_main:+.1f}% (DD {bh_dd:.1f}%), hold-out {bh_hold:+.1f}%")
        for key, (name, fn) in STRATS.items():
            r = rs.evaluate(name, "1d", fn, {"1d": d})
            r["sym"], r["spec"] = sym, (key, sym) in SPECIFIED
            rows.append(r)
    print()
    hdr = (f"{'strategija':30} {'inst.':5} {'tipas':9} {'grynasis%':>9} {'sand.':>5} {'win%':>5} {'PF':>5} {'PF5%':>5} {'DD%':>5}  "
           f"{'verdiktas':12} | hold-out: net% sand. PF  overfit")
    for title, flag in (("PAGAL LENTELĘ NURODYTI INSTRUMENTAI", True), ("INFORMACINIAI PRIEDAI (verdiktui neįskaitomi)", False)):
        print(f"═══ {title} ═══\n{hdr}")
        sub = sorted([r for r in rows if r["spec"] == flag], key=lambda r: -(r["main"]["pf"] if np.isfinite(r["main"]["pf"]) else 99))
        for r in sub:
            m, h = r["main"], r["hold"]
            print(f"{r['name']:30} {r['sym']:5} {'lentelė' if flag else 'extra':9} {m['net_pct']:>+9.1f} {m['trades']:>5} {m['win_pct']:>5.0f} "
                  f"{m['pf']:>5.2f} {m['pf5']:>5.2f} {m['dd']:>5.1f}  {r['verdict']:12} | {h['net_pct']:>+7.1f} {h['trades']:>3} {h['pf']:>5.2f}  {r['overfit']}")
        print()


if __name__ == "__main__":
    main()
