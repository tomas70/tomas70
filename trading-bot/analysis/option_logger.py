"""
Forward logger of REAL Deribit option quotes (BTC, ETH).

Why: research/options_hedged_prereg.md tested option-premium structures on a
proxy (Black-Scholes at the DVOL index). The proxy cannot show three things
that decide whether selling premium works in practice:
  * how far real straddle prices sit from the DVOL proxy (DVOL is a
    model-free index and normally above ATM IV, so the proxy overstates premium),
  * the real bid/ask cost of opening a straddle (we sell at the bid),
  * perp funding, which is the carrying cost of delta hedging.
This module records them once per UTC day, forward only. It never trades.

Each snapshot: the expiry closest to 30 days (>= 14 d), the ATM strike
nearest the forward, bid/ask/mark for the call and put (in underlying units,
i.e. a fraction of spot), bid/ask/mark IV, the latest DVOL, perp funding.
report() compares real prices with the proxy and, after expiry, settles the
naked short straddle sold at the bid against the Deribit delivery price.

Data: logs/option_quotes.csv (append only, one row per currency per UTC day).
"""
import csv
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

API = "https://www.deribit.com/api/v2/public"
CSV_FILE = Path(__file__).resolve().parent.parent / "logs" / "option_quotes.csv"
CURRENCIES = ("BTC", "ETH")
TARGET_DAYS = 30
MIN_DAYS = 14
PROXY_COST = 0.03            # same option cost as the pre-registered proxy

COLUMNS = [
    "date", "ts", "currency", "expiry", "dte", "strike", "spot", "fwd",
    "call_bid", "call_ask", "call_mark", "put_bid", "put_ask", "put_mark",
    "call_bid_iv", "call_ask_iv", "call_mark_iv", "put_bid_iv", "put_ask_iv", "put_mark_iv",
    "dvol", "funding_8h",
]


def _get(path: str, params: dict) -> dict:
    last: Optional[Exception] = None
    for _ in range(3):
        try:
            r = httpx.get(f"{API}/{path}", params=params, timeout=20)
            r.raise_for_status()
            return r.json()["result"]
        except Exception as exc:                       # network / HTTP / bad JSON
            last = exc
    raise RuntimeError(f"Deribit {path}: {last}")


# ── pure selection logic ──────────────────────────────────────────────────

def pick_expiry(instruments: list[dict], now_ms: int) -> Optional[int]:
    """Expiry timestamp (ms) closest to TARGET_DAYS, at least MIN_DAYS away."""
    best, best_gap = None, None
    for e in {i["expiration_timestamp"] for i in instruments}:
        days = (e - now_ms) / 86_400_000
        if days < MIN_DAYS:
            continue
        gap = abs(days - TARGET_DAYS)
        if best_gap is None or gap < best_gap:
            best, best_gap = e, gap
    return best


def pick_atm(instruments: list[dict], expiry: int, fwd: float) -> Optional[tuple[str, str, float]]:
    """(call name, put name, strike) for the strike nearest the forward that has both legs."""
    calls = {i["strike"]: i["instrument_name"] for i in instruments
             if i["expiration_timestamp"] == expiry and i["option_type"] == "call"}
    puts = {i["strike"]: i["instrument_name"] for i in instruments
            if i["expiration_timestamp"] == expiry and i["option_type"] == "put"}
    both = sorted(set(calls) & set(puts), key=lambda k: abs(k - fwd))
    if not both:
        return None
    k = both[0]
    return calls[k], puts[k], float(k)


def atm_call_pct(iv: float, days: float) -> float:
    """ATM call as a fraction of spot (Black-Scholes, r = 0); iv as a fraction."""
    return math.erf(iv * math.sqrt(days / 365) / 2 / math.sqrt(2)) if iv > 0 else 0.0


# ── snapshot ──────────────────────────────────────────────────────────────

def _quote(name: str) -> dict:
    ob = _get("get_order_book", {"instrument_name": name})
    return {
        "bid": ob.get("best_bid_price") or None, "ask": ob.get("best_ask_price") or None,
        "mark": ob.get("mark_price"), "bid_iv": ob.get("bid_iv") or None,
        "ask_iv": ob.get("ask_iv") or None, "mark_iv": ob.get("mark_iv"),
        "spot": ob.get("index_price"), "fwd": ob.get("underlying_price"),
    }


def _dvol(currency: str, now_ms: int) -> Optional[float]:
    res = _get("get_volatility_index_data", {"currency": currency, "resolution": "3600",
                                              "start_timestamp": now_ms - 6 * 3_600_000,
                                              "end_timestamp": now_ms})
    return res["data"][-1][4] if res.get("data") else None


def snapshot(currency: str, now: Optional[datetime] = None) -> Optional[dict]:
    now = now or datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    instruments = _get("get_instruments", {"currency": currency, "kind": "option", "expired": "false"})
    expiry = pick_expiry(instruments, now_ms)
    if expiry is None:
        return None
    probe = next(i["instrument_name"] for i in instruments if i["expiration_timestamp"] == expiry)
    fwd = _quote(probe)["fwd"]
    atm = pick_atm(instruments, expiry, fwd)
    if atm is None:
        return None
    call_name, put_name, strike = atm
    c, p = _quote(call_name), _quote(put_name)
    perp = _get("ticker", {"instrument_name": f"{currency}-PERPETUAL"})
    return {
        "date": now.strftime("%Y-%m-%d"), "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "currency": currency,
        "expiry": datetime.fromtimestamp(expiry / 1000, timezone.utc).strftime("%Y-%m-%d"),
        "dte": round((expiry - now_ms) / 86_400_000, 3), "strike": strike,
        "spot": c["spot"], "fwd": c["fwd"],
        "call_bid": c["bid"], "call_ask": c["ask"], "call_mark": c["mark"],
        "put_bid": p["bid"], "put_ask": p["ask"], "put_mark": p["mark"],
        "call_bid_iv": c["bid_iv"], "call_ask_iv": c["ask_iv"], "call_mark_iv": c["mark_iv"],
        "put_bid_iv": p["bid_iv"], "put_ask_iv": p["ask_iv"], "put_mark_iv": p["mark_iv"],
        "dvol": _dvol(currency, now_ms), "funding_8h": perp.get("funding_8h"),
    }


# ── storage ───────────────────────────────────────────────────────────────

def read_rows(path: Optional[Path] = None) -> list[dict]:
    path = path or CSV_FILE
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def _append(row: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if new:
            w.writeheader()
        w.writerow({k: ("" if row.get(k) is None else row[k]) for k in COLUMNS})


def update(now: Optional[datetime] = None, path: Optional[Path] = None) -> list[str]:
    """Log today's snapshot for each currency that does not have one yet. Returns log lines."""
    path = path or CSV_FILE
    now = now or datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    done = {(r["currency"], r["date"]) for r in read_rows(path)}
    out = []
    for cur in CURRENCIES:
        if (cur, today) in done:
            continue
        try:
            row = snapshot(cur, now)
        except Exception as exc:
            logger.warning("option logger %s: %s", cur, exc)
            continue
        if row is None:
            logger.warning("option logger %s: no suitable expiry", cur)
            continue
        _append(row, path)
        out.append(f"{cur} {today} logged")
    return out


# ── analysis ──────────────────────────────────────────────────────────────

def _f(x) -> Optional[float]:
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def derive(r: dict) -> Optional[dict]:
    """Per-row comparison of real prices with the DVOL proxy."""
    cm, pm, cb, pb = _f(r["call_mark"]), _f(r["put_mark"]), _f(r["call_bid"]), _f(r["put_bid"])
    ca, pa, dte, dvol = _f(r["call_ask"]), _f(r["put_ask"]), _f(r["dte"]), _f(r["dvol"])
    if None in (cm, pm, dte) or not dte:
        return None
    mid = cm + pm
    out = {"mid": mid, "proxy": 2 * atm_call_pct(dvol / 100, dte) if dvol else None,
           "bid": cb + pb if cb is not None and pb is not None else None,
           "ask": ca + pa if ca is not None and pa is not None else None}
    out["ratio"] = mid / out["proxy"] if out["proxy"] else None
    out["spread_pct"] = (out["ask"] - out["bid"]) / mid if out["bid"] is not None and out["ask"] is not None else None
    civ, piv = _f(r["call_mark_iv"]), _f(r["put_mark_iv"])
    out["atm_iv"] = (civ + piv) / 2 if civ is not None and piv is not None else None
    out["dvol_gap"] = dvol - out["atm_iv"] if dvol and out["atm_iv"] else None
    fund = _f(r["funding_8h"])
    out["funding_ann"] = fund * 3 * 365 if fund is not None else None
    return out


def _delivery(currency: str, date: str) -> Optional[float]:
    for offset in range(0, 400, 100):
        res = _get("get_delivery_prices", {"index_name": f"{currency.lower()}_usd", "offset": offset, "count": 100})
        for d in res.get("data", []):
            if d["date"] == date:
                return float(d["delivery_price"])
        if not res.get("data") or res["data"][-1]["date"] < date:
            break
    return None


def settle(r: dict, today: str) -> Optional[dict]:
    """Realised P&L (fraction of entry spot) of the naked straddle sold at the bid, once expired."""
    d = derive(r)
    if not d or d["bid"] is None or r["expiry"] >= today:
        return None
    dp = _delivery(r["currency"], r["expiry"])
    spot, k = _f(r["spot"]), _f(r["strike"])
    if dp is None or not spot or k is None:
        return None
    payoff = abs(dp - k) / spot
    return {"real": d["bid"] - payoff,
            "proxy": d["proxy"] * (1 - PROXY_COST) - payoff if d["proxy"] else None}


def report(path: Optional[Path] = None, today: Optional[str] = None) -> str:
    rows = read_rows(path)
    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not rows:
        return "📭 <b>Opcijų loggeris</b>\n\nDar nėra įrašų (pirmas įrašas pasirodys pirmo paleidimo metu)."
    lines = ["📈 <b>Opcijų loggeris</b>  <i>(realios Deribit kotiruotės vs DVOL proxy)</i>\n"]
    for cur in CURRENCIES:
        rs = [r for r in rows if r["currency"] == cur]
        if not rs:
            continue
        ds = [x for x in (derive(r) for r in rs) if x]
        last = ds[-1] if ds else None
        lines.append(f"<b>{cur}</b>  įrašų: {len(rs)} ({rs[0]['date']} → {rs[-1]['date']})")
        if last:
            lines.append(f"  paskutinis: straddle mid {last['mid']*100:.2f}% spot, proxy "
                         f"{(last['proxy'] or 0)*100:.2f}%, ATM IV {last['atm_iv'] or 0:.1f} vs DVOL "
                         f"{_f(rs[-1]['dvol']) or 0:.1f}")

        def mean(key):
            v = [x[key] for x in ds if x.get(key) is not None]
            return sum(v) / len(v) if v else None
        for label, key, scale, fmt in (("mid / proxy", "ratio", 1, "{:.3f}"),
                                       ("bid-ask / mid", "spread_pct", 100, "{:.1f}%"),
                                       ("DVOL − ATM IV (vol pt)", "dvol_gap", 1, "{:+.1f}"),
                                       ("perp funding (metinis)", "funding_ann", 100, "{:+.1f}%")):
            m = mean(key)
            if m is not None:
                lines.append(f"  vid. {label}: " + fmt.format(m * scale))
        sett = [s for s in (settle(r, today) for r in rs) if s]
        if sett:
            real = [s["real"] for s in sett]
            prox = [s["proxy"] for s in sett if s["proxy"] is not None]
            lines.append(f"  išsipildė {len(sett)}: plikas straddle (pardavimas už bid) vid. "
                         f"{sum(real)/len(real)*100:+.2f}% spot"
                         + (f" | proxy {sum(prox)/len(prox)*100:+.2f}%" if prox else ""))
        lines.append("")
    lines.append("<i>Tikslas: ar proxy per daug žada. Reikšminga ~3+ mėn. duomenų; plikas straddle nėra strategija.</i>")
    return "\n".join(lines)
