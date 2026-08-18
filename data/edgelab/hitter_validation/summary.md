# Hitter Projection Engine — Retrospective Grading & Calibration Audit

Status: **research-only measurement**. Nothing in this audit changes any
production formula, threshold, weight, prior, recommendation, staking, or
settlement-semantics logic. It reads only already-archived artifacts
(`data/pipeline/*/hitter_projection_board.json`,
`data/edgelab/settlements/*.jsonl`, `data/pipeline/*/hitter_features.json`)
and writes only under `data/edgelab/hitter_validation/`. Methodology,
code, and full field-level docs: `lib/research/hitter_projection_audit.py`.

**Note on GitHub issue #43**: the task brief that requested this audit
stated player-prop settlement was not yet implemented. That is out of
date — `docs/PLAYER_PROP_SETTLEMENT.md` shows issue #43 shipped
automatic hitter/pitcher-prop settlement, and this repository's archive
already contains 43,661 real settled hitter-prop rows
(`data/edgelab/settlements/*.jsonl`). This audit uses that existing,
production settlement pipeline's own output directly — it does not
build a second settlement system, and it never mutates a settlement or
bet record.

---

## 1. How many prospective hitter projections exist?

**1,872** rows, across the **entire history of this repository**, ever
carried `projectionStatus == "PROJECTED"` (i.e. the engine actually
computed a fair probability for that market, pregame, with a confirmed
lineup) in `data/pipeline/*/hitter_projection_board.json`.

This is the *complete* population — not a filtered or "recommended
bets" subset. For comparison, the same 5 archived board files also
contain 4,544 rows later excluded as `GAME_STARTED` (game already
underway by generation time — a safety exclusion, not a failure), 905
`LINEUP_UNCONFIRMED`, 30 `MODEL_ERROR`, and 14
`PLAYER_NOT_IN_STARTING_LINEUP` — 7,365 rows total, of which only 25.4%
were ever actually priced.

**The hitter projection engine has only ever been run 5 times, ever**,
each one a manual `workflow_dispatch` of
`.github/workflows/kalshi-price-check.yml`
(`scripts/run_standalone_hitter_research.py`) — it has never been on a
schedule and is not part of the daily production pipeline at all
(`docs/PROJECTION_BOARD.md`: "Hitter props remain untouched/out of
scope"; `ModelEvaluation.evaluationStatus` itself documents
`NO_MODEL_SUPPORT ... e.g. a player prop"`). The 5 archived dates:
2026-08-13, 08-14, 08-15, 08-16, 08-17.

| Date | Total rows | PROJECTED | Notes |
|---|---:|---:|---|
| 2026-08-13 | 576 | 291 | Late-day rerun — 257 rows `GAME_STARTED` |
| 2026-08-14 | 552 | 89 | Early run — 447 `LINEUP_UNCONFIRMED` |
| 2026-08-15 | 2,525 | 504 | Ran just after UTC midnight — 2,021 `GAME_STARTED` |
| 2026-08-16 | 3,341 | 988 | Best-covered run — 2,266 `GAME_STARTED` |
| 2026-08-17 | 371 | **0** | Ran too early (15:51 UTC) — 100% `LINEUP_UNCONFIRMED` |

Only 4 market families were ever priced (confirmed real Kalshi series
this engine supports, per `docs/HITTER_SIMULATION_ENGINE.md` §3):
`hitter_hits` (1,697 rows all statuses), `hitter_total_bases` (2,055),
`hitter_hits_runs_rbis` (2,523), `hitter_rbis` (1,090). **No home run,
runs-scored, or hitter-strikeout Kalshi market exists in this
repository's confirmed series catalogue** — the task brief's requested
breakdown by those families is not applicable; this repo has
independently audited and confirmed no such markets are tradable
(`docs/HITTER_SIMULATION_ENGINE.md` §3). `hitter_stolen_bases` markets
exist and are observed but are explicitly out of the engine's pricing
scope (never `PROJECTED`).

## 2. How many are truly prospective and settlement-resolvable?

- **1,783 of 1,872** (95.2%) join to a real settled outcome
  (YES/NO) in the existing production settlement pipeline
  (`data/edgelab/settlements/<date>.jsonl`) by exact `marketTicker`.
- **89** (all from 2026-08-14) are **UNRESOLVED** — the settlement
  pipeline has simply never been run for that date
  (`data/edgelab/settlements/2026-08-14.jsonl` does not exist). This is
  a genuine, closeable data-completeness gap, not an engine defect.
- **0** excluded for prospective-integrity reasons. Every `PROJECTED`
  row's own `projectionGeneratedAt` was verified to fall at-or-after its
  own `marketObservedAt` (the exact market snapshot it was priced
  against), within that run's own measured duration — see §"Provenance
  / leakage checks" below. **1,872/1,872 rows pass this check.**

**Primary-metric population for every calibration/ROI/CLV number below:
N = 1,783.**

### Provenance / leakage checks

Every row's `projectionStatus == "PROJECTED"` already implies the board
builder itself refused to price it once its game had started (reuses
`lib.edgelab.checkpoints.classify_checkpoint`'s `POST_START`
determination — `docs/HITTER_SIMULATION_ENGINE.md` §16). This audit adds
one further, independent check: does `projectionGeneratedAt` fall
shortly after (never before) the row's own `marketObservedAt`, within
that board run's own measured `elapsedSeconds` (never a fixed guessed
window — see module docstring for why a fixed window would
misclassify legitimate late-in-a-long-run hitters)? **Result: 1,872/1,872
pass.** No row was excluded from primary metrics for provenance reasons
in this audit; `provenance_audit.json` carries the full machinery for
future runs where this may not hold.

**A real data-hygiene bug was found and is reported, not silently
patched**: the 2026-08-15 board's `sourceCapturePath` filenames all
embed the date `2026-08-15`, but the market snapshot's own real capture
timestamp (`marketObservedAt`) is `2026-08-16T00:39-00:40Z` — the
standalone run was invoked with `--date 2026-08-15` shortly after UTC
midnight, and the snapshot filename reflects the requested slate date,
not the wall-clock capture date. This affects all 504 rows from that
date. It does **not** indicate leakage (the underlying timestamps are
still genuinely pregame and self-consistent) but it does mean **a
filename-based date parse of this archive is unreliable** — a concrete
finding for whoever builds a canonical automated settlement/backfill
system next. See `provenanceAudit.snapshotFilenameDateMismatch` in
`provenance_audit.json`.

## 3. How well calibrated are they overall?

| Metric | Value |
|---|---|
| N (resolved, verified-prospective) | 1,783 |
| Avg. predicted probability | 25.53% |
| Actual win rate | 24.73% |
| Calibration error (actual − predicted) | **−0.80 pp** |
| Brier score | 0.1592 |
| Log loss | 0.4929 |
| Actual win rate 95% CI | [22.79%, 26.79%] |

**In aggregate the engine looks well calibrated** — the −0.8pp error is
small and the 95% CI comfortably contains the predicted average. **This
aggregate number is misleading on its own** — see the bucket breakdown
below, which shows the good aggregate number is a blend of a large,
genuinely well-calibrated low-probability bucket and a smaller,
**consistently overconfident** set of higher-probability buckets that
happen to roughly cancel out in the overall average.

## 4. Probability-bucket calibration

| Bucket | N | Status | Avg. predicted | Actual win rate | Calib. error | 95% CI |
|---|---:|---|---:|---:|---:|---|
| <35% | 1,327 | **CALIBRATED** | 14.58% | 16.05% | **+1.47pp** | [14.2%, 18.1%] |
| 35–39.9% | 70 | DESCRIPTIVE_ONLY | 37.39% | 31.43% | −5.96pp | [21.8%, 43.0%] |
| 40–44.9% | 68 | DESCRIPTIVE_ONLY | 42.22% | 38.24% | −3.98pp | [27.6%, 50.1%] |
| 45–49.9% | 49 | DESCRIPTIVE_ONLY | 47.12% | 42.86% | −4.26pp | [30.0%, 56.7%] |
| 50–54.9% | 18 | INSUFFICIENT_SAMPLE | 52.52% | 22.22% | −30.30pp | [9.0%, 45.2%] |
| 55–59.9% | 9 | INSUFFICIENT_SAMPLE | 58.51% | 66.67% | +8.16pp | [35.4%, 87.9%] |
| 60–64.9% | 40 | DESCRIPTIVE_ONLY | 63.29% | 57.50% | −5.79pp | [42.2%, 71.5%] |
| 65–69.9% | 86 | DESCRIPTIVE_ONLY | 67.62% | 59.30% | −8.32pp | [48.7%, 69.1%] |
| 70–74.9% | 79 | DESCRIPTIVE_ONLY | 72.15% | 64.56% | −7.59pp | [53.6%, 74.2%] |
| 75%+ | 37 | DESCRIPTIVE_ONLY | 76.92% | 64.86% | −12.06pp | [48.8%, 78.2%] |

**Pattern**: the <35% bucket (75% of the whole sample, the only bucket
with enough volume to reach `CALIBRATED` status) is slightly
*underconfident* (+1.5pp — the model says these are unlikely and they're
even less likely than that). **Every bucket from 35% up shows a
negative calibration error**, and the size of that overconfidence
generally *grows* toward the top of the range (−8.3pp at 65–69.9%,
−12.1pp at 75%+, both `DESCRIPTIVE_ONLY` — real but not yet a
statistically airtight claim at n=86/n=37). This is a consistent,
directional pattern across 6 consecutive buckets (35% through 75%+, ex.
the two noisy n<20 buckets), not a one-off. **The engine's higher
declared-confidence predictions are the ones least trustworthy.**

## 5. Market family — strongest/weakest

| Family | N | Status | Calib. error | Brier | Simulated ROI | Avg CLV (¢) | % positive CLV |
|---|---:|---|---:|---:|---:|---:|---:|
| `hitter_total_bases` | 495 | CALIBRATED | +1.90pp | 0.1548 | **+7.87%** | +0.12 | 57.1% |
| `hitter_rbis` | 269 | CALIBRATED | +3.18pp | 0.1303 | −10.54% | +0.73 (n=68, DESCRIPTIVE_ONLY) | 91.2% |
| `hitter_hits_runs_rbis` | 618 | CALIBRATED | −1.98pp | 0.1821 | −7.29% | −0.03 | 48.1% |
| `hitter_hits` | 401 | CALIBRATED | −4.96pp | 0.1487 | **+6.29%** | −0.40 | 13.6% |

**Strongest**: `hitter_total_bases` — smallest calibration error of the
four, positive ROI, positive CLV, majority-positive CLV rate. Not a
resounding win, but the most internally consistent family.

**Weakest**: `hitter_rbis` — worst ROI (−10.5%) despite being the
best-calibrated by raw error (+3.2pp) and having the best CLV of any
family (+0.73¢, 91% positive, though CLV coverage here is thin — only
68 of 269 rows had a genuine T-minus-X checkpoint to compare against,
`DESCRIPTIVE_ONLY`). The ROI/CLV divergence for this family is worth a
closer look before trusting either number in isolation.

`hitter_hits` is the most overconfident family (−4.96pp) yet still
posted positive simulated ROI (+6.29%) — a reminder that calibration
error and ROI are not the same question; a family can be overconfident
in its stated probability and still profitable if its entry prices are
cheap enough relative to the (overstated but still directionally
correct) edge.

## 6. Do higher projected edges actually produce better outcomes?

**No — the opposite.** Edge-bucket calibration and simulated ROI
(`roi_by_edge_bucket.json`), by |model probability − executable price|:

| Edge bucket | N | Status | Calib. error | Brier | Simulated ROI |
|---|---:|---|---:|---:|---:|
| 0–2pp | 484 | CALIBRATED | +1.64pp | 0.1246 | **+11.01%** |
| 2–5pp | 609 | CALIBRATED | +1.38pp | 0.1483 | **+11.02%** |
| 5–10pp | 480 | CALIBRATED | −4.60pp | 0.1740 | **−14.62%** |
| 10–20pp | 204 | CALIBRATED | −4.01pp | 0.2343 | **−28.28%** |
| 20pp+ | 6 | INSUFFICIENT_SAMPLE | −3.70pp | 0.3121 | −24.44% |

This is the single clearest finding in this audit. **The smallest
declared edges (0–5pp) are the only ones that were actually profitable
in simulation, and they were the best-calibrated bucket too.** Every
bucket at 5pp of declared edge or higher was **calibration-negative
(overconfident) and lost money**, monotonically worsening through
10–20pp (−28.3% simulated ROI on 204 qualifying bets, a real sample,
not noise). A larger declared edge is, in this archive, an *anti*-signal
for both calibration and profitability — almost certainly because the
model's own probability estimate, not the market's price, is the
unreliable side of a large edge. **Recommendation: do not size or
prioritize by declared edge magnitude in this engine's current state —
see Recommendations.**

## 7. Does positive projected edge translate into positive CLV?

**No clear relationship** (`clv_summary.json`, `byEdgeBucket`):

| Edge bucket | N (CLV available) | Avg CLV (¢) | % positive |
|---|---:|---:|---:|
| 0–2pp | 263 | +0.04 | 50.2% |
| 2–5pp | 306 | +0.01 | 47.1% |
| 5–10pp | 226 | −0.04 | 43.4% |
| 10–20pp | 106 | −0.15 | 35.9% |
| 20pp+ | 6 | −0.08 (n too small) | 33.3% |

Overall CLV is essentially flat (avg **−0.015¢**, median −0.5¢, only
**45.6%** of resolved bets showed positive CLV — slightly *below* a coin
flip, on 907 rows with a genuine closing-checkpoint comparison
available — see CLV methodology note below). CLV does not improve with
larger declared edge; if anything it degrades in the same direction as
the ROI finding above (10–20pp shows the worst CLV of any edge bucket).
**There is no evidence in this archive that the engine's declared edge
identifies value the market hasn't already priced in.**

*CLV methodology note*: only pregame `T_MINUS_X` (X∈{5,15,30,60,90})
checkpoints already captured by the existing settlement pipeline's
`hypotheticalReturnsByCheckpoint` are used as the "closing" reference —
`FIRST_DAILY` and `LINEUP_CONFIRMATION` were deliberately excluded (a
correction made during this audit's own development — see module
docstring) because `FIRST_DAILY` is by construction the *earliest*
capture of the day and is very likely to *precede* this engine's own
entry, which only exists once the lineup is confirmed; using it as a
"closing" reference risks silently reversing the sign of the CLV
calculation. This leaves **50.9% CLV coverage** (907/1,783) — for the
other 49.1%, no genuine closer-to-game quote was ever captured for that
exact ticker, and CLV is honestly reported `null`, never guessed.

## 8. Does positive projected edge translate into positive simulated ROI?

**No — see §6.** Overall simulated one-unit flat-stake, fee-adjusted ROI
(`lib.edgelab.kalshi_fees.net_settlement_pl_fee_only`, Tier B fee-only,
no double-counted or zeroed-out fees, no integer-contract rounding
noise):

| Metric | Value |
|---|---|
| Qualifying bets (nonzero edge, resolved) | 1,781 |
| Gross win rate (of the model's own chosen side) | 53.28% |
| Avg. entry price | 0.5344 |
| Fee-adjusted break-even (avg.) | 0.5450 |
| Net P/L (one-unit stakes) | **−9.19 units** |
| **ROI** | **−0.52%** |
| Avg. \|edge\| | 4.87pp |
| Median \|edge\| | 3.90pp |
| Max drawdown | −127.92 units |

Overall simulated ROI is essentially breakeven-to-slightly-negative.
Per §6, this is a blend of a strongly profitable low-edge cohort and a
strongly unprofitable higher-edge cohort — **not** evidence the engine's
edge signal is worthless everywhere, but clear evidence it should not be
trusted at face value across its full declared range.

## 9. Are tail thresholds overconfident?

**Yes, directionally** — by threshold (`roi_by_threshold.json`,
`calibration_by_threshold.json`):

| Family | Threshold | N | Win rate | ROI |
|---|---:|---:|---:|---:|
| hitter_hits | 1+ | 125 | 56.8% | −0.1% |
| hitter_hits | 2+ | 125 | 25.6% | −11.0% |
| hitter_hits | 3+ | 125 | 12.8% | **+44.9%** |
| hitter_hits | 4+ | 25 (DESCRIPTIVE) | 36.0% | −68.1% |
| hitter_total_bases | 5+ | 97 (DESCRIPTIVE) | 66.0% | **+58.6%** |
| hitter_total_bases | 6+ | 23 (DESCRIPTIVE) | 82.6% | −13.0% |
| hitter_rbis | 3+ | 21 (DESCRIPTIVE) | 95.2% | +0.2% |

The pattern is **not** a clean monotonic "every extreme threshold is
overconfident" story — `hitter_hits 3+` (a genuine tail line, n=125,
`CALIBRATED`) shows a large *positive* simulated ROI, while `hitter_hits
4+` (an even more extreme tail, but only n=25) shows a severe loss.
Given the small N on the most extreme rungs (4+ hits, 6+ total bases,
3+ RBIs all sit at n=21–25, `DESCRIPTIVE_ONLY`), **this audit cannot
make a confident tail-specific overconfidence claim at the individual-
threshold level** — the clearer, statistically supported overconfidence
signal is the probability-bucket finding in §4 (which pools across
thresholds and families and reaches `CALIBRATED` status for the pattern
as a whole), not any single extreme threshold line.

## 10. Monotonicity / ladder quality

**Zero violations.** 526 hitter/game/market-family "ladders" (2+
thresholds priced by the same simulation run for the same hitter) were
checked; every one is non-increasing in threshold (`P(stat≥N+1) ≤
P(stat≥N)`, exactly as `hitter_market_distributions.run_invariant_checks()`
already enforces at generation time). Zero flat ladders either. This is
a genuine positive finding: **the engine's internal probability ladder
logic is sound** — the calibration and edge problems found in this
audit are about the *absolute level* of the probabilities, not their
internal consistency with each other.

## 11. Which snapshot timing performs best?

**This cannot be measured for the hitter engine as currently
operated.** The requested T-90/T-60/T-30/LINEUP_CONFIRMATION/CLOSING
checkpoint framework exists in this repository (`lib/edgelab/prospective_snapshot.py`,
`lib.edgelab.checkpoints`) but is **only wired to the game-level
`ModelEvaluation` pipeline**, which explicitly has `NO_MODEL_SUPPORT`
for player props. The hitter engine is a **single ad hoc snapshot per
manual run** (`docs/HITTER_SIMULATION_ENGINE.md` §15) — it has never
produced multiple checkpoints for the same game on the same day, so
there is no within-day timing variation to compare.

The closest available proxy is **which calendar run (date) performed
best** (`segmentation.json`, `snapshotSourceDate`):

| Date | N | Calib. error | ROI |
|---|---:|---:|---:|
| 2026-08-13 | 291 | +4.33pp | +2.00% |
| 2026-08-15 | 504 | **−7.57pp** | **−16.42%** |
| 2026-08-16 | 988 | +1.15pp | +6.82% |

2026-08-15 is a clear underperformer on both axes. With only 3 usable
calendar dates total, **this cannot be distinguished from ordinary
day-to-day / slate-to-slate variance** — 3 dates is not enough to claim
a systematic "which run-time is best" finding, and this audit does not
make one. This is listed under Data Gaps, not Findings.

## 12. What data gaps prevent stronger conclusions?

1. **Only 5 archived board dates ever exist, and only 3 are usable**
   (08-14 has no settlement file yet; 08-17 produced zero PROJECTED
   rows). All research above rests on **N=1,783 rows drawn from just 3
   calendar dates** (08-13, 08-15, 08-16). Per this repository's own
   documented discipline elsewhere (`docs/EDGELAB_PROSPECTIVE_MODEL_SNAPSHOTS.md`
   §9: "the independent-evidence denominator stays ~18.5 games/day, not
   [row count]"), the *independent* evidence here is closer to **3
   calendar dates / a few dozen games**, not 1,783 independent trials —
   many rows on the same date share the same underlying game outcomes
   (multiple thresholds per hitter, multiple hitters per game). Every
   `CALIBRATED`-status number in this report should be read with that
   caveat.
2. **2026-08-14 has zero settlement coverage.** 89 real prospective
   projections exist with no way to grade them yet — a closeable gap
   (rerun the existing `scripts/edgelab/settle_markets.py` /
   `scripts/edgelab/backfill_player_prop_settlement.py` for that date).
3. **The hitter engine has never run on a schedule.** It is invoked
   only via manual `workflow_dispatch`, so there is no way to grow this
   archive without a human (or a new scheduled workflow) re-triggering
   it — see Recommendations.
4. **CLV coverage is only 50.9%** — for the other half of resolved
   rows, only a `FIRST_DAILY` (excluded as unreliable, see §7) or no
   checkpoint at all was ever captured. A ticket-level bid/ask history
   (rather than the coarse checkpoint labels this audit reused) would
   materially improve CLV precision.
5. **Handedness segmentation is not measurable.** `playerIdentity.batSide`
   and `platoonContext.opposingStarterHand` are populated as `null` for
   effectively every hitter in the real archived feature-board data —
   confirmed by direct inspection, not assumed. This audit reports
   `lineupSlot` and `offenseSide` (both populated) instead; a genuine
   handedness/platoon breakdown is not currently possible from this
   repository's own captured data.
6. **Park-factor and starting-pitcher-quality segmentation are not
   measurable** for the same reason — `parkContext`/`starterContext`
   are `MISSING_DATA`/mostly-null on essentially every real archived
   hitter-feature record inspected during this audit.
7. **A snapshot filename can lie about its own capture date** (§2's
   2026-08-15 finding) — any future tooling that infers a date from
   `sourceCapturePath`'s filename instead of `marketObservedAt` will be
   silently wrong for that date.

## 13. Is the system ready for real-money hitter recommendations yet?

**No — see Recommendation Framework in `recommendations.md`.**
Classification: **B — RESEARCH-ONLY WITH PROMISING SUBSETS.**

---

## Artifact index

| File | Contents |
|---|---|
| `summary.json` | Machine-readable version of this document's headline numbers |
| `summary.md` | This document |
| `recommendations.md` | Readiness classification + concrete next steps (no implementation) |
| `graded_projections.jsonl` | Every one of the 1,872 PROJECTED rows, joined to settlement + CLV + provenance (one JSON object per line) |
| `calibration_by_bucket.json` | §4 |
| `calibration_by_market.json` | §5 (calibration half) |
| `calibration_by_threshold.json` | §9 (calibration half) |
| `roi_by_market.json` | §5 (ROI half) |
| `roi_by_threshold.json` | §9 (ROI half) |
| `roi_by_probability_bucket.json` | Simulated ROI cut by predicted-probability bucket |
| `roi_by_edge_bucket.json` | §6/§8 |
| `clv_summary.json` | §7 |
| `monotonicity_violations.json` | §10 |
| `segmentation.json` | Lineup slot / home-away / date splits (§11 and beyond) |
| `unresolved_records.json` | The 89 unresolved rows + sample detail |
| `provenance_audit.json` | Full row-status/date/family breakdown, provenance-confidence counts, the filename-date-mismatch finding |
