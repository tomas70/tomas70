import os
from dotenv import load_dotenv

load_dotenv()

# ── Optional credentials ──────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID:   str = os.getenv("TELEGRAM_CHAT_ID",   "")

# Anthropic API key: only needed for standalone mode (without Claude Desktop)
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

# Public Hyperliquid wallet address (0x...) — NOT a private key. Used to read
# live account balance and executed trade history via the public info API.
# Required for balance/status to work; the bot never signs or trades.
HYPERLIQUID_ADDRESS: str = os.getenv("HYPERLIQUID_ADDRESS", "")

# Ross Hook continuation setup. Disabled after it underperformed a coin flip
# in every window measured: -7.2pp edge over 92 pre-epoch trades, then
# -18.1pp (n=11) and -17.6pp (n=17) under the current rules, expectancy stuck
# near -0.57R throughout, and the 6 trades added between the last two
# readings went 1W/5L rather than reversing it. Kept as a flag, not deleted,
# because the plan is to revisit it with an order-book-wall stop instead of
# the OB-derived one (see analysis/liquidity_walls.py) — the entry signal may
# well be sound while the stop placement is what keeps failing.
HOOK_SETUP_ENABLED: bool = False

# Cutoff for /log statistics: rows logged before this are excluded from the
# DEFAULT (epoch-filtered) view. Bump this to "now" whenever a change to
# setup detection/gating logic ships — trade_log.csv accumulates forever, so
# without a cutoff, stats permanently blend results from every past rule-set
# with the current one, and a real improvement (or regression) gets diluted
# into invisibility. Full unfiltered history is still available via /log all.
#
# Last bumped: HOOK disabled + MIN_RR_RATIO restored to 3.0. From here the
# only setup generating alerts is SWEEP at RR>=3, so this window measures
# that one thing cleanly instead of a HOOK/SWEEP blend at two RR floors.
STRATEGY_EPOCH: str = "2026-09-03T09:41:14+00:00"

# Trading pairs — Hyperliquid perpetual futures (coin symbol only)
# Selected for: high Hyperliquid volume + TradFi presence (CME/ETF/institutional)
PAIRS: list[str] = [
    # Tier 1 — CME futures + ETF (IBIT, FBTC, ETHA...)
    "BTC", "ETH", "SOL", "BNB", "XRP",
    # Tier 2 — institutional + regulated, high open interest
    "DOGE", "AVAX", "LINK", "ADA", "DOT",
    "LTC", "BCH", "ATOM", "NEAR", "SUI",
    # Tier 3 — major DeFi + L2 + ecosystem
    # TON renamed to GRAM (ticker + symbol) on 2026-06-15 after an 81.22%
    # community vote; Hyperliquid delisted the old TON perp around the same
    # time and lists the renamed asset as GRAM — same network/holdings, new
    # symbol only, no token swap.
    "APT", "ARB", "OP", "GRAM", "UNI",
]

# Timeframes fetched for every pair on every scan
TIMEFRAMES: list[str] = ["15m", "1h", "4h"]

# Higher timeframe used for the global trend filter. Fetched lazily — only
# for setups that already passed the cheaper 15m/4H/OB/RR gates — so a scan
# adds a handful of requests, not one per pair.
GLOBAL_TREND_TIMEFRAME: str = "1d"

# Everything market_data is allowed to fetch (TIMEFRAMES + lazy ones)
SUPPORTED_TIMEFRAMES: list[str] = [*TIMEFRAMES, GLOBAL_TREND_TIMEFRAME]

# Pair whose trend defines the market-wide regime. Alts follow BTC closely,
# so an alt long during a BTC downtrend fights the dominant flow.
MARKET_LEADER_PAIR: str = "BTC"

# Data settings
CANDLES_LIMIT: int = 200
SCAN_INTERVAL_MINUTES: int = 15

# Risk management (hardcoded)
RISK_PERCENTAGE: float = 0.30       # 30% of balance per trade
# Minimum Risk:Reward ratio. Restored to 3.0 after the 1.5 experiment:
# the extra setups the lower threshold admitted lost money as a group
# (1.5-2 band: -0.60R over 7 trades; 2-3 band: 0 wins in 3, and 0 wins in 2
# at the prior reading), while everything at 3.0+ was the only band holding
# above breakeven. The looser threshold bought faster data at the cost of
# systematically worse setups, and the data it bought says to undo it.
MIN_RR_RATIO: float = 3.0
LEVERAGE: int = 5                   # Max leverage
MAX_OPEN_POSITIONS: int = 1
MIN_CONFIDENCE_SCORE: int = 7

# Challenge levels table: $20 → $40,000 over 30 levels
CHALLENGE_LEVELS: list[dict] = [
    {"level": 1,  "balance": 20,     "profit_goal": 6},
    {"level": 2,  "balance": 26,     "profit_goal": 8},
    {"level": 3,  "balance": 34,     "profit_goal": 10},
    {"level": 4,  "balance": 44,     "profit_goal": 14},
    {"level": 5,  "balance": 58,     "profit_goal": 18},
    {"level": 6,  "balance": 76,     "profit_goal": 22},
    {"level": 7,  "balance": 98,     "profit_goal": 28},
    {"level": 8,  "balance": 126,    "profit_goal": 38},
    {"level": 9,  "balance": 164,    "profit_goal": 48},
    {"level": 10, "balance": 212,    "profit_goal": 64},
    {"level": 11, "balance": 276,    "profit_goal": 82},
    {"level": 12, "balance": 358,    "profit_goal": 142},
    {"level": 13, "balance": 466,    "profit_goal": 142},
    {"level": 14, "balance": 606,    "profit_goal": 182},
    {"level": 15, "balance": 788,    "profit_goal": 236},
    {"level": 16, "balance": 1024,   "profit_goal": 308},
    {"level": 17, "balance": 1332,   "profit_goal": 400},
    {"level": 18, "balance": 1732,   "profit_goal": 520},
    {"level": 19, "balance": 2252,   "profit_goal": 674},
    {"level": 20, "balance": 2926,   "profit_goal": 878},
    {"level": 21, "balance": 3804,   "profit_goal": 1140},
    {"level": 22, "balance": 4944,   "profit_goal": 1482},
    {"level": 23, "balance": 6426,   "profit_goal": 1928},
    {"level": 24, "balance": 8354,   "profit_goal": 2506},
    {"level": 25, "balance": 10860,  "profit_goal": 3356},
    {"level": 26, "balance": 14116,  "profit_goal": 4234},
    {"level": 27, "balance": 18350,  "profit_goal": 5504},
    {"level": 28, "balance": 23854,  "profit_goal": 7156},
    {"level": 29, "balance": 31010,  "profit_goal": 9302},
    {"level": 30, "balance": 40312,  "profit_goal": 12092},
]
