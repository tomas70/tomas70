"""
Global trend filter — higher-timeframe and market-wide context.

The 4H bias gate already stops a setup from fighting its own intermediate
trend, but it says nothing about two larger forces:

  1. The pair's own DAILY trend. A 4H bullish leg inside a daily downtrend
     is a countertrend bounce — it can run, but it's the lower-probability
     half of the book.
  2. The market-wide regime, proxied by BTC's daily trend. Alts are highly
     correlated to BTC: an alt long while BTC is in a daily downtrend is
     swimming against the dominant flow, however clean the alt's own 15m
     structure looks.

Both checks are deliberately permissive in one direction: a "ranging"
daily structure does NOT block a setup. Only an actively OPPOSING daily
trend does. Filtering on ranging as well would reject most setups, since
detect_market_structure() reports "ranging" whenever it can't find enough
confirmed swings — which is common on 200 daily candles.

Cost: both lookups go through market_data's cache and are only called for
setups that already passed the cheaper gates, so a full scan adds a
handful of requests rather than one per pair.
"""
import logging

from .market_data import get_ohlcv
from .smc import detect_market_structure
from config import GLOBAL_TREND_TIMEFRAME, MARKET_LEADER_PAIR

logger = logging.getLogger(__name__)


def get_daily_bias(pair: str) -> str:
    """
    Returns the pair's daily structure bias: "bullish" | "bearish" | "ranging".

    Falls back to "ranging" (neutral — does not block) if the data can't be
    fetched, so a transient API failure never silently rejects every setup.
    """
    try:
        df = get_ohlcv(pair, GLOBAL_TREND_TIMEFRAME)
    except Exception as exc:
        logger.warning("Daily bias unavailable for %s: %s", pair, exc)
        return "ranging"

    if df.empty:
        return "ranging"

    return detect_market_structure(df)["bias"]


def check_global_trend(pair: str, bias: str) -> dict:
    """
    Checks a setup direction against the pair's daily trend and the market
    regime (BTC daily trend).

    Returns:
        {
            "ok":           bool,   # False = setup opposes a larger trend
            "reason":       str,    # why it was rejected ("" when ok)
            "daily_bias":   str,    # this pair's daily structure
            "market_regime": str,   # BTC's daily structure
        }
    """
    daily_bias = get_daily_bias(pair)

    # Only an actively opposing daily trend blocks — "ranging" is neutral.
    if daily_bias != "ranging" and daily_bias != bias:
        return {
            "ok":            False,
            "reason":        f"Daily trend is {daily_bias} but setup is {bias}",
            "daily_bias":    daily_bias,
            "market_regime": "n/a",
        }

    # BTC sets the regime for everything else; it can't gate itself.
    market_regime = daily_bias if pair == MARKET_LEADER_PAIR else get_daily_bias(MARKET_LEADER_PAIR)

    if pair != MARKET_LEADER_PAIR and market_regime != "ranging" and market_regime != bias:
        return {
            "ok":            False,
            "reason":        f"{MARKET_LEADER_PAIR} daily regime is {market_regime} but setup is {bias}",
            "daily_bias":    daily_bias,
            "market_regime": market_regime,
        }

    return {
        "ok":            True,
        "reason":        "",
        "daily_bias":    daily_bias,
        "market_regime": market_regime,
    }
