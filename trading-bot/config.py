import os
from dotenv import load_dotenv

load_dotenv()

# API Keys (from .env)
BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET: str = os.getenv("BINANCE_SECRET", "")
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# Trading pairs
PAIRS: list[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

# Timeframes
TIMEFRAMES: list[str] = ["15m", "1h", "4h"]

# Data settings
CANDLES_LIMIT: int = 200
SCAN_INTERVAL_MINUTES: int = 15

# Risk management (hardcoded, cannot be changed by user)
RISK_PERCENTAGE: float = 0.30       # 30% of balance per trade
MIN_RR_RATIO: float = 3.0           # Minimum Risk:Reward ratio
LEVERAGE: int = 5                   # Max leverage on Evedex
MAX_OPEN_POSITIONS: int = 1         # Only 1 trade at a time
MIN_CONFIDENCE_SCORE: int = 7       # Minimum AI confidence to send signal

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
