"""
Sonic "management shell" simulation WITHOUT a signal. Parameters fixed in research/sonic_shell_sim_prereg.md.

Usage: .venv/bin/python research_sonic_shell_sim.py day_bars_cache.pkl [n_seeds]
"""
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

STOP, TP, TRAIL_ACT, TRAIL_GIVE = 2.0, 4.0, 2.0, 1.0
TIME_STOP, DAY_TARGET, LOSS_WAIT = 35, 6.0, 170
TARGET_PER_DAY = 1.4
COSTS = {"K1": 0.15, "K2": 3.6}
H0, H1 = 3, 13
Q_VALUES = (0.5, 0.6, 0.7, 0.8, 0.85)


def load_prices(path: Path) -> pd.Series:
    store = pickle.loads(path.read_bytes())
    allb = {}
    for b in store.values():
        allb.update(b)
    px = pd.Series(allb).sort_index()
    px = px[(px.index >= "2026-04-07") & (px.index < "2026-10-31")]
    px = px[~px.index.duplicated()]
    return px.reindex(pd.date_range(px.index[0], px.index[-1], freq="min", tz="UTC")).ffill()


def exit_trade(path: np.ndarray, side: int, variant: str) -> tuple[float, int]:
    """path: prices from entry minute (index 0) onward. Returns (gross USD/oz, minutes held)."""
    e = path[0]
    mfe = 0.0
    for k in range(1, min(len(path), TIME_STOP + 1)):
        mv = side * (path[k] - e)
        if mv <= -STOP:
            return mv, k                                   # exits at that minute's open (slippage included)
        if variant == "E1" and mv >= TP:
            return mv, k
        if variant == "E2":
            mfe = max(mfe, mv)
            if mfe >= TRAIL_ACT and mfe - mv >= TRAIL_GIVE:
                return mv, k
    k = min(len(path) - 1, TIME_STOP)
    return side * (path[k] - e), k


def simulate(px: pd.Series, entry: str, variant: str, rng: np.random.Generator, q: float = 0.5) -> dict:
    v = px.to_numpy()
    idx = px.index
    pre5 = np.r_[np.full(5, np.nan), v[5:] - v[:-5]]
    fwd3 = np.r_[v[3:] - v[:-3], np.full(3, np.nan)]
    elig = (idx.hour >= H0) & (idx.hour <= H1) & (idx.dayofweek < 5)
    if entry == "V1":
        elig = elig & (np.abs(pre5) >= 1.0)
    cand = np.flatnonzero(elig & ~np.isnan(fwd3))
    # per-minute entry probability so that ~TARGET_PER_DAY trades occur on eligible days (rough, same for all variants)
    days = len(set(idx[cand].date)) or 1
    p = min(1.0, TARGET_PER_DAY * days / len(cand) * 1.6)
    trades, t_free, day_pnl, cur_day, day_closed = [], 0, {}, None, False
    ei = 0
    pick = rng.random(len(cand)) < p
    for j, i in enumerate(cand):
        if not pick[j] or i < t_free or i + TIME_STOP + 1 >= len(v):
            continue
        d = idx[i].date()
        if day_pnl.get(d, 0.0) >= DAY_TARGET:
            continue
        if entry == "V0":
            side = 1 if rng.random() < 0.5 else -1
        elif entry == "V1":
            side = -1 if pre5[i] > 0 else 1                 # against the 5-min move
        else:
            true = 1 if fwd3[i] > 0 else -1
            side = true if rng.random() < q else -true
        g, k = exit_trade(v[i:i + TIME_STOP + 2], side, variant)
        trades.append((i, d, g, k))
        day_pnl[d] = day_pnl.get(d, 0.0) + g
        t_free = i + k + (LOSS_WAIT if g <= 0 else 0)
    if not trades:
        return {}
    g = np.array([t[2] for t in trades])
    out = {"n": len(g), "win": float((g > 0).mean())}
    for name, c in COSTS.items():
        n = g - c
        pf = n[n > 0].sum() / -n[n <= 0].sum() if (n <= 0).any() else np.inf
        out[f"pf_{name}"] = float(min(pf, 99))
    dp = pd.Series({d: 0.0 for d in day_pnl})
    for t in trades:
        dp[t[1]] += t[2] - COSTS["K1"]
    out["lose_days"] = float((dp < 0).mean())
    eq = np.cumsum(g - COSTS["K1"])
    out["maxdd"] = float((np.maximum.accumulate(eq) - eq).max())
    out["day_pnl"] = float(dp.mean())
    out["trades_per_day"] = len(g) / len(dp)
    return out


def main() -> None:
    px = load_prices(Path(sys.argv[1]))
    seeds = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    print(f"{px.index[0].date()} → {px.index[-1].date()}, {len(px)} min, seeds {seeds}")
    print("Sonic po starto (x1): win 87%, PF 12.5, pralaimimų dienų 3%, ~1.4 sand./d., vid. dienos P&L ~$5.8/oz\n")
    combos = [("V0", None), ("V1", None)] + [("V2", q) for q in Q_VALUES]
    hdr = f"{'įėjimas':10} {'išėjimas':7} {'n/d':>5} {'win%':>11} {'PF K1':>13} {'PF K2':>13} {'pralaim.dienos%':>16} {'maxDD':>7} {'dienos P&L K1':>14}"
    print(hdr)
    for entry, q in combos:
        for variant in ("E1", "E2"):
            rows = [simulate(px, entry, variant, np.random.default_rng(1000 + s), q or 0.5) for s in range(seeds)]
            rows = [r for r in rows if r]
            df = pd.DataFrame(rows)

            def med(c, f=1.0):
                return f"{df[c].median()*f:.1f}"

            def rng_(c, f=1.0):
                return f"{df[c].quantile(.05)*f:.0f}-{df[c].quantile(.95)*f:.0f}"
            label = entry if q is None else f"V2 q={q}"
            print(f"{label:10} {variant:7} {df.trades_per_day.median():5.2f} {med('win',100):>5} ({rng_('win',100)}) "
                  f"{df.pf_K1.median():6.2f} ({df.pf_K1.quantile(.05):4.1f}-{df.pf_K1.quantile(.95):5.1f}) "
                  f"{df.pf_K2.median():6.2f} ({df.pf_K2.quantile(.05):4.1f}-{df.pf_K2.quantile(.95):5.1f}) "
                  f"{df.lose_days.median()*100:8.0f}%        {df.maxdd.median():7.1f} {df.day_pnl.median():14.2f}")


if __name__ == "__main__":
    main()
