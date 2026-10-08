"""
Daily-factor study: BTC trend following and alt cross-sectional momentum /
reversal. Hypotheses, parameters and pass criteria are fixed in
research/daily_factors_prereg.md (written before this was run).

Usage (from trading-bot/):
    .venv/bin/python research_daily_factors.py [cache_dir=logs/daily_cache]
"""
import math
import pickle
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

SIDE_FEE   = 0.0004          # 0.04% per side
K          = 6               # coins per leg
FULL_START = pd.Timestamp("2025-03-05", tz="UTC")
SPLIT      = pd.Timestamp("2023-01-01", tz="UTC")
T_PASS     = 2.7


# ── data ──────────────────────────────────────────────────────────────────

def fetch_daily(pair: str, cache_dir: Path) -> pd.Series:
    from analysis import market_data as md

    cache = cache_dir / f"{pair}_1d.pkl"
    if cache.exists():
        return pickle.loads(cache.read_bytes())
    ms, end, rows = md._GROUP_MS["1d"], datetime.now(timezone.utc), {}
    for _ in range(8):
        start = end - timedelta(milliseconds=1000 * ms)
        page = md._get_with_retry(
            f"{md.MARKET_DATA_BASE_URL}/api/history/{md.to_instrument(pair)}/list",
            params={"group": "1d", "after": start.isoformat(), "before": end.isoformat()},
        ).json()
        if not page:
            break
        for c in page:
            rows[c[0]] = float(c[2])          # close
        first = datetime.fromtimestamp(min(c[0] for c in page) / 1000, tz=timezone.utc)
        if first >= end:
            break
        end = first
    s = pd.Series(rows)
    s.index = pd.to_datetime(s.index, unit="ms", utc=True)
    s = s.sort_index()
    s = s[~s.index.duplicated()]
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(pickle.dumps(s))
    return s


# ── helpers ───────────────────────────────────────────────────────────────

def sharpe(r: pd.Series, per_year: int) -> float:
    r = r.dropna()
    if len(r) < 2 or r.std() == 0:
        return float("nan")
    return float(r.mean() / r.std() * math.sqrt(per_year))


def max_dd(r: pd.Series) -> float:
    eq = (1 + r.fillna(0)).cumprod()
    return float((eq / eq.cummax() - 1).min())


def t_stat(r: pd.Series) -> float:
    r = r.dropna()
    if len(r) < 3 or r.std() == 0:
        return float("nan")
    return float(r.mean() / (r.std() / math.sqrt(len(r))))


# ── study 1: BTC trend ────────────────────────────────────────────────────

def trend_positions(close: pd.Series, rule: str) -> pd.Series:
    """1/0 position decided at each day's close (applies to next day's return)."""
    if rule.startswith("T"):
        n = int(rule[1:])
        return (close > close.rolling(n).mean()).astype(float).where(close.rolling(n).mean().notna())
    if rule == "D55":
        hi = close.shift(1).rolling(55).max()
        lo = close.shift(1).rolling(20).min()
        pos, cur = [], 0.0
        for c, h, l in zip(close, hi, lo):
            if not np.isnan(h):
                if cur == 0.0 and c > h:
                    cur = 1.0
                elif cur == 1.0 and c < l:
                    cur = 0.0
            pos.append(cur if not np.isnan(h) else np.nan)
        return pd.Series(pos, index=close.index)
    raise ValueError(rule)


def strategy_returns(close: pd.Series, pos: pd.Series) -> pd.Series:
    ret  = close.pct_change()
    held = pos.shift(1)
    cost = pos.shift(1).diff().abs().fillna(0) * SIDE_FEE
    return (held * ret - cost).where(held.notna())


def study_trend(btc: pd.Series) -> None:
    print("═══ Tyrimas 1: BTC trend following (net) ═══")
    bh = btc.pct_change().dropna()
    segs = {"IS 2019-09→2022": lambda s: s[s.index < SPLIT], "OOS 2023→": lambda s: s[s.index >= SPLIT]}
    print(f"{'variantas':10} {'dalis':16} {'Sharpe':>7} {'B&H Sh':>7} {'return':>8} {'B&H ret':>8} {'maxDD':>7} {'B&H DD':>7}")
    verdicts = {}
    for rule in ("T50", "T100", "T200", "D55"):
        r = strategy_returns(btc, trend_positions(btc, rule)).dropna()
        oks = []
        for name, f in segs.items():
            rs, bs = f(r), f(bh).loc[f(r).index]
            tot = (1 + rs).prod() - 1
            btot = (1 + bs).prod() - 1
            sh, bsh = sharpe(rs, 365), sharpe(bs, 365)
            dd, bdd = max_dd(rs), max_dd(bs)
            print(f"{rule:10} {name:16} {sh:>7.2f} {bsh:>7.2f} {tot*100:>7.0f}% {btot*100:>7.0f}% {dd*100:>6.0f}% {bdd*100:>6.0f}%")
            oks.append((sh > bsh, sh > 0.8, dd > bdd, name))
        a = all(o[0] for o in oks); b = all(o[1] for o in oks); c = oks[1][2]
        verdicts[rule] = a and b and c
    for rule, v in verdicts.items():
        print(f"  {rule}: {'PRAEINA' if v else 'ne'}")
    print()


# ── study 2: cross-sectional ──────────────────────────────────────────────

def xs_returns(prices: pd.DataFrame, lookback: int, direction: int, hold: int, weekly: bool) -> pd.Series:
    """
    direction +1 = momentum (long top), -1 = reversal. Returns net return per
    holding period, indexed by the rebalance date.
    """
    idx = prices.index
    pos = [i for i in range(lookback, len(idx) - hold)
           if (not weekly or idx[i].dayofweek == 6)]          # Sunday close
    out, prev_w = {}, pd.Series(0.0, index=prices.columns)
    for i in pos:
        past = prices.iloc[i] / prices.iloc[i - lookback] - 1
        past = past.dropna()
        if len(past) < 2 * K:
            continue
        ranked = past.sort_values()
        longs  = ranked.index[-K:] if direction > 0 else ranked.index[:K]
        shorts = ranked.index[:K] if direction > 0 else ranked.index[-K:]
        w = pd.Series(0.0, index=prices.columns)
        w[longs] = 1 / K
        w[shorts] = -1 / K
        fwd = (prices.iloc[i + hold] / prices.iloc[i] - 1)
        gross = float((w * fwd.fillna(0)).sum())
        turnover = float((w - prev_w).abs().sum())
        out[idx[i]] = gross - turnover * SIDE_FEE
        prev_w = w
    return pd.Series(out)


def study_xs(prices: pd.DataFrame) -> None:
    print(f"═══ Tyrimas 2: alt'ų krepšelis ({prices.shape[1]} monetų, {prices.index[0].date()} → {prices.index[-1].date()}) ═══")
    tests = [("MOM7", 7, +1, 7, True), ("MOM14", 14, +1, 7, True), ("MOM28", 28, +1, 7, True),
             ("REV7", 7, -1, 7, True), ("REV14", 14, -1, 7, True), ("REV28", 28, -1, 7, True),
             ("REV1", 1, -1, 1, False)]
    print(f"{'testas':7} {'n':>4} {'vid/periodą':>12} {'t':>6} {'1/2':>8} {'2/2':>8} {'Sharpe':>7}  praeina?")
    for name, L, d, hold, weekly in tests:
        r = xs_returns(prices, L, d, hold, weekly)
        if len(r) < 3:
            print(f"{name:7} nėra duomenų")
            continue
        per_year = 52 if weekly else 365
        h = len(r) // 2
        h1, h2 = r.iloc[:h].mean(), r.iloc[h:].mean()
        t = t_stat(r); sh = sharpe(r, per_year)
        ok = len(r) >= 40 and t > T_PASS and h1 > 0 and h2 > 0 and sh > 1.0
        print(f"{name:7} {len(r):>4} {r.mean()*100:>11.2f}% {t:>6.2f} {h1*100:>7.2f}% {h2*100:>7.2f}% {sh:>7.2f}  {'TAIP' if ok else 'ne'}")
    print()


def main() -> None:
    cache_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs/daily_cache")
    from analysis.pair_selection import get_liquid_pairs

    series = {}
    for pair in get_liquid_pairs():
        try:
            s = fetch_daily(pair, cache_dir)
        except Exception as exc:
            print(f"! {pair}: {exc}")
            continue
        if len(s) and s.index[0] <= FULL_START:
            series[pair] = s
    print(f"Pilnos istorijos porų: {len(series)}\n")

    if "BTC" in series:
        study_trend(series["BTC"])
    prices = pd.DataFrame({p: s[s.index >= pd.Timestamp("2025-03-01", tz="UTC")] for p, s in series.items()})
    prices = prices.dropna(how="all")
    study_xs(prices)
    print(f"Slenkstis (tyrimas 2): t > {T_PASS}, abi pusės > 0, Sharpe > 1, ≥40 periodų. Kaštai {SIDE_FEE*100:.2f}%/pusė; funding nemodeliuotas.")


if __name__ == "__main__":
    main()
