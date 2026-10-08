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

State lives in logs/paper_portfolio.json. update() is idempotent: it
processes each completed UTC day exactly once, so running it every half hour
(or catching up after downtime) is safe.
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
SMA_N        = 50


# ── data ──────────────────────────────────────────────────────────────────

def fetch_closes() -> pd.DataFrame:
    """Completed BTC daily closes (UTC date index), column 'BTC'."""
    from .market_data import get_ohlcv

    today = pd.Timestamp(datetime.now(timezone.utc).date(), tz="UTC")
    s = get_ohlcv("BTC", "1d").set_index("timestamp")["close"]
    return pd.DataFrame({"BTC": s[s.index < today]}).sort_index()   # drop today's unfinished candle


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

def update(closes: Optional[pd.DataFrame] = None) -> list[str]:
    """
    Advance through every completed day not yet processed. Returns
    Telegram-ready messages (start, trend flips). `closes` is injectable
    for tests.
    """
    if closes is None:
        closes = fetch_closes()
    if closes.empty or "BTC" not in closes or len(closes["BTC"].dropna()) < SMA_N + 1:
        logger.warning("paper_tracker: not enough BTC daily history yet")
        return []

    btc = closes["BTC"].dropna()
    state = _load()

    if state is None:
        pos = int(btc.iloc[-1] > btc.iloc[-SMA_N:].mean())
        state = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "btc": {"equity": START_EQUITY, "pos": pos, "last_day": _day(btc.index[-1]),
                    "bh_start": float(btc.iloc[-1]), "bh_last": float(btc.iloc[-1]),
                    "flips": 0, "sma": float(btc.iloc[-SMA_N:].mean())},
        }
        _save(state)
        return [
            "📝 <b>Paper tracker pradėtas</b> ($%.0f)\n"
            "• BTC T50 (long kai kaina virš SMA50, kitaip FLAT): dabar <b>%s</b>\n"
            "<i>Tik stebėjimas, nieko neperkama. Signalai — kai keičiasi būsena.</i>"
            % (START_EQUITY, "LONG" if pos else "FLAT")
        ]

    msgs: list[str] = []
    s = state["btc"]
    for d in btc.index:
        if _day(d) > s["last_day"]:
            m = _advance(s, btc, d)
            if m:
                msgs.append(m)
    s["bh_last"] = float(btc.iloc[-1])
    s["sma"] = float(btc.iloc[-SMA_N:].mean())
    _save(state)
    return msgs


def _advance(s: dict, btc: pd.Series, d) -> Optional[str]:
    series = btc[btc.index <= d]
    ret = series.iloc[-1] / series.iloc[-2] - 1
    s["equity"] *= 1 + s["pos"] * ret
    new_pos = int(series.iloc[-1] > series.iloc[-SMA_N:].mean()) if len(series) >= SMA_N else s["pos"]
    msg = None
    if new_pos != s["pos"]:
        s["equity"] *= 1 - SIDE_FEE
        s["flips"] += 1
        msg = ("📈 <b>BTC T50: LONG</b>\nKaina %.0f uždarė virš SMA50 (%.0f). "
               "Paper: įėjimas dienos uždarymo kaina." if new_pos
               else "📉 <b>BTC T50: FLAT</b>\nKaina %.0f uždarė po SMA50 (%.0f). "
                    "Paper: išėjimas dienos uždarymo kaina.") % (
            series.iloc[-1], series.iloc[-SMA_N:].mean())
        msg += f"\nPaper equity: ${s['equity']:.2f}"
    s["pos"] = new_pos
    s["last_day"] = _day(d)
    return msg


# ── report ────────────────────────────────────────────────────────────────

def format_report() -> str:
    state = _load()
    if not state:
        return "📝 <b>Paper tracker</b>\n\nDar nepradėtas — pirmas paleidimas įvyks po kelių minučių."
    s = state["btc"]
    pct = lambda e: (e / START_EQUITY - 1) * 100
    bh = START_EQUITY * s["bh_last"] / s["bh_start"]
    return "\n".join([
        f"📝 <b>Paper tracker — BTC T50</b>  <i>(nuo {state['started_at'][:10]}, ${START_EQUITY:.0f})</i>\n",
        f"Būsena: <b>{'LONG' if s['pos'] else 'FLAT'}</b> | perjungimų: {s['flips']}",
        f"Strategija: ${s['equity']:.2f} ({pct(s['equity']):+.1f}%)",
        f"BTC buy&hold: ${bh:.2f} ({pct(bh):+.1f}%)",
        f"SMA50: {s.get('sma', 0):.0f} | BTC: {s['bh_last']:.0f}\n",
        "<i>Taisyklė patikrinta 2017–2026 istorijoje (Sharpe 1.29 vs 0.83, maxDD -64% vs -83%). "
        "Paper tikrina, ar vykdymas ir kaštai atitinka. Bulių rinkoje tikėkis maždaug buy&hold, "
        "meškų — mažesnių nuostolių.</i>",
    ])
