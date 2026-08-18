# Hitter Projection Engine — Retrospective Grading & Calibration Audit

**Updated 2026-08-18 (post-merge, post-settlement rerun)**: the 89
2026-08-14 projections that were unresolved when this document was
first written are now settled (real GitHub Actions run, MLB Stats API
network access — `data/edgelab/settlements/2026-08-14.jsonl`, 3ae85027).
Every number in this document has been regenerated against the full,
now-complete N=1,872 corpus (`scripts/research/build_hitter_projection_audit.py`,
history snapshot `history/2026-08-18T215241/`). **The headline
conclusion is unchanged (still Classification B, still a 0–5pp-edge/
5pp+-anti-signal pattern, still `hitter_total_bases` strongest) — but
2026-08-14 itself is the worst-performing date in the archive by a wide
margin (§11), and it pulls several aggregate numbers (overall ROI,
overall calibration error, the <35%-bucket ROI) meaningfully in the
negative direction. See the "What changed after settling 2026-08-14"
callout at the end of this document for the full before/after
comparison.**

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

- **1,872 of 1,872** (100%) now join to a real settled outcome
  (YES/NO) in the existing production settlement pipeline
  (`data/edgelab/settlements/<date>.jsonl`) by exact `marketTicker`.
  This includes the **89 2026-08-14 rows**, settled 2026-08-18 by
  running the existing, unmodified `scripts/edgelab/settle_markets.py`
  via `.github/workflows/edgelab-postgame.yml` in the real GitHub
  Actions environment (network access to `statsapi.mlb.com` was
  policy-blocked in the sandbox this audit was originally built in —
  see the "What changed" callout at the end of this document). All 89
  resolved deterministically from real MLB boxscore evidence: 9 YES /
  80 NO, with full `settlementEvidence` (gamePk, participation, actual
  stat value) on every row — 0 fabricated, 0 hard-coded, 0 still
  unresolved.
- **0** excluded for prospective-integrity reasons. Every `PROJECTED`
  row's own `projectionGeneratedAt` was verified to fall at-or-after its
  own `marketObservedAt` (the exact market snapshot it was priced
  against), within that run's own measured duration — see §"Provenance
  / leakage checks" below. **1,872/1,872 rows pass this check.**

**Primary-metric population for every calibration/ROI/CLV number below:
N = 1,872 (was 1,783 before the 2026-08-14 settlement).**

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

| Metric | Value | Before 08-14 settlement |
|---|---|---|
| N (resolved, verified-prospective) | 1,872 | 1,783 |
| Avg. predicted probability | 25.58% | 25.53% |
| Actual win rate | 24.04% | 24.73% |
| Calibration error (actual − predicted) | **−1.54 pp** | −0.80pp |
| Brier score | 0.1562 | 0.1592 |
| Log loss | 0.4847 | 0.4929 |
| Actual win rate 95% CI | [22.16%, 26.03%] | [22.79%, 26.79%] |

**In aggregate the engine still looks reasonably calibrated** — the
−1.54pp error is larger than before (2026-08-14 was a poorly-calibrated
date, §11) but still small and inside the 95% CI. Brier score and log
loss actually *improved* slightly (more resolved rows, less sampling
noise). **This aggregate number is still misleading on its own** — see
the bucket breakdown below, which shows the reasonable aggregate number
is a blend of a large, genuinely well-calibrated low-probability bucket
and a smaller, **consistently overconfident** set of higher-probability
buckets that happen to roughly cancel out in the overall average.

## 4. Probability-bucket calibration

| Bucket | N | Status | Avg. predicted | Actual win rate | Calib. error | 95% CI |
|---|---:|---|---:|---:|---:|---|
| <35% | 1,395 | **CALIBRATED** | 14.62% | 15.34% | **+0.72pp** | [13.5%, 17.3%] |
| 35–39.9% | 71 | DESCRIPTIVE_ONLY | 37.43% | 30.99% | −6.44pp | [21.4%, 42.5%] |
| 40–44.9% | 69 | DESCRIPTIVE_ONLY | 42.19% | 37.68% | −4.51pp | [27.2%, 49.5%] |
| 45–49.9% | 50 | DESCRIPTIVE_ONLY | 47.14% | 42.00% | −5.14pp | [29.4%, 55.8%] |
| 50–54.9% | 18 | INSUFFICIENT_SAMPLE | 52.52% | 22.22% | −30.30pp | [9.0%, 45.2%] |
| 55–59.9% | 10 | INSUFFICIENT_SAMPLE | 58.53% | 60.00% | +1.47pp | [31.3%, 83.2%] |
| 60–64.9% | 46 | DESCRIPTIVE_ONLY | 63.21% | 58.70% | −4.51pp | [44.3%, 71.7%] |
| 65–69.9% | 92 | DESCRIPTIVE_ONLY | 67.59% | 56.52% | −11.07pp | [46.3%, 66.2%] |
| 70–74.9% | 83 | DESCRIPTIVE_ONLY | 72.16% | 63.86% | −8.30pp | [53.1%, 73.4%] |
| 75%+ | 38 | DESCRIPTIVE_ONLY | 76.87% | 65.79% | −11.08pp | [49.9%, 78.8%] |

**Pattern holds, essentially unchanged**: the <35% bucket (75% of the
whole sample, the only bucket with enough volume to reach `CALIBRATED`
status) is now slightly *underconfident* by a smaller margin than
before (+0.7pp, was +1.5pp — the extra 2026-08-14 rows pulled this
bucket's calibration error toward zero from above, not away from it).
**Every bucket from 35% up still shows a negative calibration error**,
and the size of that overconfidence still generally *grows* toward the
top of the range (−8.3pp at 70–74.9%, −11.1pp at 75%+, both
`DESCRIPTIVE_ONLY`). This is still a consistent, directional pattern
across the same buckets as before, not a one-off, and settling
2026-08-14 did not change it. **The engine's higher declared-confidence
predictions remain the ones least trustworthy.**

## 5. Market family — strongest/weakest

| Family | N | Status | Calib. error | Brier | Simulated ROI | Avg CLV (¢) | % positive CLV |
|---|---:|---|---:|---:|---:|---:|---:|
| `hitter_total_bases` | 524 | CALIBRATED | +0.86pp | 0.1485 | **+5.98%** | +0.12 | 57.1% |
| `hitter_rbis` | 271 | CALIBRATED | +3.05pp | 0.1295 | −10.25% | +0.73 (n=68, DESCRIPTIVE_ONLY) | 91.2% |
| `hitter_hits_runs_rbis` | 623 | CALIBRATED | −2.24pp | 0.1821 | −7.31% | −0.03 | 48.1% |
| `hitter_hits` | 454 | CALIBRATED | −6.10pp | 0.1454 | **−1.39%** | −0.40 | 13.6% |

**Strongest, and now the *only* clearly positive-ROI family**:
`hitter_total_bases` — smallest calibration error of the four, still
positive ROI, positive CLV, majority-positive CLV rate. This is
unchanged from before settlement and remains the most internally
consistent family.

**Weakest by ROI, still**: `hitter_rbis` — worst ROI (−10.3%) despite
being the best-calibrated by raw error (+3.1pp) and having the best CLV
of any family (+0.73¢, 91% positive, `DESCRIPTIVE_ONLY`, coverage
unchanged at 68/271). The ROI/CLV divergence for this family persists
and is still worth a closer look before trusting either number in
isolation.

**Materially changed finding**: `hitter_hits` was reported as
profitable (+6.3% ROI) before the 2026-08-14 settlement; it is now
**slightly net-negative (−1.4% ROI)** and its calibration error
worsened from −5.0pp to −6.1pp — the newly-settled 2026-08-14 rows in
this family skewed unfavorably. **Only `hitter_total_bases` now shows
positive simulated ROI** among the four families (previously two of
four did). This is a real change in the evidence, not a rounding
artifact — see the "What changed" callout below.

## 6. Do higher projected edges actually produce better outcomes?

**No — the opposite.** Edge-bucket calibration and simulated ROI
(`roi_by_edge_bucket.json`), by |model probability − executable price|:

| Edge bucket | N | Status | Calib. error | Brier | Simulated ROI |
|---|---:|---|---:|---:|---:|
| 0–2pp | 510 | CALIBRATED | +0.95pp | 0.1201 | **+8.01%** |
| 2–5pp | 637 | CALIBRATED | +0.60pp | 0.1466 | **+8.29%** |
| 5–10pp | 498 | CALIBRATED | −4.97pp | 0.1721 | **−15.53%** |
| 10–20pp | 216 | CALIBRATED | −5.37pp | 0.2307 | **−30.33%** |
| 20pp+ | 11 | INSUFFICIENT_SAMPLE | −10.96pp | 0.1958 | +10.58% |

This remains the single clearest finding in this audit, **unchanged in
direction and, if anything, slightly sharper after settlement**. The
smallest declared edges (0–5pp) are still the only ones that were
actually profitable in simulation (+8.0% and +8.3%, down modestly from
+11.0%/+11.0% before settlement but still clearly positive and still
the best-calibrated buckets). Every bucket at 5pp of declared edge or
higher is still **calibration-negative (overconfident) and lost
money**, and 10–20pp got *worse* (−30.3% simulated ROI, was −28.3%, now
on 216 qualifying bets). The 20pp+ bucket flipped to a nominally
positive ROI, but stayed `INSUFFICIENT_SAMPLE` at n=11 — **this is not
a reversal of the finding, it is noise on a tiny cell, and is reported
here only so it is not silently omitted; it does not affect the
5–20pp conclusion, which rests on 498+216=714 real qualifying bets.**
A larger declared edge remains, in this archive, an *anti*-signal for
both calibration and profitability — almost certainly because the
model's own probability estimate, not the market's price, is the
unreliable side of a large edge. **Recommendation unchanged: do not
size or prioritize by declared edge magnitude in this engine's current
state — see Recommendations.**

## 7. Does positive projected edge translate into positive CLV?

**No clear relationship** (`clv_summary.json`, `byEdgeBucket`):

| Edge bucket | N (CLV available) | Avg CLV (¢) | % positive |
|---|---:|---:|---:|
| 0–2pp | 263 | +0.04 | 50.2% |
| 2–5pp | 306 | +0.01 | 47.1% |
| 5–10pp | 226 | −0.04 | 43.4% |
| 10–20pp | 106 | −0.15 | 35.9% |
| 20pp+ | 6 | −0.08 (n too small) | 30.0% |

(The 2026-08-14 settlement did not add new CLV-eligible rows — CLV
requires a genuine pregame `T_MINUS_X` checkpoint capture, which this
date never had, so these figures are unchanged from before settlement.)
Overall CLV is essentially flat (avg **−0.015¢**, median −0.5¢, only
**45.6%** of resolved bets showed positive CLV — slightly *below* a coin
flip, on 907 rows with a genuine closing-checkpoint comparison
available, now **48.5%** coverage of the larger 1,872-row corpus (was
50.9% of 1,783) — see CLV methodology note below). CLV does not improve
with larger declared edge; if anything it degrades in the same
direction as the ROI finding above (10–20pp shows the worst CLV of any
edge bucket). **There is no evidence in this archive that the engine's
declared edge identifies value the market hasn't already priced in.**

*CLV methodology note*: only pregame `T_MINUS_X` (X∈{5,15,30,60,90})
checkpoints already captured by the existing settlement pipeline's
`hypotheticalReturnsByCheckpoint` are used as the "closing" reference —
`FIRST_DAILY` and `LINEUP_CONFIRMATION` were deliberately excluded (a
correction made during this audit's own development — see module
docstring) because `FIRST_DAILY` is by construction the *earliest*
capture of the day and is very likely to *precede* this engine's own
entry, which only exists once the lineup is confirmed; using it as a
"closing" reference risks silently reversing the sign of the CLV
calculation. This leaves **48.5% CLV coverage** (907/1,872, was 907/1,783 — the
2026-08-14 rows added to the denominator but none had a T-minus-X
checkpoint capture, so the numerator is unchanged) — for the
remaining 51.5%, no genuine closer-to-game quote was ever captured for
that exact ticker, and CLV is honestly reported `null`, never guessed.

## 8. Does positive projected edge translate into positive simulated ROI?

**No — see §6.** Overall simulated one-unit flat-stake, fee-adjusted ROI
(`lib.edgelab.kalshi_fees.net_settlement_pl_fee_only`, Tier B fee-only,
no double-counted or zeroed-out fees, no integer-contract rounding
noise):

| Metric | Value | Before 08-14 settlement |
|---|---|---|
| Qualifying bets (nonzero edge, resolved) | 1,870 | 1,781 |
| Gross win rate (of the model's own chosen side) | 52.57% | 53.28% |
| Avg. entry price | 0.5273 | 0.5344 |
| Fee-adjusted break-even (avg.) | 0.5378 | 0.5450 |
| Net P/L (one-unit stakes) | **−48.18 units** | −9.19 units |
| **ROI** | **−2.58%** | −0.52% |
| Avg. \|edge\| | 4.94pp | 4.87pp |
| Median \|edge\| | 3.88pp | 3.90pp |
| Max drawdown | −166.91 units | −127.92 units |

Overall simulated ROI is now clearly negative, not
breakeven-to-slightly-negative — the 89 newly-settled 2026-08-14 bets
lost heavily as a group (§11: −43.8% ROI on that date alone, netPL
−38.99 units), which accounts for most of the swing from −9.19 to
−48.18 units overall. Per §6, this is still a blend of a strongly
profitable low-edge cohort and a strongly unprofitable higher-edge
cohort — **not** evidence the engine's edge signal is worthless
everywhere, but clearer evidence than before that it should not be
trusted at face value across its full declared range, and that overall
aggregate ROI is sensitive to which dates happen to be in the sample.

## 9. Are tail thresholds overconfident?

**Yes, directionally** — by threshold (`roi_by_threshold.json`,
`calibration_by_threshold.json`):

| Family | Threshold | N | Win rate | ROI |
|---|---:|---:|---:|---:|
| hitter_hits | 1+ | 142 | 56.3% | −1.0% |
| hitter_hits | 2+ | 142 | 24.6% | −17.5% |
| hitter_hits | 3+ | 142 | 12.0% | **+27.5%** |
| hitter_hits | 4+ | 27 (DESCRIPTIVE) | 33.3% | −70.9% |
| hitter_total_bases | 5+ | 102 | 67.7% | **+56.9%** |
| hitter_total_bases | 6+ | 23 (DESCRIPTIVE) | 82.6% | −13.0% |
| hitter_rbis | 3+ | 21 (DESCRIPTIVE) | 95.2% | +0.2% |

(2026-08-14 added rows across most family/threshold cells — `hitter_hits
1+/2+/3+/4+` and `hitter_total_bases 5+` all changed N; `hitter_rbis 3+`
and `hitter_total_bases 6+` were unaffected, no 2026-08-14 rows landed
in those specific cells.)

The pattern is still **not** a clean monotonic "every extreme threshold
is overconfident" story — `hitter_hits 3+` (a genuine tail line, n=142,
`CALIBRATED`) still shows a large *positive* simulated ROI, while
`hitter_hits 4+` (an even more extreme tail, still only n=27) still
shows a severe loss. Given the small N on the most extreme rungs (4+
hits, 6+ total bases, 3+ RBIs all still sit at n=21–27,
`DESCRIPTIVE_ONLY`), **this audit still cannot make a confident
tail-specific overconfidence claim at the individual-threshold level**
— the clearer, statistically supported overconfidence signal remains the
probability-bucket finding in §4, not any single extreme threshold
line. This conclusion is unchanged by the 2026-08-14 settlement.

## 10. Monotonicity / ladder quality

**Still zero violations after settlement** (unchanged — monotonicity is
checked against the projection board's own internal probability ladder,
not against settlement outcomes, so this result was never going to
change from settling 2026-08-14; included here for completeness). 526
hitter/game/market-family "ladders" (2+ thresholds priced by the same
simulation run for the same hitter) were checked; every one is
non-increasing in threshold (`P(stat≥N+1) ≤ P(stat≥N)`, exactly as
`hitter_market_distributions.run_invariant_checks()` already enforces at
generation time). Zero flat ladders either. This remains a genuine
positive finding: **the engine's internal probability ladder logic is
sound** — the calibration and edge problems found in this audit are
about the *absolute level* of the probabilities, not their internal
consistency with each other.

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
best** (`segmentation.json`, `snapshotSourceDate`) — now 4 dates instead
of 3, since 2026-08-14 is settled:

| Date | N | Status | Calib. error | ROI |
|---|---:|---|---:|---:|
| 2026-08-13 | 291 | CALIBRATED | +4.33pp | +2.00% |
| 2026-08-14 | 89 | **DESCRIPTIVE_ONLY** | **−16.60pp** | **−43.81%** |
| 2026-08-15 | 504 | CALIBRATED | −7.57pp | −16.42% |
| 2026-08-16 | 988 | CALIBRATED | +1.15pp | +6.82% |

**2026-08-14 is now the clear worst-performing date in the archive**,
on both calibration and ROI, by a wide margin — worse than the
previous worst date (2026-08-15) on both axes. It is the smallest of
the four dates (n=89, below the `CALIBRATED` threshold, so this specific
date's numbers are `DESCRIPTIVE_ONLY`, not a statistically airtight
per-date claim) and it single-handedly accounts for most of the overall
ROI and calibration-error movement documented in §3 and §8. With only 4
usable calendar dates total (was 3), **this still cannot be
distinguished from ordinary day-to-day / slate-to-slate variance** — 4
dates is still not enough to claim a systematic "which run-time is
best" finding, and this audit still does not make one. This remains
listed under Data Gaps, not Findings — but it is worth being explicit
that the newly-added date is not a random draw from "the same
distribution as the other three": it is the single worst date measured
so far, and a future audit with more dates should watch whether
2026-08-14-like days recur or were a one-off.

## 12. What data gaps prevent stronger conclusions?

1. **Only 5 archived board dates ever exist, and only 4 are usable**
   (08-17 produced zero PROJECTED rows; 08-14 is now settled and usable
   as of this update, up from 3 usable dates before). All research above
   rests on **N=1,872 rows drawn from just 4 calendar dates** (08-13,
   08-14, 08-15, 08-16). Per this repository's own documented discipline
   elsewhere (`docs/EDGELAB_PROSPECTIVE_MODEL_SNAPSHOTS.md` §9: "the
   independent-evidence denominator stays ~18.5 games/day, not [row
   count]"), the *independent* evidence here is closer to **4 calendar
   dates / a few dozen games**, not 1,872 independent trials — many rows
   on the same date share the same underlying game outcomes (multiple
   thresholds per hitter, multiple hitters per game). Every
   `CALIBRATED`-status number in this report should be read with that
   caveat. Going from 3 to 4 dates is progress, but 4 remains a small
   sample for any production decision — see Recommendations.
2. ~~**2026-08-14 has zero settlement coverage.**~~ **Closed as of
   2026-08-18**: settled via the existing, unmodified
   `scripts/edgelab/settle_markets.py` (`edgelab-postgame.yml`
   workflow_dispatch, real GitHub Actions network access) — all 89 rows
   now have a real, deterministic settlement outcome (§2).
3. ~~**The hitter engine has never run on a schedule.**~~ **Closed as of
   this PR's merge**: `.github/workflows/hitter-snapshot-scheduler.yml`
   now runs every 15 minutes, 13:00–23:45 UTC + 00:00–05:45 UTC, and
   captures `T_MINUS_90`/`T_MINUS_60`/`T_MINUS_30`/`LINEUP_CONFIRMATION`/
   `HITTER_CLOSING_WINDOW` checkpoints automatically — see
   `docs/HITTER_CHECKPOINT_COVERAGE_FIX.md`. This archive will now grow
   automatically without manual intervention; a future audit rerun
   should have materially more than 4 dates to work with.
4. **CLV coverage is only 48.5%** — for the other half of resolved
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

## What changed after settling 2026-08-14 (before → after)

A direct, explicit before/after comparison, so nothing above requires
cross-referencing to know what moved and by how much:

| Metric | Before (N=1,783, 3 dates) | After (N=1,872, 4 dates) | Direction |
|---|---:|---:|---|
| Unresolved rows | 89 | **0** | closed |
| Overall calibration error | −0.80pp | −1.54pp | worse (still small) |
| Overall Brier score | 0.1592 | 0.1562 | better |
| Overall log loss | 0.4929 | 0.4847 | better |
| Overall ROI | −0.52% | **−2.58%** | worse |
| <35% bucket ROI (roi_by_probability_bucket) | +3.7%\* | +0.98% | weaker, still positive |
| 0–2pp edge ROI | +11.01% | +8.01% | weaker, still strongly positive |
| 2–5pp edge ROI | +11.02% | +8.29% | weaker, still strongly positive |
| 5–10pp edge ROI | −14.62% | −15.53% | worse (anti-signal persists) |
| 10–20pp edge ROI | −28.28% | −30.33% | worse (anti-signal persists) |
| `hitter_total_bases` ROI | +7.87% | +5.98% | weaker, still the strongest family |
| `hitter_hits` ROI | +6.29% | **−1.39%** | **flipped negative** |
| `hitter_rbis` ROI | −10.54% | −10.25% | ~unchanged, still weakest by ROI |
| CLV coverage | 50.9% | 48.5% | lower (more rows, no new CLV data) |
| Monotonicity violations | 0/526 | 0/526 | unchanged |

\* the pre-settlement `roi_by_probability_bucket.json` <35%-bucket
figure was reported as +3.7% in the original `recommendations.md`
draft (drawn from that JSON file's earlier state); it is not repeated
verbatim in §4 of this document, which reports calibration, not ROI,
for that bucket.

**Bottom line**: settling 2026-08-14 did not change the *shape* of any
finding — the 0–5pp-edge-is-profitable / 5pp+-edge-is-an-anti-signal
pattern, the bottom-of-order/longshot weakness (edge-inversion
diagnostic), the `hitter_total_bases`-is-strongest-family pattern, and
the perfect monotonicity all replicated. What changed is **magnitude**:
2026-08-14 was a bad date (§11), so folding it in pulled overall ROI and
the <35%-bucket's ROI down, and flipped `hitter_hits` from
narrowly-profitable to narrowly-unprofitable. None of this is grounds
to upgrade the readiness classification (if anything, it argues harder
for staying conservative) — and per this audit's own instruction, a
sample-size increase alone (89 more rows) is never by itself grounds to
upgrade either. See `recommendations.md` for the updated classification
reasoning.

## 13. Is the system ready for real-money hitter recommendations yet?

**No — see Recommendation Framework in `recommendations.md`.**
Classification: **B — RESEARCH-ONLY WITH PROMISING SUBSETS** (re-evaluated
against the fully-settled 1,872-row corpus; unchanged from before
settlement — see `recommendations.md` for the updated reasoning).

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
| `unresolved_records.json` | Unresolved-row detail — now **0 rows** (was 89, closed 2026-08-18) |
| `provenance_audit.json` | Full row-status/date/family breakdown, provenance-confidence counts, the filename-date-mismatch finding |
