# Market-Price Calibration Audit — Kalshi MLB

_Generated 2026-08-20 (post-PR #97, from current main). Updated 2026-08-20
(post-PR #98) to move the measurement-bug fix from an audit-script
workaround into the shared canonical research-data path
(`lib/edgelab/checkpoints.py`), per follow-up task. Author: EdgeLab
research session._

**RESEARCH ONLY.** This audit asks whether **Kalshi's own executable YES
price**, on its own, is a calibrated probability of a contract settling
YES — across the complete archived MLB market universe, whether or not our
model ever had a probability for that contract and whether or not we ever
bet it. This is deliberately a **market-calibration** audit, not a
model-validation audit (see `data/edgelab/reports/retrospective_validation_audit.md`
for that). It changes nothing in projection formulas, calibration
coefficients, confidence thresholds, bankroll rules, fee logic, or
recommendation rules. One genuine measurement bug was found (in PR #98)
and is documented below; it is now **fixed at its source**,
`lib.edgelab.checkpoints.select_closing_quote()` — the single shared
closing-quote-selection function every EdgeLab report (and production CLV
collection) depends on — rather than worked around only in this audit's
own script (see "Measurement bug found and fixed at the source").

## Methodology

- Built entirely on the existing canonical research pipeline
  (`lib.edgelab.research_dataset.build_opportunity_rows`,
  `lib.edgelab.research_reports.market_family_research`,
  `lib.edgelab.research_splits.chronological_split`,
  `lib.edgelab.research_stats`'s Brier/CI helpers, and
  `lib.edgelab.kalshi_fees`'s already-computed per-row fee-aware
  hypothetical-return fields) via one new, read-only script,
  `scripts/edgelab/run_market_price_calibration_audit.py`. As of the
  shared fix (see below), the only library change anywhere in this repo
  is a 4-line correction inside `lib/edgelab/checkpoints.py`'s existing
  `select_closing_quote()` function — the audit script itself now
  contains no measurement-bug workaround at all and relies purely on the
  canonical `isClosingQuote` field.
- **Unique-contract discipline.** The canonical opportunity dataset is
  (marketTicker x researchCheckpoint) — a single contract can appear at
  FIRST_DAILY, T-90/60/30, and CLOSING. Every headline/family/price-bucket/
  date-partition table in this report uses exactly **one row per
  contract** (the settled, priced, `isClosingQuote` row) so repeated
  snapshots of the same contract are never counted as independent
  outcomes. The "snapshot timing" table is the sole intentional
  exception: each `researchCheckpoint` bucket is already one row per
  contract by the row schema's own construction, so comparing checkpoints
  requires no dedup.
- **Price buckets.** 5-cent buckets throughout (`FINE_BUCKET_WIDTH=5`);
  the script falls back to 10-cent buckets for any cut with fewer than
  500 settled contracts, though every cut in this report cleared that bar
  at 5-cent width.
- **Tie-treatment preserved.** F3/F5/F7 `inning_result` contracts are
  never pooled across a genuine "Win" ticket and a genuine "Tie" ticket —
  they are grouped by `(marketHorizon, outcomeLabel)` explicitly, so a
  team-wins-the-first-5 contract and a first-5-ends-tied contract for the
  same game are always kept separate.
- **YES/NO orientation.** Both sides of the same contracts are calibrated
  independently against their own executable price (`executableNoPrice`,
  not `1 - executableYesPrice`), since Kalshi's real bid/ask spread means
  the two are not exact mirrors.
- **Fee-aware ROI.** Never recomputed — every ROI figure below is a
  straight mean of the row-level `hypotheticalYesReturn[FeeOnly|
  RealisticExecution]` / `hypotheticalNoReturn[...]` fields
  `research_dataset` already computes via `kalshi_fees.simulate_settlement_order`,
  aggregated exactly the way `edge_backtest()` already does
  (`sum(returns)/len(returns)`). Three tiers throughout: gross, fee-only
  (Tier B), and realistic execution (Tier C, return on actual cash
  consumed — the primary betting-performance figure).
- **Date partitioning.** `chronological_split()`'s standard 60/20/20
  DEVELOPMENT/VALIDATION/HOLDOUT split, unmodified, applied to the
  corrected dataset's game dates.
- Sample-size status throughout is `lib.edgelab.research_stats.sample_size_status`
  (n<20 → INSUFFICIENT_SAMPLE, 20≤n<100 → DESCRIPTIVE_ONLY, n≥100 →
  CALIBRATED, further flagging `gameConcentrationWarning` when raw
  contract count overstates independent-game count).

## Coverage

| Metric | Value |
|---|---|
| Unique contracts observed | 80,770 |
| Unique contracts settled | 68,674 |
| Settlement coverage | 85.02% |
| Unique contracts with a **valid** closing-quote price | 35,907 |
| Total opportunity rows (all checkpoints, post shared fix) | 109,368 |
| Settled + priced opportunity rows (all checkpoints, not unique contracts) | 120,765 (`_settled_priced()` count; independent of `isClosingQuote`) |
| Independent games in the primary dataset | 136 |
| Date range | 2026-08-01 to 2026-08-20 (20 raw dates; 11 dates carry a contract with a valid closing quote and contribute to the primary dataset) |
| Sanity check: closing-quote rows with unresolved `minutesToStart` | **0** (`coverage.closingQuoteTimingSanityCheck`, confirms the shared fix took effect) |

Settlement itself is implemented and exercised for **every** family in
this audit, including all five hitter-prop families and both pitcher-prop
families (via `lib.edgelab.player_prop_settlement.settle_player_prop_market`,
dispatched from `settlement.settle_market_full`) — settlement coverage is
not the limiting factor here. The limiting factor was **price-snapshot
validity**, documented next (now fixed at the source, not merely
worked around).

## Measurement bug found and fixed at the source

**Root cause: `select_closing_quote()` treated an entirely-unresolved
start time as "no bound to check against" rather than "cannot verify
pre-start", so it fell back to returning the chronologically LAST
observation ever captured for a ticker — even one captured hours after
first pitch.**

Initial headline numbers from this audit (first produced in PR #98) were
implausible: the ≥90-cent YES-price bucket showed only a 44–55% actual
hit rate, and the overall calibration error was −0.086 — far outside
anything a functioning market could produce (a contract priced at 92%
should settle YES roughly 92% of the time, not half the time).
Root-caused to a concrete example:

- `KXMLBHRR-26AUG141910SDCLE-CLESKWAN38-5` (Steven Kwan, "at least 5
  combined hits+runs+RBI") had a `FIRST_DAILY` quote of `yesAsk=8` that
  morning — a sensible price for a rare stat line. Its `isClosingQuote`
  row was captured at `2026-08-14T23:53:18.874Z` with `yesBid=0.0,
  yesAsk=97.0` and **no two-sided `noBid`/`noAsk` quote at all** — a
  one-sided, degenerate snapshot, not a genuine executable price. This
  contract's `scheduledStart` never resolved, so
  `lib.edgelab.checkpoints.select_closing_quote()` had no start bound to
  compare captures against — its per-observation filter
  (`if start_dt is not None and captured_dt >= start_dt: continue`) was
  simply never triggered when `start_dt` was `None`, so *every*
  observation (including the 23:53Z one) counted as an eligible
  candidate, and the function returned whichever was chronologically last.
- Settlement itself was **independently correct**: the MLB Stats API
  confirms Kwan recorded 1 hit + 1 run + 0 RBI = 2, correctly settled
  `NO` against `threshold=5`. The bug was in snapshot/price selection,
  not settlement.

**The shared fix** (`lib/edgelab/checkpoints.py`, `select_closing_quote()`):
when neither `actual_start` nor `scheduled_start` resolves, the function
now returns `None` immediately — "unresolved timing" is explicit and
ineligible for pregame-closing classification, never silently guessed —
instead of falling through to an unbounded candidate search. This is a
4-line change (an early return plus removing the now-redundant
`if start_dt is not None` guard in the per-observation loop) to a single
function every downstream consumer already calls:

- `lib.edgelab.research_dataset.build_opportunity_rows` (every research/
  calibration report, including this one and
  `retrospective_validation_audit.md`)
- `lib.edgelab.clv.finalize_closing_quotes`, used by
  `scripts/edgelab/collect_clv.py` — **production CLV collection**. A
  ticker with unresolved timing now correctly reports
  `CLV_UNAVAILABLE`/`NO_VALID_PRE_CLOSE_QUOTE` instead of computing CLV
  against an unverified, possibly-post-start price. This was already
  every affected function's own documented contract ("never guesses a
  closing quote" / "never invents a quote") — the fix makes the actual
  behavior match the promise already made in these functions' docstrings,
  it does not introduce a new policy.

No production recommendation, staking, projection, or fee logic reads
`select_closing_quote()` — it feeds research/calibration reporting and
CLV collection only (verified by grepping every real call site across
the repo; see PR description for the full list). This fix cannot change
any bet that was ever placed or any recommendation that was ever
surfaced.

Restricting to rows where `minutesToStart is not None` collapses the
≥90-cent bucket's calibration error from −0.35..−0.48 to a plausible
−0.03..−0.06, and the overall calibration error from −0.086 (n=66,643, all
`isClosingQuote` rows under the *old*, unfixed behavior) to −0.020
(n=35,907, this report's primary dataset):

| Dataset | n | avgImplied | actualHit | calibrationError |
|---|---|---|---|---|
| Old behavior, all `isClosingQuote` rows (PR #98, before the shared fix) | 66,643 | 37.10% | 28.48% | −0.0862 |
| Fixed behavior, all `isClosingQuote` rows (this report) | 35,907 | 30.42% | 28.46% | −0.0196 |
| Old behavior, ≥90¢ bucket only | 7,333 | 97.25% | 53.42% | −0.4285 (worst single bucket) |
| Fixed behavior, ≥90¢ bucket only | 556 | 93.85% | 87.95% | −0.0590 |

**Regression tests** (see PR description): `tests/edgelab/test_checkpoints.py`
(new, 8 tests directly against `select_closing_quote()`),
`tests/edgelab/test_research_dataset.py` (+3 tests at the row-construction
level), `tests/edgelab/test_clv.py` (+1 test at the CLV layer), and
`tests/edgelab/test_integration_end_to_end.py` (1 existing end-to-end test
updated to assert the corrected, honest `CLV_UNAVAILABLE` outcome instead
of the old, incorrect `VALID` one for its unresolved-`scheduledStart`
fixture).

## Overall calibration (Key Question 1)

| Side | n | Games | Status | Avg implied | Actual hit rate | Calibration error | Brier | Gross ROI | Fee-only ROI | Realistic ROI |
|---|---|---|---|---|---|---|---|---|---|---|
| YES | 35,907 | 136 | CALIBRATED | 30.42% | 28.46% | **−0.0196** | 0.1574 | −7.86% | −12.73% | −12.13% |
| NO | 35,907 | 136 | CALIBRATED | 71.19% | 71.54% | **+0.0035** | — | +2.59% | +0.54% | +0.41% |

Kalshi MLB prices are **broadly, but not perfectly, calibrated**. YES
carries a small, consistent overpricing (real money buying YES loses on
average, before *and* after fees, as a blanket policy); NO is close to
perfectly calibrated and even shows a small positive raw edge before fees
(the edge does not survive realistic execution — see below). This is not
symmetric noise: it is the same underlying bias viewed from both sides
(see price-bucket tables).

## Price-bucket calibration (Key Questions 1, 3)

YES side, 5-cent buckets, all CALIBRATED (n=239–5,172, 126–134 independent
games per bucket):

| Bucket | n | Games | Avg implied | Actual hit | Calib. error | Gross ROI | Realistic ROI |
|---|---|---|---|---|---|---|---|
| 0–5% | 1,769 | 126 | 3.01% | 2.49% | −0.0053 | −16.6% | −22.0% |
| 5–10% | 5,172 | 129 | 7.00% | 5.99% | −0.0101 | −14.6% | −19.8% |
| 10–15% | 4,259 | 133 | 11.92% | 11.51% | −0.0041 | −3.5% | −9.1% |
| 15–20% | 3,582 | 132 | 16.91% | 15.61% | −0.0130 | −8.0% | −13.1% |
| 20–25% | 3,317 | 134 | 21.99% | 21.37% | −0.0062 | −3.1% | −8.2% |
| 25–30% | 2,907 | 133 | 26.93% | 24.49% | −0.0244 | −9.2% | −13.7% |
| 30–35% | 2,512 | 134 | 31.95% | 29.78% | −0.0217 | −7.0% | −11.2% |
| 35–40% | 1,924 | 134 | 36.90% | 34.46% | −0.0244 | −6.6% | −10.6% |
| 40–45% | 1,585 | 133 | 41.90% | 38.04% | −0.0386 | −9.1% | −12.7% |
| 45–50% | 1,280 | 133 | 46.95% | 41.17% | −0.0578 | −12.3% | −15.5% |
| 50–55% | 1,185 | 133 | 51.98% | 49.28% | −0.0270 | −5.3% | −8.4% |
| 55–60% | 1,205 | 132 | 57.01% | 54.77% | −0.0224 | −3.9% | −6.8% |
| 60–65% | 1,230 | 132 | 61.99% | 59.76% | −0.0223 | −3.6% | −6.2% |
| 65–70% | 1,252 | 129 | 66.97% | 63.34% | −0.0363 | −5.4% | −7.6% |
| 70–75% | 908 | 131 | 71.82% | 69.60% | −0.0222 | −3.1% | −5.0% |
| 75–80% | 547 | 127 | 76.63% | 71.48% | −0.0514 | −6.7% | −8.2% |
| 80–85% | 329 | 127 | 82.09% | 78.72% | −0.0336 | −4.1% | −5.3% |
| 85–90% | 388 | 129 | 87.11% | 80.15% | −0.0695 | −8.0% | −8.9% |
| 90–95% | 317 | 128 | 91.71% | 88.33% | −0.0338 | −3.7% | −4.3% |
| 95–100% | 239 | 130 | 96.69% | 87.45% | −0.0924 | −9.0% | −9.2% |

**Every single bucket, with no exception, shows negative calibration
error and negative gross ROI.** This is a small-to-moderate, ordinary
market-efficiency gap (not the implausible pre-fix pattern) — consistent
with a mild, well-documented "favorite/long-shot"-style behavioral bias
rather than a data artifact. NO side (its mirror, not shown row-by-row
here — see the committed JSON's `noOrientationCalibration.byPriceBucket`)
shows the complementary positive calibration error concentrated in cheap
NO buckets (≤10¢ NO, i.e. ≥90¢ YES): gross ROI up to +130% at n=98
(DESCRIPTIVE_ONLY, thin) down to a real, CALIBRATED +72%/+29%/+27%/+20%
in the 5–25¢ NO buckets — but realistic-execution ROI turns negative for
most NO buckets above 30¢, since fee drag as a fraction of a near-certain
NO stake is proportionally larger than the small remaining edge.

**Net executable-price relationship:** buying YES loses money at every
price after fees; buying cheap NO (i.e., fading expensive YES) shows a
real, CALIBRATED-tier gross edge that **partially, not fully**, survives
realistic execution.

## Market-family calibration (Key Question 2)

All CALIBRATED (n=121–8,861, 93–134 independent games):

| Family | n | Games | Avg implied | Actual hit | Calib. error | Gross ROI | Realistic ROI |
|---|---|---|---|---|---|---|---|
| inning_total (F5 totals) | 895 | 134 | 63.00% | 49.83% | **−0.1317** | −23.6% | −25.5% |
| game_total | 1,379 | 134 | 56.37% | 46.34% | **−0.1003** | −21.0% | −23.3% |
| team_total | 1,769 | 133 | 46.61% | 43.87% | −0.0275 | −6.6% | −10.0% |
| hitter_total_bases | 6,860 | 122 | 20.26% | 18.22% | −0.0204 | −9.3% | −14.1% |
| hitter_stolen_bases | 1,304 | 93 | 8.82% | 6.67% | −0.0215 | −32.3% | −36.4% |
| hitter_hits_runs_rbis | 8,861 | 116 | 34.22% | 32.82% | −0.0141 | −4.2% | −8.5% |
| hitter_hits | 6,162 | 124 | 27.05% | 25.80% | −0.0124 | −9.6% | −14.0% |
| hitter_rbis | 3,969 | 124 | 19.42% | 18.47% | −0.0095 | −7.0% | −12.0% |
| pitcher_strikeouts | 1,739 | 132 | 44.76% | 43.99% | −0.0077 | −2.2% | −5.8% |
| inning_result (F3/F5/F7 combined) | 1,128 | 133 | 33.84% | 33.60% | −0.0024 | −0.4% | −4.9% |
| game_result (full-game ML) | 247 | 127 | 50.51% | 50.61% | **+0.0010** | +0.8% | −2.7% |
| pitcher_outs | 221 | 119 | 49.97% | 50.68% | +0.0071 | +0.1% | −3.3% |
| first_inning_run (NRFI/YRFI) | 121 | 121 | 49.54% | 49.59% | +0.0005 | −1.0% | −4.4% |
| winning_margin | 1,252 | 133 | 26.21% | 27.88% | **+0.0166** | **+11.4%** | +5.8% |

**Systematically overpriced:** `inning_total` and `game_total` (both
total-runs markets) stand out sharply — 10–13 point calibration gaps and
21–24% negative gross ROI, far larger than any other family. Every
hitter-prop family is overpriced too, but much more mildly (1–2 points).
**Well-calibrated:** `game_result`, `pitcher_outs`, `first_inning_run`,
`inning_result` are all within ±0.7 points — the most information-efficient
corner of the market. **Only apparent underpricing:** `winning_margin`,
with a real (CALIBRATED, n=1,252/133 games) positive edge and the only
family with positive gross AND realistic-execution ROI.

## Threshold-level detail — hitter/pitcher props (Key Question 5)

Every threshold rung examined (14 of them, spanning `hitter_rbis`
1–3, `hitter_hits` 1–4, `hitter_total_bases` 2–6, `hitter_hits_runs_rbis`
1–5, `hitter_stolen_bases` 1) is individually CALIBRATED (n=259–1,734,
90–112 independent games) and **every single rung shows the same-sign
negative calibration error**, ranging from −0.001 to −0.033 with no
threshold reversing sign:

| Family | Threshold | n | Games | Avg implied | Actual | Calib. error |
|---|---|---|---|---|---|---|
| hitter_hits_runs_rbis | ≥5 | 1,517 | 102 | 9.74% | 9.62% | −0.0011 |
| hitter_hits_runs_rbis | ≥4 | 1,608 | 103 | 17.16% | 16.73% | −0.0043 |
| hitter_hits | ≥3 | 1,728 | 110 | 4.68% | 4.22% | −0.0046 |
| hitter_hits | ≥4 | 367 | 100 | 1.91% | 1.63% | −0.0027 |
| hitter_hits_runs_rbis | ≥1 | 1,612 | 103 | 67.57% | 66.56% | −0.0101 |
| hitter_rbis | ≥1 | 1,734 | 111 | 29.79% | 28.84% | −0.0096 |
| hitter_rbis | ≥2 | 1,729 | 112 | 10.98% | 10.01% | −0.0098 |
| hitter_total_bases | ≥5 | 1,224 | 108 | 8.18% | 7.27% | −0.0091 |
| hitter_total_bases | ≥4 | 1,633 | 110 | 14.89% | 13.35% | −0.0154 |
| hitter_hits | ≥2 | 1,697 | 110 | 21.44% | 20.27% | −0.0117 |
| hitter_hits | ≥1 | 1,690 | 109 | 60.95% | 58.93% | −0.0202 |
| hitter_hits_runs_rbis | ≥3 | 1,610 | 103 | 29.16% | 27.45% | −0.0170 |
| hitter_total_bases | ≥3 | 1,629 | 109 | 21.15% | 19.21% | −0.0194 |
| hitter_stolen_bases | ≥1 | 1,304 | 93 | 8.82% | 6.67% | −0.0215 |
| hitter_hits_runs_rbis | ≥2 | 1,612 | 103 | 46.12% | 43.11% | −0.0300 |
| hitter_total_bases | ≥2 | 1,621 | 108 | 36.03% | 32.70% | −0.0334 |

This ladder-wide consistency (no sign reversal across 16 independently
CALIBRATED rungs spanning 5 families) is stronger evidence of a real,
systematic effect than any single bucket or family cut alone — the
hitter-prop overpricing is not an artifact of one threshold or one
family, it is present at essentially every rung examined.

## F3/F5/F7 tie-treatment (Key Question 4)

Win-side and Tie-side kept fully separate, all CALIBRATED (n=121–259,
121–132 independent games):

| Horizon | Outcome | n | Games | Avg implied | Actual | Calib. error | Gross ROI |
|---|---|---|---|---|---|---|---|
| F3 | Win | 250 | 128 | 38.07% | 36.40% | −0.0167 | −4.0% |
| F3 | Tie | 124 | 124 | 24.07% | 27.42% | **+0.0335** | **+17.4%** |
| F5 | Win | 251 | 128 | 42.78% | 41.83% | −0.0095 | −1.2% |
| F5 | Tie | 123 | 123 | 15.76% | 16.26% | +0.0050 | +0.7% |
| F7 | Win | 259 | 132 | 44.51% | 45.17% | +0.0066 | +2.8% |
| F7 | Tie | 121 | 121 | 12.11% | 9.92% | **−0.0219** | **−17.6%** |

Every "Win" row is close to zero (−0.017 to +0.007) — the Win side of
these markets is well-priced, consistent with `game_result`'s and
`inning_result`'s overall calibration. The "Tie" side shows the most
dramatic single anomaly in this audit: F3-Tie looks underpriced (+17.4%
gross ROI) and F7-Tie looks overpriced in almost the same magnitude in
the opposite direction (−17.6%). **This is flagged as exploratory, not
actionable**: it rests on a single, un-partitioned 11-date sample (this
report's date-partition cut does not further segment by `outcomeLabel`,
so no independent repeatability check exists for this specific finding),
and a symmetric F3-positive/F7-negative pattern with F5 near zero in
between has no obvious economic mechanism — it could just as easily be
this season's noise in a market segment most bettors don't specialize in.

## Snapshot-timing stability (Key Question 6, partial)

All CALIBRATED (n=15–260 independent games per checkpoint). `FIRST_DAILY`'s
sample grew from 24,490 (n=120 games) to **54,006 (n=260 games)** once the
shared fix landed: FIRST_DAILY rows for unresolved-`scheduledStart`
tickers are legitimately pregame by construction (the literal first
observation captured that day, structurally never a post-game tick) and
no longer need excluding — only `CLOSING` ever needed the fix's
protection, and it now gets it at the source instead of via a
same-shaped, overly-broad workaround that had also been (unnecessarily)
dropping valid `FIRST_DAILY` rows:

| Checkpoint | n | Games | Status | Avg implied | Actual | Calib. error |
|---|---|---|---|---|---|---|
| FIRST_DAILY | 54,006 | 260 | CALIBRATED | 32.35% | 28.39% | −0.0396 |
| T_MINUS_90 | 1,892 | 15 | CALIBRATED | 34.07% | 33.56% | −0.0051 |
| T_MINUS_60 | 1,790 | 20 | CALIBRATED | 32.57% | 29.72% | −0.0285 |
| T_MINUS_30 | 1,775 | 7 | CALIBRATED | 30.66% | 24.73% | −0.0593 |
| CLOSING | 35,907 | 136 | CALIBRATED | 30.42% | 28.46% | −0.0196 |

The overpricing is present at every checkpoint, including the earliest
(FIRST_DAILY, −0.040, now with more than double the sample and a larger
game count) — it is not something that only emerges as game time
approaches. CLOSING is the best-calibrated checkpoint, suggesting
some (not all) of the mispricing corrects as more information arrives
before lock. T_MINUS_90/60/30 have far fewer independent games (7–20)
than FIRST_DAILY/CLOSING and should be read as more exploratory.

## Date-partition stability (Key Questions 6, 10)

`chronological_split()` on the 11 dates that survive the measurement-bug
filter: **`FRAMEWORK_ONLY_INSUFFICIENT_DATES`** (11 of a documented
30+-date target — even fewer usable dates than the raw 20-date corpus,
directly because of the coverage loss documented above).

| Partition | Dates | n | Games | Avg implied | Actual | Calib. error | Gross ROI |
|---|---|---|---|---|---|---|---|
| DEVELOPMENT | 7 (08-02..08-09) | 19,549 | 85 | 31.23% | 28.44% | −0.0279 | −10.6% |
| VALIDATION | 2 (08-10, 08-16) | 7,806 | 25 | 29.23% | 29.00% | −0.0023 | −2.8% |
| HOLDOUT | 2 (08-18, 08-19) | 8,552 | 26 | 29.67% | 28.01% | −0.0166 | −6.2% |

The negative-calibration-error sign is consistent across all three
partitions (never flips positive), which is meaningfully repeatable
evidence for the *direction* of the YES-side bias — but VALIDATION's
near-zero magnitude (−0.0023) versus DEVELOPMENT's larger one (−0.0279)
shows the *size* of the effect is not yet stable, and 2-date partitions
are inherently thin. Family-level date-partition detail (top families
per partition) is in the committed JSON
(`datePartitionStability.partitions.*.byFamilyTopN`); the sign is mostly,
not universally, consistent for individual families across partitions
(e.g. `pitcher_strikeouts` gross ROI: +0.2% DEV vs −18.9% VALIDATION).

## Key question answers

1. **Are Kalshi MLB prices broadly calibrated overall?** Broadly yes but
   not perfectly — a small, real, CALIBRATED-tier negative bias on YES
   (−0.0196 overall, n=35,907/136 games) that is directionally consistent
   across every price bucket, every checkpoint, and all three date
   partitions.
2. **Which market families appear systematically underpriced or
   overpriced?** Overpriced: `inning_total` and `game_total` most
   severely (−0.10 to −0.13), then every hitter-prop family and
   `team_total` more mildly (−0.01 to −0.03). Well-calibrated:
   `game_result`, `pitcher_outs`, `first_inning_run`, `inning_result`
   (all within ±0.007). Apparently underpriced: `winning_margin` alone
   (+0.017, the only family with positive realistic-execution ROI).
3. **Do low-price or high-price contracts show consistent bias?** Yes —
   every one of 20 five-cent buckets shows negative calibration error and
   negative gross ROI, mildly worsening toward the extremes (0–5%: −0.005;
   95–100%: −0.092), consistent with an ordinary favorite/long-shot-style
   effect rather than a bucket-specific artifact.
4. **Are there repeatable anomalies in F5 NO / tie-protected structures?**
   The Win side of F3/F5/F7 is well-calibrated throughout. The Tie side
   shows a striking F3-positive (+17.4% ROI) / F7-negative (−17.6% ROI)
   pattern, but this rests on a single un-partitioned sample with no
   repeatability check available at this cut — **exploratory only**.
5. **Do pitcher or hitter prop thresholds show meaningful price-vs-hit-rate
   gaps?** Hitter props: yes, small (−0.001 to −0.033) but present at
   every one of 16 independently-CALIBRATED threshold rungs across 5
   families with no sign reversal — the most internally consistent
   finding in this audit. Pitcher props: `pitcher_strikeouts` mildly
   overpriced (−0.008); `pitcher_outs` essentially perfectly calibrated
   (+0.007).
6. **Are any apparent edges stable across different date partitions?**
   The overall YES-negative/NO-positive bias direction is stable in sign
   across DEVELOPMENT/VALIDATION/HOLDOUT; its magnitude is not yet stable
   (VALIDATION ≈ 0). Family-level signs are mostly, not universally,
   consistent partition-to-partition. Given only 11 usable dates, this is
   real but immature evidence.
7. **Which findings have enough sample to be actionable versus
   exploratory?** Actionable-scale (large, CALIBRATED, cross-cut-consistent):
   the overall YES-overpriced/NO-fair pattern; `inning_total`/`game_total`
   overpricing; the 16-rung hitter-prop-threshold ladder. Exploratory
   only: the F3/F7 Tie anomaly; `winning_margin`'s positive edge (one
   partition-level check, no independent confirmation); any specific
   date-partition magnitude given 11 total dates.
8. **Are there market families where settlement or snapshot coverage is
   too weak to judge?** No family is outright unjudgeable at the top-line
   cut — every family reaches CALIBRATED status (smallest:
   `first_inning_run` at n=121/121 games). Before the shared fix, 46.1%
   of raw `isClosingQuote` rows would have had to be excluded because
   their pregame validity could not be verified; that gap is now closed
   at the source (`select_closing_quote()` never selects such a row in
   the first place), so it is no longer a live coverage constraint on
   this or any other EdgeLab report. The remaining, genuine coverage
   limit is settlement itself: 15.0% of observed contracts (80,770 −
   68,674) are unsettled for ordinary reasons (game not final,
   player-participation unresolved, etc. — see
   `phase2_model_evaluation.md`'s `settlementUnavailableReason` counts),
   which is a normal data-lag fact, not a measurement defect.

## Fee-aware simulated ROI summary

A **blanket "buy every YES at the closing price"** policy loses money
before fees (−7.9% gross) and loses more after them (−12.1% realistic
execution) — the calibration gap and transaction costs both work against
it, at every price level with no exception (see price-bucket table). A
**blanket "buy every NO"** policy shows a small positive raw edge (+2.6%
gross) driven almost entirely by cheap NO / expensive-YES contracts, but
that edge is largely consumed by fees (+0.4% realistic execution) — not
zero, but not a demonstrated profitable blanket strategy either. Neither
blanket policy is being proposed as a production rule; both are reported
purely descriptively, per this audit's explicit scope.

## Actionable conclusions

**Change:** Nothing in projection formulas, calibration coefficients,
recommendation thresholds, bankroll sizing, or fee logic — this audit's
own scope explicitly excludes that, and Key Question 6/7's answers
(stable-in-sign-only, immature partition count) do not clear that bar
regardless.

**Do not promote / no new production rule from this audit:** hitter
props remain research-only (this audit does not change that
determination — it only shows their *market* pricing is mildly
overpriced, not that our *model* should trade against it, which is a
separate, already-answered question in the prior model-validation audit).

**Fixed as part of this update (see "Measurement bug found and fixed at
the source"):** the `scheduledStart`-resolution gap that let
`select_closing_quote()` (`lib/edgelab/checkpoints.py`) select a
non-pregame snapshot is now closed at the source, benefiting every
EdgeLab report and production CLV collection, not just this audit.

**Worth prioritizing as follow-up work (outside this PR):**
- `inning_total`/`game_total` and the hitter-prop-threshold ladder are the
  most promising repeatable-mispricing candidates for a future, properly
  out-of-sample-tested strategy — not actioned here, flagged for the next
  dedicated strategy-validation pass once more trading dates accumulate.
- The F3/F7 Tie anomaly deserves a dedicated `outcomeLabel`-aware
  date-partition cut (not built in this audit) before being taken
  seriously either way.

## Artifacts

- This report: `data/edgelab/reports/market_price_calibration_audit.md`
- Machine-readable data: `data/edgelab/analytics/latest_market_price_calibration_audit.json`
- Audit script (read-only, no measurement-bug workaround anymore):
  `scripts/edgelab/run_market_price_calibration_audit.py`
- Shared fix: `lib/edgelab/checkpoints.py` (`select_closing_quote()`)
- Regression tests: `tests/edgelab/test_checkpoints.py` (new),
  `tests/edgelab/test_research_dataset.py`, `tests/edgelab/test_clv.py`,
  `tests/edgelab/test_integration_end_to_end.py`
