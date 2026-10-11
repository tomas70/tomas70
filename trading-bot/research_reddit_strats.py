"""
Backtest of 7 Reddit strategies (Python replica; Pine v5 versions in
research/reddit/pine/). Assumptions, windows and criteria are fixed in
research/reddit/reddit_strategies_prereg.md (written before any result).

Usage (from trading-bot/):
    .venv/bin/python research_reddit_strats.py <cache_dir_with_BTCUSDT_1h.pkl_and_BTCUSDT_1d.pkl>
"""
import pickle
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

START_EQ = 1000.0
FEE = 0.001
TICK = 0.01
SLIP = 2 * TICK
RISK = 0.01
END = pd.Timestamp("2026-10-10", tz="UTC")
SPLIT = pd.Timestamp("2026-04-10", tz="UTC")
MAIN_START = pd.Timestamp("2023-10-10", tz="UTC")


# ── indicators (TradingView conventions) ──────────────────────────────────

def ema(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).ewm(span=n, adjust=False).mean().to_numpy()


def sma(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n).mean().to_numpy()


def wma(x: np.ndarray, n: int) -> np.ndarray:
    w = np.arange(1, n + 1)
    return pd.Series(x).rolling(n).apply(lambda a: float((a * w).sum() / w.sum()), raw=True).to_numpy()


def rma(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).ewm(alpha=1 / n, adjust=False).mean().to_numpy()


def rsi(c: np.ndarray, n: int) -> np.ndarray:
    d = np.diff(c, prepend=c[0])
    up, dn = np.where(d > 0, d, 0.0), np.where(d < 0, -d, 0.0)
    ru, rd = rma(up, n), rma(dn, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(rd == 0, 100.0, 100 - 100 / (1 + ru / rd))


def true_range(h, l, c):
    pc = np.r_[c[0], c[:-1]]
    return np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))


def atr(h, l, c, n: int = 14) -> np.ndarray:
    return rma(true_range(h, l, c), n)


def adx(h, l, c, n: int = 13) -> np.ndarray:
    up, dn = np.diff(h, prepend=h[0]), -np.diff(l, prepend=l[0])
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = rma(true_range(h, l, c), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi, mdi = 100 * rma(plus, n) / tr, 100 * rma(minus, n) / tr
        dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    return rma(np.nan_to_num(dx), n)


def bollinger(c, n=20, k=2.0):
    m = sma(c, n)
    s = pd.Series(c).rolling(n).std(ddof=0).to_numpy()
    return m, m + k * s, m - k * s


@dataclass
class Data:
    t: pd.DatetimeIndex
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray

    @classmethod
    def from_df(cls, df: pd.DataFrame) -> "Data":
        df = df[df.index < END]
        return cls(pd.DatetimeIndex(df.index), *(df[k].to_numpy(float) for k in ("open", "high", "low", "close")))

    def window(self, a: pd.Timestamp, b: pd.Timestamp) -> tuple[int, int]:
        return int(self.t.searchsorted(a)), int(self.t.searchsorted(b))


@dataclass
class Trade:
    side: int
    ei: int             # entry bar index (fill at that bar)
    ex: float           # entry price (before slippage)
    xi: int             # exit bar index
    xp: float           # exit price (before slippage)
    stop: Optional[float]


# ── engine ────────────────────────────────────────────────────────────────

def simulate_equity(d: Data, trades: list[Trade], s: int, e: int) -> dict:
    eq = START_EQ
    curve = np.full(e - s, np.nan)
    last_i = s
    pnls = []
    for tr in trades:
        entry = tr.ex + tr.side * SLIP
        exit_ = tr.xp - tr.side * SLIP
        if tr.stop is not None:
            dist = tr.side * (entry - tr.stop)
            if dist <= 0:
                continue
            qty = RISK * eq / dist
        else:
            qty = eq / entry
        qty = min(qty, eq / (entry * (1 + FEE)))                       # no leverage
        # flat until entry
        curve[last_i - s: tr.ei - s] = eq
        fee_in = FEE * qty * entry
        for j in range(tr.ei, tr.xi + 1):
            px = exit_ if j == tr.xi else d.c[j]
            curve[j - s] = eq + tr.side * qty * (px - entry) - fee_in
        pnl = tr.side * qty * (exit_ - entry) - fee_in - FEE * qty * exit_
        eq += pnl
        curve[tr.xi - s] = eq
        pnls.append(pnl)
        last_i = tr.xi + 1
    curve[last_i - s:] = eq
    curve = pd.Series(curve).ffill().fillna(START_EQ).to_numpy()
    peak = np.maximum.accumulate(curve)
    dd = float(((peak - curve) / peak).max() * 100)
    p = np.array(pnls)
    gp, gl = p[p > 0].sum(), -p[p < 0].sum()
    pf = float(gp / gl) if gl > 0 else (float("inf") if gp > 0 else float("nan"))
    rng = np.random.default_rng(0)
    if len(p) >= 5:
        boots = []
        for _ in range(2000):
            q = rng.choice(p, len(p))
            g, l_ = q[q > 0].sum(), -q[q < 0].sum()
            boots.append(g / l_ if l_ > 0 else 50.0)
        pf5 = float(np.percentile(boots, 5))
    else:
        pf5 = float("nan")
    return {"net_pct": (eq / START_EQ - 1) * 100, "trades": len(p), "win_pct": float((p > 0).mean() * 100) if len(p) else 0.0,
            "pf": pf, "pf5": pf5, "dd": dd}


StrategyFn = Callable[[Data, int, int], list[Trade]]


def _exit_loop(d: Data, ei, entry, side, stop, tp, e, close_exit: Optional[Callable] = None) -> tuple[int, float]:
    """Walk bars from ei. Intrabar SL first, then TP. Optional close-based exit -> next open."""
    for j in range(ei, e):
        o, h, l = d.o[j], d.h[j], d.l[j]
        if stop is not None and ((l <= stop) if side == 1 else (h >= stop)):
            gap = (o <= stop) if side == 1 else (o >= stop)
            return j, (o if (gap and j > ei) else stop)
        if tp is not None and ((h >= tp) if side == 1 else (l <= tp)):
            gap = (o >= tp) if side == 1 else (o <= tp)
            return j, (o if (gap and j > ei) else tp)
        if close_exit is not None and close_exit(j) and j + 1 < e:
            return j + 1, d.o[j + 1]
    return e - 1, d.c[e - 1]


# ── strategies ────────────────────────────────────────────────────────────

def s7_qs1(d: Data, s: int, e: int) -> list[Trade]:
    e34 = ema(d.c, 34); l20 = sma(d.l, 20); ad = adx(d.h, d.l, d.c, 13)
    out, i = [], s
    while i < e - 1:
        if e34[i] < l20[i] and ad[i] > 25:
            ei, entry = i + 1, d.o[i + 1]
            xi, xp = _exit_loop(d, ei, entry, 1, entry * 0.95, entry * 1.06, e)
            out.append(Trade(1, ei, entry, xi, xp, entry * 0.95))
            i = xi
        else:
            i += 1
    return out


def s8_qs2(d: Data, s: int, e: int) -> list[Trade]:
    w = [wma(d.c, n) for n in (20, 50, 100, 200)]
    out, i = [], s
    while i < e - 1:
        if all(d.c[i] > x[i] for x in w):
            ei, entry = i + 1, d.o[i + 1]
            xi, xp = _exit_loop(d, ei, entry, 1, entry * 0.85, entry * 1.25, e, lambda j: d.c[j] < w[2][j])
            out.append(Trade(1, ei, entry, xi, xp, entry * 0.85))
            i = xi
        else:
            i += 1
    return out


def s11_atr_breakout(d: Data, s: int, e: int) -> list[Trade]:
    a = atr(d.h, d.l, d.c, 14)
    out, i = [], max(s, 1)
    while i < e:
        level = max(d.o[i - 1], d.c[i - 1]) * 1.0025
        if d.h[i] >= level and not np.isnan(a[i - 1]):
            entry, A = max(level, d.o[i]), a[i - 1]
            stop = entry - 1.5 * A
            hh, xi, xp, armed_be, armed_trail = entry, None, None, False, False
            for j in range(i, e):
                if d.l[j] <= stop:
                    gap = d.o[j] <= stop and j > i
                    xi, xp = j, (d.o[j] if gap else stop)
                    break
                hh = max(hh, d.h[j])
                if not armed_be and d.h[j] >= entry + 1.5 * A:
                    armed_be = True
                    stop = max(stop, entry)
                if d.h[j] >= entry + 3 * A:
                    armed_trail = True
                    stop = max(stop, entry + 1.5 * A)
                if armed_trail:
                    stop = max(stop, hh - 1.5 * A)
            if xi is None:
                xi, xp = e - 1, d.c[e - 1]
            out.append(Trade(1, i, entry, xi, xp, entry - 1.5 * A))
            i = xi + 1
        else:
            i += 1
    return out


def s12_bb_ema(d: Data, s: int, e: int) -> list[Trade]:
    e200 = ema(d.c, 200); m, up, lo = bollinger(d.c); a = atr(d.h, d.l, d.c, 14)
    out, i = [], max(s, 4)
    while i < e - 1:
        trend = d.c[i] > e200[i] and e200[i] > e200[i - 1]
        touched = any(d.l[k] < lo[k] for k in range(i - 3, i))
        reversal = d.c[i] > d.o[i] and d.c[i] > d.h[i - 1]
        if trend and touched and reversal and d.o[i + 1] > e200[i + 1] and not np.isnan(up[i]):
            ei, entry = i + 1, d.o[i + 1]
            dist = min(a[i], 0.05 * entry)
            stop = entry - dist
            r15 = entry + 1.5 * dist
            xi, xp = None, None
            for j in range(ei, e):
                if d.l[j] <= stop:
                    xi, xp = j, (d.o[j] if (d.o[j] <= stop and j > ei) else stop)
                    break
                tp = min(r15, up[j - 1]) if j > ei else r15           # band known at previous close
                if d.h[j] >= tp:
                    xi, xp = j, (d.o[j] if (d.o[j] >= tp and j > ei) else tp)
                    break
            if xi is None:
                xi, xp = e - 1, d.c[e - 1]
            out.append(Trade(1, ei, entry, xi, xp, stop))
            i = xi
        else:
            i += 1
    return out


def s15_ema8(d: Data, s: int, e: int) -> list[Trade]:
    e8, e20 = ema(d.c, 8), ema(d.c, 20)
    out, i = [], max(s, 1)
    while i < e - 1:
        if d.c[i] > e8[i] and e8[i] > e20[i]:
            ei, entry = i + 1, d.o[i + 1]
            xi, xp = None, None
            for j in range(ei, e):
                lvl = e20[j - 1]
                if d.l[j] <= lvl:
                    xi, xp = j, (d.o[j] if (d.o[j] <= lvl and j > ei) else lvl)
                    break
            if xi is None:
                xi, xp = e - 1, d.c[e - 1]
            out.append(Trade(1, ei, entry, xi, xp, None))
            i = xi
        else:
            i += 1
    return out


def s3_rsi2(d: Data, s: int, e: int) -> list[Trade]:
    e150 = ema(d.c, 150); r2, r20 = rsi(d.c, 2), rsi(d.c, 20); a = atr(d.h, d.l, d.c, 14)
    out, i = [], max(s, 1)
    while i < e - 1:
        if d.c[i] > e150[i] and e150[i] > e150[i - 1] and r2[i] < 15:
            ei, entry = i + 1, d.o[i + 1]
            stop = entry - 2 * a[i]

            def exit_rule(j, ei=ei, entry=entry):
                held = j - ei + 1
                return ((r20[j - 1] >= 55 > r20[j]) or (held >= 1 and d.c[j] < entry) or (held >= 6 and d.c[j] > entry))

            xi, xp = _exit_loop(d, ei, entry, 1, stop, None, e, exit_rule)
            out.append(Trade(1, ei, entry, xi, xp, stop))
            i = xi
        else:
            i += 1
    return out


def _swing_high(h, upto: int, n: int = 5, lookback: int = 300):
    """Most recent confirmed pivot high (n left/right) at or before `upto`."""
    for m in range(upto - n, max(upto - lookback, n), -1):
        if h[m] == h[m - n: m + n + 1].max():
            return m, h[m]
    return None


def _swing_low(l, upto: int, n: int = 5, lookback: int = 300):
    for m in range(upto - n, max(upto - lookback, n), -1):
        if l[m] == l[m - n: m + n + 1].min():
            return m, l[m]
    return None


def s1_supply_demand(d: Data, s: int, e: int) -> list[Trade]:
    e200 = ema(d.c, 200)
    rng = np.maximum(d.h - d.l, 1e-12)
    body = np.abs(d.c - d.o) / rng
    bull = (d.c > d.o) & (body >= 0.5) & (d.c > e200)
    bear = (d.c < d.o) & (body >= 0.5) & (d.c < e200)
    touch = (d.l <= e200) & (d.h >= e200)
    out, i = [], max(s, 300)
    while i < e - 1:
        side = 0
        if touch[i - 2] and bull[i - 1] and bull[i]:
            side = 1
        elif touch[i - 2] and bear[i - 1] and bear[i]:
            side = -1
        if side:
            ei, entry = i + 1, d.o[i + 1]
            stop = d.l[i - 1] if side == 1 else d.h[i - 1]
            piv = _swing_high(d.h, i) if side == 1 else _swing_low(d.l, i)
            if piv is not None and side * (piv[1] - entry) > 0 and side * (entry - stop) > 0:
                tp = piv[1]
                xi, xp = _exit_loop(d, ei, entry, side, stop, tp, e)
                out.append(Trade(side, ei, entry, xi, xp, stop))
                i = xi
                continue
        i += 1
    return out


STRATS = [
    ("S1  Supply&Demand 200EMA", "1h", s1_supply_demand),
    ("S3  RSI-2 + 150EMA", "1d", s3_rsi2),
    ("S7  QS-1 EMA34/SMA20(low)+ADX", "1h", s7_qs1),
    ("S8  QS-2 WMA20/50/100/200", "1d", s8_qs2),
    ("S11 ATR proveržis + FreeRoll", "1d", s11_atr_breakout),
    ("S12 BB + 200EMA reversal", "1d", s12_bb_ema),
    ("S15 8 EMA / 20 EMA", "1d", s15_ema8),
]


def verdict(r: dict) -> str:
    if r["trades"] < 30:
        return "МАЛО ДАННЫХ"
    return "РАБОТАЕТ" if (r["pf"] > 1.3 and r["dd"] < 25) else "НЕ РАБОТАЕТ"


def evaluate(name: str, tf: str, fn: StrategyFn, data: dict[str, Data]) -> dict:
    d = data[tf]
    ms, me = d.window(MAIN_START, SPLIT)
    hs, he = d.window(SPLIT, END)
    main = simulate_equity(d, fn(d, ms, me), ms, me)
    hold = simulate_equity(d, fn(d, hs, he), hs, he)
    v = verdict(main)
    ov = ""
    if v == "РАБОТАЕТ":
        ov = "РАЗВАЛИЛАСЬ" if (hold["pf"] < 1 or hold["net_pct"] < 0) else "держится"
        if hold["trades"] < 30:
            ov += f" (hold-out tik {hold['trades']} sand.)"
    return {"name": name, "tf": tf, "main": main, "hold": hold, "verdict": v, "overfit": ov}


def load(cache: Path) -> dict[str, Data]:
    h = pickle.loads((cache / "BTCUSDT_1h.pkl").read_bytes())
    dd = pickle.loads((cache / "BTCUSDT_1d.pkl").read_bytes())
    return {"1h": Data.from_df(h), "1d": Data.from_df(dd)}


def main() -> None:
    data = load(Path(sys.argv[1]))
    res = [evaluate(n, tf, fn, data) for n, tf, fn in STRATS]
    res.sort(key=lambda r: (-(r["main"]["pf"] if np.isfinite(r["main"]["pf"]) else 99)))
    print(f"BTCUSDT, pagrindinis {MAIN_START.date()}→{SPLIT.date()}, hold-out {SPLIT.date()}→{END.date()}; "
          f"$%d, komisija %.1f%%, slippage %.2f USDT, rizika %.0f%%\n" % (START_EQ, FEE * 100, SLIP, RISK * 100))
    print(f"{'strategija':32} {'TF':3} {'grynasis%':>9} {'sand.':>5} {'win%':>5} {'PF':>5} {'PF5%':>5} {'DD%':>5}  verdiktas       | hold-out: net% sand. PF DD%  overfit")
    for r in res:
        m, h = r["main"], r["hold"]
        print(f"{r['name']:32} {r['tf']:3} {m['net_pct']:>+9.1f} {m['trades']:>5} {m['win_pct']:>5.0f} {m['pf']:>5.2f} "
              f"{m['pf5']:>5.2f} {m['dd']:>5.1f}  {r['verdict']:14} | {h['net_pct']:>+7.1f} {h['trades']:>3} {h['pf']:>5.2f} {h['dd']:>5.1f}  {r['overfit']}")


if __name__ == "__main__":
    main()
