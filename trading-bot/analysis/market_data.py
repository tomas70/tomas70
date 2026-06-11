import time
import logging
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from binance.client import Client
from binance.exceptions import BinanceAPIException

from config import BINANCE_API_KEY, BINANCE_SECRET, CANDLES_LIMIT, PAIRS, TIMEFRAMES

logger = logging.getLogger(__name__)

# In-memory cache: {(pair, timeframe): (DataFrame, timestamp)}
_cache: dict[tuple[str, str], tuple[pd.DataFrame, float]] = {}
CACHE_TTL_SECONDS = 60 * 14  # refresh if older than 14 minutes

_client: Optional[Client] = None


def _get_client() -> Client:
    global _client
    if _client is None:
        _client = Client(BINANCE_API_KEY, BINANCE_SECRET)
    return _client


def _binance_tf_to_client(timeframe: str) -> str:
    mapping = {
        "1m":  Client.KLINE_INTERVAL_1MINUTE,
        "3m":  Client.KLINE_INTERVAL_3MINUTE,
        "5m":  Client.KLINE_INTERVAL_5MINUTE,
        "15m": Client.KLINE_INTERVAL_15MINUTE,
        "30m": Client.KLINE_INTERVAL_30MINUTE,
        "1h":  Client.KLINE_INTERVAL_1HOUR,
        "2h":  Client.KLINE_INTERVAL_2HOUR,
        "4h":  Client.KLINE_INTERVAL_4HOUR,
        "1d":  Client.KLINE_INTERVAL_1DAY,
    }
    return mapping[timeframe]


def _fetch_from_binance(pair: str, timeframe: str, limit: int) -> pd.DataFrame:
    client = _get_client()
    klines = client.get_klines(
        symbol=pair,
        interval=_binance_tf_to_client(timeframe),
        limit=limit,
    )
    df = pd.DataFrame(klines, columns=[
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ])
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
    Uses in-memory cache; falls back to cached data if Binance is unreachable.
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
        logger.debug("Fetched %s %s (%d candles) from Binance", pair, timeframe, len(df))
        return df.copy()

    except BinanceAPIException as exc:
        logger.warning("Binance API error for %s %s: %s", pair, timeframe, exc)
        if cache_key in _cache:
            logger.info("Returning stale cache for %s %s", pair, timeframe)
            return _cache[cache_key][0].copy()
        raise

    except Exception as exc:
        logger.error("Unexpected error fetching %s %s: %s", pair, timeframe, exc)
        if cache_key in _cache:
            logger.info("Returning stale cache for %s %s", pair, timeframe)
            return _cache[cache_key][0].copy()
        raise


def get_all_timeframes(pair: str) -> dict[str, pd.DataFrame]:
    """Fetches 15m, 1h and 4h data for a pair in one call."""
    return {tf: get_ohlcv(pair, tf) for tf in TIMEFRAMES}


def invalidate_cache(pair: Optional[str] = None, timeframe: Optional[str] = None) -> None:
    """Clears cache entries. If neither arg given, clears everything."""
    if pair is None and timeframe is None:
        _cache.clear()
        return
    keys_to_delete = [
        k for k in _cache
        if (pair is None or k[0] == pair) and (timeframe is None or k[1] == timeframe)
    ]
    for k in keys_to_delete:
        del _cache[k]
