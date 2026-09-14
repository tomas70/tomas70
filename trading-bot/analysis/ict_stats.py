"""
Win rate and expectancy sliced by the ICT confluence factors recorded in
analysis/ict.py, plus funding.

The point of the columns added there was to make four assumptions
testable instead of believed. This module is where they get tested:

  MSS      does a real structure shift beat a bare displacement candle?
  OTE      do entries landing in the 0.62-0.79 band beat shallow/deep ones?
  Session  do killzone sweeps differ from off-hours sweeps?
  Funding  does paying carry show up in the results, or is it noise?

Two rules this module enforces rather than leaves to the reader:

  1. Blank is excluded, never counted as False. Every row logged before
     the ICT layer shipped has empty ICT columns, as does every HOOK row.
     Folding those into the "no MSS" bucket would load it with ~250 rows
     of unrelated history and manufacture a difference out of nothing.

  2. Every bucket carries a two-proportion z-test against its counterpart
     and an explicit n. A 3/8 vs 7/41 split looks like a finding and is
     not one; the p-value is there so that stays visible instead of
     depending on whoever reads the table being disciplined that day.

Standalone:
    .venv/bin/python -m analysis.ict_stats
    .venv/bin/python -m analysis.ict_stats all
"""
import logging
import math
import sys
from datetime import datetime
from typing import Callable, Optional

from .trade_logger import _baseline_win_rate, _expectancy, _read_rows, _rr_of
from config import STRATEGY_EPOCH

logger = logging.getLogger(__name__)

RESOLVED = ("tp1_hit", "sl_hit", "expired")

# Below this, a bucket is reported but labelled unreliable. Not a
# significance threshold — the z-test does that job — but a floor under
# which the z-test itself is a normal approximation of very little.
MIN_RELIABLE_N = 20


# ─── Statistics ───────────────────────────────────────────────────────────────

def _two_proportion_p(hits_a: int, n_a: int, hits_b: int, n_b: int) -> Optional[float]:
    """
    Two-sided p-value for "these two win rates come from the same
    underlying rate", by normal approximation (pooled z-test).

    Returns None when either bucket is empty or the pooled rate is
    degenerate (0% or 100% everywhere), where the approximation says
    nothing. It is an approximation in both directions: with n under ~20
    per side it is optimistic, so read it alongside n, not instead of it.
    """
    if n_a == 0 or n_b == 0:
        return None

    p_pool = (hits_a + hits_b) / (n_a + n_b)
    if p_pool <= 0 or p_pool >= 1:
        return None

    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n_a + 1 / n_b))
    if se == 0:
        return None

    z = (hits_a / n_a - hits_b / n_b) / se
    # Two-sided tail of the standard normal
    return round(math.erfc(abs(z) / math.sqrt(2)), 4)


def _summarize(subset: list[dict]) -> dict:
    """Same shape as trade_logger.get_stats' summarize, plus raw hit count."""
    n = len(subset)
    if n == 0:
        return {"count": 0, "hits": 0, "win_rate": None, "baseline_wr": None,
                "edge_pp": None, "expectancy": None, "reliable": False}

    hits = sum(1 for r in subset if r["status"] == "tp1_hit")
    wr   = round(hits / n * 100, 1)
    base = _baseline_win_rate(subset)

    return {
        "count":       n,
        "hits":        hits,
        "win_rate":    wr,
        "baseline_wr": base,
        "edge_pp":     round(wr - base, 1) if base is not None else None,
        "expectancy":  _expectancy(subset),
        "reliable":    n >= MIN_RELIABLE_N,
    }


def _split(rows: list[dict], buckets: dict[str, Callable[[dict], bool]]) -> dict:
    """
    Applies each predicate to `rows` and summarizes the matches. A row
    matching no predicate is simply absent — that is how blanks stay out
    (see module docstring rule 1), so predicates must test for the value
    they want rather than negate the other one.
    """
    out = {name: _summarize([r for r in rows if pred(r)]) for name, pred in buckets.items()}

    # Pairwise comparison, but only for a genuine two-bucket factor —
    # a p-value between "london" and "ny" out of four sessions would be
    # cherry-picked by construction.
    names = [n for n in out if out[n]["count"] > 0]
    if len(names) == 2:
        a, b = (out[n] for n in names)
        out["p_value"] = _two_proportion_p(a["hits"], a["count"], b["hits"], b["count"])

    return out


# ─── Row predicates ───────────────────────────────────────────────────────────

def _f(row: dict, key: str) -> Optional[float]:
    try:
        v = row.get(key, "")
        return float(v) if v not in ("", None) else None
    except (TypeError, ValueError):
        return None


def _carry(row: dict) -> Optional[float]:
    return _f(row, "funding_carry_pct_48h")


# ─── Public API ───────────────────────────────────────────────────────────────

def get_ict_stats(all_time: bool = False) -> dict:
    """
    Every ICT slice over resolved setups.

    Scope follows /log: by default only rows at/after STRATEGY_EPOCH, so
    the table describes one rule-set rather than a blend. `scored` counts
    rows that actually carry ICT data — it will be 0 until setups logged
    after the ICT layer shipped start resolving, and a table of zeros
    means "not measured yet", not "no effect".
    """
    rows = _read_rows()

    if not all_time:
        epoch = datetime.fromisoformat(STRATEGY_EPOCH)
        rows = [r for r in rows if datetime.fromisoformat(r["logged_at"]) >= epoch]

    resolved = [r for r in rows if r["status"] in RESOLVED]
    # Only SWEEP rows logged after the ICT layer shipped carry these.
    scored = [r for r in resolved if r.get("session")]

    return {
        "all_time":  all_time,
        "epoch":     STRATEGY_EPOCH,
        "resolved":  len(resolved),
        "scored":    len(scored),
        "pending_scored": len([
            r for r in rows if r["status"] == "pending" and r.get("session")
        ]),
        "overall":   _summarize(scored),

        "by_mss": _split(scored, {
            "su MSS":  lambda r: str(r.get("mss_confirmed")) == "True",
            "be MSS":  lambda r: str(r.get("mss_confirmed")) == "False",
        }),

        "by_ote": _split(scored, {
            "OTE 0.62-0.79": lambda r: str(r.get("in_ote")) == "True",
            "už OTE":        lambda r: str(r.get("in_ote")) == "False",
        }),

        # Finer than the in_ote flag: if the band matters, the shallow
        # bucket should be visibly worse, not just different.
        "by_retracement": {
            "seklu <0.4":   _summarize([r for r in scored if (_f(r, "ote_retracement") or 9) < 0.4]),
            "0.4-0.62":     _summarize([r for r in scored if 0.4 <= (_f(r, "ote_retracement") or 9) < 0.62]),
            "OTE 0.62-0.79": _summarize([r for r in scored if 0.62 <= (_f(r, "ote_retracement") or 9) <= 0.79]),
            "gilu >0.79":   _summarize([r for r in scored if 0.79 < (_f(r, "ote_retracement") or 9) < 9]),
        },

        "by_session": {
            name: _summarize([r for r in scored if r.get("session") == name])
            for name in ("asia", "london", "ny", "off")
        },

        "by_killzone": _split(scored, {
            "killzone":    lambda r: bool(r.get("killzone")),
            "ne killzone": lambda r: r.get("session") and not r.get("killzone"),
        }),

        "by_funding": _split(scored, {
            "gauni carry": lambda r: (_carry(r) or 0) > 0,
            "moki carry":  lambda r: (_carry(r) or 0) < 0,
        }),
    }


# ─── Formatting ───────────────────────────────────────────────────────────────

def _fmt_bucket(label: str, info: dict) -> str:
    if not info["count"]:
        return f"  {label}: —"

    wr   = f"{info['win_rate']:.1f}%"
    exp  = f"{info['expectancy']:+.2f}R" if info["expectancy"] is not None else "—"
    edge = f"{info['edge_pp']:+.1f}pp" if info["edge_pp"] is not None else "—"
    warn = "" if info["reliable"] else " ⚠️"

    return f"  {label}: n={info['count']} → {wr} ({edge}), {exp}{warn}"


def _fmt_group(title: str, group: dict) -> list[str]:
    lines = [f"<b>{title}</b>"]
    for label, info in group.items():
        if label == "p_value":
            continue
        lines.append(_fmt_bucket(label, info))

    p = group.get("p_value")
    if p is not None:
        verdict = "reikšminga" if p < 0.05 else "NEreikšminga — skirtumas telpa į triukšmą"
        lines.append(f"  <i>p={p:.3f} — {verdict}</i>")

    return lines


def format_ict_stats(all_time: bool = False) -> str:
    """Telegram-ready HTML block. Safe to call with an empty log."""
    s = get_ict_stats(all_time=all_time)

    if s["scored"] == 0:
        return (
            "🔬 <b>ICT statistika</b>\n\n"
            f"Išspręstų setup'ų: {s['resolved']}, bet nė vienas neturi ICT duomenų.\n"
            f"Laukia su ICT duomenimis: {s['pending_scored']}\n\n"
            "<i>ICT stulpeliai pildomi tik nuo šio sluoksnio įdiegimo ir tik "
            "SWEEP setup'uose. Palauk, kol jie išsispręs.</i>"
        )

    scope = (
        "visa istorija" if s["all_time"]
        else f"nuo {s['epoch'][:10]}"
    )

    lines = [
        "🔬 <b>ICT statistika</b>",
        f"<i>{scope} | išspręsta {s['resolved']}, su ICT duomenimis {s['scored']}</i>\n",
        _fmt_bucket("Bendrai", s["overall"]).strip(),
        "",
    ]

    for title, key in [
        ("MSS", "by_mss"),
        ("Fibo OTE", "by_ote"),
        ("Retracement juostos", "by_retracement"),
        ("Sesija", "by_session"),
        ("Killzone", "by_killzone"),
        ("Funding", "by_funding"),
    ]:
        lines += _fmt_group(title, s[key]) + [""]

    lines.append(
        f"<i>⚠️ = mažiau nei {MIN_RELIABLE_N} setup'ų, skaičius nieko neįrodo. "
        "edge = win rate minus atsitiktinumo riba prie to paties R:R.</i>"
    )

    return "\n".join(lines)


if __name__ == "__main__":
    import re
    text = format_ict_stats(all_time=len(sys.argv) > 1 and sys.argv[1] == "all")
    print(re.sub(r"</?[bi]>", "", text))
