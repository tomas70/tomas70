"""
Ledger + candle tools for evaluating a Telegram signal group's calls against
independent price data. Parsing/export format: Telegram Desktop "Export chat
history" (HTML). Outcome analysis lives in research_kralov_eval.py and follows
research/kralov_signals_prereg.md.

Usage:
    python research_kralov.py parse  <export1.html> [<export2.html> ...] <out.pkl>
    python research_kralov.py fetch  <ledger.pkl> <cache_dir> [part total]
"""
import pickle
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

DATE_FMT = "%d %B %Y, %H:%M:%S"
NUM = r"([0-9]*\.?[0-9]+)"


ALIASES = {"PUMPUSD": "PUMPFUNUSD", "1MBABYDOGEUSD": "1000000BABYDOGEUSD"}


def _instrument(raw: str) -> str:
    """'#DOGEUSDT' / '#1000BONKUSD' (+ '.P') -> Evedex-style instrument name."""
    s = raw.lstrip("#").removesuffix(".P")
    if s.endswith("USDT"):
        s = s[:-4] + "USD"
    return ALIASES.get(s, s)


def parse_signal(text: str) -> Optional[dict]:
    side = "long" if "ПОКУПКА" in text else "short" if "ПРОДАЖА" in text else None
    if not side:
        return None
    t = re.search(r"#(\S+)", text)
    rng = re.search(NUM + r"\s*-\s*" + NUM, text.split("Диапазон входа:")[-1]) if "Диапазон входа" in text else None
    tps = [float(x) for x in re.findall(r"Тейк-профит \d:\s*" + NUM, text)]
    sl = re.search(r"Стоп-лосс:\s*" + NUM, text)
    if not (t and rng and sl and len(tps) == 5):
        return None
    a, b = float(rng.group(1)), float(rng.group(2))
    return {"instrument": _instrument(t.group(1)), "side": side,
            "zone_lo": min(a, b), "zone_hi": max(a, b), "tps": tps, "sl": float(sl.group(1))}


def parse_update(text: str) -> Optional[dict]:
    t = re.search(r"#(\S+)", text)
    if not t:
        return None
    inst = _instrument(t.group(1))
    m = re.search(r"ТП(\d)\s*" + NUM + r"\s*\n?\s*Прибыль:\s*(-?[0-9.]+)%", text)
    if m:
        return {"kind": "tp", "instrument": inst, "n": int(m.group(1)),
                "price": float(m.group(2)), "profit_pct": float(m.group(3))}
    m = re.search(r"Закрыто из-за.*?Цена закрытия:\s*" + NUM + r"\s*\n?\s*Прибыль:\s*(-?[0-9.]+)%", text, re.S)
    if m:
        return {"kind": "close", "instrument": inst, "price": float(m.group(1)),
                "profit_pct": float(m.group(2))}
    return None


def parse_exports(paths: list[str]) -> tuple[list[dict], list[dict]]:
    from bs4 import BeautifulSoup

    signals, updates = [], []
    for p in paths:
        soup = BeautifulSoup(open(p, encoding="utf-8"), "lxml")
        for m in soup.select("div.message.default"):
            d, t = m.select_one("div.date"), m.select_one("div.text")
            if not (d and t):
                continue
            when = datetime.strptime(d.get("title"), DATE_FMT)       # exporter's LOCAL time
            text = t.get_text("\n").strip()
            sig = parse_signal(text)
            if sig:
                signals.append({**sig, "local_time": when, "msg_id": m.get("id"),
                                "evedex_link": "Открыть в EVEDEX" in text})
                continue
            upd = parse_update(text)
            if upd:
                updates.append({**upd, "local_time": when, "msg_id": m.get("id")})
    key = lambda x: (x["local_time"], x["msg_id"])
    return sorted(signals, key=key), sorted(updates, key=key)


# ── candles by instrument name (bypasses the ticker map) ──────────────────

def fetch_candles(instrument: str, since: datetime, cache_dir: Path) -> pd.DataFrame:
    from analysis import market_data as md

    cache = cache_dir / f"{instrument}.pkl"
    if cache.exists():
        return pickle.loads(cache.read_bytes())
    ms = md._GROUP_MS["15m"]
    end, rows = datetime.now(timezone.utc), {}
    while end > since:
        start = end - timedelta(milliseconds=1000 * ms)
        page = md._get_with_retry(
            f"{md.MARKET_DATA_BASE_URL}/api/history/{instrument}/list",
            params={"group": "15m", "after": start.isoformat(), "before": end.isoformat()},
        ).json()
        if not page:
            break
        for c in page:
            rows[c[0]] = c
        first = datetime.fromtimestamp(min(c[0] for c in page) / 1000, tz=timezone.utc)
        if first >= end:
            break
        end = first
    df = pd.DataFrame([
        {"timestamp": c[0], "open": float(c[1]), "high": float(c[3]), "low": float(c[4]), "close": float(c[2])}
        for c in rows.values()
    ])
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.sort_values("timestamp").reset_index(drop=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(pickle.dumps(df))
    return df


def main() -> None:
    cmd = sys.argv[1]
    if cmd == "parse":
        *paths, out = sys.argv[2:]
        sig, upd = parse_exports(paths)
        pickle.dump({"signals": sig, "updates": upd}, open(out, "wb"))
        print(f"signals {len(sig)}, updates {len(upd)}, "
              f"{sig[0]['local_time']} -> {sig[-1]['local_time']}")
    elif cmd == "fetch":
        ledger = pickle.load(open(sys.argv[2], "rb"))
        cache_dir = Path(sys.argv[3])
        part, total = (int(sys.argv[4]), int(sys.argv[5])) if len(sys.argv) > 5 else (0, 1)
        since = min(s["local_time"] for s in ledger["signals"]).replace(tzinfo=timezone.utc) - timedelta(days=3)
        insts = sorted({s["instrument"] for s in ledger["signals"]})
        for i, inst in enumerate(insts):
            if i % total != part:
                continue
            try:
                df = fetch_candles(inst, since, cache_dir)
                print(f"{inst}: {len(df)} bars", flush=True)
            except Exception as exc:
                print(f"! {inst}: {exc}", flush=True)
        print("FETCHDONE", flush=True)


if __name__ == "__main__":
    main()
