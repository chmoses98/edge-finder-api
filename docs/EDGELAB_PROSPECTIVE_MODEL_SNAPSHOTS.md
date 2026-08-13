# EdgeLab Prospective Model Snapshots milestone

Status: research-only, read-only with respect to production. Stacked on
top of PR #86 ("EdgeLab research trustworthiness"). This milestone does
NOT change model probabilities, projection formulas, production
recommendation logic, bet/tier thresholds, risk gates, bankroll rules,
stake sizing, production market selection, Kalshi live execution, lineup
gates, or any production slate output.

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
exception isolation (one malformed game's inputs never abort the cycle)
plus the new workflow's `continue-on-error: true`.

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
`T_MINUS_30`/`CLOSING`, and refreshes **only** the lineup fields (a free
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
  `CLOSING`. `T_MINUS_15`/`T_MINUS_5` are deliberately excluded from the
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

`lib.edgelab.research_reports.snapshot_coverage_report` (new,
`data/edgelab/analytics/latest_research_snapshot_coverage.json`) tracks:
games scheduled/observed/with-a-prospective-snapshot, unique markets
observed/model-supported, model evaluations by checkpoint, model
coverage by family/game, causal model+market pair count, % of settled
rows with causal linkage, missing-core-checkpoint count, and
late-run/duplicate/skipped-started-game/workflow-failure counts
aggregated from this system's own `research_runs` entries — plus an
explicit, honestly-computed comparison against the PR #86 baseline (264
causal rows / 68 games), never asserted without being recomputed from
the actual data passed in. As of this PR, before any live run,
`gamesWithProspectiveSnapshot`/`duplicateOrIdempotencyCount`/`lateRunCount`
all correctly read 0 — no historical row was fabricated or retroactively
relabeled to inflate this number.
