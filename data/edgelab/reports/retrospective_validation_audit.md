# Retrospective Validation Audit — MLB/Kalshi System

_Generated 2026-08-20 (post-PR #96, commit cbf8227). Author: EdgeLab research session._

**RESEARCH ONLY.** This report is a read-only analysis produced entirely from the
canonical EdgeLab research framework (`lib/edgelab/`, `scripts/edgelab/run_*.py`,
`lib/edgelab/research_dataset` / `run_research_reports.py`). It changes nothing in
production: no projection formula, calibration coefficient, confidence threshold,
bankroll rule, fee logic, or recommendation rule was modified to produce it. All
underlying report files it cites were regenerated fresh from the unmodified canonical
scripts and are committed alongside this document. See "Measurement notes" for the
one genuine data-coverage gap found (not fixed — see rationale there).

## Methodology

- Every quoted table comes from a canonical, already-existing EdgeLab report or the
  `lib.edgelab.calibration` / `lib.edgelab.research_dataset` toolkits, run unmodified
  against the current committed JSONL corpus. No parallel analysis pipeline was built.
- Two distinct data universes are used throughout, and every finding below states
  which one it draws on:
  - **Placed-bet ledger** (`data/edgelab/bets/bets.jsonl`, joined to settlements) —
    real staked money, real win/loss, real ROI. Season-to-date n=117 decided bets
    (`status IN {'WIN','LOSS'}`), CALIBRATED tier by the simple win-rate gate, but
    most individual dimension cuts of it are far smaller (`phase2_calibration.md`).
  - **Causally-valid model-evaluation sample** (`lib.edgelab.research_dataset`,
    `run_research_reports.py`) — every historical row where a model probability
    existed *before* the market closed, deduplicated and checkpoint-aware,
    independent of whether a bet was ever placed. n=404 (104 independent games),
    CALIBRATED. This is the more statistically rigorous of the two (Brier score,
    log loss, calibration slope/intercept, `GAME_CLUSTERED_BOOTSTRAP` confidence
    intervals) and is preferred wherever both exist, because it isn't
    survivorship-biased by which bets happened to get placed.
- Sample-size discipline follows the repo's own two schemes and both are reported
  side-by-side where they diverge: `lib.edgelab.calibration.calibration_status`
  (n<20 INSUFFICIENT_SAMPLE, 20≤n<100 DESCRIPTIVE_ONLY, n≥100 CALIBRATED) and the
  research-dataset's own per-row `sampleSize.status`/`independentGames` object,
  which additionally flags `gameConcentrationWarning` when raw contract count
  overstates independent-game count.
- The strategy-validation DEV/VALIDATION/HOLDOUT split
  (`latest_research_strategy_validation.json`) is used wherever a claim needs to
  survive out-of-sample repetition, not just a single aggregate. Its own maturity
  flag (`FRAMEWORK_ONLY_INSUFFICIENT_DATES`, 20 total trading dates: 12/4/4) is
  carried through explicitly — nothing here is claimed as "validated" in the
  framework's own stricter sense.
- Every regenerated canonical report was produced today by re-running (unmodified):
  `run_calibration.py`, `run_model_evaluation_report.py`,
  `run_market_comparison_report.py`, `generate_rolling_report.py` (200/600 windows),
  `run_market_intelligence_report.py`, `run_research_reports.py`.

## Sample coverage

| Universe | n | Independent games | Status | Source |
|---|---|---|---|---|
| Full market-observation universe (YES side) | 120,765 | — (353 games) | CALIBRATED | `latest_research_market_calibration.json` |
| Causally-valid model-evaluation sample | 404 | 104 | CALIBRATED | `latest_research_model_calibration.json` |
| Total raw ModelEvaluation records | 59,753 | — | n/a (population) | `phase2_model_evaluation.md` |
| ...of which have any modelFairProbability | 1,750 (2.9%) | — | — | same |
| Placed-bet ledger, decided (WIN/LOSS) | 117 | — | CALIBRATED (win-rate gate) | `phase2_calibration.md` |
| Strategy-validation DEVELOPMENT partition | 264 (`<0`) / 168 (`10+`) rows in the two largest edge buckets | 68 / 67 | CALIBRATED | `latest_research_strategy_validation.json` |
| Strategy-validation VALIDATION partition | 61 (`<0`) / 46 (`10+`) | 14 / 14 | DESCRIPTIVE_ONLY | same |
| Strategy-validation HOLDOUT partition | 79 (`<0`) / 49 (`10+`) | 22 / 21 | DESCRIPTIVE_ONLY | same |

Date range covered: 2026-08-01 through 2026-08-20 for the full research-dataset
universe (20 trading dates total); the placed-bet ledger additionally contains a
small number of `LEGACY_BACKFILL` bets from 2026-06-12/06-17 (n≈14, isolated in its
own daily/weekly/monthly trend bucket, never merged into "current" trend rows).

**Coverage gap by family (confirmed independently in three reports —
`phase2_model_evaluation.md`, `latest_research_market_family_research.json`,
`latest_research_research_data_quality.json`):** `hitter_hits`,
`hitter_hits_runs_rbis`, `hitter_rbis`, `hitter_stolen_bases`, `hitter_total_bases`,
`pitcher_outs`, and `pitcher_strikeouts` all show **0.0% modelFairProbability
coverage** — tens of thousands of observed contracts, zero rows with a model
probability attached. `game_total` and `inning_total` are effectively the same
(2.0% and 0.0% respectively, `familyModelCoverage` in `research_data_quality`).
Any historical win/loss on these markets reflects market-price/manual selection,
not a model prediction, and is excluded from every calibration claim below.

## Core calibration findings

### Overall model calibration (causally-valid sample, n=404, CALIBRATED)

| Metric | Value |
|---|---|
| avg model probability | 40.8% |
| actual win rate | 47.0% |
| calibration error (actual − predicted) | +0.0622 |
| expected calibration error (binned) | 0.1338 |
| model Brier score | 0.271 |
| contemporaneous market Brier score (same rows) | 0.250 |
| model log loss | 0.748 |

The model's aggregate calibration error is modest (+6 points), but the much larger
expected calibration error (0.134) shows this small aggregate number is masking
larger, offsetting errors within probability bins (below). **The model's Brier
score is worse than the market's own contemporaneous price on the same rows** —
in aggregate, the market is a sharper predictor than the model.

### Calibration by model-probability bucket (same 404-row sample)

| Bucket | n | Games | Status | Avg model prob | Actual win rate | Calib. error | Model Brier | Market Brier |
|---|---|---|---|---|---|---|---|---|
| 0–10% | 4 | 4 | INSUFFICIENT_SAMPLE | 5.0% | 50.0% | +0.450 | 0.449 | 0.188 |
| 10–20% | 30 | 22 | DESCRIPTIVE_ONLY | 16.8% | 36.7% | +0.199 | 0.279 | 0.247 |
| 20–30% | 97 | 61 | DESCRIPTIVE_ONLY | 25.7% | 43.3% | +0.176 | 0.281 | 0.254 |
| 30–40% | 72 | 51 | DESCRIPTIVE_ONLY | 34.6% | 51.4% | +0.168 | 0.278 | 0.242 |
| 40–50% | 59 | 43 | DESCRIPTIVE_ONLY | 44.6% | 49.1% | +0.045 | 0.252 | 0.251 |
| 50–60% | 93 | 62 | DESCRIPTIVE_ONLY | 56.4% | 54.8% | −0.015 | 0.242 | 0.249 |
| 60–70% | 48 | 37 | DESCRIPTIVE_ONLY | 63.2% | 37.5% | **−0.257** | 0.296 | 0.259 |
| 70–80% | 1 | 1 | INSUFFICIENT_SAMPLE | 71.9% | 0.0% | −0.719 | 0.517 | 0.462 |

The 40–60% range is genuinely well-calibrated (error within ±0.05, and model Brier
≈ market Brier there — the only region where the model matches the market's
sharpness). The 10–40% range is systematically underconfident (actual win rate well
above stated probability). Most concerning: **the model's highest-volume
high-confidence bucket (60–70%, n=48, 37 games) is materially overconfident** —
actual win rate (37.5%) is 26 points below the stated probability, and this is the
worst Brier score of any bucket with a real sample. This is exactly the bucket a
production system would lean on hardest, and it is currently the weakest one.

### Calibration by canonical market family (causally-valid sample)

| Family | n | Games | Status | Model Brier | Market Brier | Calib. error | Beats market? |
|---|---|---|---|---|---|---|---|
| inning_result (F5) | 39 | 33 | DESCRIPTIVE_ONLY | **0.232** | 0.245 | −0.015 | **Yes** |
| team_total | 236 | 102 | CALIBRATED | 0.282 | 0.252 | +0.175 | No |
| first_inning_run (NRFI/YRFI) | 104 | 86 | CALIBRATED | 0.261 | 0.246 | −0.119 | No |
| game_result (full-game ML) | 25 | 20 | DESCRIPTIVE_ONLY | 0.270 | 0.255 | −0.131 | No |

**`inning_result`/F5 is the only family where the model's Brier score beats the
market's** — the sole family with real evidence the model adds information beyond
what's already priced in. `team_total` has the largest calibrated sample outside F5
(n=236) and is clearly miscalibrated (+0.175 error, worse Brier than market).
`game_result`/full-game ML and `first_inning_run` both show the model performing
worse than the market at their current (sub-100) sample sizes.

### Placed-bet ledger calibration by family (`phase2_calibration.md`, all n<45, mostly INSUFFICIENT_SAMPLE)

| Family | n | Win% | ROI | Status |
|---|---|---|---|---|
| inning_result | 42 | 47.6% | −8.6% | DESCRIPTIVE_ONLY |
| game_result | 19 | 36.8% | −18.0% | INSUFFICIENT_SAMPLE |
| pitcher_strikeouts | 17 | 47.1% | −7.0% | INSUFFICIENT_SAMPLE (no model support) |
| first_inning_run | 10 | 40.0% | −10.7% | INSUFFICIENT_SAMPLE |
| game_total | 10 | 70.0% | +28.5% | INSUFFICIENT_SAMPLE (no model support) |
| team_total | 9 | 33.3% | −44.3% | INSUFFICIENT_SAMPLE |
| pitcher_outs | 6 | 16.7% | −61.1% | INSUFFICIENT_SAMPLE (no model support) |

These raw ledger numbers should not be read as calibration findings on their own —
sample sizes are too small, and (for pitcher_outs, pitcher_strikeouts, game_total)
there was no model probability behind the bet at all. They are included for
completeness and cross-reference only.

## Edge-bucket backtest: the strongest repeatable finding

The strategy-validation framework partitions all 20 trading dates into
DEVELOPMENT (12 dates) / VALIDATION (4) / HOLDOUT (4), with **no tuning performed
on any partition** (`maturity: FRAMEWORK_ONLY_INSUFFICIENT_DATES`). Looking at the
same two edge buckets independently in each partition:

| Partition | Edge bucket | n | Games | Status | Actual win% | Gross ROI | ROI after fees | Realistic ROI (cash-consumed) |
|---|---|---|---|---|---|---|---|---|
| DEVELOPMENT | `<0%` | 264 | 68 | CALIBRATED | 50.8% | −0.84% | −4.24% | −4.16% |
| DEVELOPMENT | `10+%` | 168 | 67 | CALIBRATED | 51.8% | +4.54% | +1.04% | +0.93% |
| VALIDATION | `<0%` | 61 | 14 | DESCRIPTIVE_ONLY | 44.3% | −15.13% | −18.49% | −17.92% |
| VALIDATION | `10+%` | 46 | 14 | DESCRIPTIVE_ONLY | 58.7% | +18.84% | +15.28% | +14.72% |
| HOLDOUT | `<0%` | 79 | 22 | DESCRIPTIVE_ONLY | 49.4% | −4.18% | −7.52% | −7.38% |
| HOLDOUT | `10+%` | 49 | 21 | DESCRIPTIVE_ONLY | 55.1% | +17.57% | +13.97% | +13.30% |

**The sign is consistent in all three independently-partitioned samples**: the
`<0%` edge bucket loses money (gross and net) in DEVELOPMENT, VALIDATION, and
HOLDOUT alike; the `10+%` edge bucket makes money (gross and net) in all three.
This is the single most repeatable result in the entire audit — three
non-overlapping date ranges, same direction, same rough magnitude of effect. The
middle of the edge distribution (2–10%) is much noisier and mostly
INSUFFICIENT_SAMPLE/DESCRIPTIVE_ONLY (n=1–34 per bucket per partition) and should
not be treated as evidence either way yet.

## Fee-aware net vs. gross edge (Key Question 3)

`PlacedBet.estimatedEdgeAtEntry` is a single, gross (model-probability-vs-entry-price)
field — there is no separate stored "net executable edge" field on placed-bet
records. The fee-aware net-vs-gross comparison instead lives in the strategy-
validation backtest (table above), which is the correct source for this question.

- Fee drag alone (`feeOnlyDragPercentagePoints`) is consistently **~3.3–3.8
  percentage points** of ROI across every bucket and partition — a small, stable
  cost.
- Execution drag (`executionDragPercentagePoints`, covering realistic
  order-book execution beyond the raw fee schedule) is more variable, roughly
  1.8–8.0pp, and tends to be largest in the smallest-n buckets (execution-quantity
  granularity dominates at low volume).
- The `10+%` edge bucket's net ROI stays **positive after fees in all three
  partitions** (DEV +0.9%, VALIDATION +14.7%, HOLDOUT +13.3%), and the `<0%`
  bucket stays **negative in all three** — fees compress the edge (often by more
  than half of the gross figure, especially in the large DEVELOPMENT sample where
  gross +4.54% becomes net +0.93%) but do not reverse its direction. The apparent
  edge is not a fee-blind illusion at this sample size.
- No bug here — this is a legitimate distinction between two different, correctly
  documented ROI concepts (`roi`/`grossROI` and `roiAfterFeesOnly` are both
  return-on-allocated-budget; `roiRealisticExecution` is return-on-actual-cash-
  consumed and is the primary betting-performance metric per the framework's own
  `roiDenominatorNote`). Any future Bet-Up-To or net-EV reasoning should reference
  this backtest's fee-aware fields, not the placed-bet ledger's single gross
  `estimatedEdgeAtEntry`.

## Market-family findings (Key Question 4)

- **inning_result (F5): repeatable positive signal.** Only family where model
  Brier beats market Brier (0.232 vs 0.245). Highest market-health score
  (0.638, still DESCRIPTIVE_ONLY at n=42) in `phase2_market_intelligence.md`.
  Dominates full-game ML in 6/6 dominated-market examples found across the full
  58,495-market comparison population (same team, same day, full-game ML strictly
  dominated by F5 on `HIGHER_EV, INFERIOR_NET_EV, ...` grounds).
- **game_result (full-game ML): repeatable negative signal.** Worst calibration
  error among families with any real sample (−0.131 causally-valid, −0.200 ledger),
  negative ROI in both the ledger (−18.0%, n=19) and market-intelligence view, and
  is the losing side of every dominated-market case found.
- **team_total: miscalibrated at real sample size.** Largest non-F5 calibrated
  sample (n=236, CALIBRATED), but the most severe calibration error of the four
  families with model support (+0.175) and a worse Brier score than the market.
  Ledger ROI is also badly negative (−44.3%, n=9) though that cell alone is too
  small to weight heavily on its own.
- **first_inning_run (NRFI/YRFI): negative and moderately sized.** n=104/86 games
  (CALIBRATED), calibration error −0.119 (overconfident), Brier slightly worse
  than market. Ledger ROI −10.7% (n=10).
- **game_total, inning_total, pitcher_outs, pitcher_strikeouts, all hitter
  families: not measurable.** Effectively zero model-probability coverage
  (confirmed independently in three reports). Any observed ledger ROI on these
  markets (e.g. game_total's apparently strong +28.5% at n=10, or pitcher_outs'
  −61.1% at n=6) reflects market-price selection, not a graded model prediction,
  and must not be read as a model-calibration finding in either direction.

## Lineup-confirmation impact (Key Question 5)

A direct query of `v_placed_bets.lineupConfirmationState` for decided bets
(`lib.edgelab.calibration._DECIDED_BETS_FILTER`) gives:

| Lineup state | n | Status | Win rate | Expected win rate | Calib. error | ROI | Avg CLV |
|---|---|---|---|---|---|---|---|
| CONFIRMED | 20 | DESCRIPTIVE_ONLY | 55.0% | 53.3% | +0.017 | **+18.5%** | 0.00 |
| UNKNOWN | 97 | DESCRIPTIVE_ONLY | 42.3% | n/a | n/a | **−19.5%** | −4.26 |

This is the same 20/97 split as `data_quality_calibration`'s `full` vs `UNKNOWN`
buckets (i.e., in the current ledger, confirmed-lineup and "full data quality" are
effectively the same 20 bets) — a large (~38-point ROI), directionally consistent
gap between confirmed-lineup bets and bets placed without lineup confirmation.
CONFIRMED bets are also close to perfectly calibrated on their own (+0.017 error).
n=20 sits right at the DESCRIPTIVE_ONLY/CALIBRATED boundary — real, but not yet a
large-sample claim.

## Pitcher workload-signal / prop thresholds (Key Question 6)

No calibrated pitcher-prop model output exists to grade. `pitcher_strikeouts` and
`pitcher_outs` show 0.0% `modelFairProbability` coverage across every threshold
examined (`latest_research_market_family_research.json`), confirmed again by
`familyModelCoverage` in the data-quality report (0.0% for both). No populated
pitcher-workload-signal-class field was found anywhere in the evaluated corpus.
The observed ledger losses on these markets (pitcher_outs −61.1% ROI, n=6;
pitcher_strikeouts −7.0%, n=17) cannot be attributed to weak calibration of a
workload-signal class, because no such signal was ever scored historically — this
is a coverage gap, not a weak-but-real signal. The question as posed ("which
thresholds/classes are systematically weak") cannot be answered from current data;
the honest answer is "none have been evaluated yet."

## Hitter-prop promotion readiness (Key Question 7)

`hitter_prop_promotion_readiness()` and every coverage report agree: **0.0% model
coverage for all five hitter families** (`hitter_hits`, `hitter_total_bases`,
`hitter_rbis`, `hitter_hits_runs_rbis`, `hitter_stolen_bases`), at every threshold,
across tens of thousands of observed contracts (13,806–32,068 raw contracts
observed per family in `research_data_quality`, zero of them model-evaluated).
Default answer is no unless evidence is strong; the evidence here isn't merely
weak, it's absent. **All hitter families remain RESEARCH_ONLY.** This is not a
close call.

## CLV findings (Key Question 8)

CLV capture is itself sparse in the ledger: of 117 decided bets, only 20 fall into
a real (non-`UNKNOWN`) CLV bucket in `phase2_calibration.md`'s CLV-bucket table —
most bets have no `clvQuoteId`/`closingPrice` recorded. Within the small real-CLV
sample: the 0–5 bucket (n=13) shows positive CLV (+0.467) with positive ROI
(+25.5%); the CLV sign study shows POSITIVE (n=6, ROI −5.2%), NEUTRAL (n=7, ROI
+46.1%), and NEGATIVE (n=7, ROI −36.3%) — the NEGATIVE-CLV/negative-ROI pairing is
directionally consistent with CLV theory, but every one of these buckets is
INSUFFICIENT_SAMPLE (n<20) and the POSITIVE bucket's negative ROI doesn't fit the
story. **CLV cannot currently independently validate the model's edge claim** —
not because CLV contradicts it, but because too few bets have a captured closing
price to draw any conclusion. This is a data-capture gap (most bets show
`clvQuoteId=null`, `closingPrice=null` as in the sample record inspected), not a
finding about CLV itself, and should be prioritized as an instrumentation
improvement for future audits rather than acted on now.

## Best-expression / expressionGroup findings (Key Question 9)

- Placed-bet opportunity-cost audit: only **2 of 7** examined placed bets (28.6%)
  were dominated by a better-ranked expression, both full-game-ML-vs-F5 cases with
  modest lost-edge (−1.43, −0.06 points); lost-ROI/lost-CLV are unmeasured (no
  settlement outcome can be honestly attributed to a market that was never bet).
- Population-level: **6 of 58,495** compared markets are `DOMINATED_MARKET`, all
  6 the same pattern — full-game ML dominated by F5 for the same team/day.
- Strategy-experiment simulation: replacing every bet with its best-ranked
  expression (`DOMINATED_MARKETS_REPLACED_WITH_BEST_EXPRESSION`) and always
  preferring F5 (`ALWAYS_PREFER_F5`) both produce **exactly 0.0% ROI delta**
  versus the real n=117 baseline — too few of the actual placed bets fell in a
  dominated cluster for the experiment to move the needle yet.
- The new `expressionGroup` field (added same-day, PR #96) has no historical
  settled sample to evaluate at all — it postdates every row in this corpus.

The structural signal (F5 dominates full-game ML wherever a comparison exists) is
consistent and points the same direction as the market-family calibration finding
above, but the *realized* evidence that switching expressions would have changed
outcomes is still thin (2 cases, 0.0% simulated delta). Read as: plausible and
worth continued observation, not yet a confirmed source of lost ROI.

## Sample-size sufficiency for any calibration change (Key Question 10)

**No.** The strategy-validation framework's own maturity gate reads
`FRAMEWORK_ONLY_INSUFFICIENT_DATES` — only 20 total trading dates exist
(12 DEVELOPMENT / 4 VALIDATION / 4 HOLDOUT), short of its documented 30+-date bar
for treating a HOLDOUT result as validating a rule. Even the strongest finding in
this audit (the edge-bucket sign consistency above) rests on 14–68 independent
games per partition per bucket — real, but still short of a mature, multi-month,
out-of-sample validated result. No dimension examined in this audit clears that
bar. The single most promising candidate for eventual promotion is the
edge≥10%-bucket / edge<0%-bucket split, given its 3-for-3 sign consistency across
independent partitions — but per the task's explicit instruction, no threshold or
coefficient should change on the strength of this audit alone.

## Measurement notes (not fixed — reported only)

- **2026-08-11 through 2026-08-15: complete gap in EVALUATED ModelEvaluation
  records.** Verified directly (`phase2_model_evaluation.md`'s by-date breakdown):
  all five dates show 0.0% coverage on `modelFairProbability`/`estimatedEdge`/
  `confidence`/`tags` despite 2,735–5,074 raw rows per date. No existing incident
  documentation covers this window. This is treated as an honest historical
  pipeline gap, not a bug requiring a fix in this PR: it does not corrupt or bias
  measurement of the surrounding dates, and the sample-size-aware framework
  already excludes/down-weights those rows naturally (they simply contribute zero
  EVALUATED rows rather than wrong ones). Recommend a follow-up investigation
  ticket, but not a code change here.
- **Daily/weekly/monthly trend tables are dominated by a `None` period (n=103).**
  Most placed bets lack a resolved `entryTimestamp` bucket (`timestampStatus`
  is not `PROVIDED` for most of the season-to-date ledger), so `daily_trend_
  calibration`/`weekly_trend_calibration`/`monthly_trend_calibration` can only
  usefully split the ~14 legacy-backfilled 2026-06 bets from everything else. This
  is a data-completeness limitation on trend granularity, not a computation error;
  it means "does performance change over time" cannot currently be answered at
  finer than season-to-date resolution for the bulk of the ledger.
- No code changes were made anywhere in `lib/`, `scripts/`, or `config/` to
  produce this report. Every number above comes from re-running existing,
  unmodified canonical scripts against the current committed data.

## Actionable conclusions

**Change:**
- Nothing in projection formulas, calibration coefficients, confidence thresholds,
  bankroll sizing, fee logic, or production recommendation rules — per explicit
  task scope, and because Key Question 10's answer is no.

**Do not change, but worth prioritizing as follow-up work (outside this PR):**
- Improve CLV/closing-price capture on placed bets — 83% of decided bets
  (97/117) currently have no usable CLV bucket, which blocks Key Question 8 from
  ever being answered with real sample size no matter how much time passes.
- Investigate (not necessarily fix) the 2026-08-11–08-15 ModelEvaluation gap.
- Continue accumulating DEVELOPMENT/VALIDATION/HOLDOUT trading dates toward the
  framework's own 30+-date maturity bar before treating any edge-bucket or
  market-family finding here as validated.
- Consider adding a lineup-confirmation-specific calibration function
  (`lib/edgelab/calibration.py` currently has no dedicated one; this audit had to
  query `v_placed_bets.lineupConfirmationState` directly) since Key Question 5's
  finding is one of the more promising ones and deserves a first-class,
  reusable, regenerable report cut rather than an ad hoc query.

**Confirmed do-not-promote:**
- All five hitter-prop families: remain RESEARCH_ONLY (zero model coverage).
- pitcher_strikeouts / pitcher_outs: no calibrated signal exists to promote or
  demote; treat as unscored until model coverage exists.

## Report artifacts

- This report: `data/edgelab/reports/retrospective_validation_audit.md`
- Machine-readable summary: `data/edgelab/analytics/latest_retrospective_validation_audit.json`
- Underlying canonical reports (all regenerated fresh from unmodified scripts as
  part of this audit, committed alongside): `phase2_calibration.md`,
  `phase2_model_evaluation.md`, `phase2_market_comparison.md`,
  `phase2_market_intelligence.md`, `research_trustworthiness_summary.md`,
  `rolling_last_200.md`, `rolling_last_600.md`, and the `latest_research_*.json`
  family under `data/edgelab/analytics/`.
