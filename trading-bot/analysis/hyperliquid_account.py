"""
Live account state from Hyperliquid — balance and executed trade history.

Reads directly from the public Hyperliquid info API using the account's
public wallet address (never a private key — this is read-only, same data
anyone can see on the Hyperliquid UI for that address). No API key or
signing required.

Replaces manual /balance tracking: the account value and win/loss record
now come from what actually happened on the exchange, not from a number
the user has to remember to update.
"""
import logging
import time
from typing import Optional

import httpx

from config import HYPERLIQUID_ADDRESS

logger = logging.getLogger(__name__)

HL_BASE_URL = "https://api.hyperliquid.xyz/info"
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0


class AccountNotConfigured(Exception):
    """Raised when HYPERLIQUID_ADDRESS is not set in config/.env."""


def _require_address() -> str:
    if not HYPERLIQUID_ADDRESS:
        raise AccountNotConfigured(
            "HYPERLIQUID_ADDRESS nenustatytas .env faile. Pridėk savo viešą "
            "Hyperliquid wallet adresą (HYPERLIQUID_ADDRESS=0x...) — jį rasi "
            "Hyperliquid svetainėje, tai VIEŠAS account adresas, ne private key."
        )
    return HYPERLIQUID_ADDRESS


def _post(payload: dict, timeout: int = 10) -> dict:
    """POSTs to Hyperliquid's info endpoint with retry on transient errors."""
    last_exc: Exception = RuntimeError("no attempt made")

    for attempt in range(MAX_RETRIES):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(HL_BASE_URL, json=payload)
                response.raise_for_status()
            return response.json()

        except Exception as exc:
            last_exc = exc
            retryable = isinstance(exc, httpx.RequestError) or (
                isinstance(exc, httpx.HTTPStatusError)
                and (exc.response.status_code == 429 or exc.response.status_code >= 500)
            )
            if not retryable or attempt == MAX_RETRIES - 1:
                raise
            delay = RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning("Hyperliquid account request failed (%s), retry in %.0fs", exc, delay)
            time.sleep(delay)

    raise last_exc


# ─── Balance ───────────────────────────────────────────────────────────────────

_last_known_balance: Optional[float] = None


def get_account_balance() -> float:
    """
    Returns the account's total equity in USDC (perp margin account value):
    unrealized PnL included, exactly what Hyperliquid itself shows as
    "Account Value".

    On a transient failure (network hiccup, Hyperliquid outage), falls
    back to the last successfully fetched value rather than raising —
    balance feeds position sizing on every scheduled scan, so one failed
    request shouldn't stop the bot from scanning. Only raises when no
    successful fetch has ever happened yet (misconfiguration, or the
    very first call failing).
    """
    global _last_known_balance
    address = _require_address()

    try:
        data = _post({"type": "clearinghouseState", "user": address})
        balance = float(data["marginSummary"]["accountValue"])
        _last_known_balance = balance
        return balance

    except Exception as exc:
        if _last_known_balance is not None:
            logger.warning(
                "Hyperliquid balance fetch failed (%s) — using last known value $%.2f",
                exc, _last_known_balance,
            )
            return _last_known_balance
        raise


# ─── Trade History ─────────────────────────────────────────────────────────────

def get_recent_fills(lookback_days: int = 30) -> list[dict]:
    """
    Returns executed fills (actual exchange trades) from the last
    `lookback_days`, oldest first. Each fill includes coin, side, price,
    size, closedPnl (nonzero only on fills that closed/reduced a
    position), and fee.
    """
    address  = _require_address()
    since_ms = int((time.time() - lookback_days * 86400) * 1000)
    data = _post({
        "type":      "userFillsByTime",
        "user":      address,
        "startTime": since_ms,
    })
    return sorted(data, key=lambda f: f["time"])


def summarize_trades(fills: list[dict]) -> dict:
    """
    Groups fills into a win/loss summary using closedPnl.

    A fill only carries a nonzero closedPnl when it closes or reduces an
    existing position — opening fills are 0 and excluded, so this counts
    realized outcomes only, not every execution.
    """
    closing = [f for f in fills if float(f.get("closedPnl", 0)) != 0]
    wins    = [f for f in closing if float(f["closedPnl"]) > 0]
    losses  = [f for f in closing if float(f["closedPnl"]) < 0]

    total_pnl  = sum(float(f["closedPnl"]) for f in closing)
    total_fees = sum(float(f.get("fee", 0)) for f in fills)

    return {
        "total_fills":    len(fills),
        "closing_fills":  len(closing),
        "wins":           len(wins),
        "losses":         len(losses),
        "win_rate":       round(len(wins) / len(closing) * 100, 1) if closing else None,
        "total_pnl":      round(total_pnl, 2),
        "total_fees":     round(total_fees, 2),
        "net_pnl":        round(total_pnl - total_fees, 2),
    }
