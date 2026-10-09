"""
Evaluates the Telegram group's signals against Evedex candles. Rules, models
and pass criteria are fixed in research/kralov_signals_prereg.md (written
before any outcome was computed).

Usage (from trading-bot/):
    .venv/bin/python research_kralov_eval.py <ledger.pkl> <candle_cache_dir>
"""
import math
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

UTC_OFFSET_H = 3
SIDE_COST = 0.0006
FUND_PER_YEAR = 0.10
MAX_BARS = 672                      # 7 days
LIMIT_BARS = 12
W = np.full(5, 0.2)
THIRDS = [(pd.Timestamp("2026-05-03", tz="UTC"), pd.Timestamp("2026-06-30 23:59", tz="UTC")),
          (pd.Timestamp("2026-07-01", tz="UTC"), pd.Timestamp("2026-08-31 23:59", tz="UTC")),
          (pd.Timestamp("2026-09-01", tz="UTC"), pd.Timestamp("2026-10-10", tz="UTC"))]
NON_CRYPTO = {"SPYUSD", "TSLAUSD", "XAGUSD", "XAUTUSD", "CLUSD", "COINUSD", "MSTRUSD"}
T_PASS, Z_PASS = 3.0, 3.0


class Candles:
    def __init__(self, df: pd.DataFrame):
        d = df.sort_values("timestamp").reset_index(drop=True)
        self.ts = pd.DatetimeIndex(d["timestamp"])
        self.o, self.h, self.l, self.c = (d[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        self.n = len(d)


def first_touch(arr_hit: np.ndarray) -> int:
    idx = np.flatnonzero(arr_hit)
    return int(idx[0]) if idx.size else -1


def walk(c: Candles, e: int, stop: int, forced: bool, entry: float, side: int, sl: float,
         tps: np.ndarray, be: bool = False, fill_bar_sl_only: bool = False) -> dict:
    """
    Run the 5-tranche ladder from bar e. Bars e..stop-1 are walked; if `forced`
    the remainder exits at open[stop], else at close[stop-1].
    """
    risk = side * (entry - sl)
    px = np.full(5, np.nan); bar = np.zeros(5, int); done = np.zeros(5, bool)
    sl_cur = sl
    for j in range(e, stop):
        o, h, l = c.o[j], c.h[j], c.l[j]
        if (l <= sl_cur) if side == 1 else (h >= sl_cur):
            gap = (o <= sl_cur) if side == 1 else (o >= sl_cur)
            p = o if (gap and j > e) else sl_cur
            for k in range(5):
                if not done[k]:
                    px[k], bar[k], done[k] = p, j, True
            break
        if not (fill_bar_sl_only and j == e):
            for k in range(5):
                if not done[k] and ((h >= tps[k]) if side == 1 else (l <= tps[k])):
                    px[k], bar[k], done[k] = tps[k], j, True
            if be and done[0] and sl_cur != entry:
                sl_cur = entry
        if done.all():
            break
    if not done.all():
        j = stop if forced else stop - 1
        p = c.o[j] if forced else c.c[j]
        for k in range(5):
            if not done[k]:
                px[k], bar[k], done[k] = p, j, True
    r_k = side * (px - entry) / abs(risk)
    hours = (bar - e + 1) * 0.25
    risk_pct = abs(risk) / entry
    gross = float((W * r_k).sum())
    cost = (2 * SIDE_COST + FUND_PER_YEAR / 8760 * float((W * hours).sum())) / risk_pct
    return {"gross": gross, "net": gross - cost, "risk_pct": risk_pct}


def resolve_levels(c: Candles, e: int, stop: int, entry: float, side: int, sl: float, tps: np.ndarray) -> list:
    """Per TP: 1 hit before SL, 0 SL first, None unresolved (same bar -> SL, pessimistic)."""
    h, l = c.h[e:stop], c.l[e:stop]
    sl_t = first_touch(l <= sl if side == 1 else h >= sl)
    out = []
    for k in range(5):
        t = first_touch(h >= tps[k] if side == 1 else l <= tps[k])
        if t < 0 and sl_t < 0:
            out.append(None)
        elif sl_t >= 0 and (t < 0 or sl_t <= t):
            out.append(0)
        else:
            out.append(1)
    return out


def simulate(c: Candles, s: dict, mode: str = "M0", be: bool = False, anti: bool = False):
    """Returns result dict, or a string reason when the signal is skipped."""
    T = pd.Timestamp(s["utc"])
    i0 = int(c.ts.searchsorted(T))
    if i0 >= c.n or (c.ts[i0] - T) > pd.Timedelta(minutes=30):
        return "no_data"
    side = 1 if s["side"] == "long" else -1
    tps = np.array(s["tps"], float); sl = s["sl"]
    f = None
    if s.get("T2") is not None:
        f = int(c.ts.searchsorted(pd.Timestamp(s["T2"])))
        if f >= c.n:
            f = None

    if mode in ("M0", "M1"):
        e = i0 + (1 if mode == "M1" else 0)
        if e >= c.n:
            return "open"
        entry, fill_only_sl = c.o[e], False
    else:                                              # L: limit at zone middle
        mid = (s["zone_lo"] + s["zone_hi"]) / 2
        e = None
        for j in range(i0, min(i0 + LIMIT_BARS, c.n)):
            if f is not None and j >= f:
                break
            touched = (c.l[j] <= mid) if side == 1 else (c.h[j] >= mid)
            if touched:
                e, entry = j, (min(mid, c.o[j]) if side == 1 else max(mid, c.o[j]))
                break
            if (c.h[j] >= tps[0]) if side == 1 else (c.l[j] <= tps[0]):
                return "missed"
        if e is None:
            return "unfilled"
        fill_only_sl = True

    if f is not None and f <= e:
        return "closed_before_entry"
    if side * (entry - sl) <= 0:
        return "invalid_sl"
    if side * (tps[0] - entry) <= 0:
        return "stale"

    last = min(e + MAX_BARS, c.n)                       # exclusive
    forced = f is not None and f < last
    if forced:
        stop = f
    elif e + MAX_BARS > c.n:
        return "open"                                   # still running at end of data
    else:
        stop = last
    if stop <= e:
        return "closed_before_entry"

    if anti:
        side, sl, tps = -side, 2 * entry - sl, 2 * entry - tps
    res = walk(c, e, stop, forced, entry, side, sl, tps, be=be, fill_bar_sl_only=fill_only_sl)
    res["levels"] = resolve_levels(c, e, stop, entry, side, sl, tps)
    risk = abs(entry - sl)
    res["p_geo"] = [risk / (risk + abs(tps[k] - entry)) for k in range(5)]
    return res


# ── data prep ─────────────────────────────────────────────────────────────

def prepare(ledger: dict) -> list[dict]:
    sigs = []
    for s in ledger["signals"]:
        d = dict(s)
        d["utc"] = pd.Timestamp(s["local_time"]).tz_localize("UTC") - pd.Timedelta(hours=UTC_OFFSET_H)
        sigs.append(d)
    by_inst: dict[str, list] = {}
    for s in sigs:
        by_inst.setdefault(s["instrument"], []).append(s)
    for lst in by_inst.values():
        lst.sort(key=lambda x: x["utc"])
        for i, s in enumerate(lst):
            nxt = next((x for x in lst[i + 1:] if x["side"] != s["side"]), None)
            s["T2"] = nxt["utc"] if nxt else None
    return sorted(sigs, key=lambda x: x["utc"])


def load_candles(cache_dir: Path) -> dict[str, Candles]:
    out = {}
    for f in cache_dir.glob("*.pkl"):
        df = pickle.loads(f.read_bytes())
        if len(df):
            out[f.stem] = Candles(df)
    return out


# ── statistics ────────────────────────────────────────────────────────────

def run(sigs: list[dict], C: dict[str, Candles], **kw) -> tuple[pd.DataFrame, dict]:
    rows, skipped = [], {}
    for s in sigs:
        c = C.get(s["instrument"])
        r = simulate(c, s, **kw) if c is not None else "no_data"
        if isinstance(r, str):
            skipped[r] = skipped.get(r, 0) + 1
            continue
        rows.append({"utc": s["utc"], "inst": s["instrument"], "side": s["side"], **r})
    return pd.DataFrame(rows), skipped


def day_t(df: pd.DataFrame, col: str = "net") -> tuple[float, float, int]:
    if df.empty:
        return float("nan"), float("nan"), 0
    g = df.groupby(df["utc"].dt.floor("D"))[col].mean()
    n = len(g)
    if n < 3 or g.std() == 0:
        return float(g.mean()), float("nan"), n
    return float(g.mean()), float(g.mean() / (g.std() / math.sqrt(n))), n


def z_skill(df: pd.DataFrame, k: int) -> tuple[float, float, int]:
    hits, ps = [], []
    for lv, pg in zip(df["levels"], df["p_geo"]):
        if lv[k] is not None:
            hits.append(lv[k]); ps.append(pg[k])
    if not hits:
        return float("nan"), float("nan"), 0
    hits, ps = np.array(hits, float), np.array(ps, float)
    var = (ps * (1 - ps)).sum()
    return float((hits.sum() - ps.sum()) / math.sqrt(var)), float(hits.mean()), len(hits)


def thirds(df: pd.DataFrame) -> list[float]:
    out = []
    for a, b in THIRDS:
        seg = df[(df["utc"] >= a) & (df["utc"] <= b)]
        out.append(float(seg["net"].mean()) if len(seg) else float("nan"))
    return out


def report(name: str, df: pd.DataFrame, skipped: dict, full: bool = True) -> None:
    if df.empty:
        print(f"{name}: nėra sandorių ({skipped})")
        return
    m, t, nd = day_t(df)
    th = thirds(df)
    print(f"{name:22} n {len(df):>4} dienų {nd:>3} | bruto {df['gross'].mean():+.3f}R net {df['net'].mean():+.3f}R "
          f"(dienų t {t:+.2f}) | trečdaliai {th[0]:+.3f} {th[1]:+.3f} {th[2]:+.3f}")
    if full:
        z = [z_skill(df, k) for k in range(5)]
        print("   TP pasiekta prieš SL (fakt. / z vs atsitiktinis): " +
              "  ".join(f"TP{k+1}: {z[k][1]*100:.0f}% z={z[k][0]:+.1f}" for k in range(5)))
        print(f"   praleista: {skipped}")


def main() -> None:
    ledger = pickle.load(open(sys.argv[1], "rb"))
    C = load_candles(Path(sys.argv[2]))
    sigs = prepare(ledger)
    print(f"Signalų: {len(sigs)}, instrumentų su žvakėmis: {len(C)}\n")

    print("═══ PAGRINDINIS: M0 (rinka, kita žvakė) ═══")
    base, sk = run(sigs, C, mode="M0")
    report("M0 signalas", base, sk)
    m, t, nd = day_t(base)
    z3, hit3, n3 = z_skill(base, 2)
    th = thirds(base)
    ok = bool(m > 0 and t > T_PASS and all(x > 0 for x in th) and z3 > Z_PASS)
    print(f"\nKriterijai: net>0 ir t>{T_PASS}: {m>0 and t>T_PASS} (t={t:.2f}); kiekvienas trečdalis>0: "
          f"{all(x > 0 for x in th)}; TP3 z>{Z_PASS}: {z3 > Z_PASS} (z={z3:.2f})  →  {'PRAEINA' if ok else 'NEPRAEINA'}\n")

    print("═══ APRAŠOMIEJI ═══")
    for label, kw in (("M1 (+15 min)", dict(mode="M1")), ("L (limit viduryje)", dict(mode="L")),
                      ("M0 + BE po TP1", dict(mode="M0", be=True)),
                      ("ANTI-signalas M0", dict(mode="M0", anti=True))):
        d, s = run(sigs, C, **kw)
        report(label, d, s, full=label.startswith("L") or label.startswith("ANTI"))
    print()
    print("Pagal pusę / klasę (M0):")
    for side in ("long", "short"):
        report(f"  {side}", base[base["side"] == side], {}, full=False)
    nc = base["inst"].isin(NON_CRYPTO)
    report("  kripto", base[~nc], {}, full=False)
    report("  akcijos/žaliavos", base[nc], {}, full=False)
    by_day = base.groupby(base["utc"].dt.floor("D"))["net"].mean().sort_values(ascending=False)
    tot = by_day.sum()
    print(f"\nViršutinės 10 dienų: {by_day.head(10).sum():.2f} iš {tot:.2f} dienų-R sumos "
          f"({'n/a' if tot <= 0 else f'{by_day.head(10).sum()/tot*100:.0f}%'})")

    upd = pd.DataFrame(ledger["updates"])
    tp = upd[upd.kind == "tp"]; cl = upd[upd.kind == "close"]
    print(f"\nGrupės pačios skelbiami: TP1 pranešimų {int((tp.n == 1).sum())} iš {len(sigs)} signalų; "
          f"uždarymų {len(cl)}: vid. {cl.profit_pct.mean():+.2f}%, neigiamų {int((cl.profit_pct < 0).sum())} "
          f"({(cl.profit_pct < 0).mean()*100:.0f}%), SL pranešimų: 0 (formate jų nėra).")


if __name__ == "__main__":
    main()
