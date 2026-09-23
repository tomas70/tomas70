import time
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
import pandas as pd

from config import CANDLES_LIMIT, PAIRS, SUPPORTED_TIMEFRAMES, TIMEFRAMES

logger = logging.getLogger(__name__)

MARKET_DATA_BASE_URL = "https://market-data-api.evedex.com"
EXCHANGE_BASE_URL     = "https://trading-api.evedex.com"
CACHE_TTL_SECONDS     = 60 * 14  # refresh if older than 14 minutes

# A full scan fires ~60 requests at Evedex in a few seconds (20 pairs x
# 3 timeframes), which is enough to draw an occasional 429 or transient 5xx.
# Those are retried with exponential backoff rather than surfacing as a
# "Data fetch error" for the pair.
MAX_RETRIES      = 3
RETRY_BASE_DELAY = 1.0  # seconds; doubles each attempt (1s, 2s, 4s)

# In-memory cache: {(pair, timeframe): (DataFrame, monotonic_timestamp)}
_cache: dict[tuple[str, str], tuple[pd.DataFrame, float]] = {}

# ticker -> Evedex instrument name, e.g. "BONK" -> "1000BONKUSD". Cached
# separately from the candle cache since it almost never changes.
INSTRUMENT_MAP_TTL_SECONDS = 60 * 60
_instrument_map_cache: Optional[tuple[dict[str, str], float]] = None

# Evedex candlestick "group" values — TIMEFRAMES/GLOBAL_TREND_TIMEFRAME in
# config.py must only ever use values from this set.
_GROUP_MS: dict[str, int] = {
    "1s":  1_000,
    "1m":  60_000,
    "3m":  3  * 60_000,
    "5m":  5  * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h":  60 * 60_000,
    "4h":  4  * 60 * 60_000,
    "6h":  6  * 60 * 60_000,
    "12h": 12 * 60 * 60_000,
    "1d":  24 * 60 * 60_000,
    "1w":  7  * 24 * 60 * 60_000,
}


def _instrument_map() -> dict[str, str]:
    """
    ticker -> Evedex instrument name, built from the live instrument list
    rather than assumed as f"{ticker}USD".

    That assumption holds for most coins (BTC -> BTCUSD) but not all: Evedex
    prefixes some low-price tokens with a contract multiplier baked into the
    instrument name itself — 1000BONKUSD, 1000PEPEUSD, 1000000BABYDOGEUSD —
    and names the pump.fun perp PUMPFUNUSD rather than PUMPUSD, even though
    `from.symbol` (what pair_selection uses as the bot-internal ticker) is
    the bare "BONK"/"PEPE"/"PUMP". A scan that assumed the naive mapping
    fetched candles for a nonexistent instrument and returned nothing for
    exactly these coins.

    Skips non-tradable duplicates the same way pair_selection/market_context
    do (Evedex's ":RISK" twin per instrument), so a real BTCUSD row never
    loses to a zeroed-out BTCUSD:RISK row on ticker collision.
    """
    global _instrument_map_cache
    now = time.monotonic()

    if _instrument_map_cache is not None and now - _instrument_map_cache[1] < INSTRUMENT_MAP_TTL_SECONDS:
        return _instrument_map_cache[0]

    try:
        instruments = get_instruments()
    except Exception as exc:
        logger.warning("Instrument list fetch failed (%s) — keeping stale map if any", exc)
        return _instrument_map_cache[0] if _instrument_map_cache is not None else {}

    mapping: dict[str, str] = {}
    for inst in instruments:
        if not isinstance(inst, dict) or inst.get("type") != "perpetual-futures":
            continue
        if inst.get("trading") in ("none", "restricted"):
            continue
        ticker = (inst.get("from") or {}).get("symbol")
        name   = inst.get("name")
        if ticker and name:
            mapping[ticker] = name

    _instrument_map_cache = (mapping, now)
    return mapping


def to_instrument(pair: str) -> str:
    """
    Bot-internal ticker ("BTC") -> Evedex instrument name ("BTCUSD"), via the
    live instrument map. Falls back to the naive f"{pair}USD" guess only when
    the map is unavailable or doesn't know this ticker (e.g. it's already an
    instrument name, or a genuinely unknown symbol) — callers already handle
    that coming back as empty data.
    """
    return _instrument_map().get(pair) or f"{pair}USD"


def _is_retryable(exc: Exception) -> bool:
    """True for transient failures worth a retry: rate limits, 5xx, network."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.RequestError)  # timeouts, connection resets


def _get_with_retry(url: str, params: Optional[dict] = None, timeout: int = 10) -> httpx.Response:
    """GETs `url`, retrying transient errors with exponential backoff."""
    last_exc: Exception = RuntimeError("no attempt made")

    for attempt in range(MAX_RETRIES):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
            return response

        except Exception as exc:
            last_exc = exc
            if not _is_retryable(exc) or attempt == MAX_RETRIES - 1:
                raise
            delay = RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning(
                "Evedex request failed (%s %s), retry %d/%d in %.0fs",
                url, exc, attempt + 1, MAX_RETRIES - 1, delay,
            )
            time.sleep(delay)

    raise last_exc


def get_instruments(with_metrics: bool = False) -> list[dict]:
    """
    Every instrument Evedex lists — public, no auth. Pass with_metrics=True
    for the heavier response that also carries volume/openInterest/mark/
    funding (per the docs, this variant "should not be used frequently").
    """
    params = {"fields": "metrics"} if with_metrics else None
    response = _get_with_retry(f"{EXCHANGE_BASE_URL}/api/market/instrument", params=params)
    return response.json()


def get_order_book(pair: str, max_level: Optional[int] = None) -> dict:
    """
    Raw order book depth for `pair` — public, no auth.
    Returns {"t": ms_timestamp, "asks": [{"price","quantity"}], "bids": [...]}.
    Pass max_level=1 for just the best bid/ask.
    """
    instrument = to_instrument(pair)
    params = {"marketLevel": max_level} if max_level else None
    response = _get_with_retry(f"{EXCHANGE_BASE_URL}/api/market/{instrument}/deep", params=params)
    return response.json()


def _fetch_from_evedex(pair: str, timeframe: str, limit: int) -> pd.DataFrame:
    """
    Calls Evedex's public candle history endpoint — no API key required.

    Each candle is [timestamp_ms, open, close, max, min, volumeUsd, volume],
    confirmed against a live response rather than assumed from the docs
    prose (which doesn't name the array order).
    """
    end_ms      = int(time.time() * 1000)
    interval_ms = _GROUP_MS.get(timeframe, 15 * 60_000)
    start_ms    = end_ms - limit * interval_ms

    instrument = to_instrument(pair)
    params = {
        "group":  timeframe,
        "after":  datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).isoformat(),
        "before": datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).isoformat(),
    }

    response = _get_with_retry(f"{MARKET_DATA_BASE_URL}/api/history/{instrument}/list", params=params)

    candles = response.json()
    if not candles:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame([
        {
            "timestamp": c[0],
            "open":      float(c[1]),
            "high":      float(c[3]),
            "low":       float(c[4]),
            "close":     float(c[2]),
            "volume":    float(c[6]),
        }
        for c in candles
    ])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.sort_values("timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def get_ohlcv(
    pair: str,
    timeframe: str,
    limit: int = CANDLES_LIMIT,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """
    Returns OHLCV DataFrame for the given pair and timeframe.
    Uses in-memory cache (14 min TTL); falls back to stale cache on network error.
    No API key required — Evedex Market Data is public REST.
    """
    # Format check only, not membership in the static PAIRS list: the active
    # pair set is now discovered from the exchange (see pair_selection), so a
    # symbol can legitimately be one this file has never heard of. A genuinely
    # unknown coin comes back as an empty candle list, which callers already
    # handle. (Validating against pair_selection here would be circular — it
    # imports this module.)
    if not isinstance(pair, str) or not pair.strip():
        raise ValueError(f"Invalid pair symbol: {pair!r}")
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe: {timeframe}. Allowed: {SUPPORTED_TIMEFRAMES}")

    cache_key = (pair, timeframe)
    now = time.monotonic()

    if not force_refresh and cache_key in _cache:
        cached_df, cached_at = _cache[cache_key]
        if now - cached_at < CACHE_TTL_SECONDS:
            return cached_df.copy()

    try:
        df = _fetch_from_evedex(pair, timeframe, limit)
        _cache[cache_key] = (df, now)
        logger.debug("Fetched %s %s (%d candles)", pair, timeframe, len(df))
        return df.copy()

    except Exception as exc:
        logger.warning("Evedex fetch failed for %s %s: %s", pair, timeframe, exc)
        if cache_key in _cache:
            logger.info("Returning stale cache for %s %s", pair, timeframe)
            return _cache[cache_key][0].copy()
        raise


def get_current_price(pair: str) -> float:
    """Latest mid price (best bid/ask average) from the Evedex order book — no API key required."""
    book = get_order_book(pair, max_level=1)
    asks, bids = book.get("asks"), book.get("bids")
    if not asks or not bids:
        raise ValueError(f"Price not found for {pair} on Evedex")
    return (float(asks[0]["price"]) + float(bids[0]["price"])) / 2


def get_all_timeframes(pair: str) -> dict[str, pd.DataFrame]:
    """Fetches 15m, 1h, and 4h DataFrames for a pair."""
    return {tf: get_ohlcv(pair, tf) for tf in TIMEFRAMES}


def invalidate_cache(pair: Optional[str] = None, timeframe: Optional[str] = None) -> None:
    """Clears cache entries. Pass no arguments to clear everything."""
    if pair is None and timeframe is None:
        _cache.clear()
        return
    keys = [
        k for k in _cache
        if (pair is None or k[0] == pair) and (timeframe is None or k[1] == timeframe)
    ]
    for k in keys:
        del _cache[k]
