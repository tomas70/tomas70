import time
import logging
from typing import Optional

import httpx
import pandas as pd

from config import CANDLES_LIMIT, PAIRS, TIMEFRAMES

logger = logging.getLogger(__name__)

BINANCE_BASE_URL  = "https://api.binance.com"
CACHE_TTL_SECONDS = 60 * 14  # refresh if older than 14 minutes

# In-memory cache: {(pair, timeframe): (DataFrame, monotonic_timestamp)}
_cache: dict[tuple[str, str], tuple[pd.DataFrame, float]] = {}

_KLINE_COLUMNS = [
    "timestamp", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades",
    "taker_buy_base", "taker_buy_quote", "ignore",
]


def _fetch_from_binance(pair: str, timeframe: str, limit: int) -> pd.DataFrame:
    """Calls Binance public klines endpoint — no API key required."""
    url = f"{BINANCE_BASE_URL}/api/v3/klines"
    params = {"symbol": pair, "interval": timeframe, "limit": limit}

    with httpx.Client(timeout=10) as client:
        response = client.get(url, params=params)
        response.raise_for_status()

    df = pd.DataFrame(response.json(), columns=_KLINE_COLUMNS)
    df = df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
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
    Uses in-memory cache (14 min TTL); falls back to stale cache if Binance
    is unreachable.

    No API key required — uses Binance public REST endpoint.
    """
    if pair not in PAIRS:
        raise ValueError(f"Unsupported pair: {pair}. Allowed: {PAIRS}")
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe: {timeframe}. Allowed: {TIMEFRAMES}")

    cache_key = (pair, timeframe)
    now = time.monotonic()

    if not force_refresh and cache_key in _cache:
        cached_df, cached_at = _cache[cache_key]
        if now - cached_at < CACHE_TTL_SECONDS:
            return cached_df.copy()

    try:
        df = _fetch_from_binance(pair, timeframe, limit)
        _cache[cache_key] = (df, now)
        logger.debug("Fetched %s %s (%d candles)", pair, timeframe, len(df))
        return df.copy()

    except Exception as exc:
        logger.warning("Binance fetch failed for %s %s: %s", pair, timeframe, exc)
        if cache_key in _cache:
            logger.info("Returning stale cache for %s %s", pair, timeframe)
            return _cache[cache_key][0].copy()
        raise


def get_current_price(pair: str) -> float:
    """Fetches the latest price from Binance ticker — no API key required."""
    url = f"{BINANCE_BASE_URL}/api/v3/ticker/price"
    with httpx.Client(timeout=5) as client:
        response = client.get(url, params={"symbol": pair})
        response.raise_for_status()
    return float(response.json()["price"])


def get_all_timeframes(pair: str) -> dict[str, pd.DataFrame]:
    """Fetches 15m, 1h, and 4h DataFrames for a pair in one call."""
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
