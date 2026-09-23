"""
Which pairs to scan, chosen by live liquidity rather than a fixed list.

A hardcoded pair list rots: it silently keeps scanning assets that were
renamed or delisted, and it never picks up new listings that have since
become liquid. Asking the exchange which perps are actually liquid right
now fixes both directions at once.

Liquidity is read from GET /api/market/instrument?fields=metrics, which
returns every instrument with a metrics block. Two fields matter, and they
are NOT in the same units:

    volumeBase    24h notional volume, already USD (misleadingly named —
                  confirmed against a live BTCUSD response: ~$384M here
                  lines up with `volume` x `markPrice`, not with `volume`
                  alone)
    openInterest  open interest in COINS — must be multiplied by markPrice
                  to become USD, or the filter is wrong by whatever the
                  coin price happens to be

Perpetuals only. Evedex also lists forex/indices/equities (EURUSD, DAX40USD,
SPYUSD, TSLAUSD...), but the bot's logic assumes crypto perps throughout
(shorting, funding, the bare ticker that market_data.to_instrument expects)
— mixing them in would need a broader change than a threshold.

Standalone check (prints what currently qualifies and why):
    .venv/bin/python -m analysis.pair_selection
"""
import logging
import time
from typing import Optional

from .market_data import get_instruments
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
    Every listed crypto perp with its USD volume and USD open interest.

    Returns [{"name", "volume_usd", "oi_usd", "mark"}], unsorted. `name` is
    the bare ticker ("BTC"), not the Evedex instrument name ("BTCUSD").
    """
    try:
        instruments = get_instruments(with_metrics=True)
    except Exception as exc:
        raise PairLiquidityUnavailable(f"instrument metrics request failed — {exc}") from exc

    if not isinstance(instruments, list):
        raise PairLiquidityUnavailable(
            f"unexpected instrument list shape — expected a list, got: {str(instruments)[:200]}"
        )

    rows: list[dict] = []
    for inst in instruments:
        if not isinstance(inst, dict) or inst.get("type") != "perpetual-futures":
            continue
        if inst.get("trading") in ("none", "restricted"):
            continue
        ticker = (inst.get("from") or {}).get("symbol")
        if not ticker:
            continue
        try:
            mark     = float(inst.get("markPrice") or 0)
            volume   = float(inst.get("volumeBase") or 0)     # already USD notional
            oi_coins = float(inst.get("openInterest") or 0)
        except (TypeError, ValueError):
            logger.debug("Skipping %s — unparseable instrument %r", ticker, inst)
            continue

        rows.append({
            "name":       ticker,
            "volume_usd": volume,
            "oi_usd":     oi_coins * mark,   # openInterest is in coins, not USD
            "mark":       mark,
        })

    if not rows:
        raise PairLiquidityUnavailable("instrument metrics returned no usable perps")
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
