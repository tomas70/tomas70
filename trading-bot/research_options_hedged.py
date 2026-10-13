"""
Hedged option-premium structures on BTC/ETH (delta-hedged short ATM straddle, iron
butterfly). Structures, costs and pass criteria are fixed in
research/options_hedged_prereg.md (committed before this code was written).

Usage (from trading-bot/):
    .venv/bin/python research_options_hedged.py [dvol_cache_dir] [binance_cache_dir]
"""
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

import research_options_vrp as vr

HORIZON = vr.HORIZON
WING = 0.15
T_BTC, T_ETH = 3.0, 2.0
MIN_POS_YEARS = 4
DD_MEDIAN, DD_WORST = 0.30, 0.45


@dataclass(frozen=True)
class Params:
    c_opt: float = 0.03
    c_hedge: float = 0.0005
    wing_mult: float = 1.15


MAIN = Params()
STRESS = Params(c_opt=0.08, c_hedge=0.0015, wing_mult=1.30)

_erf = np.vectorize(math.erf, otypes=[float])


def ncdf(x: np.ndarray) -> np.ndarray:
    return 0.5 * (1 + _erf(np.asarray(x, float) / math.sqrt(2)))


def bs_call(S, K, sigma, T) -> np.ndarray:
    """Black-Scholes call, r = 0. Vectorised; T may be 0 (intrinsic)."""
    shape = np.broadcast(S, K, sigma, T).shape
    S, K, sigma, T = (np.array(a, float).ravel() for a in np.broadcast_arrays(S, K, sigma, T))
    out = np.maximum(S - K, 0.0)
    live = T > 0
    sd = sigma[live] * np.sqrt(T[live])
    d1 = (np.log(S[live] / K[live]) + 0.5 * sd ** 2) / sd
    out[live] = S[live] * ncdf(d1) - K[live] * ncdf(d1 - sd)
    return out.reshape(shape)


def bs_put(S, K, sigma, T) -> np.ndarray:
    return bs_call(S, K, sigma, T) - np.asarray(S, float) + np.asarray(K, float)


def _windows(S: np.ndarray, IV: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    n = len(S) - HORIZON
    idx = np.arange(n)[:, None] + np.arange(HORIZON + 1)[None, :]
    return S[idx], IV[idx], n


def simulate(S: np.ndarray, IV: np.ndarray, p: Params) -> pd.DataFrame:
    """Per-entry net P&L / S_t for structures A and B. S, IV: aligned daily arrays (IV as a fraction)."""
    SW, IW, n = _windows(S, IV)
    S0, ST = SW[:, 0], SW[:, -1]
    iv0, T0 = IW[:, 0], HORIZON / 365
    call0 = bs_call(S0, S0, iv0, T0)
    prem_s = 2 * call0 / S0
    settle = np.abs(ST - S0) / S0

    # A: daily delta hedge with the running DVOL, T remaining
    k = np.arange(HORIZON)[None, :]
    Tk = (HORIZON - k) / 365
    sd = IW[:, :HORIZON] * np.sqrt(Tk)
    d1 = (np.log(SW[:, :HORIZON] / S0[:, None]) + 0.5 * sd ** 2) / sd
    h = 2 * ncdf(d1) - 1
    hedge_pnl = (h * np.diff(SW, axis=1)).sum(axis=1) / S0
    trades = np.abs(np.diff(h, axis=1, prepend=0.0))
    hedge_cost = p.c_hedge * ((trades * SW[:, :HORIZON]).sum(axis=1) + np.abs(h[:, -1]) * ST) / S0
    a_prem = prem_s * (1 - p.c_opt)
    a = a_prem - settle + hedge_pnl - hedge_cost

    # B: iron butterfly, wings at +-15% with a skew mark-up on the wing vol
    kp, kc = (1 - WING) * S0, (1 + WING) * S0
    sw = p.wing_mult * iv0
    prem_w = (bs_put(S0, kp, sw, T0) + bs_call(S0, kc, sw, T0)) / S0
    wing_pay = (np.maximum(kp - ST, 0) + np.maximum(ST - kc, 0)) / S0
    b = (prem_s - prem_w) - p.c_opt * (prem_s + prem_w) - settle + wing_pay

    return pd.DataFrame({"A": a, "B": b, "A_prem": a_prem, "A_settle": -settle, "A_hedge": hedge_pnl,
                         "A_hcost": -hedge_cost, "B_prem": prem_s - prem_w}, )


def max_drawdown(returns: np.ndarray) -> float:
    eq = np.concatenate([[1.0], np.cumprod(1 + returns)])
    return float((1 - eq / np.maximum.accumulate(eq)).max())


def phase_drawdowns(pl: np.ndarray) -> np.ndarray:
    return np.array([max_drawdown(pl[p::HORIZON]) for p in range(HORIZON)])


def prepare(currency: str, cache: Path, bcache: Path) -> tuple[pd.DatetimeIndex, np.ndarray, np.ndarray]:
    dvol = vr.fetch_dvol(cache, currency)
    px = vr.load_btc(bcache / f"{currency}USDT.pkl")
    df = pd.DataFrame({"iv": dvol / 100, "px": px}).dropna()
    return df.index, df["px"].to_numpy(float), df["iv"].to_numpy(float)


def run(currency: str, cache: Path, bcache: Path, p: Params = MAIN) -> pd.DataFrame:
    idx, S, IV = prepare(currency, cache, bcache)
    out = simulate(S, IV, p)
    out.index = idx[: len(out)]
    full = vr.load_btc(bcache / f"{currency}USDT.pkl")                    # SMA warm-up on the full history
    out["long"] = (full > full.rolling(50).mean()).reindex(out.index).astype(float)
    return out


def summarise(label: str, df: pd.DataFrame, col: str) -> dict:
    x = df[col]
    m, t = vr.nw_mean_t(x)
    yr = x.groupby(x.index.year).agg(["mean", "count"])
    dd = phase_drawdowns(x.to_numpy())
    r = {"mean": m, "t": t, "pos_years": int((yr["mean"] > 0).sum()), "n_years": len(yr),
         "dd_med": float(np.median(dd)), "dd_worst": float(dd.max()), "years": yr}
    print(f"  {label:30} vid. {m*100:+.2f}%/30d  NW t {t:+.2f}  p5 {x.quantile(.05)*100:+.1f}%  "
          f"blogiausias {x.min()*100:+.1f}%  teigiamų dienų {(x > 0).mean()*100:.0f}%  "
          f"| metai teigiami {r['pos_years']}/{r['n_years']}  | DD mediana {r['dd_med']*100:.0f}% blogiausia {r['dd_worst']*100:.0f}%")
    return r


def main() -> None:
    cache = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs/vrp_cache")
    bcache = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("logs/binance_cache")
    btc, btc_s, eth = run("BTC", cache, bcache), run("BTC", cache, bcache, STRESS), run("ETH", cache, bcache)
    print(f"BTC {btc.index[0].date()} → {btc.index[-1].date()} ({len(btc)} įėjimų), "
          f"ETH {eth.index[0].date()} → {eth.index[-1].date()} ({len(eth)})\n")

    verdict = {}
    for col, name in (("A", "A delta-hedged straddle"), ("B", "B iron butterfly")):
        print(f"═══ {name} ═══")
        rb = summarise("BTC pagrindiniai", btc, col)
        rs = summarise("BTC stresas", btc_s, col)
        re_ = summarise("ETH pagrindiniai", eth, col)
        yrs = "  ".join(f"{y}: {r['mean']*100:+.2f}% (n={int(r['count'])})" for y, r in rb["years"].iterrows())
        print(f"  BTC pagal metus: {yrs}")
        c1 = rb["mean"] > 0 and rb["t"] > T_BTC
        c2 = rb["pos_years"] >= MIN_POS_YEARS
        c3 = rs["mean"] > 0
        c4 = re_["mean"] > 0 and re_["t"] > T_ETH
        c5 = rb["dd_med"] < DD_MEDIAN and rb["dd_worst"] < DD_WORST
        ok = all((c1, c2, c3, c4, c5))
        verdict[col] = ok
        print(f"  Kriterijai: 1 BTC t>{T_BTC}: {c1} | 2 metai≥{MIN_POS_YEARS}/6: {c2} | 3 stresas>0: {c3} | "
              f"4 ETH t>{T_ETH}: {c4} | 5 DD: {c5}  →  {'PRAEINA' if ok else 'NEPRAEINA'}\n")

    print("═══ APRAŠOMIEJI ═══")
    nonov = btc.iloc[::HORIZON]
    for col in ("A", "B"):
        print(f"  {col} ne persidengiantys (BTC, kas 30-a eilutė): n={len(nonov)}, vid. {nonov[col].mean()*100:+.2f}%, "
              f"teigiamų {int((nonov[col] > 0).sum())}/{len(nonov)}")
    print(f"  A skaidinys (BTC, vid. %S): premija po kaštų {btc['A_prem'].mean()*100:+.2f}, settlement "
          f"{btc['A_settle'].mean()*100:+.2f}, hedge P&L {btc['A_hedge'].mean()*100:+.2f}, hedge kaštai {btc['A_hcost'].mean()*100:+.2f}")
    print("  Kaštų jautrumas (A, BTC vid. %/30d):")
    for co in (0.0, 0.03, 0.08):
        row = []
        for ch in (0.0, 0.0005, 0.0015):
            d = run("BTC", cache, bcache, Params(co, ch, 1.15))
            row.append(f"c_hedge {ch*1e4:.0f}bps {d['A'].mean()*100:+.2f}%")
        print(f"    c_opt {co*100:.0f}%: " + " | ".join(row))
    for wm in (1.0, 1.15, 1.30):
        d = run("BTC", cache, bcache, Params(0.03, 0.0005, wm))
        print(f"  B, sparnų σ ×{wm}: vid. {d['B'].mean()*100:+.2f}%")
    for tag, mask in (("A | T50=LONG", btc["long"] == 1), ("A | T50=FLAT", btc["long"] == 0)):
        x = btc.loc[mask, "A"]
        print(f"  {tag}: n={len(x)} vid. {x.mean()*100:+.2f}% (NW t {vr.nw_mean_t(x)[1]:+.2f}; aprašomasis)")
    print(f"\nVerdiktas pagal registraciją: A {'PRAEINA' if verdict['A'] else 'NEPRAEINA'}, "
          f"B {'PRAEINA' if verdict['B'] else 'NEPRAEINA'}")
    print("(PRAEINA = tik „verta tikrinti su realiomis Deribit kotiruotėmis“.)")


if __name__ == "__main__":
    main()
