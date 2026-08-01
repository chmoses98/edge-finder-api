# EdgeLab Phase 2 Milestone 3: model evaluation persistence

Status: closes the write-path gap Milestone 2 found — model-derived
evaluation data is now durably persisted and linked to every market the
system evaluates. **Does not change production betting recommendations
or staking behavior.** No calibration/strategy logic lives here; see
`docs/EDGELAB_CALIBRATION.md` for how calibration *consumes* this data.

## 0. Root cause of the write-path gap

Milestone 2 found `modelFairProbability` unpopulated on every one of the
77 real bets' settled outcomes. The root cause: `lib/edgelab/recommendations.py`'s
`build_recommendations_from_pipeline()` reads `data/pipeline/<date>/recommendations.json`'s
per-market `marketLedger` rows — which already carry the model's fair
probability (`modelProb`), market-implied probability (`kalshiVF`/
`marketProbVF`/`executableMarketProb`), edge (`calibratedEdgeVsExecutable`/
`edge`), confidence (`confidenceTier`), lineup status (`lineupStatus`/
`lineupConfirmedOfficial`/`lineupDataQuality`), threshold/line (`line`),
and ticker identity (`ticker`/`marketTicker`/`seriesTicker`) — but only
copies a narrow subset of these onto the `Recommendation` record and
never materializes a `ModelEvaluation` record at all, despite the
`ModelEvaluation` JSON Schema existing since Phase 1
(`data/edgelab/schema_v1/model_evaluation.schema.json`) and an ID
builder (`lib.edgelab.ids.build_model_evaluation_id`) already existing,
unused. `Recommendation.modelEvaluationId` was hardcoded to `None` at
both call sites. The model's actual per-market evaluation — including
for markets ultimately *passed*, not just recommended/bet — was silently
discarded after ingestion, so nothing durably linked a settled `PlacedBet`
back to the evaluation that justified it.

**The fix is additive, not a rewrite of the model:** `lib/edgelab/model_evaluation.py`
reads the exact same `marketLedger` rows `lib/edgelab/recommendations.py`
already reads and persists whatever the pipeline already computed, plus
an explicit `evaluationStatus` classifying *why* a probability is or
isn't trustworthy. No model math is duplicated or re-derived anywhere in
this milestone.

### A second bug found while building this: two-sided single-ticker collisions

While testing against the real `2026-07-31` artifact, two `marketLedger`
rows for a run-line/spread market (`RL_Away` and `RL_Home` — the two
opposite sides of one Kalshi contract) resolved to the **same ticker**.
The old `recommendationId` key (`ticker or f"{gameId}:{marketName}"`)
used the ticker alone whenever one resolved, so both sides collapsed
onto one `recommendationId` — silently dropping one side's evaluation.
Fixed in both `lib.edgelab.recommendations` and `lib.edgelab.model_evaluation`
by always including `marketName` in the key
(`f"{ticker}:{marketName}" if ticker else f"{gameId}:{marketName}"`).
No data had ever been committed against the old formula (`recommendations.jsonl`/
`model_evaluations.jsonl` didn't exist yet in this repo), so this is a
pre-launch fix with zero migration cost — not a breaking schema change.

## 1. Authoritative source of model probability

`ModelEvaluation` is now the authoritative record of what the model
computed for a market, independent of whether a bet was ever placed on
it. Every field is either copied verbatim from the source `marketLedger`
row, looked up from an already-captured `MarketObservation` (for
`eventTicker`/`seriesTicker` — never re-parsed independently), derived
via the repo's one existing probability↔odds conversion
(`scripts.clv_from_snapshot.implied_to_american`, for `modelFairOdds`),
or left null. **Nothing is fabricated to fill a gap.**

Two fields deliberately do *not* get a new independent value, to avoid
inventing a second source of truth for a concept the repo already names
elsewhere:

- `modelSource` is the artifact's own `meta.producedBy`
  (`scripts/build_market_ledger.py` for every real record today) — not a
  new label.
- `evPerDollar` keeps the existing `evPerDollar` name used by
  `Recommendation`/the old `ModelEvaluation` schema, rather than
  introducing a second `expectedValuePerDollar` field for the same
  concept.
- `modelVersion` stays null for every pipeline-derived evaluation today
  — the upstream artifact has no concept of a model *algorithm* version
  (only an artifact schema version, a different thing) anywhere. This is
  an honest, documented gap (see §6), not a bug.

## 2. Evaluation lifecycle

For every `marketLedger` row (recommended, passed, watchlist, or
partially evaluated), `classify_evaluation_status()` assigns exactly one
status, independent of the row's own `status` field wherever the
model's own probability already answers the question:

| Status | Meaning |
|---|---|
| `EVALUATED` | A trustworthy `modelFairProbability`, a resolved ticker, a `marketImpliedProbability`, and a computed edge all exist. |
| `PARTIAL_EVALUATION` | `modelFairProbability` and `marketImpliedProbability` exist but edge couldn't be computed. |
| `NO_MODEL_SUPPORT` | The model has no method for this market at all (no probability, no specific failure reason). |
| `INVALID_PROBABILITY` | The model produced a value outside `(0, 100)` — discarded; `modelFairProbability`/`modelFairOdds` are stored as `null`, never the bad value. |
| `MISSING_MARKET_PRICE` | A fair probability exists but there's no market price to compare it against. |
| `DATA_QUALITY_BLOCK` | Upstream data (lineup, stats, etc.) was insufficient — the ledger row itself says so (`"Missing Data"`/`"Evaluation Failed"`). |
| `PARSER_UNRESOLVED` | The market/ticker identity itself never resolved. |

A `"Rejected"` ledger row (the model *did* produce a fair probability;
a later edge-threshold/portfolio rule declined to bet it) is just as
fully `EVALUATED` as an `"Accepted"` one — rejection is a
`Recommendation`-level decision, not evidence the model failed to
evaluate the market. `row.get("status")` (`Missing Data`/`Evaluation
Failed`) is only consulted as a last resort, when the row carries no
`modelProb` at all.

Markets EdgeLab observed that the model's 11-market config never
evaluates at all (the full-universe extension, mirroring
`lib.edgelab.recommendations.extend_with_full_universe`) get one
`NO_MODEL_SUPPORT` `ModelEvaluation` each — there is no `modelProb`/
`evaluationError` to classify against, so `NO_MODEL_SUPPORT` is the only
honest answer.

## 3. Linkage rules

| Link | Mechanism |
|---|---|
| `ModelEvaluation` → `Recommendation` | Both share the exact same deterministic key (source artifact's `meta.createdAt` + `ticker:marketName`, or `date:ticker` for full-universe rows) — `Recommendation.modelEvaluationId` and `ModelEvaluation.recommendationId` are computed independently from the identical key, so they always match with no lookup or join table. |
| `PlacedBet` → `ModelEvaluation` | `PlacedBet.modelEvaluationId` (new, additive field), backfilled by `lib.edgelab.bets.link_bets_to_recommendations()` — see §4. |
| `MarketObservation` ↔ `ModelEvaluation` | **Query-time join by `marketTicker`, no new stored field.** `MarketObservation` is a git-committed, append-only historical record (Milestone 1's "never rewrite historical files" principle) — adding a foreign key would mean rewriting it. `ModelEvaluation.eventTicker`/`seriesTicker` are looked up from a matching `MarketObservation` at write time instead (see §1). |
| `Settlement` ↔ `ModelEvaluation` | **Query-time join by `marketTicker`**, same reasoning as `MarketObservation` — `Settlement` gets no new field either. |
| Calibration | Reads through `lib.edgelab.analytics`'s `v_placed_bets`, which now `LEFT JOIN`s `v_model_evaluations` — see §5. |

## 4. Manual-bet behavior

A manually placed bet must remain fully representable even with no
model evaluation behind it — `PlacedBet.modelEvaluationId` is `None` by
default (an explicit, overridable keyword on `build_manual_bet_record`,
same pattern as `recommendation_id`) and is never fabricated for a bet
with no real link.

Linking happens as an explicit **backfill** step
(`lib.edgelab.bets.link_bets_to_recommendations`), run from
`scripts/edgelab/build_recommendations.py` right after that date's
`Recommendation`/`ModelEvaluation` ledgers are built — not only at
bet-creation time, because the two ledgers are written on different
schedules (a bet can be logged before or after the day's pipeline sync
for the same ticker). The backfill:

- Never overwrites a `recommendationId`/`modelEvaluationId` a bet
  already carries (each field is backfilled independently — a bet that
  already has one but not the other only gains the missing one).
- Never fabricates a link for a ticker with no matching recommendation
  — a genuinely manual, model-independent bet stays permanently unlinked.
- Uses the exact same upsert mechanism (`storage.upsert_records(bets_path,
  ..., "betId")`) `scripts/edgelab/settle_markets.py` already uses to
  update existing `PlacedBet` rows in place — not a new mechanism.

## 5. How calibration consumes ModelEvaluation

`lib.edgelab.analytics`'s `v_placed_bets` view now `LEFT JOIN`s
`v_model_evaluations` on `modelEvaluationId` (only when the
`model_evaluations` entity has any files at all — a fixture/test session
without it behaves exactly as before). For `modelFairProbability`,
`estimatedEdgeAtEntry`/`estimatedEdge`, `confidence`, and `thesisTags`,
the view does `COALESCE(evaluation's value, bet's own copy)` — the
linked `ModelEvaluation` is authoritative whenever it resolves and has a
value; the bet's own (possibly stale, entry-time-only) copy is the
fallback, used when there's no link, the link doesn't resolve (a join
miss), or the evaluation's own field happens to be null. This is a
single, uniform precedence rule, not a per-query decision — every
existing `lib.edgelab.calibration` function reads these columns by the
same names as before and transparently benefits without any query
changes. Two additional columns, `modelVersion` and
`lineupConfirmationState`, are exposed on `v_placed_bets` for the first
time (sourced only from a linked `ModelEvaluation` — `PlacedBet` itself
has no such fields), `NULL` when no link exists.

## 6. Historical backfill

`data/pipeline/2026-07-30/recommendations.json` and
`data/pipeline/2026-07-31/recommendations.json` are real, already-committed
pipeline artifacts with strong provenance (the model's actual output,
not reconstructed). `scripts/edgelab/build_recommendations.py` was run
against both:

- **110** `ModelEvaluation` records backfilled for 2026-07-30, **165**
  for 2026-07-31 (275 total).
- **7** already-logged `PlacedBet` rows gained a real
  `recommendationId`/`modelEvaluationId` link (3 from 2026-07-30, 4 from
  2026-07-31) — every other field on every other bet is untouched (see
  the PR diff — only `recommendationId`, `modelEvaluationId`, and
  `updatedAt` changed on exactly those 7 rows).
- No values were inferred or reconstructed — every persisted field is
  either copied verbatim from the artifact or derived via an existing,
  reused function (§1).

**No older `recommendations.json`/`execution.json` artifacts exist in
this repo** — `data/pipeline/` currently holds only these two dates.
There is nothing further to backfill; earlier evaluations, if the
pipeline ran before these two dates, were never captured in an
immutable artifact and are permanently unrecoverable. This is stated
here as an honest limitation, not papered over.

## 7. Current limitations

- **`modelVersion` is null for every real record.** The upstream pipeline
  has no concept of a model algorithm version today (see §1) — this is
  visible directly in the population report's "breakdown by model
  version/source" (currently one row: `UNKNOWN` / `scripts/build_market_ledger.py`).
- **`thesisTags` is empty on every `ModelEvaluation`.** Thesis tagging is
  a human, bet-time activity the production pipeline does not attach to
  a raw model evaluation — matches the same 0%-coverage finding
  Milestone 2 already documented for `PlacedBet.thesisTags`.
- **`eventTicker` is null whenever no matching `MarketObservation` exists**
  for that date (the upstream artifact itself never populates this
  field) — real for the 2026-07-30/07-31 backfill, since
  `data/edgelab/observations/` has no committed files yet.
- **`side`/`selection` are execution-time concepts** the raw evaluation
  usually can't populate (a market can be evaluated before a concrete
  tradable side is chosen) — `side` is null on essentially every real
  record today.
- **Only 2.5% of real `ModelEvaluation` records (7/275) link to a
  `PlacedBet`** — expected, since betting only 7 of 275 evaluated markets
  is the normal, conservative selection rate, not a bug.

## 8. Running it

```bash
python3 scripts/edgelab/build_recommendations.py --date YYYY-MM-DD
python3 scripts/edgelab/run_model_evaluation_report.py
```

The first command builds/updates that date's `Recommendation` and
`ModelEvaluation` ledgers and backfills any matching `PlacedBet` links
(also wired into the `edgelab-postgame.yml` GitHub Actions workflow, run
automatically after `Update CLV (Post-Slate Review)` completes). The
second writes `data/edgelab/analytics/latest_model_evaluation_report.json`
and `data/edgelab/reports/phase2_model_evaluation.md` — the population
report from §7, regenerated (not appended) and committed, same
convention as every other EdgeLab report.
