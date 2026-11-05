"""
Perp funding cash-and-carry (long spot / short perp) on Deribit funding history.
Variants, costs and pass criteria are fixed in research/funding_carry_prereg.md
(committed before any funding value was downloaded).

Usage (from trading-bot/):
    .venv/bin/python research_funding_carry.py [cache_dir] [binance_cache_dir]
"""
import pickle
import sys
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

import research_options_vrp as vr
from research_daily_factors import trend_positions

API = "https://www.deribit.com/api/v2/public/get_funding_rate_history"
START = pd.Timestamp("2019-06-01", tz="UTC")
CHUNK_DAYS = 10
ENTRY_COST = 0.0015                 # spot 0.10% + perp 0.05%, per switch on and per switch off
CAPITAL_MULT = 1.25
HURDLE = 0.04
LAG = 7
T_BTC, T_ETH = 3.0, 2.0
INCOMPLETE_MAX = 0.02
FULL_YEARS = range(2021, 2026)


# ── data ──────────────────────────────────────────────────────────────────

def fetch_funding(currency: str, cache: Path) -> pd.DataFrame:
    f = cache / f"funding_{currency.lower()}_1h.pkl"
    if f.exists():
        return pickle.loads(f.read_bytes())
    name = f"{currency}-PERPETUAL"
    rows, t = {}, START
    end = pd.Timestamp.now(tz="UTC")
    while t < end:
        nxt = t + pd.Timedelta(days=CHUNK_DAYS)
        r = httpx.get(API, timeout=30, params={"instrument_name": name,
                                               "start_timestamp": int(t.timestamp() * 1000),
                                               "end_timestamp": int(nxt.timestamp() * 1000)})
        r.raise_for_status()
        for x in r.json()["result"]:
            rows[x["timestamp"]] = x["interest_1h"]
        t = nxt
    s = pd.Series(rows).sort_index()
    s.index = pd.to_datetime(s.index, unit="ms", utc=True)
    df = s.rename("interest_1h").to_frame()
    cache.mkdir(parents=True, exist_ok=True)
    f.write_bytes(pickle.dumps(df))
    return df


def daily_funding(hourly: pd.DataFrame, last_complete_day: pd.Timestamp | None = None) -> tuple[pd.Series, dict]:
    """Daily funding rate F_d (sum of hourly interest per UTC day); incomplete days are dropped."""
    g = hourly["interest_1h"].groupby(hourly.index.normalize())
    f, n = g.sum(), g.count()
    if last_complete_day is None:
        last_complete_day = hourly.index[-1].normalize() - pd.Timedelta(days=1)
    f, n = f[f.index <= last_complete_day], n[n.index <= last_complete_day]
    full = pd.date_range(f.index[0], f.index[-1], freq="D", tz="UTC")
    missing = len(full) - int((n.reindex(full).fillna(0) >= 24).sum())
    info = {"days": len(full), "incomplete": missing, "share": missing / len(full)}
    f = f[n >= 24].reindex(full)                                   # incomplete / absent days -> NaN (dropped later)
    return f, info


# ── strategy ──────────────────────────────────────────────────────────────

def carry_returns(F: pd.Series, variant: str, funding_mult: float = 1.0, cost_mult: float = 1.0) -> pd.Series:
    """Daily net P&L as a fraction of notional. C0: always on; C1: on if the trailing 7-day mean F (to d-1) > 0."""
    F = F.dropna()
    if variant == "C0":
        on = pd.Series(1.0, index=F.index)
    elif variant == "C1":
        trail = F.rolling(LAG).mean().shift(1)
        on = (trail > 0).astype(float).where(trail.notna())
        F, on = F[on.notna()], on.dropna()
    else:
        raise ValueError(variant)
    switches = on.diff().abs().fillna(on.iloc[0])                  # entering on day 0 costs an entry
    pnl = on * F * funding_mult - switches * ENTRY_COST * cost_mult
    pnl.iloc[-1] -= on.iloc[-1] * ENTRY_COST * cost_mult           # close the position at the end
    return pnl


def on_capital(pnl: pd.Series) -> pd.Series:
    return pnl / CAPITAL_MULT


def annual(pnl: pd.Series) -> float:
    return float(on_capital(pnl).mean() * 365)


def excess_t(pnl: pd.Series) -> float:
    return vr.nw_mean_t(on_capital(pnl) - HURDLE / 365, lag=LAG)[1]


def evaluate(label: str, F: pd.Series, variant: str, t_limit: float, full: bool) -> bool:
    pnl = carry_returns(F, variant)
    a, t = annual(pnl), excess_t(pnl)
    yr = on_capital(pnl).groupby(pnl.index.year).sum()
    yr_full = [yr[y] for y in FULL_YEARS if y in yr.index]
    stress = annual(carry_returns(F, variant, funding_mult=0.7, cost_mult=2.0))
    on_share = float((pnl != 0).mean())
    print(f"  {label:10} {variant}: grynoji metinė (nuo kapitalo 1.25×) {a*100:+.1f}%  NW t(vs 4%) {t:+.2f}  "
          f"| įjungta {on_share*100:.0f}% dienų  | stresas {stress*100:+.1f}%")
    print("             metai: " + "  ".join(f"{y}: {v*100:+.1f}%" for y, v in yr.items()))
    c1 = a > HURDLE and t > t_limit
    ok = c1 and stress > 0
    if full:
        pos_years = sum(v > 0 for v in yr_full)
        recent = float(on_capital(pnl)[pnl.index.year >= 2024].mean() * 365)
        c2 = pos_years >= 4 and recent >= HURDLE
        ok = c1 and c2 and stress > 0
        print(f"             kriterijai: 1 (>4% ir t>{t_limit}) {c1} | 2 (≥4/5 metų: {pos_years}/5; 2024-25 {recent*100:+.1f}% ≥4%) {c2} | "
              f"3 stresas>0 {stress > 0}")
    return ok


def main() -> None:
    cache = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs/vrp_cache")
    bcache = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("logs/binance_cache")
    data = {}
    for cur in ("BTC", "ETH"):
        F, info = daily_funding(fetch_funding(cur, cache))
        data[cur] = F
        print(f"{cur}: {F.index[0].date()} → {F.index[-1].date()}, {info['days']} d., neišsamių/trūkstamų {info['incomplete']} "
              f"({info['share']*100:.1f}%{' — VIRŠIJA 2% RIBĄ' if info['share'] > INCOMPLETE_MAX else ''})")
    print()

    verdict = {}
    for variant in ("C0", "C1"):
        print(f"═══ {variant} ═══")
        ok_btc = evaluate("BTC", data["BTC"], variant, T_BTC, full=True)
        eth_pnl = carry_returns(data["ETH"], variant)
        a_eth, t_eth = annual(eth_pnl), excess_t(eth_pnl)
        ok_eth = a_eth > HURDLE and t_eth > T_ETH
        print(f"  ETH        {variant}: grynoji metinė {a_eth*100:+.1f}%  NW t(vs 4%) {t_eth:+.2f}  → kriterijus 4 (>4% ir t>{T_ETH}): {ok_eth}")
        verdict[variant] = ok_btc and ok_eth
        print(f"  → {variant} {'PRAEINA' if verdict[variant] else 'NEPRAEINA'}\n")

    print("═══ APRAŠOMIEJI ═══")
    for cur in ("BTC", "ETH"):
        F = data[cur].dropna()
        ann = F.groupby(F.index.year).mean() * 365
        neg = F.groupby(F.index.year).apply(lambda x: (x < 0).mean())
        streak = (F < 0).astype(int).groupby((F >= 0).cumsum()).cumsum().max()
        worst30 = F.rolling(30).sum().min()
        h = len(F) // 2
        print(f"  {cur} funding (metinis, vid.): " + "  ".join(f"{y}: {v*100:+.1f}%" for y, v in ann.items()))
        print(f"     neigiamų dienų dalis: " + "  ".join(f"{y}: {v*100:.0f}%" for y, v in neg.items()))
        print(f"     ilgiausia neigiama serija {int(streak)} d.; blogiausia 30 d. suma {worst30*100:+.2f}% nominalo; "
              f"pirma pusė {F.iloc[:h].mean()*365*100:+.1f}%/m. → antra pusė {F.iloc[h:].mean()*365*100:+.1f}%/m.")
    c0, c1 = carry_returns(data["BTC"], "C0"), carry_returns(data["BTC"], "C1")
    print(f"  BTC C0 − C1 metinė grąža (nuo kapitalo): {annual(c0)*100:+.1f}% vs {annual(c1)*100:+.1f}%")
    btc = pd.read_pickle(bcache / "BTCUSDT.pkl")[1]["close"]
    btc.index = pd.to_datetime(btc.index, utc=True).normalize()
    pos = trend_positions(btc, "T50").astype(float).reindex(data["BTC"].index)
    Fb = data["BTC"]
    m = pos.notna() & Fb.notna()
    flat, long_ = m & (pos == 0), m & (pos > 0)
    print(f"  BTC funding T50 FLAT dienomis (n={int(flat.sum())}): vid. {Fb[flat].mean()*365*100:+.1f}%/m.; "
          f"T50 LONG dienomis (n={int(long_.sum())}): {Fb[long_].mean()*365*100:+.1f}%/m.")
    both = pd.concat([c0, btc.pct_change()], axis=1).dropna()
    print(f"  koreliacija C0 dienos P&L su BTC grąža: {both.corr().iloc[0, 1]:+.2f}")
    print(f"\nVerdiktas pagal registraciją: C0 {'PRAEINA' if verdict['C0'] else 'NEPRAEINA'}, "
          f"C1 {'PRAEINA' if verdict['C1'] else 'NEPRAEINA'}")
    print("(PRAEINA = tik „premija egzistuoja Deribit ir aplenkia grynuosius po kaštų“; bazės/likvidacijos rizika nemodeliuota.)")


if __name__ == "__main__":
    main()
