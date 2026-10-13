"""
Frozen BTC T50 rule (long when daily close > SMA50, else flat) on ETH and on a
point-in-time top-10 basket. Rule, universe, costs and pass criteria are fixed in
research/t50_generalization_prereg.md (committed before any ETH/basket T50 statistic
was computed).

Usage (from trading-bot/):
    .venv/bin/python research_t50_generalization.py [binance_cache_dir]
"""
import math
import pickle
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import research_binance_data as rbd
from research_daily_factors import max_dd, sharpe, trend_positions
from research_multi_cycle import PERIODS, SIDE_COST, strat_returns

STRESS_COST = 0.0025
BOOT_N, BOOT_BLOCK, BOOT_SEED = 10_000, 30, 1
P_ETH, P_BASKET = 0.05, 0.025
T_ALPHA = 2.0
BASKET_N, MIN_HISTORY = 10, 90
EXTRA_STABLES = {"U", "RLUSD", "USDS", "USDSOLD", "USDSB", "BKRW", "KGST"}
EXACT_LEVERAGED = {"BULL", "BEAR"}


# ── data ──────────────────────────────────────────────────────────────────

def load_close(path: Path) -> pd.Series:
    """Daily close, dropping the (partial) bar of the day the cache file was written."""
    _, df = pickle.loads(path.read_bytes())
    fetched = pd.Timestamp(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)).normalize()
    s = df["close"]
    s.index = pd.to_datetime(s.index, utc=True).normalize()
    return s[s.index < fetched]


def tradable(base: str) -> bool:
    """Corrected universe filter (audit 2026-10-13): extra stablecoins and BULL/BEAR tokens excluded."""
    if base in EXTRA_STABLES or base in EXACT_LEVERAGED:
        return False
    return rbd.tradable_universe(base)


# ── statistics ────────────────────────────────────────────────────────────

def block_bootstrap_p(strat: np.ndarray, bh: np.ndarray, n_boot: int = BOOT_N, block: int = BOOT_BLOCK,
                      seed: int = BOOT_SEED) -> tuple[float, float]:
    """Joint moving-block bootstrap of the Sharpe difference; returns (one-sided P(diff <= 0), 5th pct)."""
    n = len(strat)
    rng = np.random.default_rng(seed)
    n_blocks = math.ceil(n / block)
    starts = rng.integers(0, n - block + 1, size=(n_boot, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_boot, -1)[:, :n]

    def sh(x):
        return x.mean(axis=1) / x.std(axis=1, ddof=1) * math.sqrt(365)
    diff = sh(strat[idx]) - sh(bh[idx])
    return float((diff <= 0).mean()), float(np.percentile(diff, 5))


def alpha_t(strat: pd.Series, bh: pd.Series, lag: int = 5) -> tuple[float, float, float]:
    """OLS strat = a + b*bh with Newey-West covariance; returns (alpha p.a., NW t of alpha, beta)."""
    y, x = strat.to_numpy(float), bh.to_numpy(float)
    X = np.column_stack([np.ones(len(x)), x])
    n = len(y)
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    u = y - X @ beta
    Xu = X * u[:, None]
    S = Xu.T @ Xu / n
    for k in range(1, lag + 1):
        G = Xu[k:].T @ Xu[:-k] / n
        S += (1 - k / (lag + 1)) * (G + G.T)
    A = np.linalg.inv(X.T @ X / n)
    V = A @ S @ A / n
    return float(beta[0] * 365), float(beta[0] / math.sqrt(V[0, 0])), float(beta[1])


def evaluate(label: str, strat: pd.Series, bh: pd.Series, strat_stress: pd.Series, p_limit: float) -> bool:
    d = pd.concat([strat, bh, strat_stress], axis=1).dropna()
    d.columns = ["s", "b", "x"]
    s, b = d["s"], d["b"]
    print(f"── {label} ──  dienų {len(d)}, {d.index[0].date()} → {d.index[-1].date()}")
    sh_s, sh_b = sharpe(s, 365), sharpe(b, 365)
    P, p5 = block_bootstrap_p(s.to_numpy(), b.to_numpy())
    c1 = sh_s > sh_b and P <= p_limit
    print(f"  Sharpe T50 {sh_s:.2f} vs B&H {sh_b:.2f} (skirtumas {sh_s-sh_b:+.2f}); bootstrap P(skirt.≤0) = {P:.3f} "
          f"(riba {p_limit}), 5-as pct {p5:+.2f}")
    a, t, beta = alpha_t(s, b)
    c2 = a > 0 and t > T_ALPHA
    print(f"  alfa vs beta-suderintas B&H: {a*100:+.1f}%/m., NW t {t:+.2f}, beta {beta:.2f} (reikia t > {T_ALPHA})")
    dd_s, dd_b = max_dd(s), max_dd(b)
    c3 = dd_s > dd_b
    print(f"  maxDD T50 {dd_s*100:.0f}% vs B&H {dd_b*100:.0f}%")
    wins, parts = 0, []
    for name, (lo, hi) in PERIODS.items():
        m = (d.index >= lo) & (d.index <= hi)
        if m.sum() < 30:
            continue
        ps, pb = sharpe(d.loc[m, "s"], 365), sharpe(d.loc[m, "b"], 365)
        wins += ps > pb
        parts.append(f"{name}: {ps:.2f} vs {pb:.2f}")
    c4 = wins >= 2
    print("  laikotarpiai: " + " | ".join(parts) + f"  → laimėta {wins}")
    sh_x = sharpe(d["x"], 365)
    c5 = sh_x > sh_b
    print(f"  kaštų stresas (0.25%/pusę): Sharpe {sh_x:.2f} vs B&H {sh_b:.2f}")
    ok = all((c1, c2, c3, c4, c5))
    print(f"  Kriterijai: 1 {c1} | 2 {c2} | 3 {c3} | 4 {c4} | 5 {c5}  →  {'PRAEINA' if ok else 'NEPRAEINA'}")
    ex = (s - b).groupby([d.index.year, d.index.month]).sum()
    top3 = ex.sort_values(ascending=False).head(3)
    print(f"  aprašomieji: metinė grąža T50 {((1+s).prod()**(365/len(s))-1)*100:.0f}% vs B&H {((1+b).prod()**(365/len(b))-1)*100:.0f}%; "
          f"top-3 mėn. sudaro {top3.sum()/ex.sum()*100:.0f}% viso pranašumo ({', '.join(f'{y}-{m:02d}' for y, m in top3.index)})")
    print()
    return ok


# ── ETH ───────────────────────────────────────────────────────────────────

def single_asset(close: pd.Series, rule: str = "T50", fee: float = SIDE_COST) -> pd.Series:
    return strat_returns(close, trend_positions(close, rule), fee)


def run_eth(close: pd.Series) -> tuple[bool, pd.Series]:
    print("═══ TESTAS 1 (PIRMINIS): ETH ═══")
    s = single_asset(close)
    ok = evaluate("ETH T50", s, close.pct_change(), single_asset(close, fee=STRESS_COST), P_ETH)
    for rule in ("T100", "T200"):
        r = single_asset(close, rule).dropna()
        print(f"  (aprašomasis, be verdikto) ETH {rule}: Sharpe {sharpe(r, 365):.2f} vs B&H {sharpe(close.pct_change().loc[r.index], 365):.2f}")
    print()
    return ok, s


# ── basket ────────────────────────────────────────────────────────────────

def month_universes(close: pd.DataFrame, qv: pd.DataFrame, n: int = BASKET_N) -> dict:
    """First-of-month -> top-n coins by 30d avg quote volume, using data up to the previous close."""
    out = {}
    hist = close.notna().cumsum()
    for t0 in close.index[close.index.to_series().dt.is_month_start | (close.index == close.index[0])]:
        prev = close.index[close.index.get_loc(t0) - 1] if close.index.get_loc(t0) > 0 else None
        if prev is None:
            continue
        ok = close.loc[prev].notna() & (hist.loc[prev] >= MIN_HISTORY)
        cand = [c for c in ok[ok].index if tradable(c)]
        if len(cand) < n:
            continue
        vol30 = qv.loc[:prev].tail(30)[cand].mean()
        out[t0] = list(vol30.sort_values(ascending=False).index[:n])
    return out


def basket_returns(close: pd.DataFrame, qv: pd.DataFrame, fee: float = SIDE_COST) -> tuple[pd.Series, pd.Series]:
    """(T50 basket, always-long basket): 10 slots x 10%, equal weights reset daily, monthly membership."""
    uni = month_universes(close, qv)
    starts = sorted(uni)
    member = pd.DataFrame(False, index=close.index, columns=close.columns)
    for i, t0 in enumerate(starts):
        t1 = starts[i + 1] if i + 1 < len(starts) else close.index[-1] + pd.Timedelta(days=1)
        member.loc[(member.index >= t0) & (member.index < t1), uni[t0]] = True
    pos = pd.DataFrame({c: trend_positions(close[c].dropna(), "T50").reindex(close.index) for c in close.columns}).fillna(0)
    ret = close.pct_change().fillna(0)
    w = 1.0 / BASKET_N
    have = close.notna()
    held_t50 = (member & have).astype(float) * (pos > 0).astype(float)
    held_bh = (member & have).astype(float)
    res = []
    for held in (held_t50, held_bh):
        r = w * (held.shift(1).fillna(0) * ret).sum(axis=1)
        r = r - fee * w * held.diff().abs().fillna(0).sum(axis=1)
        res.append(r[member.shift(1, fill_value=False).any(axis=1)])
    return res[0], res[1]


def run_basket(cache: Path) -> bool:
    print("═══ TESTAS 2 (ANTRINIS): point-in-time top-10 krepšelis ═══")
    closes, vols = {}, {}
    for f in sorted(cache.glob("*.pkl")):
        base, df = pickle.loads(f.read_bytes())
        if df.empty:
            continue
        fetched = pd.Timestamp(datetime.fromtimestamp(f.stat().st_mtime, timezone.utc)).normalize()
        df = df[df.index < fetched]
        closes[base], vols[base] = df["close"], df["quote_volume"]
    close = pd.DataFrame(closes).sort_index()
    close.index = pd.to_datetime(close.index, utc=True).normalize()
    qv = pd.DataFrame(vols).sort_index()
    qv.index = pd.to_datetime(qv.index, utc=True).normalize()
    qv = qv.reindex(close.index)
    s, b = basket_returns(close, qv)
    sx, _ = basket_returns(close, qv, STRESS_COST)
    print(f"  universas: {len(close.columns)} monetų cache'e (point-in-time, su delistuotomis)")
    ok = evaluate("Krepšelis T50", s, b, sx, P_BASKET)
    return ok


def main() -> None:
    cache = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs/binance_cache")
    eth = load_close(cache / "ETHUSDT.pkl")
    btc = load_close(cache / "BTCUSDT.pkl")
    ok_eth, s_eth = run_eth(eth)
    ok_bsk = run_basket(cache)
    s_btc = single_asset(btc)
    both = pd.concat([s_btc, s_eth], axis=1).dropna()
    print(f"Aprašomasis: BTC T50 ir ETH T50 dienos grąžų koreliacija {both.corr().iloc[0, 1]:.2f}")
    print(f"\nVerdiktas pagal registraciją: ETH {'PRAEINA' if ok_eth else 'NEPRAEINA'}, "
          f"krepšelis {'PRAEINA' if ok_bsk else 'NEPRAEINA'}")


if __name__ == "__main__":
    main()
