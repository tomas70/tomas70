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
from .liquidity_walls import nearest_protective_wall
from .market_data import from_instrument, get_ohlcv
from .smc import detect_market_structure
from .trade_logger import find_recent_alert
from config import TP1_FIXED_PCT

logger = logging.getLogger(__name__)

# Why "no matched alert" is the common case, not a bug: the bot has no
# execution path (see evedex_account.py's module docstring — read-only,
# never signs or places orders), so every position here was opened
# manually. And even a position opened straight off a bot alert often
# won't have a matching trade_log.csv row: scan_markets() in main.py logs
# and sends only the SINGLE highest-R:R setup per scan cycle — every other
# pair that also qualified that cycle shows up in the scan's own checklist
# but is never logged.
NO_ALERT_NOTE = (
    "botas nesudaro orderių ir kiekvieną scan'ą į žurnalą įrašo tik vieną, geriausią R:R setup'ą — "
    "kitos tą kartą atitikusios poros niekur neišlieka"
)


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


def _estimated_levels(pair: str, bias: str, entry: float) -> dict:
    """
    Reference SL/TP1 for a position with no matched bot alert (see
    NO_ALERT_NOTE) — built the same way the bot sizes a FRESH entry at this
    exact price, so the comparison in _decide() below still means
    something instead of just giving up:

      TP1  entry ± config.TP1_FIXED_PCT, the same fixed target every bot
           setup uses (multi_timeframe.py's _tp_targets) regardless of
           structure.
      SL   the nearest live order-book wall on the protective side
           (liquidity_walls.nearest_protective_wall) — a stand-in for the
           structural sweep level the bot would have used had it actually
           detected an entry here. Not a claim that this is what the bot
           WOULD have alerted, only the closest analogue computable after
           the fact; None when no wall is visible nearby (a real
           possibility, not an error — see liquidity_walls.py's own
           "KNOWN LIMIT" note).
    """
    tp1 = entry * (1 + TP1_FIXED_PCT) if bias == "bullish" else entry * (1 - TP1_FIXED_PCT)
    try:
        wall = nearest_protective_wall(pair, entry, bias)
    except Exception:
        wall = None
    return {"sl": wall.price if wall else None, "tp1": tp1}


def _decide(
    bias: str,
    current_price: float,
    structure_bias: str,
    sl: float | None,
    tp1: float | None,
    is_estimate: bool,
) -> tuple[str, str]:
    """
    The two situations that actually call for action, checked in order:
    the structure that justified the direction has flipped, or price has
    already cleared SL/TP1 (from a matched alert, or — failing that — the
    estimate from _estimated_levels) without the position apparently
    having been closed. Everything else is "hold".
    """
    if structure_bias is not None and structure_bias != bias:
        return (
            "PERŽIŪRĖTI",
            f"4H struktūra apsivertė į {structure_bias} — pradinė {bias} tezė daugiau nebegalioja.",
        )

    if current_price is None or (not sl and not tp1):
        return (
            "LAIKYTI (be konteksto)",
            f"Struktūra vis dar sutampa su pozicijos kryptimi, bet SL/TP1 palyginimui duomenų "
            f"nėra ({NO_ALERT_NOTE}, o artima order book siena šiuo metu nematoma).",
        )

    sl_label  = "įvertinto (artimiausios order book sienos)" if is_estimate else "bot'o alerto"
    tp1_label = "įvertinto (fiksuoto 2%)" if is_estimate else "bot'o alerto"
    tag       = " (įvertis, ne bot'o alertas)" if is_estimate else ""

    hit_sl  = bool(sl)  and ((bias == "bullish" and current_price <= sl)  or (bias == "bearish" and current_price >= sl))
    hit_tp1 = bool(tp1) and ((bias == "bullish" and current_price >= tp1) or (bias == "bearish" and current_price <= tp1))

    if hit_sl:
        return (
            "PATIKRINTI SL",
            f"Kaina jau peržengė {sl_label} SL lygį (${sl:,.6g}) — patikrink, ar tavo "
            f"stop'as biržoje realiai suveikė.{tag}",
        )
    if hit_tp1:
        return (
            "SL Į BREAKEVEN",
            f"Kaina jau pasiekė {tp1_label} TP1 (${tp1:,.6g}) — struktūra vis dar sutampa, "
            f"apsvarstyk SL perkėlimą į breakeven ir važiavimą link TP2.{tag}",
        )
    return (
        "LAIKYTI",
        f"Struktūra vis dar sutampa su pozicijos kryptimi, kaina tarp {sl_label} SL ir TP1.{tag}",
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

    if matched:
        try:
            sl, tp1 = float(matched.get("sl") or 0) or None, float(matched.get("tp1") or 0) or None
        except (TypeError, ValueError):
            sl = tp1 = None
        is_estimate = False
    elif current_price is not None:
        est = _estimated_levels(pair, bias, entry)
        sl, tp1 = est["sl"], est["tp1"]
        is_estimate = True
    else:
        sl = tp1 = None
        is_estimate = True

    if current_price is None:
        action, reason = "NEŽINOMA", "Rinkos duomenys šiuo metu nepasiekiami."
    else:
        action, reason = _decide(bias, current_price, structure_bias, sl, tp1, is_estimate)

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
        "reference_sl":     sl,
        "reference_tp1":    tp1,
        "levels_estimated": is_estimate,
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
