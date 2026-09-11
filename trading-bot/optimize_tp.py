"""
TP distance optimizer — replays every logged setup's actual entry and SL
against real historical 15m candles at a range of TP distances (in units
of that setup's own SL distance), to see which target actually pays off.

Doesn't change the strategy or touch trade_log.csv. Answers one question
with data instead of a guess: given the entries and stops the bot already
produced, what TP distance would have maximized win rate / expectancy?

Every row's SL stays exactly as logged — only the TP distance is varied,
so this isolates the TP question from the entry/SL question. A row's
entry_type still matters (HOOK's SL is OB-based and wider than the old
TTE's signal-bar SL), but since results are expressed as an R-multiple of
that row's own SL distance, comparisons across types stay scale-fair.

Fetches deep enough history per pair to replay every logged row from its
own timestamp forward, not just the last 48h get_ohlcv() would give a
fresh scan — trade_log.csv can hold weeks of rows by now.

Usage (on the Mac where the bot runs):
    cd /Users/tomassipelis/trading-bot
    .venv/bin/python optimize_tp.py
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from analysis.market_data import get_ohlcv
from analysis.trade_logger import MAX_OUTCOME_CANDLES, _read_rows, simulate_walk
from config import STRATEGY_EPOCH

# Expressed as a multiple of the row's own (already logged) SL distance.
R_MULTIPLES = [0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 7.0]

# 15m candles/hour; capped so one very old row can't trigger a runaway fetch
CANDLES_PER_HOUR = 4
MAX_CANDLES_FETCH = 4000  # ~6 weeks of 15m data


def _load_usable_rows(all_time: bool = False) -> list[dict]:
    """
    Logged setups with usable entry/SL, restricted to the current strategy
    epoch unless all_time is set.

    Epoch filtering matters more here than anywhere else: rows either side
    of a bump came from different gating rules AND (after 2026-09-08) a
    pair universe that turned over by more than half. Averaging across
    that boundary produces a number describing no configuration that ever
    actually ran — which is exactly how a replay can report a healthy
    win rate for a setup that is losing live.
    """
    rows = _read_rows()
    epoch = datetime.fromisoformat(STRATEGY_EPOCH)

    usable, excluded_old = [], 0
    for r in rows:
        if not (r.get("entry") and r.get("sl") and r.get("logged_at") and r.get("bias")):
            continue
        try:
            entry, sl = float(r["entry"]), float(r["sl"])
            logged_at = datetime.fromisoformat(r["logged_at"])
        except ValueError:
            continue
        if entry == sl:
            continue
        if not all_time and logged_at < epoch:
            excluded_old += 1
            continue
        usable.append(r)

    if excluded_old:
        print(f"({excluded_old} setup'ų iš senesnių strategijos versijų neįskaičiuota — "
              f"pilnai istorijai: --all)")
    return usable


def _fetch_pair_data(rows: list[dict]) -> dict:
    """
    Fetches enough 15m history per pair to cover every row's logged_at up
    to now — a plain get_ohlcv() call only returns the last ~48h, which
    would silently truncate replay for anything logged further back.
    """
    now = datetime.now(timezone.utc)
    by_pair: dict[str, list[dict]] = {}
    for r in rows:
        by_pair.setdefault(r["pair"], []).append(r)

    df_by_pair = {}
    for pair, pair_rows in by_pair.items():
        earliest = min(datetime.fromisoformat(r["logged_at"]) for r in pair_rows)
        hours_needed = (now - earliest).total_seconds() / 3600 + 2  # +2h buffer
        limit = min(int(hours_needed * CANDLES_PER_HOUR) + 10, MAX_CANDLES_FETCH)
        try:
            df_by_pair[pair] = get_ohlcv(pair, "15m", limit=limit)
        except Exception as exc:
            print(f"  ⚠️  {pair}: fetch failed ({exc}) — {len(pair_rows)} rows skipped")
    return df_by_pair


def _summarize(statuses: list[str], r_mult: float) -> dict:
    n = len(statuses)
    wins   = statuses.count("tp1_hit")
    losses = statuses.count("sl_hit")
    win_rate  = wins / n * 100 if n else None
    baseline  = 1 / (1 + r_mult) * 100
    edge      = (win_rate - baseline) if win_rate is not None else None
    expectancy = (wins * r_mult - losses) / n if n else None
    return {
        "n": n, "wins": wins, "losses": losses, "expired": n - wins - losses,
        "win_rate": win_rate, "baseline": baseline, "edge": edge, "expectancy": expectancy,
    }


def main() -> None:
    all_time = "--all" in sys.argv

    rows = _load_usable_rows(all_time=all_time)
    if not rows:
        print("Nėra eilučių su entry/sl trade_log.csv faile dar.")
        return

    scope = "VISA istorija (maišo strategijos versijas)" if all_time else f"nuo epochos {STRATEGY_EPOCH[:10]}"
    print(f"Replaying {len(rows)} logged setups across {len(R_MULTIPLES)} TP distances  [{scope}]...")
    df_by_pair = _fetch_pair_data(rows)

    # results[R]["ALL" | entry_type] -> list of status strings
    results: dict[float, dict[str, list[str]]] = {r: {"ALL": []} for r in R_MULTIPLES}
    skipped_no_data = 0
    skipped_unresolved = 0

    for row in rows:
        df = df_by_pair.get(row["pair"])
        if df is None or df.empty:
            skipped_no_data += 1
            continue

        entry, sl, bias = float(row["entry"]), float(row["sl"]), row["bias"]
        sl_dist = abs(entry - sl)
        logged_at = datetime.fromisoformat(row["logged_at"])
        et = row.get("entry_type") or "?"

        for r_mult in R_MULTIPLES:
            tp = entry + r_mult * sl_dist if bias == "bullish" else entry - r_mult * sl_dist
            status, _, _ = simulate_walk(df, logged_at, bias, tp, sl, MAX_OUTCOME_CANDLES)
            if status is None:
                skipped_unresolved += 1
                continue
            results[r_mult]["ALL"].append(status)
            results[r_mult].setdefault(et, []).append(status)

    print(f"\n{'R multiple':>10} | {'n':>4} | {'TP':>4} | {'SL':>4} | {'Exp':>4} | "
          f"{'Win rate':>9} | {'Baseline':>9} | {'Edge':>7} | Expectancy")
    print("-" * 90)
    for r_mult in R_MULTIPLES:
        s = _summarize(results[r_mult]["ALL"], r_mult)
        if s["n"] == 0:
            print(f"{r_mult:>10} | no data")
            continue
        print(
            f"{r_mult:>10} | {s['n']:>4} | {s['wins']:>4} | {s['losses']:>4} | {s['expired']:>4} | "
            f"{s['win_rate']:>8.1f}% | {s['baseline']:>8.1f}% | {s['edge']:>+6.1f}p | {s['expectancy']:>+.3f}R"
        )

    print("\nPagal setup tipą:")
    entry_types = sorted({et for r_mult in R_MULTIPLES for et in results[r_mult] if et != "ALL"})
    for et in entry_types:
        print(f"\n  {et}:")
        for r_mult in R_MULTIPLES:
            s = _summarize(results[r_mult].get(et, []), r_mult)
            if s["n"] == 0:
                continue
            print(
                f"    R={r_mult:>4} n={s['n']:>3} win_rate={s['win_rate']:5.1f}% "
                f"(baseline {s['baseline']:4.1f}%, edge {s['edge']:+5.1f}p) expectancy={s['expectancy']:+.3f}R"
            )

    if skipped_no_data:
        print(f"\n({skipped_no_data} eilutės praleistos — nepavyko gauti duomenų porai)")
    if skipped_unresolved:
        print(
            f"({skipped_unresolved} R-bandymai praleisti — nepakanka žvakių nuo logged_at iki dabar, "
            f"kad būtų galima nustatyti rezultatą; didesni R natūraliai turi daugiau tokių)"
        )

    print(
        "\nSVARBU: SL kiekvienoje eilutėje LIEKA TOKS, koks buvo užrašytas — keičiamas tik TP "
        "atstumas. Tai atsako 'koks TP atstumas geriausias prie DABARTINIŲ entry/SL', o ne "
        "'koks geriausias entry/SL derinys'. Baseline apytiksliai (simetrinio atsitiktinio "
        "klaidžiojimo modelis) — naudok kaip apatinę ribą, ne tikslų skaičiavimą."
    )
    print(
        "\nNELYGINK R tarp skirtingų setup tipų: R matuojamas TO PATIES setup'o SL atstumu, o "
        "SL pločiai skiriasi iš esmės. TTE SL buvo signalo baro plotis (~0.2-0.5%), tad '7R' jam "
        "reiškia ~1.5-3.5% judesį. SWEEP SL yra už sweep ekstremumo (platus), tad '7R' reiškia "
        "kur kas didesnį judesį. Tas pats R skaičius = skirtingi reikalavimai rinkai. Palyginimas "
        "prasmingas TIK juostos viduje (ta pati eilutė), ne tarp eilučių."
    )


if __name__ == "__main__":
    main()
