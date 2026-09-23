"""
Order book liquidity walls — where real resting size actually sits.

Every other level the bot reasons about (order blocks, swing highs/lows,
previous-day range) is derived from HISTORICAL candles: an inference about
where participants once acted. A liquidity wall is different in kind — it
is live resting limit orders, visible right now, at a price someone is
currently willing to trade. That distinction is why this exists: the
HOOK setup's OB-derived stop underperformed a coin flip across every
sample we measured, and an OB is exactly that kind of historical
inference.

Snapshot over stream: the bot scans every 15 minutes, so it needs the book
at decision time, not continuously. Evedex's GET .../deep endpoint serves
the same data as the {env}:orderBook websocket channel as a plain REST
call, which avoids a background thread, reconnect handling, and shared
mutable state for 20 pairs. (A websocket feed is the right tool for a
human watching one pair live — it is the wrong one for a 15-minute cron.)

Wall threshold is RELATIVE, not a hardcoded size per coin: a level counts
as a wall when its size is WALL_SIZE_MULT times the median level size in
that same book. A fixed "3 BTC / 30 ETH / 500 SOL" table cannot generalise
across 20 pairs and silently goes stale as price and volatility move; a
multiple of the book's own median re-calibrates itself on every request.

KNOWN LIMIT: Evedex doesn't document a level cap the way Hyperliquid did
(a live BTCUSD snapshot returned 82 asks / 100 bids), but the book is
still a snapshot of what's resting right now — a wall can still sit
outside whatever range the book happens to return. Treat a missing wall
as "none visible nearby", never as "none exists".

Evedex order book levels don't carry a resting-order count (Hyperliquid's
"n" did) — Wall.orders is always 0 here, kept in the dataclass only so
callers don't need a second shape for this field.

Usage as a standalone check (non-interactive, one pair per call):
    .venv/bin/python -m analysis.liquidity_walls BTC
"""
import logging
from dataclasses import dataclass
from statistics import median
from typing import Literal, Optional

from .market_data import get_order_book

logger = logging.getLogger(__name__)

# A level is a "wall" when its size is at least this multiple of the median
# level size in the same book snapshot.
WALL_SIZE_MULT = 3.0

# How many walls per side to keep, largest first.
MAX_WALLS = 5


@dataclass
class Wall:
    """A price level holding unusually large resting size."""
    side: Literal["bid", "ask"]
    price: float
    size: float
    orders: int
    distance_pct: float   # absolute distance from mid price, in percent
    size_vs_median: float # how many times the median level size this is


class OrderBookUnavailable(Exception):
    """Raised when the book can't be fetched or doesn't match the expected shape."""


# ─── Fetch ────────────────────────────────────────────────────────────────────

def get_orderbook(coin: str) -> tuple[list[dict], list[dict]]:
    """
    Returns (bids, asks) for `coin`, each a list of
    {"px": float, "sz": float, "n": int}, best price first.

    Validates the response shape explicitly rather than indexing blindly, so
    an API change surfaces as a clear error instead of a confusing KeyError
    or a silently empty wall list.
    """
    try:
        data = get_order_book(coin)
    except Exception as exc:
        raise OrderBookUnavailable(f"{coin}: order book request failed — {exc}") from exc

    bids_raw = data.get("bids") if isinstance(data, dict) else None
    asks_raw = data.get("asks") if isinstance(data, dict) else None
    if not isinstance(bids_raw, list) or not isinstance(asks_raw, list):
        raise OrderBookUnavailable(
            f"{coin}: unexpected order book response shape — expected a dict with "
            f"'bids'/'asks' lists, got: {str(data)[:200]}"
        )

    def parse(side_levels: list, label: str) -> list[dict]:
        out = []
        for lvl in side_levels:
            try:
                out.append({
                    "px": float(lvl["price"]),
                    "sz": float(lvl["quantity"]),
                    "n":  0,   # Evedex doesn't report a resting-order count per level
                })
            except (KeyError, TypeError, ValueError) as exc:
                raise OrderBookUnavailable(
                    f"{coin}: malformed order book level in '{label}': {lvl!r} ({exc})"
                ) from exc
        return out

    return parse(bids_raw, "bids"), parse(asks_raw, "asks")


# ─── Wall detection ───────────────────────────────────────────────────────────

def find_walls(
    levels: list[dict],
    side: Literal["bid", "ask"],
    mid_price: float,
    size_mult: float = WALL_SIZE_MULT,
) -> list[Wall]:
    """
    Levels whose resting size stands out against the rest of the book.

    The comparison baseline is the median level size in this same snapshot,
    so the threshold adapts to the pair and to the moment. Median rather
    than mean specifically because one enormous wall would drag a mean up
    far enough to hide itself.
    """
    if not levels or mid_price <= 0:
        return []

    sizes = [lvl["sz"] for lvl in levels if lvl["sz"] > 0]
    if not sizes:
        return []

    baseline = median(sizes)
    if baseline <= 0:
        return []

    threshold = baseline * size_mult
    walls = [
        Wall(
            side=side,
            price=lvl["px"],
            size=lvl["sz"],
            orders=lvl["n"],
            distance_pct=abs(lvl["px"] - mid_price) / mid_price * 100,
            size_vs_median=round(lvl["sz"] / baseline, 2),
        )
        for lvl in levels
        if lvl["sz"] >= threshold
    ]
    walls.sort(key=lambda w: w.size, reverse=True)
    return walls[:MAX_WALLS]


def get_liquidity_walls(coin: str) -> dict:
    """
    Both sides of the book summarised: mid price plus the standout walls.

    Returns {"mid": float, "bid_walls": [Wall], "ask_walls": [Wall]}.
    Raises OrderBookUnavailable if the book can't be read.
    """
    bids, asks = get_orderbook(coin)
    if not bids or not asks:
        raise OrderBookUnavailable(f"{coin}: empty order book side (bids={len(bids)}, asks={len(asks)})")

    best_bid = bids[0]["px"]
    best_ask = asks[0]["px"]
    mid = (best_bid + best_ask) / 2

    return {
        "mid":       mid,
        "best_bid":  best_bid,
        "best_ask":  best_ask,
        "bid_walls": find_walls(bids, "bid", mid),
        "ask_walls": find_walls(asks, "ask", mid),
    }


def nearest_protective_wall(coin: str, entry: float, bias: str) -> Optional[Wall]:
    """
    The wall a protective stop would sit behind: for a long, the nearest bid
    wall BELOW entry (buyers defending); for a short, the nearest ask wall
    ABOVE entry (sellers defending).

    Returns None when no qualifying wall is visible — which, given the
    20-level cap, usually means "not within the visible band" rather than
    "does not exist". Callers must treat None as no information, not as a
    signal.
    """
    try:
        book = get_liquidity_walls(coin)
    except OrderBookUnavailable as exc:
        logger.warning("Liquidity walls unavailable for %s: %s", coin, exc)
        return None

    if bias == "bullish":
        candidates = [w for w in book["bid_walls"] if w.price < entry]
        # nearest below entry = highest price among those below
        return max(candidates, key=lambda w: w.price) if candidates else None

    candidates = [w for w in book["ask_walls"] if w.price > entry]
    # nearest above entry = lowest price among those above
    return min(candidates, key=lambda w: w.price) if candidates else None


# ─── Standalone check ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    coin = (sys.argv[1] if len(sys.argv) > 1 else "BTC").upper()

    try:
        book = get_liquidity_walls(coin)
    except OrderBookUnavailable as exc:
        print(f"❌ {exc}")
        sys.exit(1)

    mid = book["mid"]
    fmt = (lambda p: f"${p:,.4f}") if mid < 1 else (lambda p: f"${p:,.2f}")

    print("=" * 68)
    print(f" EVEDEX LIKVIDUMO SIENOS: {coin}")
    print("=" * 68)
    print(f"Mid: {fmt(mid)} | Bid: {fmt(book['best_bid'])} | Ask: {fmt(book['best_ask'])}")
    print(f"Siena = lygis, kurio dydis >= {WALL_SIZE_MULT}x knygos medianos\n")

    print(f"🔴 PARDAVIMO SIENOS (virš kainos):")
    if not book["ask_walls"]:
        print("  [nėra matomų sienų šioje zonoje]")
    for w in book["ask_walls"]:
        print(f"  • {fmt(w.price)} | {w.size:>12,.2f} {coin} "
              f"(+{w.distance_pct:.2f}%) | {w.size_vs_median}x medianos | {w.orders} ord.")

    print(f"\n🟢 PIRKIMO SIENOS (žemiau kainos):")
    if not book["bid_walls"]:
        print("  [nėra matomų sienų šioje zonoje]")
    for w in book["bid_walls"]:
        print(f"  • {fmt(w.price)} | {w.size:>12,.2f} {coin} "
              f"(-{w.distance_pct:.2f}%) | {w.size_vs_median}x medianos | {w.orders} ord.")

    print("\n" + "=" * 68)
    print("PASTABA: matomas tik gautas knygos gylis — tolimesnės sienos gali būti nematomos.")
