"""
Multi-cycle study on Binance spot daily data (point-in-time universe,
delisted coins included). Hypotheses, parameters and pass criteria are fixed
in research/multi_cycle_prereg.md, written before any return was computed.

Usage (from trading-bot/), after research_binance_data.py has run:
    .venv/bin/python research_multi_cycle.py [cache_dir=logs/binance_cache]
"""
import math
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from research_daily_factors import max_dd, sharpe, t_stat, trend_positions

SIDE_COST   = 0.0010          # 0.04% fee + 0.06% slippage per side
SHORT_DRAG  = 0.10 * 7 / 365  # 10%/yr on the short leg, per weekly period
K           = 6
UNIVERSE_N  = 30
MIN_HISTORY = 90
MIN_ELIGIBLE = 18
LOOKBACKS   = (7, 14, 28, 56)
T_PASS      = 2.7
MIN_PERIODS = 200
PERIODS = {
    "P1 2018-20": (pd.Timestamp("2018-01-01", tz="UTC"), pd.Timestamp("2020-12-31", tz="UTC")),
    "P2 2021-22": (pd.Timestamp("2021-01-01", tz="UTC"), pd.Timestamp("2022-12-31", tz="UTC")),
    "P3 2023-":   (pd.Timestamp("2023-01-01", tz="UTC"), pd.Timestamp("2100-01-01", tz="UTC")),
}


# ── data ──────────────────────────────────────────────────────────────────

def load_panel(cache_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    closes, vols = {}, {}
    today = pd.Timestamp.now(tz="UTC").normalize()
    for f in sorted(cache_dir.glob("*.pkl")):
        base, df = pickle.loads(f.read_bytes())
        if df.empty:
            continue
        df = df[df.index < today]
        closes[base] = df["close"]
        vols[base] = df["quote_volume"]
    close = pd.DataFrame(closes).sort_index()
    qv = pd.DataFrame(vols).sort_index().reindex(close.index)
    return close, qv


def eligible_universe(close: pd.DataFrame, qv: pd.DataFrame, t) -> list[str]:
    """Point-in-time: ≥90 days of history, a price at t, top-30 by 30d avg quote volume."""
    hist = close.loc[:t].notna().sum()
    ok = close.loc[t].notna() & (hist >= MIN_HISTORY)
    cand = ok[ok].index
    if len(cand) < MIN_ELIGIBLE:
        return []
    vol30 = qv.loc[:t].tail(30)[cand].mean()
    return list(vol30.sort_values(ascending=False).index[:UNIVERSE_N])


# ── study A ───────────────────────────────────────────────────────────────

def period_return(close: pd.DataFrame, coin: str, t, hold: int = 7) -> float:
    """Entry close at t -> last available close within (t, t+hold]; 0 if none."""
    i = close.index.get_loc(t)
    window = close[coin].iloc[i + 1: i + 1 + hold].dropna()
    entry = close[coin].iloc[i]
    if window.empty or not entry > 0:
        return 0.0
    return float(window.iloc[-1] / entry - 1)


def xs_weekly(close: pd.DataFrame, qv: pd.DataFrame, lookback: int, direction: int,
              side_cost: float = SIDE_COST, short_drag: float = SHORT_DRAG) -> pd.Series:
    """Net weekly returns of the long-top/short-bottom basket, Sunday closes."""
    out, prev_w = {}, {}
    idx = close.index
    for i, t in enumerate(idx):
        if t.dayofweek != 6 or i < lookback or i + 7 >= len(idx):
            continue
        uni = eligible_universe(close, qv, t)
        if not uni:
            continue
        past = (close.loc[t, uni] / close.iloc[i - lookback][uni] - 1).dropna()
        if len(past) < 2 * K:
            continue
        ranked = past.sort_values()
        longs = list(ranked.index[-K:] if direction > 0 else ranked.index[:K])
        shorts = list(ranked.index[:K] if direction > 0 else ranked.index[-K:])
        w = {**{c: 1 / K for c in longs}, **{c: -1 / K for c in shorts}}
        gross = (sum(period_return(close, c, t) for c in longs)
                 - sum(period_return(close, c, t) for c in shorts)) / K
        turnover = sum(abs(w.get(c, 0) - prev_w.get(c, 0)) for c in set(w) | set(prev_w))
        out[t] = gross - turnover * side_cost - short_drag
        prev_w = w
    return pd.Series(out, dtype=float)


def analyze_weekly(r: pd.Series) -> dict:
    res = {"n": len(r), "mean": float(r.mean()) if len(r) else float("nan"),
           "t": t_stat(r), "sharpe": sharpe(r, 52)}
    res["periods"] = {}
    for name, (a, b) in PERIODS.items():
        seg = r[(r.index >= a) & (r.index <= b)]
        res["periods"][name] = (len(seg), float(seg.mean()) if len(seg) else float("nan"))
    top10 = r.nlargest(10).sum()
    res["top10_share"] = float(top10 / r.sum()) if r.sum() > 0 else float("nan")
    pos_periods = sum(1 for n, m in res["periods"].values() if n and m > 0)
    p3 = res["periods"]["P3 2023-"]
    res["passes"] = bool(
        len(r) >= MIN_PERIODS and res["t"] > T_PASS and res["sharpe"] > 0.8
        and pos_periods >= 2 and p3[0] and p3[1] > 0
    )
    return res


def study_a(close: pd.DataFrame, qv: pd.DataFrame) -> None:
    print("═══ A: cross-sectional momentum / reversal (net, pagrindiniai kaštai) ═══")
    print(f"{'testas':7} {'n':>4} {'vid/sav':>8} {'t':>6} {'Sharpe':>7} "
          f"{'P1':>8} {'P2':>8} {'P3':>8} {'top10':>6}  praeina?")
    for L in LOOKBACKS:
        for name, d in ((f"MOM{L}", +1), (f"REV{L}", -1)):
            r = xs_weekly(close, qv, L, d)
            if len(r) < 10:
                print(f"{name:7} per mažai duomenų")
                continue
            a = analyze_weekly(r)
            p = a["periods"]
            fmt = lambda k: f"{p[k][1]*100:>7.2f}%" if p[k][0] else "    n/a "
            print(f"{name:7} {a['n']:>4} {a['mean']*100:>7.2f}% {a['t']:>6.2f} {a['sharpe']:>7.2f} "
                  f"{fmt('P1 2018-20')} {fmt('P2 2021-22')} {fmt('P3 2023-')} "
                  f"{a['top10_share']:>6.2f}  {'TAIP' if a['passes'] else 'ne'}")
    print("\nAprašomieji (ne sprendimui):")
    for L in LOOKBACKS:
        r0 = xs_weekly(close, qv, L, +1, side_cost=0.0004, short_drag=0.0)
        r1 = xs_weekly(close, qv, L, +1)
        w = r1[r1.index >= pd.Timestamp("2025-03-01", tz="UTC")]
        print(f"  MOM{L}: mažesni kaštai vid {r0.mean()*100:.2f}%/sav (t {t_stat(r0):.2f}) | "
              f"2025-03→ langas vid {w.mean()*100:.2f}%/sav (t {t_stat(w):.2f}, n {len(w)})")
    print()


# ── study B ───────────────────────────────────────────────────────────────

def strat_returns(close: pd.Series, pos: pd.Series, fee: float = SIDE_COST) -> pd.Series:
    ret = close.pct_change()
    held = pos.shift(1)
    cost = pos.shift(1).diff().abs().fillna(0) * fee
    return (held * ret - cost).where(held.notna())


def study_b(btc: pd.Series) -> None:
    print("═══ B: BTC trend following (net, 0.10%/pusę) ═══")
    bh = btc.pct_change().dropna()
    print(f"BTC istorija: {btc.index[0].date()} → {btc.index[-1].date()}")
    print(f"{'variantas':9} {'dalis':11} {'Sharpe':>7} {'B&H Sh':>7} {'return':>9} {'B&H ret':>9} {'maxDD':>7} {'B&H DD':>7}")
    for rule in ("T50", "T100", "T200", "D55"):
        r = strat_returns(btc, trend_positions(btc, rule)).dropna()
        wins = 0
        for name, (a, b) in PERIODS.items():
            rs = r[(r.index >= a) & (r.index <= b)]
            bs = bh.loc[rs.index]
            if len(rs) < 30:
                continue
            s_, bs_ = sharpe(rs, 365), sharpe(bs, 365)
            wins += s_ > bs_
            print(f"{rule:9} {name:11} {s_:>7.2f} {bs_:>7.2f} {((1+rs).prod()-1)*100:>8.0f}% "
                  f"{((1+bs).prod()-1)*100:>8.0f}% {max_dd(rs)*100:>6.0f}% {max_dd(bs)*100:>6.0f}%")
        bh_all = bh.loc[r.index]
        ok = wins >= 2 and sharpe(r, 365) > 0.8 and max_dd(r) > max_dd(bh_all)
        print(f"{rule:9} KOPA: Sharpe {sharpe(r,365):.2f} (B&H {sharpe(bh_all,365):.2f}), "
              f"maxDD {max_dd(r)*100:.0f}% (B&H {max_dd(bh_all)*100:.0f}%), laimėta dalių: {wins}/3 "
              f"→ {'PRAEINA' if ok else 'ne'}")
    print()


def main() -> None:
    cache_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs/binance_cache")
    close, qv = load_panel(cache_dir)
    print(f"Simbolių: {close.shape[1]} (įskaitant išlistintus), dienų: {close.shape[0]}, "
          f"{close.index[0].date()} → {close.index[-1].date()}\n")
    if "BTC" in close:
        study_b(close["BTC"].dropna())
    study_a(close, qv)
    print(f"Slenkstis A: |t|>{T_PASS}, Sharpe>0.8, ≥2/3 laikotarpių teigiami ir P3 teigiamas, n≥{MIN_PERIODS}. "
          f"Kaštai {SIDE_COST*100:.2f}%/pusę + 10%/m. šorto kojai.")


if __name__ == "__main__":
    main()
