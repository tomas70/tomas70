"""
Live account state from Evedex — balance and closed-position history.

Reads via the account's read-only API key (Settings -> API -> Create API
Key on the Evedex exchange), sent as the x-api-key header. No wallet
private key or order signing involved — this is read-only, the same data
the account owner sees on the Evedex UI.

Replaces manual /balance tracking: the account value and win/loss record
now come from what actually happened on the exchange, not from a number
the user has to remember to update.
"""
import logging
import time
from typing import Optional

import httpx

from config import EVEDEX_API_KEY

logger = logging.getLogger(__name__)

EXCHANGE_BASE_URL = "https://trading-api.evedex.com"
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0


class AccountNotConfigured(Exception):
    """Raised when EVEDEX_API_KEY is not set in config/.env."""


def _require_api_key() -> str:
    if not EVEDEX_API_KEY:
        raise AccountNotConfigured(
            "EVEDEX_API_KEY nenustatytas .env faile. Susikurk read-only API "
            "raktą Evedex svetainėje (Settings -> API -> Create API Key) ir "
            "įrašyk jį kaip EVEDEX_API_KEY=... — botas jo naudoja tik "
            "balansui/istorijai skaityti, niekada orderiams siųsti."
        )
    return EVEDEX_API_KEY


def _get(path: str, params: Optional[dict] = None, timeout: int = 10) -> dict:
    """GETs `path` on the Exchange Service with the account's API key, retrying transient errors."""
    api_key = _require_api_key()
    url = f"{EXCHANGE_BASE_URL}{path}"
    last_exc: Exception = RuntimeError("no attempt made")

    for attempt in range(MAX_RETRIES):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.get(url, params=params, headers={"x-api-key": api_key})
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
            logger.warning("Evedex account request failed (%s), retry in %.0fs", exc, delay)
            time.sleep(delay)

    raise last_exc


# ─── Balance ───────────────────────────────────────────────────────────────────

_last_known_balance: Optional[float] = None


def get_account_balance() -> float:
    """
    Returns the account's total equity in USDT: funding (cash) balance plus
    unrealized PnL on every open position — the same thing Evedex's own UI
    shows as account value.

    Evedex's available-balance endpoint gives spendable margin, not total
    equity, so this combines it with GET /api/position (which carries each
    position's unRealizedPnL) rather than using either alone.

    On a transient failure (network hiccup, Evedex outage), falls back to
    the last successfully fetched value rather than raising — balance feeds
    position sizing on every scheduled scan, so one failed request shouldn't
    stop the bot from scanning. Only raises when no successful fetch has
    ever happened yet (misconfiguration, or the very first call failing).
    """
    global _last_known_balance

    try:
        available = _get("/api/market/available-balance")
        cash = float(available["funding"]["balance"])

        positions = _get("/api/position").get("list", [])
        unrealized = sum(float(p.get("unRealizedPnL", 0) or 0) for p in positions)

        balance = cash + unrealized
        _last_known_balance = balance
        return balance

    except Exception as exc:
        if _last_known_balance is not None:
            logger.warning(
                "Evedex balance fetch failed (%s) — using last known value $%.2f",
                exc, _last_known_balance,
            )
            return _last_known_balance
        raise


# ─── Trade History ─────────────────────────────────────────────────────────────

def get_closed_positions(lookback_days: int = 30) -> list[dict]:
    """
    Returns closed positions (actual exchange trades, realized) from the
    last `lookback_days`, oldest first. Each entry includes instrument,
    side, realizedPnL, fee, and open/close timestamps.

    Evedex has no bulk fills-by-time endpoint (fills are only readable
    per-order via GET /api/order/{orderId}/fill); GET /api/position/history
    gives the same win/loss signal at the position level instead, which is
    what summarize_trades below actually needs.
    """
    since_ms = int((time.time() - lookback_days * 86400) * 1000)
    data = _get("/api/position/history")
    closed = data.get("list", []) if isinstance(data, dict) else []

    def closed_at_ms(p: dict) -> float:
        from datetime import datetime
        return datetime.fromisoformat(p["closeAt"].replace("Z", "+00:00")).timestamp() * 1000

    recent = [p for p in closed if closed_at_ms(p) >= since_ms]
    return sorted(recent, key=closed_at_ms)


def summarize_trades(positions: list[dict]) -> dict:
    """
    Groups closed positions into a win/loss summary using realizedPnL.
    """
    wins    = [p for p in positions if float(p.get("realizedPnL", 0)) > 0]
    losses  = [p for p in positions if float(p.get("realizedPnL", 0)) < 0]

    total_pnl  = sum(float(p.get("realizedPnL", 0)) for p in positions)
    total_fees = sum(float(p.get("fee", 0)) for p in positions)

    return {
        "total_fills":    len(positions),
        "closing_fills":  len(positions),
        "wins":           len(wins),
        "losses":         len(losses),
        "win_rate":       round(len(wins) / len(positions) * 100, 1) if positions else None,
        "total_pnl":      round(total_pnl, 2),
        "total_fees":     round(total_fees, 2),
        "net_pnl":        round(total_pnl - total_fees, 2),
    }
