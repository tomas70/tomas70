"""
Historical test of following Sonic AI's published XAUUSD trades on Evedex XAUTUSD.
Delays, costs, sample and pass criteria are fixed in research/sonic_follow_prereg.md
(committed before this code ran).

Usage (from trading-bot/):
    .venv/bin/python research_sonic_follow.py trades_raw.txt trades_new.csv [bars_cache.pkl]
"""
import math
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import analysis.sonic_follow as sf

SAMPLE_START = pd.Timestamp("2026-04-07", tz="UTC")
T_PASS = 3.0
CORR_MIN = 0.8
SCENARIOS = {
    "PIRMINIS: 0.04%/pusę + 0.20 USD spread": dict(fee=0.0004, half_spread=0.20),
    "tik spread'as (be fee)": dict(fee=0.0, half_spread=0.20),
    "0.02%/pusę + spread (maker? NEPATIKRINTA)": dict(fee=0.0002, half_spread=0.20),
    "CFD tipo 0.10 USD round trip": dict(fee=0.0, half_spread=0.05),
}


def load_trades(raw: Path, new: Path) -> list[dict]:
    trades = {t["open"]: t for t in sf.parse_raw(raw.read_text())}
    for t in sf.parse_new_csv(new.read_text()):
        trades.setdefault(t["open"], t)                    # same rule as the monitor's build.py
    return sorted(trades.values(), key=lambda t: t["open"])


def bars_cached(trades: list[dict], cache: Path) -> dict:
    store = pickle.loads(cache.read_bytes()) if cache.exists() else {}
    for i, t in enumerate(trades):
        key = t["open"].isoformat()
        if key not in store:
            try:
                store[key] = sf.bars_for(t)
            except Exception as exc:                       # keep going; counted as missing
                print(f"  candles failed {key}: {exc}")
                store[key] = {}
        if i % 40 == 39:
            cache.write_bytes(pickle.dumps(store))
    cache.write_bytes(pickle.dumps(store))
    return store


def summarise(label: str, df: pd.DataFrame, col: str) -> dict:
    v = df[col]
    t = sf.day_clustered_t(v, df["day"])
    pf = v[v > 0].sum() / -v[v <= 0].sum() if (v <= 0).any() else float("inf")
    print(f"    {label:44} vid. {v.mean():+6.2f} bps  laimi {(v > 0).mean()*100:3.0f}%  PF {pf:5.2f}  suma {v.sum():+8.0f} bps  dienų t {t:+5.2f}")
    return {"mean": float(v.mean()), "t": t}


def main() -> None:
    raw, new = Path(sys.argv[1]), Path(sys.argv[2])
    cache = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("sonic_bars.pkl")
    trades = load_trades(raw, new)
    sample, excl = [], {"prieš imties pradžią": 0, "laikymas > 12 h": 0}
    for t in trades:
        if t["open"] < SAMPLE_START:
            excl["prieš imties pradžią"] += 1
        elif (t["close"] - t["open"]).total_seconds() / 60 > sf.MAX_HOLD_MIN:
            excl["laikymas > 12 h"] += 1
        else:
            sample.append(t)
    print(f"Sonic sandorių iš viso {len(trades)}; imtis {len(sample)} ({sample[0]['open'].date()} → {sample[-1]['open'].date()}); "
          f"atmesta: {excl}")
    store = bars_cached(sample, cache)

    rows, missing = [], 0
    for t in sample:
        bars = store.get(t["open"].isoformat(), {})
        rec = {"open": t["open"], "day": t["open"].date().isoformat(), "sonic_pts": t["sonic_pts"]}
        ok = True
        for name, kw in SCENARIOS.items():
            for d in sf.DELAYS:
                r = sf.follow_trade(t, bars, d, **kw)
                if r is None:
                    ok = False
                    break
                rec[(name, d, "net")], rec[(name, d, "gross")], rec[(d, "pts")] = r["net"], r["gross"], r["gross_pts"]
            if not ok:
                break
        if ok:
            rows.append(rec)
        else:
            missing += 1
    df = pd.DataFrame(rows)
    print(f"Pilnai įvertinta {len(df)} sandorių; be Evedex žvakių: {missing}\n")

    prim = list(SCENARIOS)[0]
    print("═══ REZULTATAI PAGAL VĖLAVIMĄ IR KAŠTUS (bps nuo įėjimo kainos, 1 sandoris) ═══")
    res = {}
    for name in SCENARIOS:
        print(f"  {name}")
        for d in sf.DELAYS:
            tmp = pd.DataFrame({"v": df[(name, d, "net")], "day": df["day"]})
            res[(name, d)] = summarise(f"d = {d} min", tmp.rename(columns={"v": "net"}), "net")
    print("\n  Bruto (be kaštų):")
    for d in sf.DELAYS:
        g = df[(prim, d, "gross")]
        print(f"    d = {d} min: vid. bruto {g.mean():+.2f} bps = break-even round-trip kaštai (~{g.mean()/2:.2f} bps/pusę)")
    print(f"\n  Sonic paskelbtas vid. judesys: {df['sonic_pts'].mean():+.2f} USD/oz;  sekėjas d=0: {df[(0, 'pts')].mean():+.2f} USD/oz")

    corr = float(np.corrcoef(df["sonic_pts"], df[(0, "pts")])[0, 1])
    print(f"\n═══ KRITERIJAI (pirminis scenarijus) ═══\n  Sąlyga 4 — proxy koreliacija d=0 vs Sonic judesys: {corr:.2f} (reikia > {CORR_MIN})")
    c1 = res[(prim, 3)]["mean"] > 0 and res[(prim, 3)]["t"] > T_PASS
    c2 = res[(prim, 5)]["mean"] > 0
    thirds = np.array_split(df[(prim, 3, "net")].to_numpy(), 3)
    c3 = sum(x.mean() > 0 for x in thirds) >= 2
    print(f"  1  d=3: vid. {res[(prim, 3)]['mean']:+.2f} bps, dienų t {res[(prim, 3)]['t']:+.2f} (reikia > {T_PASS}):  {c1}")
    print(f"  2  d=5 vid. > 0 ({res[(prim, 5)]['mean']:+.2f}):  {c2}")
    print(f"  3  trečdaliai (d=3): " + ", ".join(f"{x.mean():+.2f}" for x in thirds) + f"  → ≥2 teigiami:  {c3}")
    ok = c1 and c2 and c3
    verdict = "PRAEINA" if ok else "NEPRAEINA"
    if corr <= CORR_MIN:
        verdict += "  (bet proxy koreliacija per maža — išvada NEAIŠKI)"
    print(f"\nVerdiktas pagal registraciją: {verdict}")
    print("(Nei vienas rezultatas netikrina, ar Sonic paskyra tikra.)")


if __name__ == "__main__":
    main()
