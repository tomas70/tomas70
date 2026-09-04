"""
Which pairs to scan, chosen by live liquidity rather than a fixed list.

A hardcoded pair list rots: it silently keeps scanning assets that were
renamed or delisted (the TON->GRAM rename cost a pair for weeks, showing
up only as "Empty candle data"), and it never picks up new listings that
have since become liquid. Asking the exchange which perps are actually
liquid right now fixes both directions at once.

Liquidity is read from the metaAndAssetCtxs info endpoint, which returns
`universe` (asset names) alongside a parallel array of per-asset context.
Two fields matter, and they are NOT in the same units:

    dayNtlVlm     24h notional volume, already USD
    openInterest  open interest in COINS — must be multiplied by markPx
                  to become USD, or the filter is wrong by whatever the
                  coin price happens to be

Perpetuals only. Hyperliquid also lists spot markets, but the bot's logic
assumes perps throughout (shorting, funding, the bare `coin` symbol that
candleSnapshot expects), and spot symbols use a different naming scheme —
mixing them in would need a broader change than a threshold.

Standalone check (prints what currently qualifies and why):
    .venv/bin/python -m analysis.pair_selection
"""
import logging
import time
from typing import Optional

from .market_data import post_info
from config import (
    MAX_ACTIVE_PAIRS, MIN_DAY_VOLUME_USD, MIN_OPEN_INTEREST_USD,
    PAIRS as STATIC_PAIRS, USE_DYNAMIC_PAIRS,
)

logger = logging.getLogger(__name__)

# The liquid-pair set moves on the scale of days, not minutes, so it is
# cached rather than refetched on every 15-minute scan.
CACHE_TTL_SECONDS = 60 * 60
_cache: Optional[tuple[list[str], float]] = None


class PairLiquidityUnavailable(Exception):
    """Raised when the exchange metadata can't be read or doesn't parse."""


def fetch_pair_liquidity() -> list[dict]:
    """
    Every listed perp with its USD volume and USD open interest.

    Returns [{"name", "volume_usd", "oi_usd", "mark"}], unsorted.
    """
    try:
        data = post_info({"type": "metaAndAssetCtxs"})
    except Exception as exc:
        raise PairLiquidityUnavailable(f"metaAndAssetCtxs request failed — {exc}") from exc

    if not isinstance(data, list) or len(data) < 2:
        raise PairLiquidityUnavailable(
            f"unexpected metaAndAssetCtxs shape — expected [meta, ctxs], got: {str(data)[:200]}"
        )

    meta, ctxs = data[0], data[1]
    universe = meta.get("universe") if isinstance(meta, dict) else None
    if not isinstance(universe, list) or not isinstance(ctxs, list):
        raise PairLiquidityUnavailable("metaAndAssetCtxs missing 'universe' or context array")

    rows: list[dict] = []
    for asset, ctx in zip(universe, ctxs):
        if not isinstance(asset, dict) or not isinstance(ctx, dict):
            continue
        name = asset.get("name")
        if not name or asset.get("isDelisted"):
            continue
        try:
            mark     = float(ctx.get("markPx") or 0)
            volume   = float(ctx.get("dayNtlVlm") or 0)
            oi_coins = float(ctx.get("openInterest") or 0)
        except (TypeError, ValueError):
            logger.debug("Skipping %s — unparseable context %r", name, ctx)
            continue

        rows.append({
            "name":       name,
            "volume_usd": volume,
            "oi_usd":     oi_coins * mark,   # openInterest is in coins, not USD
            "mark":       mark,
        })

    if not rows:
        raise PairLiquidityUnavailable("metaAndAssetCtxs returned no usable assets")
    return rows


def get_liquid_pairs(force_refresh: bool = False) -> list[str]:
    """
    Pairs meeting the liquidity thresholds, most liquid first.

    A pair qualifies on EITHER metric — volume or open interest — since a
    market can be worth trading on strong turnover with modest positioning
    open, or the reverse. Capped at MAX_ACTIVE_PAIRS because every extra
    pair costs three candle requests on every scan.

    Falls back to the static PAIRS list if the exchange metadata can't be
    read, so a metadata outage degrades the pair set rather than stopping
    the bot from scanning at all.
    """
    global _cache

    if not USE_DYNAMIC_PAIRS:
        return list(STATIC_PAIRS)

    now = time.monotonic()
    if not force_refresh and _cache and now - _cache[1] < CACHE_TTL_SECONDS:
        return list(_cache[0])

    try:
        rows = fetch_pair_liquidity()
    except PairLiquidityUnavailable as exc:
        logger.warning("Liquidity-based pair selection failed (%s) — using static list", exc)
        return list(_cache[0]) if _cache else list(STATIC_PAIRS)

    qualifying = [
        r for r in rows
        if r["volume_usd"] >= MIN_DAY_VOLUME_USD or r["oi_usd"] >= MIN_OPEN_INTEREST_USD
    ]
    # Rank by the larger of the two metrics so a pair strong on either one
    # isn't pushed out of the cap by pairs merely mediocre on both.
    qualifying.sort(key=lambda r: max(r["volume_usd"], r["oi_usd"]), reverse=True)

    pairs = [r["name"] for r in qualifying[:MAX_ACTIVE_PAIRS]]
    if len(qualifying) > MAX_ACTIVE_PAIRS:
        logger.info(
            "%d pairs met the liquidity thresholds; scanning the top %d",
            len(qualifying), MAX_ACTIVE_PAIRS,
        )

    _cache = (pairs, now)
    logger.info("Active pairs (%d): %s", len(pairs), ", ".join(pairs))
    return list(pairs)


# ─── Standalone check ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    def _m(x: float) -> str:
        return f"${x/1e6:,.1f}M"

    try:
        rows = fetch_pair_liquidity()
    except PairLiquidityUnavailable as exc:
        print(f"❌ {exc}")
        raise SystemExit(1)

    qualifying = [
        r for r in rows
        if r["volume_usd"] >= MIN_DAY_VOLUME_USD or r["oi_usd"] >= MIN_OPEN_INTEREST_USD
    ]
    qualifying.sort(key=lambda r: max(r["volume_usd"], r["oi_usd"]), reverse=True)

    print("=" * 72)
    print(f" LIKVIDŽIOS POROS  (volume >= {_m(MIN_DAY_VOLUME_USD)} ARBA OI >= {_m(MIN_OPEN_INTEREST_USD)})")
    print("=" * 72)
    print(f"Iš viso listinguota perp'ų: {len(rows)}  |  Atitinka kriterijų: {len(qualifying)}")
    print(f"Skenuojama (riba MAX_ACTIVE_PAIRS={MAX_ACTIVE_PAIRS}): {min(len(qualifying), MAX_ACTIVE_PAIRS)}\n")
    print(f"{'#':>3}  {'PORA':<10} {'24h VOLUME':>14} {'OPEN INTEREST':>15}  KODĖL")
    print("-" * 72)

    for i, r in enumerate(qualifying[:MAX_ACTIVE_PAIRS], 1):
        why = []
        if r["volume_usd"] >= MIN_DAY_VOLUME_USD:
            why.append("vol")
        if r["oi_usd"] >= MIN_OPEN_INTEREST_USD:
            why.append("OI")
        print(f"{i:>3}  {r['name']:<10} {_m(r['volume_usd']):>14} {_m(r['oi_usd']):>15}  {'+'.join(why)}")

    dropped = [r["name"] for r in qualifying[MAX_ACTIVE_PAIRS:]]
    if dropped:
        print(f"\nVirš ribos, neskenuojama ({len(dropped)}): {', '.join(dropped)}")

    static_only = sorted(set(STATIC_PAIRS) - {r["name"] for r in qualifying[:MAX_ACTIVE_PAIRS]})
    if static_only:
        print(f"\n⚠️  Senajame sąraše, bet NEatitinka kriterijaus: {', '.join(static_only)}")
