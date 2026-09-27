"""
Rule-based recommendations for currently open Evedex positions.

The bot never places orders (see evedex_account.py), so an open position's
real SL/TP live only on the exchange or in the user's head — this module
can't read them back directly. What it CAN do:

  1. Look up the bot's own trade_log.csv for a recent alert on the same
     pair + direction (trade_logger.find_recent_alert) and, when one
     exists, treat its SL/TP1/TP2 as "the plan" to compare current price
     against.
  2. Re-run the 4H structure read fresh, independent of whether a NEW
     entry setup currently qualifies — get_full_analysis() in
     multi_timeframe.py is built to answer "is there a fresh setup right
     now", and returns bare {"valid": False, "reason": ...} on most of its
     many rejection paths (R:R gate, trend gate, no pattern at all), which
     is the ordinary state for a pair that's already in a position. Asking
     it for "what's the current structure" would come back empty almost
     every time.

Deliberately rule-based, not an LLM opinion: ANTHROPIC_API_KEY is off by
default (see ai/claude_analyst.py's module docstring), so there is no
model to ask here, and a plain rule ("structure flipped -> reconsider") is
more auditable than a model guessing at the same thing from the same
inputs would be.

Standalone check:
    .venv/bin/python -m analysis.position_tracker
"""
import logging
from datetime import datetime

from .evedex_account import get_open_positions
from .market_data import from_instrument, get_ohlcv
from .smc import detect_market_structure
from .trade_logger import find_recent_alert

logger = logging.getLogger(__name__)


def _position_bias(side: str) -> str:
    return "bullish" if side.upper() == "BUY" else "bearish"


def _current_context(pair: str) -> dict:
    """
    Current price + 4H structural bias for `pair`, independent of whether a
    fresh entry setup currently qualifies. See module docstring for why this
    doesn't just call multi_timeframe.get_full_analysis().
    """
    df_15m = get_ohlcv(pair, "15m")
    df_4h  = get_ohlcv(pair, "4h")
    if df_15m.empty or df_4h.empty:
        return {"current_price": None, "structure_bias": None}

    return {
        "current_price":  float(df_15m["close"].iloc[-1]),
        "structure_bias": detect_market_structure(df_4h)["bias"],
    }


def _decide(bias: str, current_price: float, structure_bias: str, matched: dict | None) -> tuple[str, str]:
    """
    The two situations that actually call for action, checked in order:
    the structure that justified the direction has flipped, or price has
    already cleared the bot's own planned SL/TP1 without the position
    apparently having been closed. Everything else is "hold".
    """
    if structure_bias is not None and structure_bias != bias:
        return (
            "PERŽIŪRĖTI",
            f"4H struktūra apsivertė į {structure_bias} — pradinė {bias} tezė daugiau nebegalioja.",
        )

    if matched is None or current_price is None:
        return (
            "LAIKYTI (be konteksto)",
            "Struktūra vis dar sutampa su pozicijos kryptimi, bet nerastas atitinkamas bot'o "
            "alertas šiai pozicijai — SL/TP1 palyginimui duomenų nėra.",
        )

    try:
        sl  = float(matched.get("sl")  or 0)
        tp1 = float(matched.get("tp1") or 0)
    except (TypeError, ValueError):
        sl = tp1 = 0.0

    hit_sl  = sl  and ((bias == "bullish" and current_price <= sl)  or (bias == "bearish" and current_price >= sl))
    hit_tp1 = tp1 and ((bias == "bullish" and current_price >= tp1) or (bias == "bearish" and current_price <= tp1))

    if hit_sl:
        return (
            "PATIKRINTI SL",
            f"Kaina jau peržengė bot'o alerto SL lygį (${sl:,.6g}) — patikrink, ar tavo "
            f"stop'as biržoje realiai suveikė.",
        )
    if hit_tp1:
        return (
            "SL Į BREAKEVEN",
            f"Kaina jau pasiekė bot'o alerto TP1 (${tp1:,.6g}) — struktūra vis dar sutampa, "
            f"apsvarstyk SL perkėlimą į breakeven ir važiavimą link TP2.",
        )
    return (
        "LAIKYTI",
        "Struktūra vis dar sutampa su pozicijos kryptimi, kaina tarp bot'o alerto SL ir TP1.",
    )


def recommend(position: dict) -> dict:
    """One open position -> a recommendation dict (see get_position_recommendations)."""
    instrument = position.get("instrument", "")
    pair       = from_instrument(instrument)
    side       = position.get("side", "?").upper()
    bias       = _position_bias(side)
    entry      = float(position.get("avgPrice", 0) or 0)
    quantity   = float(position.get("quantity", 0) or 0)
    unrealized = float(position.get("unRealizedPnL", 0) or 0)
    created_at = position.get("createdAt")

    ctx = _current_context(pair)
    current_price  = ctx["current_price"]
    structure_bias = ctx["structure_bias"]

    matched = None
    if created_at:
        try:
            created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            matched = find_recent_alert(pair, bias, created_dt)
        except ValueError:
            logger.debug("%s: unparseable createdAt %r", pair, created_at)

    if current_price is None:
        action, reason = "NEŽINOMA", "Rinkos duomenys šiuo metu nepasiekiami."
    else:
        action, reason = _decide(bias, current_price, structure_bias, matched)

    return {
        "pair":             pair,
        "side":             side,
        "entry":            entry,
        "quantity":         quantity,
        "leverage":         position.get("leverage"),
        "unrealized_pnl":   unrealized,
        "current_price":    current_price,
        "structure_bias":   structure_bias,
        "matched_alert":    matched,
        "action":           action,
        "reason":           reason,
    }


def get_position_recommendations() -> list[dict]:
    """Every open position with a recommendation, alphabetical by pair."""
    positions = get_open_positions()
    recs = [recommend(p) for p in positions]
    recs.sort(key=lambda r: r["pair"])
    return recs


# ─── Standalone check ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    recs = get_position_recommendations()
    if not recs:
        print("Atvirų pozicijų nėra.")
    for r in recs:
        pnl_sign = "+" if r["unrealized_pnl"] >= 0 else ""
        print(f"{r['pair']} {r['side']} @ {r['entry']:,.6g} | "
              f"uPnL {pnl_sign}${r['unrealized_pnl']:.2f} | {r['action']} — {r['reason']}")
