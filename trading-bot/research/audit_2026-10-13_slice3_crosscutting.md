# Audit slice 3: cross-cutting statistics for the whole project (2026-10-13)

This audit was read-only and nothing in the repo was modified. It builds on `research/audit_2026-10-12.md`. My scripts are in
`scratchpad/s3/` (`t50_deflate.py`, `hedged_power.py`, `deribit_term.py`).

**What I verified by running:**
- `s3/t50_deflate.py`: T50 reproduction, start-date sensitivity, reality check, alpha t-statistic, deflated Sharpe ratio (DSR), Bonferroni, regime split.
- `s3/hedged_power.py`: the repo's own `research_options_hedged.py` on the cached Deribit DVOL and Binance data. Covers ETH power, the BTC−ETH difference and the size of the DVOL proxy gap.
- `s3/deribit_term.py`: a live Deribit snapshot of ATM IV against DVOL.
- The archived Evedex daily-factors study, run from cache via `run_vp_daily.py daily`.
- The earlier `audit_forking.py`.
- Cache file modification times, used to check that the prereg came before the data.

**What I only read:** every `research/*.md` prereg, all 135 commit subjects plus the bodies of the June–October bot commits, the `origin/archive/sweep-bot` tree, the result files, and the bias-pullback, multi-cycle and VP outputs already in the scratchpad.

---

## Bottom line (ranked)

1. **There is one weak, real-looking edge: BTC trend filtering (T50 and its family).** It works as crash avoidance, not as alpha you can lean on. After correcting for forking paths, I put the probability that T50 beats buy-and-hold forward at **roughly 0.65–0.85**.
   - Plausible forward Sharpe gain over buy-and-hold is **0.2–0.35**, not 0.46.
   - 84% of the total log outperformance comes from 3 months (2018-03, 2018-11, 2022-06).
   - It **does not survive** a project-wide Holm correction: p 0.0022 against a threshold of 0.00106 at N=47.
   - It **does survive** Bonferroni for an effective N of up to about 20.
2. **The hedged BTC short straddle (A) is the second candidate. It is unconfirmed, and its evidence is weaker than its t-statistic of 4.77 suggests.**
   - The BTC leg had already been computed by the audit before the prereg.
   - The ETH confirmation failure is **informative, not a power problem**: power was 0.93 if ETH had BTC's effect, and the BTC−ETH gap has a t of 2.57.
   - The premium is decaying: ETH since 2023 is +0.18% (t 0.43).
   - Part of it is a proxy artefact: DVOL sits above the tradable ATM IV.
   - It is not tradable until it is checked against real quotes.
3. **Nothing else in the project shows an edge.** That covers SWEEP/HOOK/TTE/ICT, POC/VA reactions, alt cross-sectional momentum and reversal, the 1h pullback, the Telegram group, the naked VRP, the ETH high-IV rule, the iron butterfly and the Reddit table. The negative verdicts are well powered for VP and kralov, where costs dominate. They are weakly powered for alt momentum and Reddit.

---

## 1. Forking-paths ledger

"Variants" means formal pass/fail tests. "Extra looks" are descriptive slices that could still have steered later choices.

| # | Study (prereg commit) | Data / instruments / window | Variants (formal) | Extra looks | Outcome | Prereg before data? |
|---|---|---|---|---|---|---|
| 0 | **SWEEP/HOOK/TTE/ICT live bot** (06-14 → 10-08, `archive/sweep-bot`) | 20 Hyperliquid perps → up to 60 → 39 Evedex → 52 incl. TradFi/pre-IPO; 15m/1h/4H; live outcomes, n≈23–250 per epoch | None registered. ≥25 outcome-driven rule changes: 3 setups; 4 TTE stop rules; MIN_RR 3→1.5→3→2; TP swing → unswept swing → fixed 2% after a **12-target replay on 23 trades**; sweep window 12→24; FVG→level entry; OB gate hard→bonus→hard; PD/freshness gates toggled; 5 epoch resets | 11 slice dimensions in `/log` and `/ict` (type, RR band, OB, tier, FVG, MSS, OTE, retracement, session, killzone, funding) | Abandoned ("no edge after costs", `9b50d10`) | **No.** Every change was decided on in-sample live results, often n<30 per branch. The commit bodies themselves note the issues: p=0.085, "neither survives Bonferroni" |
| 1 | POC/VAH/VAL reactions (`b03a537`) | 52 Evedex perps, 15m, 120 days | 6 | – | 0/6, every net t < 0 (−2.2 … −6.6) | Yes (cache 09:10:50 after commit 09:09:52) |
| 2 | Daily factors, Evedex (`4cb0cc9`) | BTC 2019-09→; 30 alts 2025-03→ | 4 trend + 7 alt = 11 | **MOM14 failed (t 2.58 < 2.7) but went onto the paper tracker 26 min later** (`57592bb`) | 1/11 (T50) | Yes (BTC_1d 09:38:02 after 09:37:17) |
| 3 | Multi-cycle, Binance (`28db608`) | BTC 2017-08→; 687 symbols, point-in-time top 30 | 4 trend + 4 two-sided cross-sectional | low-cost sensitivity, Evedex window | Trend 3/4 (T50, T100, D55; T200 fails); cross-sectional 0/4 | Binance data yes (BTCUSDT 10:16 after 10:12). **But T50 had already passed on ≈78% of the same days** (study 2) |
| 4 | 4H bias + 1h pullback (`d41610e`) | BTC, ETH 1h 2017→ | 4 | tiers A/B/C, no-trigger control, unweighted | 0/4 (best t 1.12) | Yes (1h cache 11:11 after 11:08). It is a cousin of T50, and the T50 result was known |
| 5 | Kralov Telegram signals (`ef5b0a5`) | 3,435 signals, 42 instruments, 5 months | 1 primary (3 criteria) | M1, limit fill, break-even, anti-signal, side/class/month (~10) | Fail | Candles were downloaded before the prereg (stated), returns after. The group's own claimed profits were known (they motivated the test) |
| 6 | BTC VRP + T50 filter (`d318956`) | BTC DVOL 2021-03→ | 3 (H1, H2, H3) | IV terciles, puts, costs 0/3/10% | H1 passes (premium exists); straddle and filter fail | Yes (DVOL 19:09:15 after 19:03). H3 reuses the known T50 |
| 7 | ETH high-IV (`b3836db`) | ETH DVOL | 2 | BTC point-in-time version, mid tercile, puts | Fail (t 1.71 / 2.26) | Yes (ETH DVOL 04:08:50 after 04:07:55). The hypothesis came from BTC descriptives, and the prereg says so |
| 8 | Reddit table, BTC (`de8a86f`) | BTC 1h/1d 2023-10→2026-04 + 6-month hold-out | 7 (8 rows dropped as untestable) | bootstrap PF, hold-out | S3, S11 pass the lax criteria; PF 5th percentile < 1; S3 fails the hold-out | Yes (cache 05:04 after 05:02) |
| 8b | Reddit stocks addendum (`aeabd52`) | SPY, NVDA, SAP daily 2022-10→ | 3 | **12 extra strategy×stock combinations** | S3/SPY passes, then the hold-out fails | Written **after** the BTC results (06:02 vs 05:06), so the switch of instrument is itself a fork |
| 9 | Hedged options (`884186e`) | BTC/ETH DVOL proxy | 2 structures (A, B) × 3 legs | 9-cell cost grid, 3 wing settings, T50 split | A: BTC passes, ETH fails; B is negative | **Partly.** The audit had already computed BTC hedged proxies (t 4.35). Prereg, code and results were committed within 73 seconds, so git cannot show the order |
| 10 | Audit 10-12 diagnostics | – | – | ~25: SMA 30–150 (13 lengths), fees, percentile cut-offs, windows, 30 phases, delta-hedged and variance-swap proxies | Motivated #9 | Not a prereg |
| 11 | Forward trackers | Evedex T50 paper (since 10-08); option-quote logger (since 10-13) | – | – | Too young to say anything | n/a |

**Totals.**
- **47 formal pre-registered tests** across 10 studies.
- About 70 descriptive looks.
- At least 40 adaptive configurations in the SWEEP era.

That comes to **about 150–170 looks in roughly 12–15 distinct hypothesis families**. Nearly all of them reuse **the same BTC 2017–2026 price path**: multi-cycle, pullback, VRP, Reddit and hedged all test against it. So each new BTC test adds less new information than its sample size suggests.

**Passes:**
- The trend family (T50 on Evedex; T50, T100 and D55 on Binance). These are correlated: daily-return correlations are 0.71–0.87.
- VRP existence (H1). This is not a strategy.
- Reddit S3, S11 and S3/SPY. These pass lax criteria and fail every robustness check.
- Hedged A on BTC, which then failed its confirmation.

## 2. Prereg timing integrity

- **The good part:** in every study the cache modification times show the data was fetched *after* the prereg commit (listed in the table). The repo's rule of no edits after results was respected; I found no prereg file changed after its first commit.
- **Commit timestamps cannot prove precedence.** Prereg and script commits are 38–83 seconds apart, and for hedged options results arrived 73 seconds after the prereg. The work was batched locally and committed together. Only the external data-fetch times corroborate the order, and only where data was new. For #9 (data reused) nothing does.
- **Flagged cases:**
  - (a) **Multi-cycle** (#3) confirmed T50 on data overlapping study #2 by about 78%. The only new data is 2017-10 → 2019-08: T50 Sharpe 1.43 vs buy-and-hold 0.92, **P(diff ≤ 0) = 0.20 (n=695)**. That is consistent with the earlier result but not significant on its own. The prereg's line "variants chosen before any return computed" is true only for #2.
  - (b) **Hedged options** (#9): the BTC leg was known from the audit. The prereg admits this and raised the BTC bar to t > 3. That makes ETH the only informative leg, and it failed.
  - (c) **MOM14 promotion**: a failed variant was put into a live tracker before the next study, then dropped (`d8b6098`). This was handled honestly, but it shows the pull toward near-misses.
  - (d) **ETH high-IV** (#7) and **Reddit stocks** (#8b) were both generated by looking at earlier results. Both were labelled correctly.
  - (e) **SWEEP era**: no preregistration at all. Every reported win rate is in-sample and is affected by outcome-driven epoch resets.

## 3. What survives: BTC T50 after correction

Base case (`t50_deflate.py`):
- Binance 2017-10-06 → 2026-10-07, n=3289, 0.10% per side.
- T50 Sharpe **1.288** vs buy-and-hold **0.832**, difference **0.456**. This matches the existing audit exactly.

| Correction | Result | Assumption |
|---|---|---|
| Single test, 30-day block bootstrap | P = **0.025** | **Start-date fragile.** From 2018-01: 0.044; from 2018-03: 0.057; from 2019-01: 0.067; Evedex window: 0.048. 0.025 is the most favourable start |
| White reality check, best of the 4 pre-registered trend variants (2018-03+ common window) | P = **0.16** | Bootstrap under the null, demeaned |
| Reality check, best of 20 (SMA 20…200 plus D55) | T50: P **0.21**. Best member (T120, diff 0.58): P **0.074** | "Trend filters beat buy-and-hold" as a family is borderline (~7%). T50 in particular is not |
| Alpha vs beta-matched buy-and-hold | β 0.49, α 32.6%/yr, Newey-West t **2.84** (lag 30), one-sided p 0.0022, information ratio 0.99/yr | This is a timing measure: the edge is B&H +0.317%/day when long vs −0.037% when flat (t 2.91) |
| Bonferroni on alpha p | N=4: 0.009 · N=20: **0.045** · N=50: 0.11 · N=100: 0.22 | Survives up to N≈20 |
| Holm across the 47 formal tests | Threshold 0.00106 → **fails** (only VRP-H1 and the pre-seen BTC hedged A survive) | Treats all 47 as one family, which is conservative because many hypotheses are unrelated |
| Harvey-Liu-Zhu t > 3 hurdle | 2.84 → **just fails** | |
| Deflated Sharpe on the alpha series (skew 0.83, kurtosis 11.5) | Dispersion = null noise for 9 years (σ_SR = 0.33/yr): N=20 **0.86**, N=50 **0.76**, N=100 0.67. With σ_SR = 0.5: 0.55 / 0.32 / 0.20 | Dispersion is the key assumption. With the trend family's own dispersion (0.15) it is ≥0.96, but that is wrong for N > 20 unrelated trials |
| Shrinkage (family mean 0.34, James-Stein) | Sharpe difference ≈ **0.34–0.36** (2018-03 window); minus the max-of-4 selection premium 0.08 → **~0.3** | |

**Regime concentration:**
- Log outperformance by year:
  - 2018: +0.75
  - 2021: +0.35
  - 2022: +0.34
  - 2026: +0.21
  - 2025: +0.11
  - 2019: −0.06
  - 2023: −0.06
  - 2024: −0.22
  - Total: 1.44, which matches x81.9 vs x19.4.
- The **top 3 months make up 84%** of the total and the top 6 make up 141%. The rest of the time is a net drag.
- Leaving out any single year keeps the Sharpe difference at 0.38–0.52. The volatility reduction is broad; the *return* edge is a few crash months.
- Since 2023: Sharpe 1.45 vs 1.16, P 0.19.

**Verdict.** The prior is good: trend-following on crypto is published and predates this project, and 17 of 19 SMA lengths beat buy-and-hold. That prior, not the project's own p-values, carries the case.
- Expected forward Sharpe gain is about 0.3. It mostly arrives in deep bear legs.
- It lags in bull years.
- **The paper tracker cannot validate it.** The Sharpe-difference SE is about 0.26 per 9 years, so detecting 0.3 at 2σ needs about 25 years. Detecting alpha IR ≈ 0.7 at t=2 needs about 8 years. Treat the tracker as an implementation check only.

## 4. Option-premium line (hedged straddle A)

From `hedged_power.py`; the repo's results reproduce exactly:
- BTC: +1.672%, t 4.77, SE 0.350%.
- ETH: +0.827%, t 1.70, SE 0.485%.

**Power of the ETH test (t > 2):**

| True ETH effect assumed | Power |
|---|---|
| = BTC point estimate | **0.93** |
| = BTC estimate shrunk by 1 SE (winner's curse) | 0.77 |
| Same t per observation as BTC | 0.997 |
| Half of BTC | 0.39 |

Joint power (BTC t > 3 and ETH t > 2) if both are true at the BTC estimate: 0.89.

**Why the failure is informative:**
- BTC−ETH P&L difference: +0.85%, Newey-West **t 2.57** (correlation 0.71).
- ETH's VRP is **4.5 vol points** vs BTC's **8.4**.
- So ETH's premium really is about half and cannot be told apart from zero. This is not bad luck at adequate power.
- It also does not refute BTC; an asset-specific premium is plausible.

**Problems with the BTC leg:**
- (i) It was seen before the prereg.
- (ii) It is decaying. 60% of P&L came from 2021–22. Since 2023: BTC +0.99% (t 3.04), ETH +0.18% (t 0.43).
- (iii) **Proxy bias.**
  - The live snapshot today, interpolated to 30 days in total variance, shows DVOL above ATM IV by **+1.4 vol points for BTC** (0.33% of S) and **+3.2 for ETH** (0.72% of S, which is 87% of ETH's mean).
  - Removing a constant gap of g vol points:

    | g (vol points) | BTC t | ETH t |
    |---|---|---|
    | 1 | 4.14 | – |
    | 2 | 3.50 | 0.79 |
    | 3 | **2.87** (below its own bar) | – |

  - This is one snapshot. Historical option trades are not reachable here: `history.deribit.com` is blocked by the proxy, and `www.deribit.com` returns no 2022/2024 trades.
  - Today's ATM straddle half-spread is 1.0–1.7% of premium, so the 3% cost is realistic for small size.

**Was the prereg fair?** Yes. It was transparent, used a stricter BTC bar, a lenient ETH bar (t > 2), a 30-day Newey-West lag suited to the overlap, and stress costs. Its one structural weakness: because the BTC leg was pre-seen, the "pass" could only come from ETH. For a single-asset premium it was always going to be a coin-toss confirmation. The verdict "not proven, likely smaller than the BTC proxy suggests" is correct.

## 5. Three ideas worth a new prereg (ranked)

1. **Cross-asset replication of the frozen T50 rule.**
   - Design: ETH as primary; a point-in-time equal-weight top-10 alt basket from the existing Binance 687-symbol cache as secondary; time-series filter per coin; 0.10% per side.
   - Primary statistic: alpha t vs beta-matched buy-and-hold, plus block-bootstrap Sharpe difference. Sample from 2017–18 on.
   - Why: it is the only clean, cheap confirmation of the one survivor. **ETH T50 has never been computed in the repo**, so I deliberately did not run it. The bias-pullback tiers touched the ETH daily SMA50 only indirectly.
   - Power is about 0.8 if ETH's effect matches BTC's. Commit the prereg *before* loading ETH returns.
2. **Real-quote validation of hedged straddle A, BTC only.**
   - Use the new option logger, or bought Deribit history.
   - Pre-register that the median 30-day DVOL−ATM gap must be under ~2.5 vol points and real-mark A must stay at t > 3. Also pre-register the decay test (2023+ sub-period).
   - Why: it is the largest point estimate in the project. The open question is the proxy, not the statistics. Forward-only data needs years (≈12 independent 30-day blocks per year), so buying history is the efficient path.
3. **Perp funding / basis carry: long spot, short perp when funding is positive.**
   - This has never been tested here.
   - Why: like VRP it is a structural premium rather than a prediction, it is well documented, and it is low-beta. It is complementary to T50, whose flat periods are idle cash.
   - Check data availability first (Binance futures funding history, or Evedex funding).
   - Register costs, funding sign flips and exchange risk up front.

**What not to do:**
- Re-test SMA lengths, ETH percentile thresholds, Reddit variants or kralov fill models on the same data. Each of those adds trials without adding information.
