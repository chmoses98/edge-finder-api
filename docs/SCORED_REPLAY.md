# Scored Postgame Replay

Scores the immutable, already-written output of the Level 2 Historical
Replay Engine (`docs/REPLAY_ENGINE.md`, `lib/edgelab/replay.py`) against
canonical postgame evidence -- settlement, CLV, and placed-bet records --
once outcomes become available, so future model changes can be evaluated
objectively against what the model actually predicted pregame.

Research-only, same discipline as the replay engine it reads: never
changes probability models, thresholds, staking, settlement logic, or
recommendation gates. It only reads already-written canonical records and
writes a new, separate scoring artifact.

## The gap this closes

`lib/edgelab/replay.py`'s `execute_replay()` already joins settlement/CLV
onto every market it replays, but its own `performance` score
(`score_resolved_results()`) is computed over `replayedModelProbability`
-- the probability CURRENT code produces when re-run against frozen
historical inputs, used for regression-testing model *code* changes.
Nothing scored the model's actual **immutable pregame prediction**
(`originalModelProbability` -- what production genuinely said before the
game, independent of any later re-run), and nothing existed to break
that scoring down by market family, confidence tier, recommended-vs-
passed, CLV coverage, or realized betting P/L from confirmed wagers.

## Design

`lib/edgelab/scored_replay.py`:

- **`score_replay_result(result, ...)`** (pure): scores one ReplayResult.
  Every prediction/tier/recommendation field is copied verbatim from
  that result's `original*` fields -- never `replayed*`, never
  recomputed. Objective outcome and CLV are read directly from the
  result's own `settlementLinkage`/`clvLinkage` (already resolved by the
  replay engine against the frozen, integrity-verified postgame
  snapshot) -- never re-derived. CLV is available only when
  `clvLinkage` resolved from the market's `isClosingQuote=True` row, a
  valid pre-suspension/pre-first-pitch close by construction
  (`lib/edgelab/clv.py:select_closing_quote`). Wager result and realized
  P/L, when a confirmed bet exists, come from
  `lib.edgelab.bets.realized_bet_economics()` -- the same
  confirmed-receipt-preferred calculation used everywhere else in this
  repo, never a second implementation.

- **`aggregate_scored_results(scored_results)`** (pure): Brier score,
  probability-decile calibration buckets, breakdowns by market family
  and confidence tier, recommended-vs-passed, CLV coverage/average, and
  realized P/L -- computed strictly over rows where the underlying data
  actually exists (a market with no prediction or no resolved settlement
  contributes to no Brier/calibration number; realized P/L sums only
  `CONFIRMED_BET` rows). Works over any list of ScoredReplayResults, not
  just one run's -- a future multi-run/date-range rollup can reuse it
  directly.

- **`score_replay_run(replay_run_id)`** (I/O, read-only against the
  original): loads a completed ReplayRun + its ReplayResults via
  `lib.edgelab.replay.load_replay_run`/`load_replay_results`, looks up
  ModelEvaluation/Recommendation ids from the already-ingested
  `data/edgelab/model_evaluations/<date>.jsonl` /
  `recommendations/<date>.jsonl` (matched by the exact
  `(marketTicker, selection/marketName)` key both writers already use --
  never a recomputed hash guess), and the full Settlement record (for
  its `betId`) by calling `lib.edgelab.replay._linked_settlement_and_clv`
  -- the exact same canonical, integrity-verified source the
  ReplayResult's own linkage was built from -- using only the ReplayRun's
  own already-durable `snapshotId`/`snapshotDate` fields (see "Linkage
  durability" below). A confirmed bet is then looked up by that `betId`
  in the canonical `data/edgelab/bets/bets.jsonl` ledger. Also calls
  `assess_ingestion_readiness(date)` and folds its reasons into
  `limitationReasons` -- see "Postgame ingestion readiness" below.

- **`write_scored_replay_outputs(scored_run, scored_results)`**: writes
  to `data/edgelab/scored_replay_runs/<scoredReplayRunId>/` --
  physically separate from `data/edgelab/replay_runs/`, so this module
  can never collide with or overwrite the original ReplayRun/
  ReplayResult files it reads.

- **`build_date_report(scored_run, scored_results)`** (pure) /
  **`write_scored_replay_date_report(report)`**: reshapes one date's
  scoring into the date-keyed coverage summary
  `data/edgelab/reports/scored_replay/<date>.json` -- see "Date-level
  coverage report" below.

## Identity and idempotency (requirement 8)

`scoredReplayRunId = sha1('scored_replay_run', replayRunId,
scoringFrameworkVersion)` -- deterministic over the run identity and
scoring-framework version only, **not** over the settlement/CLV/bet
content. Rerunning scoring against unchanged canonical inputs re-derives
byte-identical content (`contentHash` matches) and is a true no-op
(`writeOutcome: "noop_unchanged"`, the original `scoredAt` timestamp is
preserved). Rerunning after a genuine correction (e.g. a settlement fix)
re-derives the SAME `scoredReplayRunId` but different content, and
`write_scored_replay_outputs` updates that record in place
(`writeOutcome: "updated"`). The source ReplayRun is never written to by
either path.

## Distinguishing evaluation states (requirement 4)

Three independent fields, checked directly rather than folded into one
enum:

- **`predictionStatus`**: `AVAILABLE` / `UNAVAILABLE` -- whether the
  model produced a pregame probability at all.
- **`wager.evaluationStage`**: `PREDICTION_UNAVAILABLE` (checked first)
  → `CONFIRMED_BET` (a genuine, non-`CANCELLED` PlacedBet is linked) →
  `RECOMMENDED_NO_CONFIRMED_BET` (`recommendationActionStatus ==
  'Accepted'` but no such bet) → `EVALUATED_NO_BET_PLACED` (otherwise).
- **`objectiveOutcome.settlementStatus`**: `MARKET_SETTLED` /
  `UNRESOLVED_SETTLEMENT`.
- **`clv.clvStatus`**: `CLV_AVAILABLE` / `CLV_UNAVAILABLE`.

## Automatic postgame integration (Operationalizing Scored Replay milestone)

`scripts/score_replay.py --date <DATE>` is the automated entrypoint,
wired as the "Score replay against postgame outcomes" step in
`.github/workflows/edgelab-postgame.yml` -- deliberately the LAST step
before that workflow's commit, after:

1. **Sync recommendation / pass-decision ledger**
   (`build_recommendations.py` -- writes `recommendations/<date>.jsonl` +
   `model_evaluations/<date>.jsonl`)
2. **Settle full observed market universe** (`settle_markets.py` --
   writes `settlements/<date>.jsonl`)
3. **Re-ingest legacy bet ledgers** (picks up newly-settled bets)
4. **Create immutable POST_GAME_SETTLEMENT snapshot** (links the
   settlement/CLV evidence back to the day's PRE_GAME_DECISION snapshot
   -- required for wager linkage)
5. **Create immutable CLOSING_LINE snapshot**

`--date` mode resolves that date's `replayRunId` from
`data/edgelab/forward_replay_status.json` -- the same status file
`scripts/run_forward_replay.py` already writes each pregame day (as
`BLOCK 10` of `fetch-slate.yml`, immediately after PRE_GAME_DECISION
snapshot capture) -- so a ReplayRun already exists for essentially every
production day without this milestone needing to trigger a new replay
itself. If no `COMPLETED` forward replay is recorded yet, scoring is
skipped (recorded honestly to `data/edgelab/scored_replay_status.json`,
never a workflow failure) -- a later rerun of this workflow step picks
it up. `continue-on-error: true`, same policy as every other research
step in this pipeline: it must never block production postgame
settlement.

### Postgame ingestion readiness (requirement 2)

`assess_ingestion_readiness(date)` checks whether
`recommendations/<date>.jsonl`, `model_evaluations/<date>.jsonl`, and
`settlements/<date>.jsonl` exist AT ALL for the date (not their row
content -- per-market gaps are already tracked via
`UNRESOLVED_SETTLEMENT`/`CLV_UNAVAILABLE`). Scoring is **never hard-
blocked** by this -- every per-row linkage already degrades honestly
when a ledger hasn't been ingested yet, and scoring is idempotent (a
later rerun with more data lands as an `"updated"` write). Instead, its
reasons (e.g. `SETTLEMENTS_NOT_YET_INGESTED_FOR_DATE`) are folded into
`limitationReasons` and exposed as `ingestionReadiness` on the
`ScoredReplayRun`, so a genuinely too-early scoring attempt is never
mistaken for a fully-covered one. In the normal automated case, the
workflow step ordering above already means ingestion has run by the
time scoring does; this is the honest fallback for the case where an
upstream step failed or a settlement is still pending (e.g. a delayed
game).

### Linkage durability (requirement 3)

Earlier, `_full_settlement_rows_for_run` located the full
PRE_GAME_DECISION manifest via `lib.edgelab.snapshot.find_manifest_by_id`
-- a directory walk over `data/edgelab/snapshots/` -- just to read two
scalar fields off it (`snapshotId`, `snapshotDate`);
`_linked_settlement_and_clv` never reads anything else from that
manifest. Both fields are already stored durably on the ReplayRun record
itself (`data/edgelab/replay_runs/<id>/replay_run.json`, committed the
same day the snapshot is captured). The manifest lookup added an
unnecessary dependency on the full manifest tree still being present on
whatever runner/checkout scoring happens to execute on. This module now
builds a minimal `{"snapshotId", "snapshotDate"}` dict from the
ReplayRun's own fields instead -- removing that dependency entirely
while preserving the exact same integrity guarantee
`_linked_settlement_and_clv` already enforces (the postgame snapshot
must list this `snapshotId` in its own `linkedSnapshotIds`). No new bet
ledger, no parallel settlement source, no fabricated wager -- the same
canonical `lib.edgelab.replay` function and the same frozen `SETTLEMENT`
component are still the only source.

### Date-level coverage report (requirement 4)

`data/edgelab/reports/scored_replay/<date>.json` (via `build_date_report`
+ `write_scored_replay_date_report`) surfaces, for one date: scoreable
vs. total predictions, settlement coverage, Recommendation/ModelEvaluation
linkage coverage, wager-linkage coverage (`confirmedBetCount`,
`recommendedNoConfirmedBetCount`, and the rate among rows that were
actually recommended), CLV coverage, Brier score + calibration buckets
where available, and `missingCoverageReasons` -- a frequency tally of the
exact reason string behind every unresolved-settlement/CLV-unavailable
row, so a coverage gap is always traceable, never a bare count. It is a
pure, always-overwritable projection of the already-idempotent
ScoredReplayRun/ScoredReplayResult data -- regenerating it from
unchanged inputs reproduces it byte-for-byte.

## Files

- `lib/edgelab/scored_replay.py` -- scoring engine
- `lib/edgelab/ids.py` -- `build_scored_replay_run_id`,
  `build_scored_replay_result_id`
- `data/edgelab/schema_v1/scored_replay_run.schema.json`,
  `scored_replay_result.schema.json` -- schemas, registered in
  `lib/edgelab/schema.py`
- `scripts/score_replay.py` -- CLI (`--replay-run-id` for manual use,
  `--date` for the automated postgame-workflow entrypoint)
- `.github/workflows/edgelab-postgame.yml` -- "Score replay against
  postgame outcomes" step + extended commit path list
- `tests/edgelab/test_scored_replay.py`,
  `tests/edgelab/test_scored_replay_workflow.py`

## Known measurement gap

`ModelEvaluation`/`Recommendation` linkage depends on those records
having already been ingested for the snapshot's date (via
`scripts/edgelab/build_recommendations.py`) -- a ReplayRun scored before
that ingestion has run yields `modelEvaluationId`/`recommendationId:
null` for every market, never a guessed id; `ingestionReadiness` on the
`ScoredReplayRun` makes this explicit rather than silent, and a later
rerun (idempotent) fills the gap in once ingestion catches up.

The wager join depends on a POST_GAME_SETTLEMENT snapshot being linked
to the PRE_GAME_DECISION snapshot for the date (`wagerLinkageAvailable:
false` + a `WAGER_LINKAGE_UNAVAILABLE` limitation reason when it isn't
yet) -- normal before `create_snapshot.py POST_GAME_SETTLEMENT` has run
for the date, or if that step itself failed. Every row's
`wager.evaluationStage` then degrades to a prediction/recommendation-
only classification (never fabricates `CONFIRMED_BET`).

Both gaps are visible, structured, and self-healing on the next
automated rerun -- neither one silently under-reports coverage as if it
were complete.
