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
  its `betId`) by re-locating the linked PRE_GAME_DECISION manifest and
  reusing `lib.edgelab.replay._linked_settlement_and_clv` -- the exact
  same canonical, integrity-verified source the ReplayResult's own
  linkage was built from. A confirmed bet is then looked up by that
  `betId` in the canonical `data/edgelab/bets/bets.jsonl` ledger.

- **`write_scored_replay_outputs(scored_run, scored_results)`**: writes
  to `data/edgelab/scored_replay_runs/<scoredReplayRunId>/` --
  physically separate from `data/edgelab/replay_runs/`, so this module
  can never collide with or overwrite the original ReplayRun/
  ReplayResult files it reads.

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

## Files

- `lib/edgelab/scored_replay.py` -- scoring engine (new)
- `lib/edgelab/ids.py` -- `build_scored_replay_run_id`,
  `build_scored_replay_result_id`
- `data/edgelab/schema_v1/scored_replay_run.schema.json`,
  `scored_replay_result.schema.json` -- new schemas, registered in
  `lib/edgelab/schema.py`
- `scripts/score_replay.py` -- CLI, mirrors `scripts/run_replay.py`
- `tests/edgelab/test_scored_replay.py`

## Known measurement gap

`ModelEvaluation`/`Recommendation` linkage depends on those records
having already been ingested for the snapshot's date (via
`scripts/edgelab/build_recommendations.py`) -- a ReplayRun scored before
that ingestion has run yields `modelEvaluationId`/`recommendationId:
null` for every market, never a guessed id. Similarly, the wager join
depends on the PRE_GAME_DECISION manifest still being locatable on disk
(`lib.edgelab.snapshot.find_manifest_by_id`); if it has been pruned, every
row's `wager.evaluationStage` degrades to a prediction/recommendation-
only classification (never `CONFIRMED_BET`) and the run carries a
`WAGER_LINKAGE_UNAVAILABLE` limitation reason rather than silently
under-reporting confirmed bets.
