import os
from dotenv import load_dotenv

load_dotenv()

# ── Credentials ───────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID:   str = os.getenv("TELEGRAM_CHAT_ID",   "")

# Evedex API key (read-only account access — created under Settings -> API
# on the Evedex exchange). Sent as the x-api-key header on every private
# request. Required for /status and /positions; the bot never signs or
# places orders, so a read-only key is enough.
EVEDEX_API_KEY: str = os.getenv("EVEDEX_API_KEY", "")

# ── Pair selection (used by analysis/pair_selection.py) ──────────────────────
# Scan every perp that is actually liquid right now. Evedex lists only a few
# dozen tradable perps, so the thresholds are 0 (no filter).
USE_DYNAMIC_PAIRS: bool = True
MIN_DAY_VOLUME_USD: float    = 0   # 24h notional volume
MIN_OPEN_INTEREST_USD: float = 0   # open interest, converted to USD
MAX_ACTIVE_PAIRS: int = 60

# Fallback list, used when USE_DYNAMIC_PAIRS is False or exchange metadata
# can't be read (coin symbol only; market_data appends "USD" for the
# instrument name).
PAIRS: list[str] = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "LINK", "DOT",
    "LTC", "BCH", "ATOM", "NEAR", "SUI", "APT", "ARB", "OP",
]

# ── Market data ───────────────────────────────────────────────────────────────
# Timeframes market_data is allowed to fetch.
TIMEFRAMES: list[str] = ["15m", "1h", "4h"]
GLOBAL_TREND_TIMEFRAME: str = "1d"
SUPPORTED_TIMEFRAMES: list[str] = [*TIMEFRAMES, GLOBAL_TREND_TIMEFRAME]
CANDLES_LIMIT: int = 200
