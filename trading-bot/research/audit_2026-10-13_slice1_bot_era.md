# Audit, slice 1: the Evedex/Hyperliquid SWEEP/HOOK/ICT signal-bot era (2026-06-14 → 2026-10-09)

Read-only. Nothing in the repo was modified. The archive branch was extracted with `git archive origin/archive/sweep-bot`
into `scratchpad/s1_archive` (no worktree, so the repo's `.git` is untouched). Audit scripts are in `scratchpad/s1_scripts/`.
This report does not repeat `research/audit_2026-10-12.md`. The VP-reaction and bias+pullback studies are only mentioned
where they bear on the removal decision.

**What I verified by running:**
- The archived tests `test_limit_walk.py`, `test_market_replay.py`, `test_ict.py` and `test_ict_stats.py`: 37 passed.
- A historical replay that calls the **archived production functions** (`detect_sweep`, `find_displacement_in_range`,
  `detect_market_structure`, `_build_levels`, `_try_hook_setup`, `simulate_walk`, `simulate_limit_walk`,
  `market_entry_walk`). It runs on the cached **Evedex 15m candles of 42 instruments from 2026-04-26 to 2026-10-09**
  (`scratchpad/kralov_cache`, the same venue and period the bot ran live).
  - Decisions use closed bars only. 4H, 1H and 1D bars are resampled from the 15m bars in UTC.
  - It applies the final config: entry at the swept level, TP1 = 2%, MIN_RR 2.0, the TP1-already-hit gate, the global
    trend gate on or off, and one pending row per pair and direction.
  - HOOK was forced on with the same final TP design. HOOK covers 12 pairs and runs without the global gate.

**What I only read:** all commit bodies (`git log --all`), and the archived `trade_logger.py`, `multi_timeframe.py`,
`liquidity.py`, `smc.py`, `ross_hook.py`, `ict.py`, `optimize_tp.py`, `replay_market_entry.py`, `risk/position_sizer.py`,
`main.py`, `config.py` and `global_trend.py`. The live `logs/trade_log.csv` is not in git (it lived on the user's Mac), so
every live figure below is quoted from commit messages and could not be re-derived.

## Bottom line

- **(a) Were the edge claims ever supported?** No. Every positive live claim for SWEEP was a measurement artifact:
  - The headline figures were 84.8% win (n=33, `2f5b550`) and "83.0% / +3.09R" (n=88, `f09fabe`).
  - Until `b397f1b` (2026-10-08) the outcome walker credited TP1 without requiring price to come back to the
    resting-limit entry.
  - My replay reproduces the effect and sizes it: 66.8% win / +2.45R with the old walker, against 19.2% win / −0.25R net
    with fills required, and 88% of the old "wins" never filled.
- **(b) Was removal justified?** Yes for SWEEP and HOOK in their final form. I found no false negative that the data
  supports.
  - The HOOK disable decision (`88a07a8`) rested on evidence biased *against* HOOK. Re-modelled correctly, HOOK is still
    negative, but on a tiny sample.
  - The removal commit's stated reason ("research/*.md showed no edge") cites studies that never tested SWEEP or HOOK.
    The right reason is the fill-corrected log plus this kind of replay.
- **(c) Concrete flaws:** ranked below.

## Replay results (verified by running)

**SWEEP, final rules: 2114 setups with the global gate, 3490 without; 42 pairs; 137–161 trading days.**

| Scoring | Win | Expectancy | Notes |
|---|---|---|---|
| A. Old no-fill walker (the /log convention before `b397f1b`) | 66.8% | **+2.45R** gross | 1/(1+R) baseline 20.5% → "edge" +46pp |
| B. Resting limit at the level (the `b397f1b` walker) | 19.2% of filled | −0.09R gross / **−0.25R net** | fill 40.5%, missed 58.8%; day-clustered t −2.78 (−4.33 without the gate) |
| C. Market entry at next open, TP 2%, RR≥2 | 23.1% | −0.08R gross / −0.19R net | 1711 of 2114 rows fail the RR≥2 gate at market price |
| C'. Market entry, TP = 2R, all rows | 27.2% | −0.13R gross / −0.18R net | t −2.08 net (−3.58 without the gate); both halves negative |

Fees in this table are 0.08% round trip, as in `replay_market_entry.py`. Majors alone (BTC, ETH, HYPE, SOL, XRP, BNB) are
+0.02R gross, n=269, which is no edge before fees.

The "smoking gun" is a quartile split by how far price had already left the entry when the setup was logged:

| Distance from price to entry | Old-walker win | Fill rate |
|---|---|---|
| ≤0.6% | 32% | 84% |
| 0.6–1.35% | 57% | 53% |
| 1.35–1.78% | 83% | 20% |
| 1.78–2.0% | **96%** | **6%** |

The old win rate is almost entirely a function of how little move was left to TP1 from the *current* price.

**HOOK (forced on, final TP design): 66 setups.**
- Old walker: 15.2% win, −0.39R.
- **41% of the old walker's SL losses were trades whose stop entry never triggered.**
- With a correct stop entry: n=46, 21.7% win, −0.13R gross / −0.22R net.
- Market entry: −0.36R net, t −2.25.

The live `/log` after the fix reported fill 54.9% and win 14.3%. The only small-n combination consistent with both figures
is 28 filled out of 51 decided setups with 4 wins (56/102 with 8 wins also fits). For 4/28 the Wilson 95% CI is 6–31%,
against a ~29% breakeven at R:R ≈2.45. That is directionally the same as the replay, but on its own it is about a week of
data with p ≈ 0.06.

## Findings, ranked by severity

1. **The outcome walker ignored entry fills (`trade_logger.py:206-255`, `simulate_walk`; production until `b397f1b`).**
   - **Severity: critical. Bias: strongly optimistic for SWEEP, about +48pp win rate and +2.7R per trade in the replay.**
   - SWEEP's entry is a resting limit at the swept level (`multi_timeframe.py:377-378`), which sits *behind* price once
     the displacement has gone. A TP1 at entry ×1.02 was scored as a win the moment it traded, even if the limit never
     filled.
   - The loss side was real, because reaching the SL requires passing through the entry. That makes the bias one-sided.
   - Every positive SWEEP number in the commit history comes from this walker:
     - `2f5b550`: 84.8%, n=33.
     - `f09fabe`: 83.0% / +3.09R.
     - `8e0bdbb` and `optimize_tp.py`: +0.80R at 2%.
   - Under H0 = 29% (the breakeven win rate), 28/33 has p ≈ 4e-11. That is not plausible as real skill.
   - The developer's own fix commit (`b397f1b`) and docstring (`trade_logger.py:268-276`) describe the mechanism
     correctly; the remaining problem is that the earlier tuning was never revisited.

2. **Sequential tuning optimised the artifact (forking paths on contaminated data).**
   - **Severity: high. Bias: optimistic, and it explains the low live fill rate.**
   - Each September rule change moved the entry *further* from price, which is exactly what inflates the no-fill
     walker:
     - `8e0bdbb` set TP1 to a fixed 2%, chosen from `optimize_tp.py`'s no-fill replay of 12 variants on 23 trades.
       Near targets benefit most from the artifact.
     - `1bb64ac` widened the sweep window from 12 to 24 bars. Older sweeps mean price has drifted further from the level.
     - `f09fabe` switched to entry at the level instead of the FVG edge, on 83%/+3.09R vs 71%/+1.25R. The level is deeper
       than the FVG edge, so the gap is the artifact's size, not a better filter.
   - The result: live fill fell to 54.9%, and to 40% in the replay.
   - Before that, about 20 gate and parameter changes between June and September were each chosen on in-sample live
     samples of 10–100 trades. Examples:
     - The OB gate was optional, then hard (`c472d99` → `7c84dde`, 17.6% vs 5.0% with no test). The later `bb60284`
       shows a comparable split on SWEEP data was p ≈ 0.6.
     - TP1 freshness was gated, then ungated.
     - MIN_RR went 3 → 1.5 → 3 → 2.
     - The TTE SL was set at 0.25×ATR, then 0.75×ATR, then structural, then TTE was disabled.

3. **HOOK and TTE (stop-entry setups) were scored with the same no-fill walker. HOOK was disabled on biased evidence
   (`88a07a8`).**
   - **Severity: medium. Bias: pessimistic for HOOK.**
   - HOOK's entry is the hook swing high (`multi_timeframe.py:282`, `ross_hook.py:167-170`), which sits above price. In
     Ross's method that is a buy stop.
   - The walker counted SL touches even when the stop entry never triggered: 41% of losses in the replay.
   - The "−7.2pp / −18.1pp / −17.6pp edge" and "p = 0.085" figures are therefore biased against HOOK.
   - After `b397f1b`, `simulate_limit_walk` would treat a HOOK entry as a *limit*, which is mis-specified in the other
     direction. HOOK was already disabled by then, so this did not matter in practice.
   - **No false negative results, though:** correctly modelled, HOOK is still −0.13R gross (n=46). Its rejection stands,
     with low power.
   - TTE was disabled (`7c84dde`) by comparing a raw 7% win rate against 37.5% across very different R:R values.
     `0e84460` later found that TTE cleared its own baseline by *more* than HOOK did. That rejection was mis-specified, but
     there is no evidence TTE worked either.

4. **The random-walk baseline is mis-specified (`trade_logger.py:505-522`, `optimize_tp.py:114`).**
   - **Severity: medium. Bias: depends on the setup.**
   - The baseline 1/(1+R) is the no-time-limit probability for a path that starts *at the entry*. The win rate it is
     compared with:
     - counts expired trades as non-wins, which biases measured "edge" negative for far targets (HOOK and the old
       swing-TP SWEEP; 10.6% of HOOK rows expired in the replay);
     - comes from paths that start at the current price, not the entry. That inflates SWEEP's "edge" (+46pp in the
       replay) and deflates HOOK's.

5. **No costs anywhere in the live statistics (`trade_logger.py:525-548`).**
   - **Severity: medium. Bias: optimistic, about 0.1–0.2R per trade.**
   - Expectancy has no fees, no slippage and no funding. Funding was recorded (`a608095`) but never subtracted.
   - With a median SL of ~0.5–0.8%, 0.08% round-trip fees cost 0.1–0.16R per trade. Stop slippage on the thin alts that
     made up most rows (87% non-major in the replay) is extra.
   - Expired trades are counted as 0R rather than marked to market.

6. **The fill model after the fix is still mildly optimistic (`trade_logger.py:311-329`).**
   - **Severity: low. Bias: optimistic, which makes the negative verdict conservative.**
   - Touch counts as a fill, with no queue or trade-through requirement. That over-fills exactly the touch-and-reverse
     winners.
   - A candle that touches both TP and SL after the fill credits TP (line 328).
   - Not crediting TP on the fill candle is pessimistic, but rarely binds with a 2% target.

7. **Restatement was partial (`trade_logger.py:346-430`).**
   - **Severity: low to medium. Bias: optimistic in `/log all` and in historical quotes.**
   - `restate_fills` only re-walks rows that are both in the current epoch and less than 30 days old.
   - The 109 pre-epoch SWEEP rows behind the 83%/+3.09R claim, and all HOOK and TTE rows, keep their no-fill statuses.
     `/log all` stays inflated.

8. **The removal rationale cites the wrong evidence (`9b50d10`).**
   - **Severity: low as a process issue.**
   - The commit says "research/*.md showed no edge after costs". None of the preregistered studies in `research/` test
     SWEEP or HOOK: VP reactions, daily factors, multi-cycle and bias+pullback all test other rules.
   - The actual SWEEP evidence was the fill-corrected `/log` (n≈28) and `replay_market_entry.py` output, which was never
     committed. The decision is right; the record should say why.
   - Note also that `replay_market_entry.py` (the market-entry test) was well specified: next-bar open, SL first on
     same-bar touches, fees, a fixed-R variant to avoid RR-gate selection. My replay with its function agrees: negative.

9. **Look-ahead in swing/pivot detection does not affect the live log.**
   - **Severity: low. Bias: none on the live data.**
   - `find_swing_highs/lows` (`smc.py:81-96`) need `window` bars on the right, so live detection is lagged, not
     look-ahead.
   - `_detect_bos_events` registers a swing at its own index before it is confirmed (`smc.py:157-163`). That is a
     historical anachronism inside the structure sequence. It cannot leak future data into a live decision, because the
     data ends at "now". It would matter only in a bar-by-bar backtest that recomputes swings on a full series, which no
     bot-era result did. My replay recomputes on truncated windows only.
   - The live scanner *does* use the forming 15m candle (`market_data.py:194-204`; no drop of the last bar). Sweeps,
     reclaims and the TP1-hit gate can therefore fire on a non-final close (repaint). That adds noise, not a directional
     bias in the stats, because outcomes are walked only on bars after `logged_at`.

10. **Resolver window edge case (`trade_logger.py:440-456`).**
    - **Severity: low. Bias: mixed.**
    - Pending rows are resolved against `get_ohlcv` (200 bars, about 50h). If the Mac or the bot was down long enough
      that `logged_at` falls before the window, the walk silently starts at the window start, not at `logged_at`. It then
      skips the early bars where a fill or SL may have happened.

11. **Selection in what got logged (`main.py:147-160`).**
    - **Severity: low. Bias: unclear.**
    - Only the single highest-R:R setup per scan was logged, which means the tightest stop. Dedup by pair and direction
      (`trade_logger.py:125-143`) also applies.
    - The log is therefore a selected subsample, not "the strategy". Survivorship of pairs is not an issue live, because
      the universe was point in time.
    - My replay universe is the 42 instruments still listed in October 2026 (3 empty files), which is minor over 5.5
      months.

12. **Risk module (`config.py:100,125`; `risk/position_sizer.py:93-96`).**
    - **Severity: high for capital, not for the research.**
    - Risk was 30% of balance per trade, with leverage up to 20×. At the true ~15–20% win rate this sizing means near-
      certain ruin within a handful of trades.
    - Raising the leverage cap (`3bba06f`) to make tight-stop alerts "affordable" made it worse.

13. **The ICT, VP and wall factors were never evaluated, so they are neither supported nor rejected.**
    - MSS, OTE, session and killzone (`a608095`/`bb60284`), POC/VAH/VAL (`331f234`) and liquidity walls (`88a07a8`) were
      recorded only. `/ict` reported no resolved rows before removal.
    - The pooled z-test and n<20 flags in `ict_stats.py` were sound design.
    - Rejecting them is not a false negative: there was no evidence either way, and the base setup they would filter
      is negative by about 0.25R.

## What would change decisions

- Nothing argues for reviving SWEEP or HOOK in their archived form. The fill-corrected, cost-inclusive replay over the
  bot's own venue and period is negative under limit entry, market entry and stop entry.
- Retract the historical claims "84.8%", "83.0% / +3.09R", "TP 2% band +0.62..+0.80R", "SWEEP above breakeven", and the
  FVG-vs-level and window-24 conclusions. All of them are no-fill artifacts.
- If any sweep idea is revisited, it needs a new preregistered, bar-by-bar backtest with:
  - fills required, and a trade-through rather than a touch;
  - SL first on same-bar touches;
  - fees and funding;
  - day-clustered t;
  - a hold-out.
  The replay here suggests the reversal at a reclaimed prior-day level has *negative* gross drift on alts (C': −0.10 to
  −0.13R gross). That is descriptive only; I did not test fading it, which would be a new hypothesis.
