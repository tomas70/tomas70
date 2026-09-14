"""
Perp-specific context for a pair: funding rate, open interest, premium.

These have no equivalent in the chart-pattern model the bot implements,
and they are the part of Hyperliquid that a spot-derived setup framework
is blind to:

  funding    A perp position is paid or charged every hour. A short into
             positive funding is paid to hold; a long into positive
             funding bleeds. Over a 48h outcome window at an extreme rate
             this is a real fraction of the 2% TP1 target, so it belongs
             in the record even before anyone decides to gate on it.

  open       OI rising while price sweeps a level means new positions were
  interest   opened into the move; OI falling means positions were closed
             out — which is what a stop hunt looks like from the
             positioning side. Only the level is recorded here; the delta
             across the sweep needs a history this endpoint doesn't give,
             and is the obvious next step once these rows exist.

  premium    Mark vs oracle. A large premium is crowding in one direction.

Source is metaAndAssetCtxs, the same endpoint pair_selection already
calls, so this adds one cached request per scan rather than one per pair.
Cached for 10 minutes: funding updates hourly, and a scan runs every 15.

Values are recorded, never gated on. Same reasoning as analysis/ict.py.
"""
import logging
import time
from typing import Optional

from .market_data import post_info

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60 * 10

# {coin: {"funding_hourly", "oi_usd", "premium", "mark"}}
_cache: Optional[tuple[dict[str, dict], float]] = None


def _fetch_all() -> dict[str, dict]:
    """
    Per-coin funding / OI / premium for every listed perp.

    openInterest is denominated in COINS (the same trap pair_selection
    documents), so it is multiplied by markPx here — an OI figure that is
    silently in coins for BTC and in coins for a $0.10 alt is not
    comparable across pairs and would make any later threshold nonsense.
    """
    data = post_info({"type": "metaAndAssetCtxs"})

    if not isinstance(data, list) or len(data) < 2:
        raise ValueError(f"unexpected metaAndAssetCtxs shape: {str(data)[:200]}")

    meta, ctxs = data[0], data[1]
    universe = meta.get("universe") if isinstance(meta, dict) else None
    if not isinstance(universe, list) or not isinstance(ctxs, list):
        raise ValueError("metaAndAssetCtxs missing 'universe' or context array")

    out: dict[str, dict] = {}
    for asset, ctx in zip(universe, ctxs):
        if not isinstance(asset, dict) or not isinstance(ctx, dict):
            continue
        name = asset.get("name")
        if not name:
            continue
        try:
            mark     = float(ctx.get("markPx") or 0)
            funding  = float(ctx.get("funding") or 0)      # hourly, as a fraction
            oi_coins = float(ctx.get("openInterest") or 0)
            premium  = float(ctx.get("premium") or 0)
        except (TypeError, ValueError):
            continue

        out[name] = {
            "funding_hourly": funding,
            "oi_usd":         oi_coins * mark,
            "premium":        premium,
            "mark":           mark,
        }

    return out


def _all_context() -> dict[str, dict]:
    global _cache
    now = time.monotonic()

    if _cache is not None and now - _cache[1] < CACHE_TTL_SECONDS:
        return _cache[0]

    try:
        fresh = _fetch_all()
        _cache = (fresh, now)
        return fresh
    except Exception as exc:
        logger.warning("market_context: metaAndAssetCtxs failed — %s", exc)
        return _cache[0] if _cache is not None else {}


def get_market_context(pair: str, bias: str) -> Optional[dict]:
    """
    Funding / OI / premium for `pair`, plus what the funding means for a
    position in `bias` direction.

    funding_carry_pct_48h is signed from the TRADE's point of view:
    positive means the position is paid to be held for the 48h outcome
    window, negative means it pays. It is expressed in percent of notional
    so it can be read directly against TP1_FIXED_PCT (2%) and sl_pct.

    Returns None when the endpoint is unavailable — the caller records the
    absence rather than substituting a zero, which would read as "funding
    was flat" instead of "funding was unknown".
    """
    ctx = _all_context().get(pair)
    if not ctx:
        return None

    hourly = ctx["funding_hourly"]
    # Longs pay positive funding, shorts receive it.
    carry_48h = (-hourly if bias == "bullish" else hourly) * 48 * 100

    return {
        "funding_hourly":        round(hourly, 8),
        "funding_apr_pct":       round(hourly * 24 * 365 * 100, 3),
        "funding_carry_pct_48h": round(carry_48h, 4),
        "oi_usd":                round(ctx["oi_usd"], 0),
        "premium":               round(ctx["premium"], 6),
    }
