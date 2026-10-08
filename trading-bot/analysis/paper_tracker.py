"""
Forward paper-trading of the two candidates that survived the daily-factor
study (research/daily_factors_prereg.md, research_daily_factors.py):

  * BTC  — long when the daily close is above its 50-day SMA, else flat (T50)
  * MOM  — weekly cross-sectional momentum: at each Sunday's daily close,
           rank the 30-coin universe by 14-day return, long the top 6 and
           short the bottom 6 (1/6 of equity per coin, so each leg is 100%
           of equity), hold 7 days (MOM14)

Purpose: the backtests are in-sample for these rules. The only thing that
can confirm or kill them is data that did not exist when the rules were
written, so this runs strictly forward — it starts at the first run and
never back-fills — with every parameter frozen to the pre-registration.
Nothing here places an order.

Each sleeve starts with START_EQUITY dollars of paper money. Costs match
the study: 0.04% per side on notional traded. Funding on the short leg is
NOT modeled (the study didn't either), so treat results as optimistic by
whatever shorting really costs.

State lives in logs/paper_portfolio.json. update() is idempotent: it
processes each completed UTC day exactly once, so running it every half
hour (or catching up after downtime) is safe.
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

STATE_FILE   = Path(__file__).resolve().parent.parent / "logs" / "paper_portfolio.json"
START_EQUITY = 100.0
SIDE_FEE     = 0.0004
K            = 6
LOOKBACK     = 14
SMA_N        = 50
MIN_COINS    = 2 * K
REBALANCE_DOW = 6            # Sunday close (Mon 00:00 UTC)
MIN_PERIODS_FOR_VERDICT = 40

# Same 30 coins as the study's universe (full daily history from 2025-03).
UNIVERSE = [
    "BTC", "ETH", "SOL", "XRP", "NEAR", "LINK", "DOGE", "PEPE", "TRX", "ATOM",
    "WLD", "STRK", "AAVE", "OP", "AVAX", "ZRO", "BNB", "BCH", "FARTCOIN", "TAO",
    "LTC", "BONK", "ENA", "APT", "PENGU", "ARB", "VIRTUAL", "DOT", "PIPPIN", "SUI",
]


# ── data ──────────────────────────────────────────────────────────────────

def fetch_closes(pairs: list[str] = UNIVERSE) -> pd.DataFrame:
    """Completed daily closes (UTC date index), one column per coin."""
    from .market_data import get_ohlcv

    today = pd.Timestamp(datetime.now(timezone.utc).date(), tz="UTC")
    cols = {}
    for pair in pairs:
        try:
            df = get_ohlcv(pair, "1d")
        except Exception as exc:
            logger.warning("paper_tracker: %s daily data unavailable — %s", pair, exc)
            continue
        s = df.set_index("timestamp")["close"]
        cols[pair] = s[s.index < today]          # drop today's unfinished candle
    return pd.DataFrame(cols).sort_index()


# ── state ─────────────────────────────────────────────────────────────────

def _load() -> Optional[dict]:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return None


def _save(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    os.replace(tmp, STATE_FILE)


def _day(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


# ── update ────────────────────────────────────────────────────────────────

def update(closes: Optional[pd.DataFrame] = None) -> list[str]:
    """
    Advance both sleeves through every completed day not yet processed.
    Returns Telegram-ready messages for events worth announcing (start,
    BTC trend flips, weekly rebalances). `closes` is injectable for tests.
    """
    if closes is None:
        closes = fetch_closes()
    if closes.empty or "BTC" not in closes or len(closes["BTC"].dropna()) < SMA_N + 1:
        logger.warning("paper_tracker: not enough BTC daily history yet")
        return []

    msgs: list[str] = []
    state = _load()
    latest = closes.index[-1]
    btc = closes["BTC"].dropna()

    if state is None:
        pos = int(btc.iloc[-1] > btc.iloc[-SMA_N:].mean())
        state = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "universe": [c for c in UNIVERSE if c in closes.columns],
            "btc": {"equity": START_EQUITY, "pos": pos, "last_day": _day(latest),
                    "bh_start": float(btc.iloc[-1]), "bh_last": float(btc.iloc[-1]), "flips": 0},
            "mom": {"equity": START_EQUITY, "last_day": _day(latest),
                    "positions": None, "periods": []},
        }
        _save(state)
        return [
            "📝 <b>Paper tracker pradėtas</b> ($%.0f kiekvienam sleeve)\n"
            "• BTC T50: dabar %s\n"
            "• MOM14 krepšelis: pirmas perbalansavimas po artimiausio sekmadienio dienos uždarymo (pirmadienį 00:00 UTC)\n"
            "<i>Tik stebėjimas, nieko neperkama.</i>" % (START_EQUITY, "LONG" if pos else "FLAT")
        ]

    before_btc = state["btc"]["last_day"]
    before_mom = state["mom"]["last_day"]
    new_days = [d for d in closes.index if _day(d) > min(before_btc, before_mom)]

    for d in new_days:
        if _day(d) > before_btc:
            m = _advance_btc(state["btc"], closes, d)
            if m:
                msgs.append(m)
        if _day(d) > before_mom:
            m = _advance_mom(state["mom"], closes, d)
            if m:
                msgs.append(m)
    state["btc"]["bh_last"] = float(btc.iloc[-1])
    _save(state)
    return msgs


def _advance_btc(s: dict, closes: pd.DataFrame, d) -> Optional[str]:
    series = closes["BTC"].dropna()
    series = series[series.index <= d]
    ret = series.iloc[-1] / series.iloc[-2] - 1
    s["equity"] *= 1 + s["pos"] * ret
    new_pos = int(series.iloc[-1] > series.iloc[-SMA_N:].mean()) if len(series) >= SMA_N else s["pos"]
    msg = None
    if new_pos != s["pos"]:
        s["equity"] *= 1 - SIDE_FEE
        s["flips"] += 1
        msg = ("📈 <b>Paper BTC T50: LONG</b> (kaina %.0f virš SMA50)" if new_pos
               else "📉 <b>Paper BTC T50: FLAT</b> (kaina %.0f po SMA50)") % series.iloc[-1]
        msg += f"\nEquity: ${s['equity']:.2f}"
    s["pos"] = new_pos
    s["last_day"] = _day(d)
    return msg


def _advance_mom(s: dict, closes: pd.DataFrame, d) -> Optional[str]:
    s["last_day"] = _day(d)
    if pd.Timestamp(d).dayofweek != REBALANCE_DOW:
        return None

    px_now = closes.loc[d]
    old = s["positions"]
    period_ret = 0.0
    if old:
        def leg(coins):
            r = [px_now[c] / old["entry"][c] - 1 for c in coins
                 if c in px_now.index and pd.notna(px_now[c])]
            return sum(r) / len(r) if r else 0.0
        period_ret = leg(old["longs"]) - leg(old["shorts"])
        s["equity"] *= 1 + period_ret

    loc = closes.index.get_loc(d)
    if loc < LOOKBACK:
        return None
    past = (px_now / closes.iloc[loc - LOOKBACK] - 1).dropna()
    if len(past) < MIN_COINS:
        return None
    ranked = past.sort_values()
    shorts, longs = list(ranked.index[:K]), list(ranked.index[-K:])

    old_w = {}
    if old:
        old_w.update({c: 1 / K for c in old["longs"]})
        old_w.update({c: -1 / K for c in old["shorts"]})
    new_w = {**{c: 1 / K for c in longs}, **{c: -1 / K for c in shorts}}
    turnover = sum(abs(new_w.get(c, 0) - old_w.get(c, 0)) for c in set(new_w) | set(old_w))
    s["equity"] *= 1 - turnover * SIDE_FEE

    s["positions"] = {"since": _day(d), "longs": longs, "shorts": shorts,
                      "entry": {c: float(px_now[c]) for c in longs + shorts}}
    if old:
        s["periods"].append({"day": _day(d), "ret": period_ret, "equity": s["equity"]})

    per = s["equity"] / K
    fmt = lambda coins: ", ".join(coins)
    return (
        f"🧺 <b>Paper MOM14 perbalansavimas</b> ({_day(d)} uždarymas)\n"
        + (f"Praeita savaitė: {period_ret*100:+.2f}%\n" if old else "")
        + f"Equity: ${s['equity']:.2f}\n\n"
        f"🟢 LONG (po ~${per:.1f}): {fmt(longs)}\n"
        f"🔴 SHORT (po ~${per:.1f}): {fmt(shorts)}\n"
        f"<i>Paper. Funding nemodeliuotas.</i>"
    )


# ── report ────────────────────────────────────────────────────────────────

def format_report() -> str:
    s = _load()
    if not s:
        return "📝 <b>Paper tracker</b>\n\nDar nepradėtas — pirmas paleidimas įvyks po kelių minučių."
    b, m = s["btc"], s["mom"]
    pct = lambda e: (e / START_EQUITY - 1) * 100
    bh = START_EQUITY * b["bh_last"] / b["bh_start"]
    periods = m["periods"]
    wins = sum(1 for p in periods if p["ret"] > 0)
    lines = [
        f"📝 <b>Paper tracker</b>  <i>(nuo {s['started_at'][:10]}, kiekvienas ${START_EQUITY:.0f})</i>\n",
        f"<b>BTC T50</b>: ${b['equity']:.2f} ({pct(b['equity']):+.1f}%) | dabar "
        f"{'LONG' if b['pos'] else 'FLAT'} | perjungimų: {b['flips']}",
        f"  BTC buy&hold: ${bh:.2f} ({pct(bh):+.1f}%)\n",
        f"<b>MOM14 krepšelis</b>: ${m['equity']:.2f} ({pct(m['equity']):+.1f}%) | "
        f"savaičių: {len(periods)}"
        + (f" | teigiamų: {wins}/{len(periods)}" if periods else ""),
    ]
    pos = m["positions"]
    if pos:
        lines.append(f"  🟢 {', '.join(pos['longs'])}")
        lines.append(f"  🔴 {', '.join(pos['shorts'])}  (nuo {pos['since']})")
    lines.append(
        f"\n<i>Išvadai reikia ≥{MIN_PERIODS_FOR_VERDICT} savaičių "
        f"({len(periods)} turime). Iki tol tai tik stebėjimas.</i>"
    )
    return "\n".join(lines)
