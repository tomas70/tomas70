import time
import logging
from typing import Optional

import httpx
import pandas as pd

from config import CANDLES_LIMIT, PAIRS, SUPPORTED_TIMEFRAMES, TIMEFRAMES

logger = logging.getLogger(__name__)

HL_BASE_URL       = "https://api.hyperliquid.xyz/info"
CACHE_TTL_SECONDS = 60 * 14  # refresh if older than 14 minutes

# A full scan fires ~60 requests at Hyperliquid in a few seconds (20 pairs x
# 3 timeframes), which is enough to draw an occasional 429 or transient 5xx.
# Those are retried with exponential backoff rather than surfacing as a
# "Data fetch error" for the pair.
MAX_RETRIES      = 3
RETRY_BASE_DELAY = 1.0  # seconds; doubles each attempt (1s, 2s, 4s)

# In-memory cache: {(pair, timeframe): (DataFrame, monotonic_timestamp)}
_cache: dict[tuple[str, str], tuple[pd.DataFrame, float]] = {}

# Hyperliquid interval → milliseconds
_INTERVAL_MS: dict[str, int] = {
    "1m":  60_000,
    "3m":  3  * 60_000,
    "5m":  5  * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h":  60 * 60_000,
    "2h":  2  * 60 * 60_000,
    "4h":  4  * 60 * 60_000,
    "8h":  8  * 60 * 60_000,
    "12h": 12 * 60 * 60_000,
    "1d":  24 * 60 * 60_000,
}


def _is_retryable(exc: Exception) -> bool:
    """True for transient failures worth a retry: rate limits, 5xx, network."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.RequestError)  # timeouts, connection resets


def _post_with_retry(payload: dict, timeout: int = 10) -> httpx.Response:
    """POSTs to Hyperliquid, retrying transient errors with exponential backoff."""
    last_exc: Exception = RuntimeError("no attempt made")

    for attempt in range(MAX_RETRIES):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(HL_BASE_URL, json=payload)
                response.raise_for_status()
            return response

        except Exception as exc:
            last_exc = exc
            if not _is_retryable(exc) or attempt == MAX_RETRIES - 1:
                raise
            delay = RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning(
                "Hyperliquid request failed (%s), retry %d/%d in %.0fs",
                exc, attempt + 1, MAX_RETRIES - 1, delay,
            )
            time.sleep(delay)

    raise last_exc


def post_info(payload: dict, timeout: int = 10) -> dict:
    """
    Public wrapper around the retrying POST: sends `payload` to Hyperliquid's
    info endpoint and returns the parsed JSON.

    Exists so every module reading Hyperliquid's public API shares one
    definition of retry/backoff behaviour instead of each keeping its own copy.
    """
    return _post_with_retry(payload, timeout).json()


def _fetch_from_hyperliquid(pair: str, timeframe: str, limit: int) -> pd.DataFrame:
    """Calls Hyperliquid public candleSnapshot endpoint — no API key required."""
    end_ms   = int(time.time() * 1000)
    interval_ms = _INTERVAL_MS.get(timeframe, 15 * 60_000)
    start_ms = end_ms - limit * interval_ms

    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin":      pair,
            "interval":  timeframe,
            "startTime": start_ms,
            "endTime":   end_ms,
        },
    }

    response = _post_with_retry(payload)

    candles = response.json()
    if not candles:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame([
        {
            "timestamp": c["t"],
            "open":      float(c["o"]),
            "high":      float(c["h"]),
            "low":       float(c["l"]),
            "close":     float(c["c"]),
            "volume":    float(c["v"]),
        }
        for c in candles
    ])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
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
    No API key required — Hyperliquid public REST.
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
        df = _fetch_from_hyperliquid(pair, timeframe, limit)
        _cache[cache_key] = (df, now)
        logger.debug("Fetched %s %s (%d candles)", pair, timeframe, len(df))
        return df.copy()

    except Exception as exc:
        logger.warning("Hyperliquid fetch failed for %s %s: %s", pair, timeframe, exc)
        if cache_key in _cache:
            logger.info("Returning stale cache for %s %s", pair, timeframe)
            return _cache[cache_key][0].copy()
        raise


def get_current_price(pair: str) -> float:
    """Fetches the latest mid price from Hyperliquid — no API key required."""
    response = _post_with_retry({"type": "allMids"}, timeout=5)
    mids = response.json()
    if pair not in mids:
        raise ValueError(f"Price not found for {pair} on Hyperliquid")
    return float(mids[pair])


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
