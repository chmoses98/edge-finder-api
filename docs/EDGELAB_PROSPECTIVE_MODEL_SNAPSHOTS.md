# EdgeLab Prospective Model Snapshots milestone

Status: research-only, read-only with respect to production. Stacked on
top of PR #86 ("EdgeLab research trustworthiness"). This milestone does
NOT change model probabilities, projection formulas, production
recommendation logic, bet/tier thresholds, risk gates, bankroll rules,
stake sizing, production market selection, Kalshi live execution, lineup
gates, or any production slate output.

**Reliability/correctness pass (this revision):** after the initial
implementation below, a targeted review found and fixed a real
lineup-confirmation discovery-ordering bug (§3a), a workflow persistence
gap that could silently drop a run's snapshots on an ephemeral runner
(§1a), and closed out several trustworthiness gaps the original design
left implicit: market-price staleness/age (§5a), evaluation-time vs.
input-freshness provenance (§3b), the model-side "closing" checkpoint's
naming collision with PR #86's canonical Kalshi closing quote (§3c), and
honest run-level status (§1b). Every fix below is additive to the
architecture already described in this document — nothing in §0-§9
below was invalidated, only sharpened.

## 0. The problem this solves

PR #86 built a provably causal ("no-look-ahead") join between a
`ModelEvaluation` and a market checkpoint, using
`ModelEvaluation.pipelineRunId` (the production pipeline's own immutable
run timestamp). But the production pipeline
(`scripts/build_market_ledger.py`, invoked by `fetch-slate.yml`) runs
only **once per day**, in the evening — so almost no market checkpoints
were captured *after* that day's single run. Result: of 75,280
historical opportunity rows (13 dates), only 264 (68 games) had a
causally valid model evaluation at all.

**We cannot retroactively manufacture historical predictions.** This
milestone does not backfill the 264/75,280 gap — it builds the
collection system that starts closing it prospectively, from deployment
onward.

## 1. Architecture chosen, and why not "attach model logic to
   capture-snapshots-scheduled.yml"

`capture-snapshots-scheduled.yml` is deliberately minimal: fetch Kalshi
prices, archive, commit — "no model logic, no validation gates, no
pipeline steps" (its own header comment). Bolting model evaluation onto
it would:
- couple two failure domains that must stay independent — a lineup-fetch
  or model-evaluation exception must never risk the one thing that
  workflow exists to guarantee (every real-money bet gets a pre-start
  Kalshi price);
- run needlessly often (every 30 min) for something that doesn't need
  that cadence and would triple the workflow's runtime for no benefit.

Instead, this milestone adds a **new, separate** workflow
(`.github/workflows/model-snapshot-scheduler.yml`) with its own
concurrency group (`edgelab-model-snapshot`), sharing no job, no step, no
concurrency group, and (aside from a read-only read of `data/slate.json`)
no write path with `capture-snapshots-scheduled.yml` or `fetch-slate.yml`.
A failure in one can never block the other — proven structurally in
`tests/edgelab/test_prospective_snapshot.py::test_workflow_files_are_structurally_independent`,
and functionally in `lib/edgelab/prospective_snapshot.py`'s own per-game
exception isolation (one malformed game's inputs never abort the cycle).

### 1a. Workflow persistence: from `continue-on-error` to a visible, recoverable failure

The original version of this workflow set job-level
`continue-on-error: true` and swallowed the commit step's exit code with
`|| echo "Push failed..."` — reasoning that a failure here should never
be allowed to look like a broken CI check. That reasoning was right about
*isolation* but wrong about *visibility*: on GitHub's ephemeral runners,
"will retry next cycle" is not a true recovery path — if the commit/push
ultimately fails after `scripts/ci/git_data_commit.py`'s own bounded
retries (4 attempts, 5/10/15/20s backoff) are exhausted, the runner's
local filesystem (every uncommitted `ModelEvaluation`/`ResearchRunMetadata`
record this run generated) is discarded when the job ends. The next
scheduled run starts from a fresh checkout 15 minutes later — by which
time the exact checkpoint window this run targeted (a specific game's
`T_MINUS_30`, say) may already have closed, so that exact model state can
never be regenerated. A swallowed exit code turned a permanent, silent
data-loss event into an invisible green checkmark.

Fixed: the job no longer sets `continue-on-error`, and the "Commit new
model evaluations" step (`id: commit`) is never followed by `|| echo ...`
or any other exit-code-swallowing pattern. Before that step runs, a "Back
up generated snapshot files" step copies `data/edgelab/model_evaluations`
and `data/edgelab/research_runs` to `/tmp/prospective-snapshot-backup/`
— *outside* the git working tree, and *before* the commit is attempted,
because `scripts/ci/git_data_commit.py`'s own `_abort_and_reset` runs
`git reset --hard origin/<branch>` on a failed push, so anything captured
from the working tree *after* that point would already be the wrong,
reverted content. If the commit step's outcome is `failure`, an
`actions/upload-artifact@v4` step uploads that backup
(`prospective-model-snapshot-unpersisted-<run-id>`, 14-day retention) and
a final step emits an `::error::` annotation and `exit 1`, failing the
job visibly. Net effect: the exact generated snapshot data for a run is
now always either (a) committed, or (b) recoverable from that run's own
artifact — never silently discarded — while a failure here still shares
no job, step, or concurrency group with `capture-snapshots-scheduled.yml`
or `fetch-slate.yml`, so it can never block or slow either of those.
Regression coverage: `tests/edgelab/test_prospective_snapshot.py::test_workflow_job_has_no_continue_on_error`,
`test_workflow_commit_step_never_swallows_failure_with_bare_or_echo`,
`test_workflow_commit_step_uses_canonical_git_commit_script`,
`test_workflow_backs_up_generated_files_before_attempting_commit`,
`test_workflow_uploads_artifact_on_persistence_failure`,
`test_workflow_fails_visibly_when_persistence_fails`.

### 1b. Honest run-level status

The run-metadata record (`data/edgelab/research_runs/<date>.jsonl`,
`runType: "PROSPECTIVE_SNAPSHOT"`) previously hardcoded `"status": "success"`
unconditionally — a run that hit real per-game failures (e.g.
`evaluate_game` raising) still reported success as long as the script
reached its end. Fixed via `scripts/edgelab/run_prospective_snapshots.py`'s
new pure `compute_run_status(evaluated_count, genuine_failure_count)`:
`"no_op"` (nothing was due this cycle — the common steady state),
`"success"` (at least one snapshot captured, zero genuine failures),
`"partial"` (at least one snapshot captured AND at least one genuine
failure), `"failed"` (attempted but nothing succeeded). A per-game SKIP
for an ineligible/not-yet-due reason (`STARTED`, `POSTPONED`,
`NO_CHECKPOINT_DUE`, a failed lineup poll that correctly left a game
unconfirmed) is never counted as a failure here — only
`evaluate_game raised ...` skip reasons are. `research_run.schema.json`'s
`status` enum was additively extended with `"no_op"` (previously
`running`/`success`/`partial`/`failed` only); its `runType` enum was
separately found to be missing `"PROSPECTIVE_SNAPSHOT"` entirely — every
research-run record this system had ever written was schema-invalid —
and fixed additively. `run_record["errors"]` is now populated from the
genuine failures instead of always being `[]`. Tests:
`tests/edgelab/test_run_prospective_snapshots_script.py` (5 tests,
including `test_never_returns_success_merely_because_process_reached_the_end`).

The evaluation logic itself reuses `scripts.build_market_ledger.evaluate_game`
and `compute_game_projection_context` **directly, unmodified** — the
identical reuse pattern `lib/edgelab/replay.py` already established for
exactly this reason ("call the existing production functions rather than
duplicating their math"). There is no second "research model."

## 2. Why inputs are not fully re-fetched every checkpoint

`evaluate_game()`'s own `modelProb` computation depends on projections
(team/pitcher/bullpen/weather), **not** on the current Kalshi price — the
Kalshi price only feeds `evaluate_game()`'s own `kalshiVF`/
`marketProbVF`/`estimatedEdge` fields, which this system does not rely on
for research at all: `lib.edgelab.research_dataset`'s
`contemporaneousEdge` already correctly re-derives edge against each
checkpoint's own contemporaneously-captured `MarketObservation` price,
independent of whatever price `evaluate_game()` itself saw.

So this milestone re-runs `evaluate_game()` against the day's
already-fetched slate context **unchanged** for `T_MINUS_90`/`T_MINUS_60`/
`T_MINUS_30`/`MODEL_CLOSING_WINDOW`, and refreshes **only** the lineup fields (a free
MLB Stats API call, via `scripts.fetch_lineups.fetch_lineup_for_game` +
`compute_game_lineup_stats_fields` — both already-pure, already-safe
functions, called in-memory, never writing `data/slate.json`) for the
`LINEUP_CONFIRMATION` checkpoint specifically. Odds (the-odds-api.com)
and Baseball Savant scraping are both metered/rate-limited services — a
documented 429-flood incident already exists in this repo's history
(`docs/KALSHI_PRICE_CHECKER_STRICT_REGISTRY.md`) — so re-fetching them
4-6x/day/game is a real, avoided cost/risk, not a theoretical one. This
is a deliberate, documented scope limitation: a future milestone may add
odds/weather refresh if the tradeoff proves worthwhile.

## 3. Cadence and checkpoints

- **Cadence**: `model-snapshot-scheduler.yml` runs every 15 minutes,
  16:00–23:45 and 00:00–05:45 UTC (the same MLB window
  `capture-snapshots-scheduled.yml` covers, at half its interval, so each
  target is reliably caught within tolerance).
- **Core checkpoints targeted** (`lib.edgelab.prospective_snapshot.CORE_CHECKPOINTS`):
  `T_MINUS_90`, `T_MINUS_60`, `T_MINUS_30`, `LINEUP_CONFIRMATION`,
  `MODEL_CLOSING_WINDOW` (renamed from the original `"CLOSING"` — see
  §3c below). `T_MINUS_15`/`T_MINUS_5` are deliberately excluded from the
  default set (spec's explicit allowance: "if operationally expensive or
  unreliable, document the tradeoff rather than compromising the
  reliable core system") — a caller may still pass a wider
  `--checkpoints` set.
- **Per-game scheduling**: `determine_due_checkpoint()` reuses
  `lib.edgelab.checkpoints.classify_checkpoint` directly (never a second,
  competing time-bucketing scheme) against the ACTUAL evaluation instant
  (`now`, real wall-clock time each run), never an assumed cron-fired
  time. At most one checkpoint is evaluated per game per cycle, and never
  the same checkpoint twice for the same game
  (`already_captured_checkpoints`, read from that date's already-ingested
  `ModelEvaluation` rows).
- **Lineup confirmation**: no clean webhook/event exists in this repo (a
  fact this milestone's audit confirmed) — lineups are only discoverable
  by polling. `determine_due_checkpoint()` treats `LINEUP_CONFIRMATION`
  as due the moment `awayTeamStats.lineupConfirmedOfficial` and
  `homeTeamStats.lineupConfirmedOfficial` are both first observed true
  (checked every 15-minute cycle), takes priority over a coincidentally
  overlapping `T_MINUS_X` target, and is captured at most once. Only that
  one checkpoint's in-memory game copy gets the refreshed lineup fields —
  every other checkpoint's evaluation uses whatever lineup state was
  already on the base game object, so a later official lineup can never
  leak backward into an earlier evaluation (proven in
  `test_lineup_checkpoint_only_evaluates_with_refreshed_lineup_state`).

### 3a. Fixed: LINEUP_CONFIRMATION discovery-ordering bug

The description above states the intended behavior; the *original*
implementation of `run_prospective_snapshot_cycle()` did not actually
achieve it. `determine_due_checkpoint()` decided whether
`LINEUP_CONFIRMATION` was due by reading `awayTeamStats.lineupConfirmedOfficial`/
`homeTeamStats.lineupConfirmedOfficial` off the **stale, on-disk**
`data/slate.json` game object — and the live lineup poll
(`refresh_lineup_fields`, wrapping `scripts.fetch_lineups.fetch_lineup_for_game`)
was only ever invoked **after** a checkpoint had already been chosen as
due. Since `data/slate.json` is only refreshed by the once-daily
production pipeline, a lineup that became officially confirmed at, say,
2:10pm would never be discovered by this system until the *next day's*
pipeline run rewrote the slate file — `LINEUP_CONFIRMATION` could
essentially never fire on its own from a live poll, defeating the whole
point of the checkpoint.

Fixed in `lib/edgelab/prospective_snapshot.py`'s `run_prospective_snapshot_cycle()`:
for every eligible, not-yet-started game whose `LINEUP_CONFIRMATION`
checkpoint hasn't already been captured, the live lineup poll now runs
**first**, in memory, producing a `lineup_refreshed_game` copy —
`refresh_lineup_fields` never writes `data/slate.json` itself, so
production's own slate file is untouched. Whether the lineup is "newly
confirmed" is computed by comparing confirmation state on the ORIGINAL
game object (before poll) against the REFRESHED copy (after poll).
`determine_due_checkpoint()` is then called against the refreshed copy,
so a lineup that just became confirmed this cycle correctly makes
`LINEUP_CONFIRMATION` due immediately — same cycle it's discovered, not
next-pipeline-run. The evaluation itself uses the refreshed game object
only when the checkpoint being captured IS `LINEUP_CONFIRMATION`; every
other checkpoint (`T_MINUS_90/60/30`, `MODEL_CLOSING_WINDOW`) still
evaluates against the original, unrefreshed game object, so a same-cycle
lineup confirmation can never leak backward into an earlier checkpoint's
snapshot. A lineup-fetch failure is a poll failure only — it is recorded
as a warning and the game is left with whatever confirmation state it
already had; confirmation is never fabricated from an API error. Once
`LINEUP_CONFIRMATION` has been captured for a game, it is never polled
again (checked against `already_captured`).

Every run-log entry (`SKIPPED` and `EVALUATED`) now also carries
`lineupPollAttempted`, `lineupPollFailed`, and `lineupNewlyConfirmed`
booleans, which both `snapshot_coverage_report` (§10) and
`scripts/edgelab/run_prospective_snapshots.py`'s own run-metadata counts
(`lineupPollAttempts`/`lineupPollSuccesses`/`lineupPollFailures`)
aggregate.

Regression tests (`tests/edgelab/test_prospective_snapshot.py`):
`test_stale_slate_discovers_newly_confirmed_lineup_via_live_poll`,
`test_stale_slate_still_unconfirmed_after_live_poll_no_snapshot`,
`test_lineup_api_failure_never_fabricates_confirmation`,
`test_already_captured_lineup_confirmation_does_not_poll_again`,
`test_lineup_refresh_in_memory_never_mutates_slate_game_object`,
`test_t_minus_30_snapshot_keeps_its_own_earlier_lineup_state_even_when_lineup_confirms_same_cycle`.

### 3b. Input-freshness provenance: evaluation time vs. input freshness

A `T_MINUS_30` snapshot is genuinely *evaluated* at T-30, but most of its
inputs (pitcher/bullpen/weather/odds projections) are whatever
`data/slate.json` already held from the day's single pipeline fetch — §2
above explains why those are deliberately not re-fetched every cycle.
Without saying so explicitly, a reader could reasonably assume a
"snapshot at T-30" means everything feeding it was fetched at T-30, which
is false. Every `ModelEvaluation` record now carries an additive,
nullable `inputFreshnessNote` field
(`data/edgelab/schema_v1/model_evaluation.schema.json`) set to one of two
values (`lib/edgelab/prospective_snapshot.py`):
`INPUT_FRESHNESS_LINEUP_REFRESHED` ("LINEUP_REFRESHED_LIVE_OTHER_INPUTS_PERSISTED_FROM_SLATE")
for the `LINEUP_CONFIRMATION` checkpoint specifically, and
`INPUT_FRESHNESS_ALL_PERSISTED` ("ALL_INPUTS_PERSISTED_FROM_SLATE_AT_LAST_PIPELINE_FETCH")
for every other checkpoint. A third constant,
`INPUT_TIMESTAMP_UNAVAILABLE`, is reserved for a future case where even
that coarse classification can't be determined — never silently
defaulted to a specific/confident value. The record's existing
`pipelineRunId` remains the actual model-evaluation instant (unchanged
meaning); `inputFreshnessNote` is what distinguishes "computed at T"
from "computed at T using inputs as fresh as T." A prospective snapshot
should be read precisely as: **production-equivalent model computation
at time T using persisted inputs available at T** (lineup fields
excepted at the `LINEUP_CONFIRMATION` checkpoint, which are live as of
T). Test: `test_input_freshness_note_distinguishes_lineup_refresh_from_persisted_inputs`.

### 3c. MODEL_CLOSING_WINDOW: resolving a naming collision with PR #86's Kalshi closing quote

PR #86's `lib.edgelab.checkpoints`/`lib.edgelab.research_dataset` already
use the bare label `"CLOSING"` for a specific, carefully-selected
concept: the canonical Kalshi **closing quote** for a market
(`select_closing_quote`, never "just the last tick seen"). This
milestone's model-side checkpoint originally reused the same string for a
different concept — "the final model evaluation in the pregame closing
window" — which is not the same instant, and conflating the two labels
risked a reader (or future code) assuming a `ModelEvaluation` with
`checkpoint: "CLOSING"` was causally paired with the market's actual
closing quote, when in fact the model-side snapshot may land several
minutes before Kalshi's own designated closing window resolves. Fixed by
renaming the model-side constant to `MODEL_CLOSING_WINDOW =
"MODEL_CLOSING_WINDOW"` everywhere in `lib/edgelab/prospective_snapshot.py`
(the `CORE_CHECKPOINTS` tuple, `determine_due_checkpoint()`,
`ModelEvaluation.checkpoint`) and in
`data/edgelab/schema_v1/model_evaluation.schema.json`'s `checkpoint`
enum, with an explicit docstring/description distinguishing it from PR
#86's market-side `CLOSING`. Research pairing a `MODEL_CLOSING_WINDOW`
evaluation with a market observation continues to use PR #86's
`select_closing_quote`/`lib.edgelab.temporal_alignment` logic
independently and unchanged — this rename only removes the label
collision, it does not change which market price research treats as
canonical. Test: `test_model_closing_window_distinct_from_market_closing_checkpoint`.

## 4. Started-game safety

`classify_game_eligibility()` uses two independent signals, neither
trusted alone:
1. `lib.edgelab.checkpoints.classify_checkpoint` against the fresh `now`
   and the game's own scheduled start — always accurate regardless of
   when any other field was fetched, since `now` is never stale by
   construction. `POST_START` → excluded, unconditionally.
2. A fresh (this-run) MLB Stats API live status
   (`lib.edgelab.mlb_schedule.fetch_schedule` +
   `build_schedule_game_context`, one call per cycle, not per game) —
   catches Postponed/Cancelled/Suspended, which pure clock-time can't. A
   schedule-fetch failure degrades to "proceed on clock-time alone,"
   never a hard whole-day blackout from one flaky network call.

A delayed game whose clock-time has passed scheduled start but isn't
actually live yet is conservatively excluded — a missed evaluation is
recoverable next cycle; a `POST_START` leak is not.

## 5. Contemporaneous Kalshi observation linkage

Every `ModelEvaluation` record carries `eventTicker`/`seriesTicker`
enriched from the exact matching `MarketObservation` already captured
that date by the independent `capture-snapshots-scheduled.yml`/
`edgelab-capture.yml` pipeline (`lib.edgelab.model_evaluation._ticker_lookup_from_observations`,
reused unchanged) — reused, never re-parsed. Downstream research (PR
#86's `lib.edgelab.research_dataset.build_opportunity_rows`) pairs each
now-causal `ModelEvaluation` with the market's own contemporaneous
checkpoint price via `lib.edgelab.temporal_alignment` unchanged — this
milestone makes the *evaluation* side of that join far less sparse; it
does not need to touch the *observation* side, which PR #86 already
built correctly.

### 5a. Market-price age: how stale was the Kalshi price a model evaluation was paired against?

PR #86's/§5's join above answers "which `ModelEvaluation` is valid for a
given market checkpoint's own `MarketObservation`" — anchored on the
checkpoint. It does not answer the reverse, equally important research
question: *given a model evaluation, how old was the most recent Kalshi
price actually available at that instant?* `lib/edgelab/research_dataset.py`
now computes this as a second, independent, reverse-direction pairing.
For each opportunity row's selected `ModelEvaluation`, `_select_latest_observation_at_or_before()`
finds the latest `MarketObservation` for that ticker with
`capturedAt <= modelEvaluatedAt` (mirroring `temporal_alignment`'s own
selector, but anchored on the model-evaluation instant instead of the
checkpoint instant). Three new fields land on every opportunity row:
`marketObservationCapturedAtForModelEval` (the prior observation's own
timestamp, or `null`), `marketPriceAgeSeconds` (`modelEvaluatedAt -
marketObservationCapturedAt`, computed by `_seconds_between()`, which
returns `None` — never a negative number — if the only candidate
observation is not strictly at-or-before the evaluation instant), and
`marketPriceAgeBucket` (one of `<=5min`, `>5-15min`, `>15-30min`,
`>30min`, or `unavailable` when no prior observation exists at all — via
`market_price_age_bucket()`). If no prior observation exists, the fields
are left `null`/`unavailable`; the age is never future-filled or defaulted
to zero. `marketObservationCapturedAt <= modelEvaluatedAt` holds for
every non-null pairing by construction — proven in
`test_earlier_market_observation_links_with_correct_positive_age`,
`test_later_market_observation_never_used_for_price_age`, and
`test_market_price_age_never_negative`.

`lib.edgelab.research_reports.market_price_staleness_report(rows)` (new
report section, "market_price_staleness") surfaces the full, unfiltered
distribution of these buckets plus median/p90 age across every causally
valid row by default — staleness is never hidden behind a filter a
reader has to know to apply. `edge_backtest(rows, side_filter=None,
max_market_price_age_seconds=None)` gained an optional filter parameter
for backtests that specifically need to exclude stale pairings; the
default (`None`) drops zero rows, preserving every existing caller's
behavior unchanged.

## 6. Model snapshot identity, and proof multiple states survive

No new ID concept was needed — PR #86's/Milestone 3's existing scheme
already supports it correctly:
`ModelEvaluationId = sha1(pipelineRunId + marketTicker + selection)`.
Two calls with genuinely different `pipelineRunId` values (this
milestone: the actual live evaluation instant, one per checkpoint) for
the same ticker always produce two different, both-preserved records —
`lib.edgelab.storage.append_records` is a pure additive append keyed by
this ID, never an overwrite. An exact rerun (identical `pipelineRunId`)
produces an identical ID, which `append_records` correctly treats as a
no-op duplicate.

Proven directly:
`tests/edgelab/test_prospective_snapshot.py::test_multiple_checkpoints_persist_through_storage_append`
writes a `T_MINUS_90` evaluation, then a `T_MINUS_30` evaluation for the
SAME game/ticker, through the real `storage.append_records`, and asserts
both rows exist on disk with their distinct `modelFairProbability`
values intact; `test_exact_duplicate_run_is_idempotent` proves a retried
identical cycle adds zero new rows.

This required refactoring (not reimplementing)
`lib/edgelab/model_evaluation.py`'s per-row mapping into a shared
`build_model_evaluation_records_for_games()` function, called by both the
existing once-daily pipeline-derived path and this new intraday path —
confirmed behavior-preserving for the existing path by its full existing
test suite passing unchanged. `ModelEvaluation.schema.json` gained one
additive, optional `checkpoint` field (null for every pre-existing row).

### 6a. Checkpoint identity vs. actual evaluation time

A 15-minute cron cannot land exactly on T-90/T-60/T-30 — recording only
the checkpoint label would make a coarse, jittery schedule look more
precise than it is. `ModelEvaluation.pipelineRunId` keeps recording the
real evaluation instant (unchanged), and `ModelEvaluation.checkpoint`
keeps recording the target checkpoint name (unchanged) — this pass adds
the derived gap between them at *research* time rather than growing the
raw schema further. `lib/edgelab/research_dataset.py`'s
`build_opportunity_rows()` computes `modelEvaluationMinutesToStart`
(actual minutes between the evaluation instant and the game's scheduled
start) and, for the three time-target checkpoints
(`T_MINUS_90`/`T_MINUS_60`/`T_MINUS_30`, each with a nominal target in
`_CHECKPOINT_NOMINAL_TARGET_MINUTES`), `checkpointTimingErrorSeconds =
(actualMinutesToStart - nominalTargetMinutes) * 60`. `LINEUP_CONFIRMATION`
and `MODEL_CLOSING_WINDOW` are event-driven/window-driven, not
fixed-offset targets, so `checkpointTimingErrorSeconds` is left `null`
for them rather than compared against a meaningless nominal value. Tests:
`test_checkpoint_timing_error_computed_for_time_target_checkpoints`,
`test_checkpoint_timing_error_none_for_non_time_target_checkpoints`.
`snapshot_coverage_report`'s `minutesToStartDistributionByCheckpoint`
(§10) and `lateRunCount` (`|checkpointTimingErrorSeconds| > 300`) are
both derived from these same two fields.

## 7. Producer commit/config provenance

Every prospective-snapshot record carries `modelCommitSha`
(`GITHUB_SHA`/`git rev-parse HEAD`, same helper the pipeline-derived path
already uses) and `modelConfigVersion` (`config/rules.json`'s own
`_version`), via the same shared `build_model_evaluation_records_for_games()`
call — no separate/divergent provenance scheme for the two paths.
`artifactSource="prospective_snapshot"` and `checkpoint=<label>`
distinguish these rows from pipeline-derived ones in every report.
Candidate-model replay (running NEWER code against OLDER historical
inputs) remains `lib/edgelab/replay.py`'s separate, already-existing
research mode — this milestone never blends the two populations.

## 8. Bid/ask spread scale bug (PR #86 follow-up, now fixed)

Confirmed against every real committed `data/edgelab/clv_quotes/*.jsonl*`
file: `yesBid`/`yesAsk` are 0-100 on disk (e.g. `45.0`; 99.7% of real
values > 1, impossible on a 0-1 scale). `lib/edgelab/market_comparison.py`'s
`normalize_market_input()` previously computed `bidAskSpread` as a raw
`(yesAsk - yesBid)` difference and compared it against
`LOW_LIQUIDITY_SPREAD`/the score component's `0.20` divisor, both of
which correctly assume 0-1 — so a normal 1-2 cent spread (numerically
1.0-2.0 unconverted) was almost always misclassified `LOW_LIQUIDITY`.
Fixed at the source (divide by 100 once, where `bidAskSpread` is
computed); updated the two existing tests that encoded the wrong 0-1
`clv_row` fixture convention, and added a regression test for a
realistic spread that must NOT trigger `LOW_LIQUIDITY`. Isolated to the
research/comparison layer — `scripts/build_market_ledger.py`/
`scripts/risk_gate.py` never import this module, so no production
selection/ranking path changed.

## 9. Estimated growth (prospective, not retroactive)

From this corpus's real 13-day averages — **raw counts, not independent
evidence**: 18.5 games/day, 3,688 unique Kalshi tickers/day, 5,791 raw
opportunity rows/day (unaffected by this milestone: full-universe Kalshi
capture already happens independently). Model-supported markets: the
11-market model config evaluates ≈11 markets/game ≈ 204 model-supported
ticker-markets/day.

With 5 targeted checkpoints/game/day, the **upper bound** is
204 × 5 ≈ **1,020 new causally-valid ModelEvaluation rows/day**. Realistic
capture will be lower — not every checkpoint fires for every game (a game
added late to the schedule can miss `T_MINUS_90`; a lineup that never
resolves before first pitch never fires `LINEUP_CONFIRMATION`) — a
defensible estimate is **600-900 causally-valid opportunity rows/day**,
i.e. roughly **35-55x** the historical rate (264 rows / 13 days ≈ 20/day)
once this system has run for a comparable window.

**The independent-evidence denominator stays ~18.5 games/day, not
600-900.** Every one of those ~600-900 rows is 1 of 5 checkpoints × 1 of
~11 correlated markets for one of ~18.5 games — `lib.edgelab.research_stats`'s
game-clustered bootstrap (PR #86) is exactly the machinery that already
accounts for this; nothing about this milestone changes how many
*independent* games exist per day, only how many *times* each game's
markets get a durable, causally-timestamped read.

## 10. Data quality report

`lib.edgelab.research_reports.snapshot_coverage_report(rows, evaluations,
games=None, research_runs=None)` (new,
`data/edgelab/analytics/latest_research_snapshot_coverage.json`) tracks,
as of this reliability pass:

- **Coverage**: `gamesScheduled`, `gamesObserved`, `eligibleGames` (games
  considered minus those excluded for `STARTED`/`POSTPONED`/
  `CANCELLED_OR_SUSPENDED`/`MISSING_SCHEDULED_START`, aggregated across
  `research_runs`), `gamesWithProspectiveSnapshot`, `uniqueMarketsObserved`,
  `uniqueMarketsModelSupported`.
- **Evaluation volume**: `modelEvaluationsCapturedTotal`,
  `modelEvaluationsCapturedProspective`, `modelEvaluationsWritten` (from
  each run's own `storage.append_records` write count, so duplicates
  correctly don't inflate it), `checkpointsTargeted`,
  `checkpointsSuccessfullyCaptured`, `modelEvaluationsByCheckpoint`,
  `modelCoverageByCanonicalFamily`, `modelCoverageByGame`.
- **Causal linkage** (PR #86 direction: market checkpoint → valid model
  evaluation): `causalModelMarketPairCount`,
  `causalModelMarketIndependentGames`,
  `pctSettledOpportunityRowsWithCausalLinkage`.
- **Market-price staleness** (this milestone's new reverse-direction
  pairing, §5a): `marketLinkedSnapshots` (rows with a prior market
  observation found at all), `snapshotsLackingEarlierMarketObservation`,
  `medianMarketPriceAgeSeconds`, `p90MarketPriceAgeSeconds`,
  `marketPriceAgeBucketCounts` — all reused directly from
  `market_price_staleness_report()` rather than recomputed a second way.
- **Input-freshness / timing honesty**: `evaluationsByInputFreshnessNote`
  (§3b), `minutesToStartDistributionByCheckpoint` (n/min/max/median actual
  minutes-to-start per checkpoint, derived from `checkpointTimingErrorSeconds`
  and `modelEvaluationMinutesToStart`, §6a), `missingCoreCheckpointCount`,
  `missingCheckpointReasons`.
- **Lineup-poll outcomes** (§3a): `lineupConfirmationAttempts`,
  `lineupConfirmationSuccesses`, `lineupConfirmationApiFailures`.
- **Run health**: `lateRunCount` (genuinely computed from
  `|checkpointTimingErrorSeconds| > 300`, no longer hardcoded 0),
  `duplicateOrIdempotencyCount`, `skippedStartedGameCount`,
  `persistenceFailureCount` (runs with `status == "failed"`, §1b),
  `workflowFailureCount` (persistence failures plus any run's own
  `errors` — a `no_op` run is never counted as a failure).
- **Baseline comparison**: `improvementOverPR86Baseline` — an explicit,
  honestly-computed comparison against the PR #86 baseline (264 causal
  rows / 68 games), never asserted without being recomputed from the
  actual `rows`/`evaluations` passed in, and never retroactively applied
  to the historical baseline itself (`baselineCausalOpportunityRows` is a
  fixed reference constant).

As of this PR, before any live run, `gamesWithProspectiveSnapshot`/
`duplicateOrIdempotencyCount`/`lateRunCount`/`lineupConfirmation*` all
correctly read 0 — no historical row was fabricated or retroactively
relabeled to inflate this number. A companion report,
`lib.edgelab.research_reports.market_price_staleness_report(rows)`
(`data/edgelab/analytics/latest_research_market_price_staleness.json`,
generated by `scripts/edgelab/run_research_reports.py`), surfaces the
full unfiltered price-age distribution independently — see §5a.
