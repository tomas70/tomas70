"""
Replay logged setups as MARKET entries instead of resting limits at the swept
level.

Why: with fills required (see trade_logger.simulate_limit_walk), the level
limit entry lost money, because the setups that run straight to TP1 never
retrace to the level and so never fill. This asks the opposite question on
the same rows: if the signal were taken at market the moment it arrived, is
the setup profitable?

Per logged row (current STRATEGY_EPOCH, status irrelevant):
  * entry  = open of the first 15m candle after logged_at (delay 0), or of
             the one after that (delay 1 = reacted within 15 minutes)
  * SL     = the logged SL (structural, unchanged)
  * TP1    = entry +/- TP1_FIXED_PCT, same rule the live strategy uses
  * R:R    = recomputed from the market entry; rows under MIN_RR_RATIO are
             dropped, because the live gate would have dropped them
  * walk   = from the entry candle (entry is its open); a candle touching
             both SL and TP1 counts as SL (pessimistic); 192 candles max,
             expired trades count 0R
  * costs  = ROUND_TRIP_FEE_PCT of notional charged per trade, shown as net R

Run from trading-bot/:  .venv/bin/python replay_market_entry.py [fee_pct] [--all]
Needs the real trade_log.csv and Evedex access (run it where the bot runs).
"""
import math
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

from config import MIN_RR_RATIO, STRATEGY_EPOCH, TP1_FIXED_PCT

MAX_CANDLES = 192
ROUND_TRIP_FEE_PCT = 0.08   # % of notional, entry + exit; override via argv[1]
DELAYS = (0, 1)
R_TARGETS = (1.5, 2.0, 3.0)   # fixed in advance; not tuned to the data


def market_entry_walk(
    after: pd.DataFrame,
    bias: str,
    sl: float,
    delay: int = 0,
    tp_pct: float = TP1_FIXED_PCT,
    min_rr: float = MIN_RR_RATIO,
    max_candles: int = MAX_CANDLES,
    tp_r: Optional[float] = None,
) -> dict:
    """
    `after` = candles strictly after the signal, oldest first. Returns a dict:
      status  "tp1_hit" | "sl_hit" | "expired" | "pending" | "invalid" | "low_rr"
      r       gross R (None unless resolved)
      risk_pct stop distance as a fraction of entry (None if invalid)

    tp_r: if given, TP1 sits tp_r x the stop distance from entry (a fixed-R
    target) instead of tp_pct from entry, and the min_rr gate is skipped —
    R:R is tp_r by construction. This keeps every row in play, where the
    fixed-percent target drops any setup whose stop is wide at market price.
    """
    if len(after) <= delay:
        return {"status": "pending", "r": None, "risk_pct": None}

    bullish = bias == "bullish"
    start   = after.iloc[delay:].reset_index(drop=True)
    entry   = float(start.iloc[0]["open"])

    risk = (entry - sl) if bullish else (sl - entry)
    if risk <= 0 or entry <= 0:       # price already through the stop
        return {"status": "invalid", "r": None, "risk_pct": None}
    risk_pct = risk / entry
    if tp_r is not None:
        rr = tp_r
        tp = entry + tp_r * risk if bullish else entry - tp_r * risk
    else:
        rr = tp_pct / risk_pct
        if rr < min_rr:
            return {"status": "low_rr", "r": None, "risk_pct": risk_pct}
        tp = entry * (1 + tp_pct) if bullish else entry * (1 - tp_pct)

    for _, c in start.iloc[:max_candles].iterrows():
        sl_hit = c["low"] <= sl if bullish else c["high"] >= sl
        tp_hit = c["high"] >= tp if bullish else c["low"] <= tp
        if sl_hit:                     # pessimistic when both touch
            return {"status": "sl_hit", "r": -1.0, "risk_pct": risk_pct}
        if tp_hit:
            return {"status": "tp1_hit", "r": rr, "risk_pct": risk_pct}

    if len(start) >= max_candles:
        return {"status": "expired", "r": 0.0, "risk_pct": risk_pct}
    return {"status": "pending", "r": None, "risk_pct": risk_pct}


def _wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d * 100, (c + m) / d * 100)


def summarize(results: list[dict], fee_pct: float) -> dict:
    done = [x for x in results if x["status"] in ("tp1_hit", "sl_hit", "expired")]
    wins = sum(1 for x in done if x["status"] == "tp1_hit")
    gross = [x["r"] for x in done]
    net = [x["r"] - (fee_pct / 100) / x["risk_pct"] for x in done]
    avg = lambda v: round(sum(v) / len(v), 2) if v else None
    lo, hi = _wilson(wins, len(done))
    return {
        "n": len(done),
        "wins": wins,
        "win_rate": round(wins / len(done) * 100, 1) if done else None,
        "ci": (round(lo), round(hi)),
        "gross_R": avg(gross),
        "net_R": avg(net),
        "pending": sum(1 for x in results if x["status"] == "pending"),
        "invalid": sum(1 for x in results if x["status"] == "invalid"),
        "low_rr": sum(1 for x in results if x["status"] == "low_rr"),
    }


def main() -> None:
    from analysis import trade_logger as tl
    from analysis.market_data import get_candles_since

    args    = [a for a in sys.argv[1:] if not a.startswith("--")]
    use_all = "--all" in sys.argv
    fee_pct = float(args[0]) if args else ROUND_TRIP_FEE_PCT
    # --all: include rows from before STRATEGY_EPOCH (SWEEP only — HOOK is a
    # different setup). SL is structural and independent of the entry rule
    # that changed at the epoch, so the market-entry replay is still valid.
    epoch   = datetime.fromisoformat("2000-01-01T00:00:00+00:00") if use_all \
              else datetime.fromisoformat(STRATEGY_EPOCH)

    rows = []
    for r in tl._read_rows():
        try:
            logged = datetime.fromisoformat(r["logged_at"])
            float(r["sl"])
        except (TypeError, ValueError):
            continue
        if use_all and r.get("entry_type") != "SWEEP":
            continue
        if logged >= epoch:
            rows.append((r, logged))
    if not rows:
        print("Nėra eilučių dabartinėje epochoje.")
        return

    earliest: dict[str, datetime] = {}
    for r, logged in rows:
        earliest[r["pair"]] = min(logged, earliest.get(r["pair"], logged))
    candles: dict[str, pd.DataFrame] = {}
    for pair, since in earliest.items():
        try:
            candles[pair] = get_candles_since(pair, "15m", since - timedelta(minutes=30))
        except Exception as exc:
            print(f"! {pair}: nepavyko gauti žvakių ({exc})")

    results: dict[int, list[dict]] = {d: [] for d in DELAYS}
    fixed_r: dict[tuple[int, float], list[dict]] = {(d, k): [] for d in DELAYS for k in R_TARGETS}
    for r, logged in rows:
        df = candles.get(r["pair"])
        if df is None or df.empty:
            continue
        after = df[df["timestamp"] > pd.Timestamp(logged)].reset_index(drop=True)
        for d in DELAYS:
            results[d].append(market_entry_walk(after, r["bias"], float(r["sl"]), delay=d))
            for k in R_TARGETS:
                fixed_r[(d, k)].append(
                    market_entry_walk(after, r["bias"], float(r["sl"]), delay=d, tp_r=k))

    resolved_limit = [r for r, _ in rows if r["status"] in ("tp1_hit", "sl_hit", "expired")]
    scope = "VISA istorija (SWEEP)" if use_all else f"Epocha nuo {STRATEGY_EPOCH[:10]}"
    print(f"{scope} | eilučių: {len(rows)} | fee {fee_pct}% round-trip\n")
    for d in DELAYS:
        s = summarize(results[d], fee_pct)
        label = "iškart (kita žvakė)" if d == 0 else "+1 žvakė (≤15 min vėliau)"
        print(f"MARKET įėjimas, {label}:")
        print(f"  sandorių {s['n']} | TP1 {s['wins']} | win rate {s['win_rate']}% "
              f"(95% CI {s['ci'][0]}–{s['ci'][1]}%)")
        print(f"  expectancy: {s['gross_R']}R bruto | {s['net_R']}R po fee")
        print(f"  praleista: R:R<{MIN_RR_RATIO} {s['low_rr']}, SL jau pramuštas {s['invalid']}, "
              f"dar nesprendžiasi {s['pending']}\n")
    print("MARKET įėjimas, TP = k × realus stop (visos eilutės, be R:R filtro):")
    for d in DELAYS:
        for k in R_TARGETS:
            s = summarize(fixed_r[(d, k)], fee_pct)
            be = round(100 / (1 + k), 1)   # breakeven win rate before fees
            print(f"  delay {d} | TP {k}R | n {s['n']} | win {s['win_rate']}% "
                  f"(CI {s['ci'][0]}–{s['ci'][1]}%, breakeven {be}%) | "
                  f"{s['gross_R']}R bruto, {s['net_R']}R po fee")
    print()
    print(f"Palyginimui — limit prie lygio (iš loga): užpildyta {len(resolved_limit)} iš {len(rows)}")


if __name__ == "__main__":
    main()
