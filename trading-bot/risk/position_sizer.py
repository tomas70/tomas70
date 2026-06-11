import logging
from config import CHALLENGE_LEVELS, LEVERAGE, RISK_PERCENTAGE

logger = logging.getLogger(__name__)


# ─── Level Tracking ───────────────────────────────────────────────────────────

def get_current_level(account_balance: float) -> dict:
    """
    Returns the current challenge level and progress info for the given balance.

    Walks the table from the top so the highest matching level is returned
    (handles the case where balance has grown past level.balance).

    Returns:
        {
            'level':              int,
            'balance':            float,   # level start balance
            'profit_goal':        float,   # profit needed to advance
            'next_level_balance': float,   # target balance for next level
            'remaining_profit':   float,   # still needed from current balance
            'progress_pct':       float,   # 0–100% through current level
        }
    """
    matched = CHALLENGE_LEVELS[0]
    for row in CHALLENGE_LEVELS:
        if account_balance >= row["balance"]:
            matched = row
        else:
            break

    next_balance    = matched["balance"] + matched["profit_goal"]
    remaining       = max(0.0, next_balance - account_balance)
    earned_so_far   = max(0.0, account_balance - matched["balance"])
    progress_pct    = round(earned_so_far / matched["profit_goal"] * 100, 1) if matched["profit_goal"] else 100.0

    return {
        "level":              matched["level"],
        "balance":            matched["balance"],
        "profit_goal":        matched["profit_goal"],
        "next_level_balance": next_balance,
        "remaining_profit":   round(remaining, 4),
        "progress_pct":       progress_pct,
    }


def is_level_complete(account_balance: float) -> bool:
    """Returns True if balance has reached the next level's start balance."""
    level_info = get_current_level(account_balance)
    return account_balance >= level_info["next_level_balance"]


# ─── Position Sizing ──────────────────────────────────────────────────────────

def calculate_position(
    account_balance: float,
    entry_price: float,
    sl_price: float,
) -> dict:
    """
    Calculates position size using fixed-fractional risk model.

    Formula (from spec):
        risk_amount       = account_balance * RISK_PERCENTAGE   (30%)
        sl_distance_pct   = |entry - sl| / entry
        position_size_usd = risk_amount / sl_distance_pct       (notional)
        leverage          = min(LEVERAGE, position_size_usd / risk_amount)

    Note: margin_usd = position_size_usd / leverage.
    If margin_usd > account_balance the exchange won't allow the trade —
    caller should compare and warn the user.

    Args:
        account_balance: current USDT balance
        entry_price:     planned entry price
        sl_price:        stop-loss price

    Returns:
        {
            'risk_usd':      float,  # USDT at risk (= account * 30%)
            'position_usd':  float,  # notional position size
            'leverage':      float,  # actual leverage (capped at LEVERAGE)
            'qty':           float,  # base currency quantity (position_usd / entry)
            'margin_usd':    float,  # margin required = position_usd / leverage
            'sl_pct':        float,  # SL distance as % of entry
        }
    """
    if entry_price <= 0 or sl_price <= 0:
        raise ValueError("Prices must be positive")

    sl_distance_pct = abs(entry_price - sl_price) / entry_price
    if sl_distance_pct == 0:
        raise ValueError("Entry and SL prices cannot be equal")

    risk_amount      = account_balance * RISK_PERCENTAGE
    position_usd     = risk_amount / sl_distance_pct
    leverage         = min(float(LEVERAGE), position_usd / risk_amount)
    leverage         = max(1.0, round(leverage, 1))
    margin_usd       = position_usd / leverage
    qty              = position_usd / entry_price

    logger.debug(
        "Position: balance=%.2f risk=%.2f notional=%.2f lev=%.1fx margin=%.2f qty=%.6f",
        account_balance, risk_amount, position_usd, leverage, margin_usd, qty,
    )

    return {
        "risk_usd":     round(risk_amount, 4),
        "position_usd": round(position_usd, 4),
        "leverage":     leverage,
        "qty":          round(qty, 6),
        "margin_usd":   round(margin_usd, 4),
        "sl_pct":       round(sl_distance_pct * 100, 4),
    }


# ─── Combined Summary ─────────────────────────────────────────────────────────

def get_position_summary(
    account_balance: float,
    entry_price: float,
    sl_price: float,
) -> dict:
    """
    Merges level info and position sizing into a single dict for
    the Telegram notification and Claude analyst modules.

    Returns:
        All fields from calculate_position plus:
        {
            'level':              int,
            'progress_pct':       float,
            'remaining_profit':   float,
            'next_level_balance': float,
            'margin_ok':          bool,   # False if margin > account_balance
        }
    """
    level_info = get_current_level(account_balance)
    position   = calculate_position(account_balance, entry_price, sl_price)

    return {
        **position,
        "level":              level_info["level"],
        "progress_pct":       level_info["progress_pct"],
        "remaining_profit":   level_info["remaining_profit"],
        "next_level_balance": level_info["next_level_balance"],
        "margin_ok":          position["margin_usd"] <= account_balance,
    }
