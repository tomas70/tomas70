# Audit, slice 2: the middle research studies (VP reactions, daily factors / Study A, bias+pullback, Binance data)

Read-only. Nothing in `/home/user/tomas70/trading-bot` was modified (`git status` clean). My scripts and outputs are in
`scratchpad/a2/`. Areas already covered in `research/audit_2026-10-12.md` (T50 / Study B, VRP, Reddit, kralov) are not redone here.

## What I verified by running, and what I only read

**Verified by running (scratchpad venv, original caches, study code unmodified):**
- `research_vp_reactions.py` from `archive/sweep-bot`. The archived copy is identical to commit `9cf785f`. Run on `vp_cache` (52 pairs).
  It **reproduces `vp_run.log` (2026-10-08) byte for byte**: 0/6 pass.
- `research_daily_factors.py` on `daily_cache` (30 coins). T50 passes, and so does nothing else. MOM14 gets t = 2.58, which is below the 2.7 bar. These numbers match
  the earlier audit and the original paper-tracker universe exactly.
- `research_multi_cycle.py` on `binance_cache` (687 symbols). Identical to `multi_cycle_out.txt`. Study A passes 0/8.
- `research_bias_pullback.py` on `binance_1h_cache`. Identical to `bias_pullback_out.txt`. 0/4 pass.
- The tests for this slice pass: test_daily_factors, test_multi_cycle and test_bias_pullback give 16 passed; archived test_vp_research gives 5 passed.
- My own checks:
  - Evedex array order, confirmed with a live request: `[ts, open, close, high, low, volUsd, volBase]`.
  - Data quality of the Binance panel (gaps, redenominations, stablecoins).
  - A truncation look-ahead test of the bias features.
  - Re-running Study A with problem coins excluded, at zero cost, and with gross returns per period.
  - Power and MDE for every study.
  - Splitting the VP results into crypto-only events and wide-stop events.

**Only read:** the four preregistrations, `research_binance_data.py` (its cache was used, not re-downloaded), `compute_volume_profile`,
`get_liquid_pairs`, and the commit history. No document in my slice contains SWEEP or market-entry replay numbers. The only reference is
the claim in `9b50d10` / `main.py:4-6` that "the research record showed no edge". The research/*.md files hold no SWEEP results,
so that claim rests on the replay work in the other auditor's slice.

## (a) Do the verdicts follow from the preregistrations? Yes, all of them

| Study | Registered rule | Executed result | Verdict follows? |
|---|---|---|---|
| VP reactions (6 tests) | day-clustered t>2.6, both halves >0, ≥100 events/≥40 days | t from −2.17 to −6.55, all 6 have net<0 → 0/6 | Yes |
| Daily factors, study 2 (7 tests) | t>2.7, both halves>0, Sharpe>1, ≥40 periods | best is MOM14: t 2.58 (Sharpe 2.07) → 0/7 | Yes. **But see finding 2** |
| Multi-cycle Study A (4 lookbacks × direction = 8) | t>2.7, Sharpe>0.8, ≥2/3 periods and P3 >0, n≥200 | best is MOM7: t 1.72, P3 −0.03% → 0/8 | Yes |
| Bias + pullback (4 tests) | n≥300, t>3.0, ≥2/3 periods and P3 >0, weighted Sharpe>0.8 | best is ETH-E2: t 1.12, P3 <0 → 0/4 | Yes |

Code and preregistration agree on: entry timing, SL-first rule within a candle, minimum stop, the 96-bar window, fees in R units, point-in-time
eligibility (≥90 days of history, top-30 by 30-day quote volume, ≥18 eligible coins), Sunday-to-Sunday holding, turnover cost plus 10%/yr short drag, the stop
clamp and ATR, both exits, tier sizing, and Sharpe computed on exit days.

## (b) Findings, ranked by severity

1. **MEDIUM (interpretation / false negative): Study A tests a costly implementation. It does not show that alt momentum is absent.**
   - The test is underpowered. Weekly net SD is 11–12%, n = 401. Passing t>2.7 needs a net annual Sharpe above 0.97. **Detecting a
     true effect with 80% power needs a net Sharpe of 1.28.**
   - The observed gross effect for MOM7 is real-ish: gross 1.44%/week, **t 2.63, gross Sharpe 0.95**. Power at that effect size is about 47%.
   - Costs eat most of it: turnover is 3.09 per week × 0.10% per side, plus a 0.192%/week short drag, giving **0.50%/week, about 26%/yr**. That takes net t down to 1.72.
   - The gross effect is fading over time. By period: 2019–20 gives 1.67% (t 1.90), 2021–22 gives 3.05% (t 1.95), 2023–26 gives only **0.47% (t 0.79)**.
   - The ten best weeks carry 103% of MOM7's net P&L (68% of gross).
   - Verdict: rejecting this as a tradable sleeve at these costs is justified. "No momentum" is not. The longer lookbacks (14/28/56) are weak even gross (t 1.68 / 1.65 / −0.15).

2. **MEDIUM (protocol): MOM14 failed the daily-factors preregistration but was paper-tracked as a "survivor". That study was also too small to detect much.**
   - MOM14 scored t 2.58 against a 2.7 bar, and the script prints `ne` (fail). Yet commit `57592bb` (`analysis/paper_tracker.py` docstring) calls MOM14 one of
     "the two candidates that survived the daily-factor study".
   - With 81 weeks of data, passing t>2.7 needs an annual Sharpe of at least 2.16. **The MDE at 80% power is Sharpe ≈ 2.8**, so the study could neither confirm nor refute anything.
     The preregistration says this itself.
   - The universe is today's liquid Evedex pairs that were listed by 2025-03 (`research_daily_factors.py:186-193`), which is survivorship plus current-liquidity selection.
   - On Binance's point-in-time universe over the same window, MOM14 makes 1.05%/week with t 1.11, against 2.42% with t 2.58 on Evedex. The near-pass looks
     universe- and cost-driven (0.04% per side, no funding).
   - No harm done in the end: the multi-cycle study was run and MOM14 was removed (`d8b6098`). The labelling was still wrong.

3. **LOW–MEDIUM (bug, universe construction): stablecoins and 4-character leveraged tokens get through `tradable_universe`**
   (`research_binance_data.py:25-40`).
   - The `STABLES` list misses `U`, `RLUSD`, `USDS` (2026), `USDSOLD`, `USDSB`, `BKRW` and `KGST`. Their annual volatility is between 0.3% and 8%.
   - `len(base_asset) > 4` on line 38 lets `BULL` and `BEAR` through. These are 3x tokens; BULL fell 84% on 2020-03-12.
   - U, RLUSD and USDSOLD were in the top-30 universe for 23–25 weeks each and entered MOM7 baskets up to 12 times.
   - Excluding them moves MOM7 net t from 1.72 to 1.82 and MOM14 from 0.92 to 1.01. **No verdict changes.**

4. **LOW (bug, corporate actions): redenominations and relistings are treated as price moves** (`research_multi_cycle.py:37-49, 86`).
   - There are 14 gaps longer than 3 days where price jumps by a ratio, for example VEN ×5e-5, QUICK 1000:1, SUN, BNX, COCOS ×1295, DREP ×108, and the LUNA relisting ×177 000.
   - I checked the effect. No forward holding window of a held coin spans a gap, so P&L is never corrupted. Only rankings are: 0 cases for L = 7/14,
     3 for L = 28 and 8 for L = 56 (LUNA and BNX shorted on a fake −92% to −99% lookback).
   - No verdict impact. Coins are keyed by base asset with no gap handling, so this would bite a future study.

5. **LOW (preregistration deviation): the VP universe was not crypto-only.**
   - The preregistration says "tradable crypto perps". `get_liquid_pairs` (commit `7201fc4`) also returned CL, SPY, TSLA, COIN, MSTR, CRCL, EUR, USDJPY, XAG,
     XAUT, ANTHROPIC, OPENAI and SPCX.
   - These account for 8.6–13.7% of events. CL has 20% zero-volume bars, which makes its volume profile meaningless.
   - Crypto-only results give the same conclusion: net t from −1.83 to −7.09.

6. **LOW (robustness): the "today" filter uses run time while the caches are stale, so a partial last candle is kept on re-runs**
   (`research_multi_cycle.py:39-44`, `research_bias_pullback.py:215-218`). The VP and daily fetchers never drop the running candle at all
   (`research_vp_reactions.py:65-71` in archive, `research_daily_factors.py:42-51`). For example, the BTC 1h cache ends with the 11:00 bar fetched at about 11:14.
   The effect is negligible: outputs are identical to the originals.

7. **LOW: in bias+pullback, trades entered from 2017-08 to 2017-12 are counted in n and t but in none of P1/P2/P3**
   (`research_bias_pullback.py:197-207`). That is 41, 13, 41 and 30 trades across the four tests. Negligible.

8. **INFO (inference):**
   - VP's day-clustered t ignores cross-day autocorrelation. Day-mean lag-1 autocorrelation is up to 0.18 (VAL_REJ/POC), which would shrink |t| by about 15%. Irrelevant, since every t is negative.
   - Study A and bias+pullback use non-overlapping weekly periods or one position at a time, so plain t-tests are appropriate. No Newey-West or bootstrap was needed or used.
   - Bias features passed a 25-point truncation test with no look-ahead (0 mismatches). The 4H bar is used only after it closes, and the 1d bar after day end.

9. **INFO (cost model):**
   - VP fee is 0.08% round-trip with no slippage. The median fee is 0.16R (median stop 0.50%), which errs toward passing.
   - Study A's 0.10% per side plus 10%/yr short drag is reasonable for top-30 alts. It ignores that most 2019–20 alts were hard to short on Binance (also toward passing).
   - Neither assumption threatens a negative verdict.
   - Evedex volume (`c[6]`) is in base units. That is fine for a single-day profile; checked: BTC shows 84 coins ≈ $7M per 15m.

## (c) False negatives: power, MDE and what the rejections rule out

**VP reactions: the rejection is solid.**
- Day-level SD is 0.48–0.65R over 121–123 days, so the **MDE is 0.15–0.20R net per trade**.
- The one-sided 95% upper bound on net R is negative for all six tests: −0.03 (VAL_REJ/POC) to −0.25R (VAL_ACC). A positive net edge is excluded.
- Gross day-means sit between −0.17 and +0.055R, and none is significantly positive.
- Side note, post-hoc and not actionable: the ACC (breakout) tests are significantly negative *gross* (VAL_ACC t −3.45, VAH_ACC −2.64). So breaks out of the prior value
  area tend to revert. The mirrored gross of about 0.12–0.17R is below the 0.16R median fee.
- The sample is only about 4 months (Jun–Oct 2026), a single regime.

**Bias + pullback:**
- E1 is effectively refuted: point estimates are ≤0, and the upper 95% bounds are +0.07R (BTC) and +0.04R (ETH).
- **E2 is "not proven", not refuted.** MDE at 80% power is 0.20–0.25R per trade, while observed is +0.05 / +0.07R (power about 2–3%). The upper bounds are +0.14R (BTC) and +0.18R (ETH).
- Even so, it is not a meaningful miss:
  - The control without the trigger has the same mean (+0.03 / +0.04R), so the pullback trigger adds nothing. Whatever exists is 4H trend exposure.
  - P3 (2023→) is negative for both assets.
  - The weighted Sharpe of 0.45 is below buy-and-hold (0.82 BTC / 0.71 ETH).
- Pooling BTC and ETH for E2 (not registered) gives t 1.50.

**Daily factors (Evedex): no conclusion possible either way.** The MDE is Sharpe ≈ 2.8 (finding 2). Study A superseded it.

**Study A:** see finding 1. The MDE is a net Sharpe of 1.28. That is above what a weekly top-30 momentum sleeve plausibly earns net, so this test could only ever catch a very strong effect.
The honest summary: "weekly alt momentum gross t ≈ 2.6 over 2019–26, decaying since 2023, not tradable net of about 26%/yr turnover and short costs".
A lower-turnover construction (rank buffers, longer holding) would need a **new** preregistration on fresh data. Re-testing on this panel would be fishing.

## (d) Re-run comparison

All four studies reproduce their saved outputs exactly: `vp_run.log`, `multi_cycle_out.txt`, `bias_pullback_out.txt`, and the daily-factors numbers cited in the earlier
audit and paper tracker. No document in my slice records other numbers to compare against. Minor documentation slips:
- The daily-factors preregistration says "31 coins with BTC"; the actual universe is 30 including BTC.
- It also says "~13-test correction", while it registers 11 tests.

## Files

- `/tmp/claude-0/-home-user-tomas70/6eaacc6a-223b-5ebe-be38-bf11929b92ca/scratchpad/a2/`
  - `run_cached.py` (cache-only runner)
  - `vp_rerun.txt`, `daily_rerun.txt`, `multi_cycle_rerun.txt`, `bias_rerun.txt`
  - `data_checks.py`, `studyA_audit.py`, `studyA_gaps.py`, `studyA_stables.py`, `vp_audit.py`, `bp_audit.py`
