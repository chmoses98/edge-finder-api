# REPLAY_ENGINE.md

Level 2 Historical Replay Engine milestone. Builds on top of the
Historical Capture Completeness and Immutable Snapshot Foundation
milestone (`docs/SNAPSHOT_ARCHITECTURE.md`) — a Snapshot is a *capture*
operation; this milestone is the first real *consumption* operation that
reads Snapshots.

This is research-only infrastructure. It does not change live model
probabilities, production recommendations, thresholds, tiers, staking,
risk gates, settlement, CLV, or market selection. It calls the exact
same production functions production itself calls, against frozen
historical inputs, and writes its output to a separate,
never-read-by-production tree (`data/edgelab/replay_runs/`).

## 1. What "Level 2 replay" means

Snapshot completeness (`completenessStatus` on a `SnapshotManifest`) and
replay eligibility are two *different*, independently evaluated
concepts. A Snapshot can be `PARTIAL_REPLAY` for its own capture purposes
while still being fully replay-eligible, or vice versa — because replay
only cares about the inputs `evaluate_game()`/`apply_tt_safety()`/
`apply_portfolio_rules()`/`build_execution_artifact_payload()` actually
read, not everything a Snapshot bothers to capture for completeness.

**Level 2 (`ELIGIBLE_LEVEL_2` / `LEVEL_2_PRODUCTION_EQUIVALENT`)** means:
production-equivalent input fidelity. The replay engine had, with
verified integrity, every input production's own decision-time
functions actually consumed: the exact normalized slate, raw projection
inputs, market universe, recommendation output, risk-gate output, and a
known (if incompletely populated) rules config version. It does **not**
mean bit-for-bit reproduction of the historical production commit — see
§6.

**Level 1 (`ELIGIBLE_LEVEL_1_ONLY` / `LEVEL_1_APPROXIMATE`)** means one or
more *nice-to-have* inputs (lineup/bullpen/weather/market-observation/
park-factor evidence) are missing, so the replay is still real (same
production functions, same core inputs) but some context production had
may be absent. The CLI never runs this level unless the caller
explicitly asks (`--allow-approximate`) — see §9.

## 2. Replay eligibility rule table

`lib/edgelab/replay.py:assess_replay_eligibility()` evaluates, in order,
first match wins:

| # | Outcome | Condition |
|---|---------|-----------|
| 1 | `INELIGIBLE_UNSUPPORTED_VERSION` | `manifest.schemaVersion` not in the set this engine understands |
| 2 | `INELIGIBLE_INTEGRITY_FAILURE` | `snapshot.verify_snapshot()` fails (manifest hash or any frozen/referenced component's content hash doesn't match) |
| 3 | `INELIGIBLE_TEMPORAL_SKEW` | `manifest.temporalConsistency.skewDetected` |
| 4 | `INELIGIBLE_MISSING_INPUT` | any Level-2-required component (`NORMALIZED_SLATE`, `RAW_PROJECTIONS`, `RECOMMENDATION_OUTPUT`, `MARKET_UNIVERSE`, `RISK_GATE_OUTPUT`, `EFFECTIVE_CONFIG`) is `MISSING` |
| 5 | `INELIGIBLE_CONFIG_AMBIGUITY` | `manifest.rulesConfigVersion` is `None` — cannot even identify which config version was in force |
| 6 | `ELIGIBLE_LEVEL_1_ONLY` | a nice-to-have component (`LINEUP_STATE`, `BULLPEN_STATE`, `WEATHER`, `MARKET_OBSERVATIONS`, `EXECUTABLE_PRICES`, `BID_ASK`, `PARK_FACTORS`) is `MISSING` |
| 7 | `ELIGIBLE_LEVEL_2` | otherwise |

`LEVEL_2_REQUIRED_COMPONENT_TYPES` is deliberately **narrower** than
`lib.edgelab.snapshot.REQUIRED_COMPONENT_TYPES`: `PRODUCTION_SLATE_INPUT`
is snapshot-required (it's the record of what was published) but not
replay-required, since `evaluate_game()` never reads it — it reads
`NORMALIZED_SLATE`.

**`EFFECTIVE_CONFIG` being `PARTIAL` does not, by itself, block Level 2.**
Every real snapshot's `EFFECTIVE_CONFIG` is `PARTIAL` today (hardcoded
production thresholds outside `config/rules.json` aren't structurally
captured — see `SNAPSHOT_ARCHITECTURE.md`), and this milestone
deliberately treats "partial provenance" and "ambiguous version" as two
different claims: Level 2 fidelity is about whether replay had the
inputs production actually consumed, not about whether every
hardcoded-in-code threshold has a capture record. Only a genuinely
unknown `rulesConfigVersion` (rule 5) blocks eligibility. This is
recorded as a limitation reason (`EFFECTIVE_CONFIG_PARTIAL`), never
silently hidden.

## 3. ReplayRun / ReplayResult schemas

`data/edgelab/schema_v1/replay_run.schema.json` and `replay_result.schema.json`.
One `ReplayRun` per (snapshotId, replayMode, candidateModelCommitSha,
replayFrameworkVersion) — see `lib/edgelab/ids.py:build_replay_run_id`,
deterministic and write-once, same convention as `SnapshotManifest`'s own
identity. One `ReplayResult` per evaluated market row within a run.

Fields are never fabricated: `originalPreferredExpression` /
`replayedPreferredExpression` are always `null` this milestone (the
market-expression-preference concept lives in the separate, not-yet-wired
`market_comparison.py` research engine), and `performance.roi` is always
`null` (see §8). `marketFamily` is legitimately `null` (not a schema
violation) when a market row has no ticker — see §11 for why it is
deliberately **not** in `replay_result.schema.json`'s `required` array.

## 4. Replay loading and integrity

`scripts/run_replay.py` resolves a manifest by exact `--snapshot-id`, or
by `--date` (+ optional `--production-run-key`, defaulting to the latest
`PRE_GAME_DECISION` run for that date) via `lib.edgelab.snapshot`'s own
read interface (`find_manifest_by_id` / `load_manifest` /
`load_latest_pregame_manifest`). Eligibility assessment re-verifies the
manifest's own hash and every component's content hash
(`snap.verify_snapshot`) before anything else runs — a tampered or
partially-deleted snapshot is rejected as `INELIGIBLE_INTEGRITY_FAILURE`,
never silently used.

The snapshot's `manifestHash` is copied verbatim into every ReplayRun's
`snapshotManifestHash` field — proof of exactly which (hash-verified)
version of the snapshot produced a given replay, even if the underlying
snapshot were somehow superseded later.

## 5. Structural postgame-leakage prevention

`run_candidate_replay()` has **two independent** guards, both
enforced every call, neither a substitute for the other:

1. It raises `ReplayError` unless `manifest.snapshotStage ==
   PRE_GAME_DECISION` — a `POST_GAME_SETTLEMENT`/`CLOSING_LINE` manifest
   can never enter candidate evaluation, regardless of what a caller
   passes.
2. It raises `ReplayError` if the manifest's own `SETTLEMENT`/`CLV`
   components are ever `AVAILABLE` (they must always be
   `NOT_APPLICABLE_FOR_STAGE` placeholders on a `PRE_GAME_DECISION`
   manifest — see `lib.edgelab.snapshot`'s own look-ahead-bias guard).
   If this ever fires, it means a future `snapshot.py` change broke that
   guarantee, and candidate evaluation must fail loudly rather than
   silently ingest postgame data.

Settlement and CLV data enter the *comparison* step separately, joined
from a distinct, already-linked `POST_GAME_SETTLEMENT` snapshot (§8) —
never from anything passed into `run_candidate_replay()` itself.

## 6. Replay modes: CANDIDATE_MODEL vs HISTORICAL_PRODUCTION

**`CANDIDATE_MODEL`** (the only mode implemented this milestone): runs
the *current* checkout's production functions
(`scripts.build_market_ledger.compute_game_projection_context`/
`evaluate_game`, `scripts.risk_gate.apply_tt_safety`/
`apply_portfolio_rules`/`build_execution_artifact_payload`) against the
snapshot's frozen decision-time inputs. This doubles as the item-10
regression test: comparing today's code against a real historical
snapshot's original decision.

**`HISTORICAL_PRODUCTION`** (checking out and executing the exact
historical production commit captured in the snapshot): **unsupported
this milestone**, always `runStatus=REJECTED_UNSUPPORTED_MODE`, never a
fabricated result. Checking out and running an old commit inside a
shared research process is unsafe (dependency drift, no process
isolation, no guarantee the old commit even runs under the current
Python/toolchain) and unnecessary for this milestone's actual objective
— comparing today's code against historical inputs is exactly what a
regression/reproduction test needs, and doesn't require executing
historical code at all.

This is also why `replayFidelity` never reaches `LEVEL_3_BIT_FOR_BIT`
today: `productionModelCommitSha` (copied from the snapshot's own
`productionCommitSha`) is always `null` — no upstream artifact in this
repository records its own producing commit (a documented gap, same one
`SNAPSHOT_ARCHITECTURE.md` already notes) — so bit-for-bit historical
reproduction can never be honestly claimed yet.

## 7. Why replay does not duplicate model math

Every probability/edge/pricing computation is a direct call into
`scripts.build_market_ledger`'s already-pure functions
(`compute_game_projection_context`, `evaluate_game`) and
`scripts.risk_gate`'s already-isolable functions (`apply_tt_safety`,
`apply_portfolio_rules`, `build_execution_artifact_payload` — all three
operate on an in-memory slate dict with no file I/O). The one small
block replicated rather than imported is `risk_gate.py main()`'s inline
"PAPER_ONLY third pass" (`_apply_paper_only_downgrade`) — it is not
itself a callable function in `risk_gate.py`, and is a mechanical
decision-application step (no probability/pricing math), not model
logic. `tests/edgelab/test_replay.py`'s
`TestDeterministicReplay::test_real_game_with_identical_original_and_replayed_pipeline_is_unchanged`
proves this directly: original artifacts built from the same functions
replay calls come back `UNCHANGED` on every comparable market.

## 8. Decision comparison semantics

For every evaluated market row, `classify_comparison()` compares
original vs replayed `modelProb`, edge (`calibratedEdgeVsExecutable` if
present, else `edge`), recommendation status, and tier, and returns
exactly one of:

`UNCHANGED`, `PROBABILITY_CHANGED_ONLY`, `EDGE_CHANGED`,
`RECOMMENDATION_ADDED`, `RECOMMENDATION_REMOVED`, `TIER_UPGRADE`,
`TIER_DOWNGRADE`, `EXPRESSION_CHANGED` (reserved, never emitted this
milestone), `NOT_COMPARABLE` (neither side produced a probability),
`ORIGINAL_DATA_MISSING` (no original row exists for this market at all).

**A changed decision is never itself interpreted as an improvement.**
`classify_comparison()` is purely descriptive. Separately,
`classify_mismatch_reason()` gives a best-effort, evidence-based
category for *why* a changed decision occurred, checked in order:

1. `EXPECTED_MODEL_VERSION_CHANGED` — an F5 market row whose original
   `f5PricingVersion` differs from the current
   `F5_PRICING_VERSION_CURRENT` (a real, concrete signal already present
   on original rows — see `docs/F5_THREE_WAY_PRICING.md`).
2. `EXPECTED_CONFIG_INCOMPLETE` — the run's `EFFECTIVE_CONFIG_PARTIAL`
   limitation is active.
3. `REPLAY_ENGINE_DEFECT_SUSPECTED` — no concrete signal found; **never**
   assumed benign, flagged for manual investigation.

There is no fourth "production nondeterminism" branch with a concrete
detector today (nothing in this repository currently proves a specific
row is nondeterministic vs simply un-categorized) — a
`REPLAY_ENGINE_DEFECT_SUSPECTED` classification is the honest fallback
for that case too, pending a future concrete signal.

Real execution against 2026-08-01's Level-2-eligible snapshot found 24
changed decisions, all mechanically categorized
`EXPECTED_MODEL_VERSION_CHANGED` via the F5 pricing-version signal above
— a real, already-known, already-documented model change, not a replay
defect.

## 9. Settlement, CLV, and scoring

Settlement and CLV are joined from the same date's linked
`POST_GAME_SETTLEMENT` snapshot (`_linked_settlement_and_clv`): the
pregame snapshot's `snapshotId` must appear in the postgame snapshot's
`linkedSnapshotIds`, the postgame snapshot's own integrity must verify,
and matching is by `marketTicker` — settlement/CLV rules themselves are
never reimplemented here, only the already-computed frozen records
(`lib.edgelab.settlement`/`lib.edgelab.clv`'s own output) are read. A
market with no linked postgame snapshot, or no settlement/CLV record for
its ticker, is `UNRESOLVED` with an honest reason — never inferred from
a related-but-different market.

CLV here is a **market-level proxy**, not the real placed-bet CLV:
`closing_implied_pct - entry_implied_pct` (average of the closing
quote's yesBid/yesAsk minus the replayed row's own `kalshiVF`). This is
deliberately distinct from `lib.edgelab.clv`'s placed-bet CLV, since a
replayed decision may never have been placed as a real bet with a real
entry.

Scoring (`score_resolved_results`) applies only over settlement-resolved
YES/NO markets, and reports:

- `n`, `sampleSizeStatus` (`lib.edgelab.calibration`'s existing gate:
  `n<20` insufficient / `n<100` descriptive-only / `n>=100` calibrated —
  the number is always computed and returned, the status is a mandatory
  reading instruction, never a filter that withholds it)
- `winRate`, `expectedWinRate`, `calibrationError` (`winRate -
  expectedWinRate`)
- `avgBrierScore`, `avgLogLoss` (new, standard formulas — brand new to
  this repository, not duplicated from anywhere)
- `roi`: **always `null`.** ROI requires a real staked amount, and a
  replayed decision is research-only — it may never have been placed as
  a real `PlacedBet` with a real stake. Fabricating a flat-unit stake to
  compute a number would be inventing data never actually risked. The
  field exists (per this milestone's report requirements) but stays
  `null` until a real staking/ledger link is designed in a future
  milestone.

`performance` is `null` (not a fabricated all-zero report) when zero
markets resolved this run.

## 10. Walk-forward integrity

No fitted model exists yet in this repository, so there is nothing that
could "see" future data during a fit. The safeguards already active:

- **Structural leakage prevention** (§5): postgame/closing data can
  never enter `run_candidate_replay()`, enforced twice independently.
- **Chronological processing**: `sorted_snapshot_dates()` always returns
  dates oldest-first; `scripts/replay_eligibility_report.py` processes
  every date (and every run-key within a date) in that order, never
  discovery order.
- **No cross-run leakage of settled data**: settlement/CLV linkage reads
  strictly from the *same date's* linked postgame snapshot, never a
  cache spanning multiple dates.

Not yet relevant (will become so only once a future milestone introduces
a fitted model, per this milestone's explicit scope boundary — no model
fitting, uncertainty estimation, Kelly sizing, or portfolio optimization
is implemented here): candidate-parameter training-cutoff identification,
and cache-invalidation-by-date for a model whose parameters could
themselves encode future information.

## 11. Known schema-validator gotcha: nullable-but-required fields

`lib.edgelab.schema.validate_record()`'s validator is intentionally
shallow: any field listed in a schema's `required` array whose recorded
value is `None` is reported as a missing-field error, even when `None`
is the legitimate, honest value for that field in that state. This
milestone's development hit this twice:

- `snapshot_component.schema.json`'s `storageMode` (fixed in the
  Snapshot Foundation milestone, prior to this one).
- `replay_result.schema.json`'s `marketFamily` — found while dogfooding
  `scripts/run_replay.py` against real 2026-08-01 data (every market row
  without a resolvable ticker legitimately has `marketFamily=None`).
  Fixed the same way: removed from `required`, documented in the field's
  own schema description.

Any future field on either schema that can legitimately be `null` must
not be added to `required` — add it to `properties` with a `["...",
"null"]` type instead, exactly as `marketFamily` and
`originalPreferredExpression` already are.

## 12. Output structure

`data/edgelab/replay_runs/<replayRunId>/replay_run.json` (the ReplayRun)
and `replay_results.jsonl` (one ReplayResult per line), write-once with
the same discipline as `lib.edgelab.snapshot`: an identical rerun
verifies and no-ops (`writeOutcome: "noop_verified"`), a rerun with
*different* content under the same identity key is quarantined under
`conflicts/<timestamp>/` rather than overwriting the original.

`scripts/replay_eligibility_report.py` additionally writes
`data/edgelab/reports/replay_eligibility_report.json`: eligibility counts,
ineligible-reason counts, which replay runs were actually executed, and
aggregate settlement/CLV coverage across every snapshot this repository
has. Replayed research output is never mixed into `data/bets.json`,
`data/edgelab/recommendations/`, or any other live/production-read
ledger — it lives exclusively under `data/edgelab/replay_runs/`.

## 13. Sample-size policy

Same three-tier gate as `lib.edgelab.calibration`
(`INSUFFICIENT_SAMPLE`/`DESCRIPTIVE_ONLY`/`CALIBRATED` at n<20/n<100/n>=100)
— applied to `performance`'s settlement-resolved sample, never a separate
scheme invented for replay.

## 14. Why this remains research-only

Nothing in `lib/edgelab/replay.py`, `scripts/run_replay.py`, or
`scripts/replay_eligibility_report.py` writes to `data/slate.json`,
`data/bets.json`, `config/rules.json`, or any file a production workflow
reads. Every production function replay calls is invoked read-only
against an in-memory copy of frozen snapshot data
(`copy.deepcopy(normalized_games)` before mutation) — the live
production pipeline's own artifacts are never touched. This is verified
directly by `tests/edgelab/test_replay.py`'s
`TestResearchLiveDataIsolation`.

## 15. Known gaps blocking full historical reproduction

- **`HISTORICAL_PRODUCTION` mode is unsupported** (§6) — no snapshot
  today can be replayed against the exact historical commit that
  produced it.
- **`productionCommitSha` is always `null`** — no upstream artifact
  records its own producing commit, so `LEVEL_3_BIT_FOR_BIT` fidelity can
  never be honestly claimed yet, and `HISTORICAL_PRODUCTION` mode
  couldn't identify which commit to check out even if it were
  implemented.
- **`EFFECTIVE_CONFIG` is always `PARTIAL`** — hardcoded-in-code
  thresholds outside `config/rules.json` are not structurally captured,
  so a config-driven mismatch between original and replayed decisions
  cannot always be distinguished from a genuine model change without
  manual investigation (this is exactly what
  `MISMATCH_EXPECTED_CONFIG_INCOMPLETE` flags, honestly, rather than
  silently explaining away).
- **Only two of four existing historical snapshots are Level-2-eligible**
  (2026-07-30/07-31 remain `ELIGIBLE_LEVEL_1_ONLY` — a genuine historical
  data gap in what was captured for those dates, not a replay-engine
  bug).
- **`roi` is always `null`** (§9) — no staking/ledger link exists for a
  replayed (never-necessarily-placed) decision.
- **Settlement coverage is currently 0%** across every executed Level 2
  replay run to date — the linked `POST_GAME_SETTLEMENT` snapshots for
  the dates replayed so far have no resolved settlement records yet (CLV
  coverage is non-zero and works correctly independent of settlement).

## 16. Recommendation for the next milestone

The natural next milestone (backtesting/uncertainty) should build
directly on this replay engine's `CANDIDATE_MODEL` mode and
`ReplayResult` output rather than re-deriving historical
probability/edge data from scratch — this milestone's `performance`
scoring (Brier/log-loss/calibration error) is the natural foundation for
a future model-fitting evaluation harness, once a real training-cutoff
and walk-forward discipline (§10) is layered on top. Recommend
prioritizing (a) a real `productionCommitSha` capture path (unblocks
`LEVEL_3_BIT_FOR_BIT` and eventually `HISTORICAL_PRODUCTION` mode) and
(b) expanding the historical snapshot sample size before any fitted
model's calibration claims could be taken seriously (`CALIBRATED` needs
n>=100 resolved decisions; today's real sample is far below that).
