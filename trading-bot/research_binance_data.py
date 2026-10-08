"""
Downloads daily spot history (close + USDT quote volume) for every Binance
USDT symbol — INCLUDING delisted / halted ones (status BREAK). Keeping the
dead coins matters: a universe built only from symbols alive today is
survivorship-biased in exactly the direction that flatters momentum.

Usage (from trading-bot/):
    .venv/bin/python research_binance_data.py [cache_dir=logs/binance_cache]

Uses the market-data-only host, which serves public data without a key.
Resumable: symbols already cached are skipped.
"""
import pickle
import re
import sys
import time
from pathlib import Path

import pandas as pd
import httpx

BASE  = "https://data-api.binance.vision/api/v3"
START = int(pd.Timestamp("2017-07-01", tz="UTC").timestamp() * 1000)

STABLES = {
    "USDC", "TUSD", "BUSD", "USDP", "DAI", "FDUSD", "USDD", "UST", "USTC", "PAX",
    "SUSD", "EUR", "AEUR", "GBP", "TRY", "BRL", "ARS", "RUB", "UAH", "NGN", "PLN",
    "RON", "CZK", "JPY", "MXN", "COP", "USD1", "XUSD", "USDE", "PYUSD", "EURI",
    "PAXG", "XAUT", "WBTC", "WBETH", "BETH", "STETH", "BFUSD", "AUD", "ZAR", "IDRT", "BVND",
}
LEVERAGED = re.compile(r"(UP|DOWN|BULL|BEAR)$")


def tradable_universe(base_asset: str) -> bool:
    """Rule used by both the downloader and the analysis."""
    if base_asset in STABLES:
        return False
    if len(base_asset) > 4 and LEVERAGED.search(base_asset):
        return False
    return True


def symbols() -> list[tuple[str, str]]:
    info = httpx.get(f"{BASE}/exchangeInfo", timeout=30).json()
    out = [(s["symbol"], s["baseAsset"]) for s in info["symbols"] if s["quoteAsset"] == "USDT"]
    return sorted(x for x in out if tradable_universe(x[1]))


def fetch(symbol: str) -> pd.DataFrame:
    rows, start = [], START
    while True:
        for attempt in range(5):
            r = httpx.get(f"{BASE}/klines", timeout=30,
                             params={"symbol": symbol, "interval": "1d", "startTime": start, "limit": 1000})
            if r.status_code in (418, 429):
                time.sleep(15 * (attempt + 1))
                continue
            r.raise_for_status()
            break
        page = r.json()
        if not page:
            break
        rows += page
        if len(page) < 1000:
            break
        start = page[-1][0] + 1
        time.sleep(0.1)
    if not rows:
        return pd.DataFrame(columns=["close", "quote_volume"])
    df = pd.DataFrame(rows).iloc[:, [0, 4, 7]]
    df.columns = ["ts", "close", "quote_volume"]
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df[["close", "quote_volume"]] = df[["close", "quote_volume"]].astype(float)
    return df.set_index("ts")


def main() -> None:
    cache = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs/binance_cache")
    cache.mkdir(parents=True, exist_ok=True)
    syms = symbols()
    print(f"{len(syms)} USDT symbols (stablecoins / leveraged tokens excluded)", flush=True)
    for i, (sym, base) in enumerate(syms, 1):
        f = cache / f"{sym}.pkl"
        if f.exists():
            continue
        try:
            df = fetch(sym)
        except Exception as exc:
            print(f"! {sym}: {exc}", flush=True)
            continue
        f.write_bytes(pickle.dumps((base, df)))
        if i % 25 == 0:
            print(f"{i}/{len(syms)} {sym}", flush=True)
        time.sleep(0.05)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
