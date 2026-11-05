"""
Forward paper-trading of BTC trend following (T50): long when the daily
close is above its 50-day SMA, otherwise flat.

Why this and nothing else: of everything tested, it is the only rule that
passed its pre-registered criteria across several market cycles
(research/multi_cycle_prereg.md; Binance spot 2017-2026, net of 0.10% per
side: Sharpe 1.29 vs 0.83 buy&hold, max drawdown -64% vs -83%, better than
buy&hold in all three regimes tested). It earns its edge in bear markets by
being flat; in bull markets it roughly matches buy&hold. It is BTC beta with
a drawdown brake, not alpha. The cross-sectional alt momentum basket that
used to be tracked here failed the same study (0 of 8 variants) and was
removed.

This tracker runs strictly forward from its first run and never back-fills,
so it checks what a backtest cannot: that the rule behaves the same on data
that did not exist when it was chosen, with realistic execution. Nothing
here places an order.

START_EQUITY dollars of paper money; 0.04% fee per side (the live Evedex
taker rate used elsewhere; the study used a stricter 0.10% incl. slippage).

Two sleeves are tracked, both with a spot-like and a PERP equity (the perp one pays/receives the
Deribit funding rate on crypto longs; research/funding_carry_results.txt: BTC funding was ~+14 %/yr
on T50 LONG days, which a spot-like paper account ignores):
  * btc  — BTC T50 (as above)
  * t3   — TREND-3 (research/trend3_prereg.md): BTC, ETH and gold (Evedex XAUT), each long above its
           own SMA50, inverse-volatility weights over the three, cash for assets without a signal.
           Failed its pre-registered bootstrap criterion (P 0.08) — tracked forward, not endorsed.
           Compared with the same weights without the signal (IV-B&H).

State lives in logs/paper_portfolio.json. update() is idempotent: it
processes each completed UTC day exactly once, so running it every half hour
(or catching up after downtime) is safe.
"""
import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

STATE_FILE   = Path(__file__).resolve().parent.parent / "logs" / "paper_portfolio.json"
START_EQUITY = 100.0
SIDE_FEE     = 0.0004
SMA_N        = 50
VOL_N        = 60
ASSETS       = ("BTC", "ETH", "GOLD")
FUNDED       = ("BTC", "ETH")                              # legs that pay Deribit perp funding
EVEDEX_SYMBOL = {"BTC": "BTC", "ETH": "ETH", "GOLD": "XAUT"}
DERIBIT_FUNDING = "https://www.deribit.com/api/v2/public/get_funding_rate_history"


# ── data ──────────────────────────────────────────────────────────────────

def fetch_closes() -> pd.DataFrame:
    """Completed daily closes (UTC date index): BTC, ETH and GOLD (Evedex XAUT). Missing assets are omitted."""
    from .market_data import get_ohlcv

    today = pd.Timestamp(datetime.now(timezone.utc).date(), tz="UTC")
    cols = {}
    for k, sym in EVEDEX_SYMBOL.items():
        try:
            c = get_ohlcv(sym, "1d").set_index("timestamp")["close"]
            cols[k] = c[c.index < today]                   # drop today's unfinished candle
        except Exception as exc:
            logger.warning("paper_tracker: %s daily candles unavailable: %s", k, exc)
    return pd.DataFrame(cols).sort_index()


def _funding_daily(currency: str, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, float]:
    """Deribit perp funding per complete UTC day in [start, end]: sum of hourly interest_1h (long pays when > 0)."""
    rows: dict[int, float] = {}
    t = start.normalize()
    stop = end.normalize() + pd.Timedelta(days=1)
    while t < stop:
        nxt = min(t + pd.Timedelta(days=10), stop)
        r = httpx.get(DERIBIT_FUNDING, timeout=20, params={
            "instrument_name": f"{currency}-PERPETUAL",
            # Deribit's start is exclusive and records are stamped at hour boundaries, so [t, nxt) needs t-1ms .. nxt-1ms
            "start_timestamp": int(t.timestamp() * 1000) - 1, "end_timestamp": int(nxt.timestamp() * 1000) - 1})
        r.raise_for_status()
        for x in r.json().get("result", []):
            rows[x["timestamp"]] = x["interest_1h"]
        t = nxt
    s = pd.Series(rows)
    if s.empty:
        return {}
    s.index = pd.to_datetime(s.index, unit="ms", utc=True)
    g = s.groupby(s.index.normalize())
    full = g.sum()[g.count() >= 24]
    return {_day(d): float(v) for d, v in full.items()}


# ── state ─────────────────────────────────────────────────────────────────

def _load() -> Optional[dict]:
    try:
        state = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return None
    state.pop("mom", None)          # sleeve removed; ignore it in older state files
    state.pop("universe", None)
    return state


def _save(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    os.replace(tmp, STATE_FILE)


def _day(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


# ── update ────────────────────────────────────────────────────────────────

FundingFn = Callable[[str, pd.Timestamp, pd.Timestamp], dict]


def update(closes: Optional[pd.DataFrame] = None, funding: Optional[FundingFn] = None) -> list[str]:
    """
    Advance through every completed day not yet processed. Returns Telegram-ready messages (start, trend
    flips). `closes` and `funding` are injectable for tests.
    """
    if closes is None:
        closes = fetch_closes()
    if closes.empty or "BTC" not in closes or len(closes["BTC"].dropna()) < SMA_N + 1:
        logger.warning("paper_tracker: not enough BTC daily history yet")
        return []
    funding = funding or _funding_daily

    btc = closes["BTC"].dropna()
    state = _load()
    msgs: list[str] = []

    if state is None:
        pos = int(btc.iloc[-1] > btc.iloc[-SMA_N:].mean())
        state = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "btc": {"equity": START_EQUITY, "equity_perp": START_EQUITY, "pos": pos,
                    "last_day": _day(btc.index[-1]), "funding_since": _day(btc.index[-1]),
                    "bh_start": float(btc.iloc[-1]), "bh_last": float(btc.iloc[-1]),
                    "flips": 0, "funding_paid": 0.0, "funding_missing": 0,
                    "sma": float(btc.iloc[-SMA_N:].mean())},
        }
        msgs.append(
            "📝 <b>Paper tracker pradėtas</b> ($%.0f)\n"
            "• BTC T50 (long kai kaina virš SMA50, kitaip FLAT): dabar <b>%s</b>\n"
            "<i>Tik stebėjimas, nieko neperkama. Signalai — kai keičiasi būsena.</i>"
            % (START_EQUITY, "LONG" if pos else "FLAT"))
    else:
        s = state["btc"]
        new_days = [d for d in btc.index if _day(d) > s["last_day"]]
        fmap = _funding_map("BTC", new_days, funding)
        for d in new_days:
            m = _advance(s, btc, d, fmap.get(_day(d)))
            if m:
                msgs.append(m)
        s["bh_last"] = float(btc.iloc[-1])
        s["sma"] = float(btc.iloc[-SMA_N:].mean())

    msgs += _t3_update(state, closes, funding)
    _save(state)
    return msgs


def _funding_map(currency: str, days: list, funding: FundingFn) -> dict:
    if not days:
        return {}
    try:
        return funding(currency, pd.Timestamp(days[0]), pd.Timestamp(days[-1])) or {}
    except Exception as exc:
        logger.warning("paper_tracker: %s funding unavailable (%s) — perp equity ignores funding for these days", currency, exc)
        return {}


def _advance(s: dict, btc: pd.Series, d, f: Optional[float] = None) -> Optional[str]:
    """One completed day for the BTC T50 sleeve. `f` = that day's Deribit funding rate (None = unknown)."""
    series = btc[btc.index <= d]
    ret = series.iloc[-1] / series.iloc[-2] - 1
    s.setdefault("equity_perp", s["equity"])                 # accounts created before funding tracking
    s.setdefault("funding_paid", 0.0)
    s.setdefault("funding_missing", 0)
    s.setdefault("funding_since", s["last_day"])
    s["equity"] *= 1 + s["pos"] * ret
    s["equity_perp"] *= 1 + s["pos"] * (ret - (f or 0.0))
    if s["pos"]:
        if f is None:
            s["funding_missing"] += 1
        else:
            s["funding_paid"] += f
    new_pos = int(series.iloc[-1] > series.iloc[-SMA_N:].mean()) if len(series) >= SMA_N else s["pos"]
    msg = None
    if new_pos != s["pos"]:
        s["equity"] *= 1 - SIDE_FEE
        s["equity_perp"] *= 1 - SIDE_FEE
        s["flips"] += 1
        msg = ("📈 <b>BTC T50: LONG</b>\nKaina %.0f uždarė virš SMA50 (%.0f). "
               "Paper: įėjimas dienos uždarymo kaina." if new_pos
               else "📉 <b>BTC T50: FLAT</b>\nKaina %.0f uždarė po SMA50 (%.0f). "
                    "Paper: išėjimas dienos uždarymo kaina.") % (
            series.iloc[-1], series.iloc[-SMA_N:].mean())
        msg += f"\nPaper equity: ${s['equity']:.2f} (perp su funding: ${s['equity_perp']:.2f})"
    s["pos"] = new_pos
    s["last_day"] = _day(d)
    return msg


# ── TREND-3 sleeve ────────────────────────────────────────────────────────

def _t3_target(close: pd.DataFrame) -> tuple[dict, dict, dict]:
    """(weights, signals, always-on inverse-vol weights) decided at the last row's close. Needs VOL_N+1 clean rows."""
    ret = close.pct_change()
    vol = ret.iloc[-VOL_N:].std(ddof=1) * math.sqrt(365)
    inv = 1.0 / vol
    base = inv / inv.sum()
    sig = close.iloc[-1] > close.iloc[-SMA_N:].mean()
    return ({a: float(base[a]) if bool(sig[a]) else 0.0 for a in ASSETS}, {a: bool(sig[a]) for a in ASSETS},
            {a: float(base[a]) for a in ASSETS})


def _t3_update(state: dict, closes: pd.DataFrame, funding: FundingFn) -> list[str]:
    if not all(a in closes for a in ASSETS):
        return []
    close = closes[list(ASSETS)].dropna()
    if len(close) < max(VOL_N + 1, SMA_N + 1):
        return []
    t3 = state.get("t3")
    if t3 is None:
        w, sig, wbh = _t3_target(close)
        state["t3"] = {"started_at": datetime.now(timezone.utc).isoformat(), "last_day": _day(close.index[-1]),
                       "equity": START_EQUITY, "equity_perp": START_EQUITY, "bh": START_EQUITY, "bh_perp": START_EQUITY,
                       "w": w, "sig": sig, "w_bh": wbh, "flips": 0, "funding_missing": 0}
        return ["📝 <b>TREND-3 paper pradėtas</b> ($%.0f)\n%s\n<i>BTC+ETH+auksas (XAUT), SMA50 filtras, atvirkštinis "
                "volatilumo svoris. Nepraėjo prereg bootstrap kriterijaus — tik stebime.</i>" % (START_EQUITY, _t3_line(w, sig))]

    msgs: list[str] = []
    new_days = [d for d in close.index if _day(d) > t3["last_day"]]
    fmaps = {a: _funding_map(a, new_days, funding) for a in FUNDED}
    for d in new_days:
        i = close.index.get_loc(d)
        if i < max(VOL_N, SMA_N):
            t3["last_day"] = _day(d)
            continue
        r = close.iloc[i] / close.iloc[i - 1] - 1
        for key, wkey, fkey in (("equity", "w", "equity_perp"), ("bh", "w_bh", "bh_perp")):
            w = t3[wkey]
            gross = sum(w[a] * float(r[a]) for a in ASSETS)
            fund = 0.0
            for a in FUNDED:
                f = fmaps[a].get(_day(d))
                if f is None:
                    if wkey == "w" and w[a] > 0:
                        t3["funding_missing"] += 1
                else:
                    fund += w[a] * f
            t3[key] *= 1 + gross
            t3[fkey] *= 1 + gross - fund
        w_new, sig_new, wbh_new = _t3_target(close.iloc[: i + 1])
        for key, fkey, old, new in (("equity", "equity_perp", t3["w"], w_new), ("bh", "bh_perp", t3["w_bh"], wbh_new)):
            fee = SIDE_FEE * sum(abs(new[a] - old[a]) for a in ASSETS)
            t3[key] *= 1 - fee
            t3[fkey] *= 1 - fee
        if sig_new != t3["sig"]:
            t3["flips"] += 1
            msgs.append("🔀 <b>TREND-3: signalai pasikeitė</b>\n%s\nPaper equity: $%.2f (perp su funding: $%.2f)"
                        % (_t3_line(w_new, sig_new), t3["equity"], t3["equity_perp"]))
        t3["w"], t3["sig"], t3["w_bh"] = w_new, sig_new, wbh_new
        t3["last_day"] = _day(d)
    return msgs


def _t3_line(w: dict, sig: dict) -> str:
    return " | ".join(f"{a}: {'✓' if sig[a] else '✗'} {w[a]*100:.0f}%" for a in ASSETS) + \
        f" | grynieji {max(0.0, 1 - sum(w.values()))*100:.0f}%"


# ── report ────────────────────────────────────────────────────────────────

def format_report() -> str:
    state = _load()
    if not state:
        return "📝 <b>Paper tracker</b>\n\nDar nepradėtas — pirmas paleidimas įvyks po kelių minučių."
    s = state["btc"]
    pct = lambda e: (e / START_EQUITY - 1) * 100
    bh = START_EQUITY * s["bh_last"] / s["bh_start"]
    lines = [
        f"📝 <b>Paper tracker — BTC T50</b>  <i>(nuo {state['started_at'][:10]}, ${START_EQUITY:.0f})</i>\n",
        f"Būsena: <b>{'LONG' if s['pos'] else 'FLAT'}</b> | perjungimų: {s['flips']}",
        f"Strategija (spot): ${s['equity']:.2f} ({pct(s['equity']):+.1f}%)",
    ]
    if "equity_perp" in s:
        lines.append(f"Strategija (perp, su funding): ${s['equity_perp']:.2f} ({pct(s['equity_perp']):+.1f}%)  "
                     f"<i>funding nuo {s.get('funding_since', '?')}, sumokėta {s.get('funding_paid', 0)*100:.2f}% nominalo"
                     + (f", trūko {s['funding_missing']} d." if s.get("funding_missing") else "") + "</i>")
    lines += [f"BTC buy&hold: ${bh:.2f} ({pct(bh):+.1f}%)",
              f"SMA50: {s.get('sma', 0):.0f} | BTC: {s['bh_last']:.0f}\n"]
    t3 = state.get("t3")
    if t3:
        lines += [f"🔀 <b>TREND-3</b>  <i>(nuo {t3['started_at'][:10]})</i>",
                  _t3_line(t3["w"], t3["sig"]),
                  f"Spot: ${t3['equity']:.2f} ({pct(t3['equity']):+.1f}%) | perp su funding: ${t3['equity_perp']:.2f} "
                  f"({pct(t3['equity_perp']):+.1f}%)",
                  f"Be signalo (IV-B&H): ${t3['bh']:.2f} ({pct(t3['bh']):+.1f}%) | signalų pasikeitimų: {t3['flips']}\n"]
    lines.append("<i>BTC T50 patikrinta 2017–2026 (Sharpe 1.29 vs 0.83, maxDD -64% vs -83%); TREND-3 pagal prereg "
                 "nepraėjo (P 0.08). Funding — Deribit proxy, ne Evedex. Paper tikrina vykdymą ir kaštus.</i>")
    return "\n".join(lines)
