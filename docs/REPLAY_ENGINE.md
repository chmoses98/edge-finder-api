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
§7.

**Level 1 (`ELIGIBLE_LEVEL_1_ONLY` / `LEVEL_1_APPROXIMATE`)** means one or
more *nice-to-have* inputs (lineup/bullpen/weather/market-observation/
park-factor evidence) are missing, so the replay is still real (same
production functions, same core inputs) but some context production had
may be absent. The CLI never runs this level unless the caller
explicitly asks (`--allow-approximate`) — see §12.

**What Level 2 does NOT and cannot claim, by construction of this
repository** (a maintainer review of PR #36 required this to be stated
explicitly rather than left implicit): recommendation thresholds, tier
thresholds, risk-gate concentration limits, and other "effective
production constants" are hardcoded directly in
`scripts/build_market_ledger.py`/`scripts/risk_gate.py` — **neither of
those files reads `config/rules.json` at runtime at all** (confirmed by
inspection: no `open()`/`json.load()` of the rules config anywhere in
either module). This means:

- `EFFECTIVE_CONFIG` being `PARTIAL` cannot possibly affect what code
  actually executes during replay — the thresholds that run are whatever
  is hardcoded in the current checkout, identically for original capture
  and for replay's own re-execution. `EFFECTIVE_CONFIG`'s completeness is
  purely an **audit/provenance** signal (can we explain, after the fact,
  whether a hardcoded threshold changed between capture and replay?), not
  an **input-fidelity** signal (did replay have what the code needed to
  run?). Level 2 eligibility is about the latter; `EFFECTIVE_CONFIG_PARTIAL`
  in `limitationReasons` on every real Level 2 run honestly flags the
  former as an open, permanent gap — never hidden.
- This is also *why* `LINEUP_STATE`, `EXECUTABLE_PRICES`, `BID_ASK`,
  `MARKET_OBSERVATIONS`, and `PARK_FACTORS` are correctly classified
  `NICE_TO_HAVE` rather than Level-2-required: none of them is actually
  read by `run_candidate_replay()`. Lineup fields
  (`lineupConfirmed`/`lineupStatus`/etc.) and park factor
  (`park.parkFactor`) are embedded directly inside each game dict in the
  **required** `NORMALIZED_SLATE` component — `LINEUP_STATE` and
  `PARK_FACTORS` are denormalized *pointers* to that same evidence, not a
  second, independently-required source. `EXECUTABLE_PRICES`/`BID_ASK`/
  `MARKET_OBSERVATIONS` are audit-trail captures of the full market
  universe; the executable price `evaluate_game()` actually computes
  (`executablePriceUsed`/`executableMarketProb`) is derived purely from
  the odds already embedded in the frozen game dict
  (`scripts/build_market_ledger.py:executable_ask_price_cents`), never
  from a separately-fetched live order-book read. Verified directly by
  `tests/edgelab/test_replay.py::TestExecutablePriceFidelity`.

Given this, **Level 2 is honestly named for what it is: production-
equivalent fidelity for every input the actual code path reads.** It is
not renamed/subdivided into `ELIGIBLE_LEVEL_2_PROBABILITY_REPLAY` vs
`ELIGIBLE_LEVEL_2_DECISION_REPLAY` because that distinction doesn't
apply here — §2 below shows the *entire* decision pipeline (not just
probability) is re-executed for `CANDIDATE_MODEL` mode. What Level 2
does NOT claim — bit-for-bit historical-commit reproduction, or complete
hardcoded-threshold provenance — was already out of scope for what any
snapshot in this repository could honestly support, and is called out
explicitly above and in §8/§18 rather than being implied by the name.

## 2. Replay scope: which production stages are re-executed

| Stage | Re-executed by replay? | How |
|---|---|---|
| Projection (runs/F5 runs) | **Yes** | `compute_game_projection_context()` called fresh on the frozen `NORMALIZED_SLATE` game dict |
| Pricing (vig-free %, executable price) | **Yes** | Inside `evaluate_game()`, over the same frozen odds |
| Edge calculation | **Yes** | Inside `evaluate_game()` (`calibratedEdgeVsExecutable` etc.) |
| Recommendation status (Accepted/Rejected) | **Yes** | `evaluate_game()`'s own gate logic, unmodified |
| Tier assignment (HIGH/MEDIUM/PAPER) | **Yes** | `apply_tt_safety()` + `apply_portfolio_rules()` (+ the replicated PAPER_ONLY third pass, §8) |
| Risk gate (TT safety, concentration limits) | **Yes** | `apply_tt_safety()` + `apply_portfolio_rules()`, called on the freshly-evaluated slate |
| Market-expression selection | **No** | `market_comparison.py`'s expression-preference engine is a separate, not-yet-wired research tool this milestone doesn't invoke — `originalPreferredExpression`/`replayedPreferredExpression` are always `null` |

The **original** side of every comparison (`original*` fields on
`ReplayResult`) is never re-executed — it is read verbatim from the
snapshot's frozen `RECOMMENDATION_OUTPUT`/`RISK_GATE_OUTPUT` components,
exactly as production wrote them at decision time. Only the **replayed**
side runs the production functions above, fresh, against the frozen
inputs. A replay that only recalculated probabilities and diffed them
against archived recommendation/tier/risk-gate output would NOT prove
what this milestone claims — this implementation re-executes the full
decision chain (through the risk gate and tier assignment, not just
`evaluate_game()`), which is what makes `originalRecommendationStatus`/
`originalTier` a genuine "what would today's code decide" comparison
rather than a probability-only proxy.

## 3. Replay eligibility rule table

`lib/edgelab/replay.py:assess_replay_eligibility()` evaluates, in order,
first match wins:

| # | Outcome | Condition |
|---|---------|-----------|
| 1 | `INELIGIBLE_UNSUPPORTED_VERSION` | `manifest.schemaVersion` not in the set this engine understands |
| 2 | `INELIGIBLE_INTEGRITY_FAILURE` | `snapshot.verify_snapshot()` fails (manifest hash or any frozen/referenced component's content hash doesn't match) |
| 3 | `RESEARCH_ONLY_NO_DECISION` | `snapshot.is_schedule_triggered_run(manifest)` — a schedule-triggered run never had an authoritative, risk-gated decision to begin with (fetch-slate.yml's BLOCK 7 never executes on a `schedule` trigger — a deliberate safety boundary). Checked before the decision-replay-specific questions below because they don't apply here: "there was a decision and we cannot replay it" (the `INELIGIBLE_*` family) and "there was never supposed to be a decision in this run type" (this) are different states. See corpus-health audit, 2026-08-25 follow-up. |
| 4 | `INELIGIBLE_TEMPORAL_SKEW` | `manifest.temporalConsistency.skewDetected` |
| 5 | `INELIGIBLE_MISSING_INPUT` | any Level-2-required component (`NORMALIZED_SLATE`, `RAW_PROJECTIONS`, `RECOMMENDATION_OUTPUT`, `MARKET_UNIVERSE`, `RISK_GATE_OUTPUT`, `EFFECTIVE_CONFIG`) is `MISSING` |
| 6 | `INELIGIBLE_CONFIG_AMBIGUITY` | `manifest.rulesConfigVersion` is `None` — cannot even identify which config version was in force |
| 7 | `ELIGIBLE_LEVEL_1_ONLY` | a nice-to-have component (`LINEUP_STATE`, `BULLPEN_STATE`, `WEATHER`, `MARKET_OBSERVATIONS`, `EXECUTABLE_PRICES`, `BID_ASK`, `PARK_FACTORS`) is `MISSING` |
| 8 | `ELIGIBLE_LEVEL_2` | otherwise |

`RESEARCH_ONLY_NO_DECISION` is not in `ELIGIBLE_STATUSES` (so
`execute_replay()` never runs candidate evaluation against it), but it is
also not an `INELIGIBLE_*` rejection: `execute_replay()` gives it its own
terminal `runStatus=NOT_APPLICABLE_NO_DECISION` — never
`REJECTED_INELIGIBLE` — since nothing about the snapshot is broken or
ineligible; a betting decision simply never existed for this run type. A
schedule-triggered run's `RISK_GATE_OUTPUT` is therefore never REQUIRED-
and-MISSING at the snapshot level either: see
`lib.edgelab.snapshot.build_pre_game_manifest()`, which records it as
`NOT_APPLICABLE_FOR_STAGE` (reason `NOT_APPLICABLE_FOR_RUN_TYPE_SCHEDULE_
TRIGGERED`) instead, and `effective_completeness_status()`, which
reclassifies an already-committed manifest predating this fix the same
way without altering its stored record.

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

## 4. ReplayRun / ReplayResult schemas

`data/edgelab/schema_v1/replay_run.schema.json` and `replay_result.schema.json`.
One `ReplayRun` per (snapshotId, replayMode, candidateModelCommitSha,
replayFrameworkVersion) — see `lib/edgelab/ids.py:build_replay_run_id`,
deterministic and write-once, same convention as `SnapshotManifest`'s own
identity. One `ReplayResult` per evaluated market row within a run.

Fields are never fabricated: `originalPreferredExpression` /
`replayedPreferredExpression` are always `null` this milestone (the
market-expression-preference concept lives in the separate, not-yet-wired
`market_comparison.py` research engine), and `performance.roi` is always
`null` (see §10). `marketFamily` is legitimately `null` (not a schema
violation) when a market row has no ticker — see §13 for why it is
deliberately **not** in `replay_result.schema.json`'s `required` array.

## 5. Replay loading and integrity

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

## 6. Structural postgame-leakage prevention

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
from a distinct, already-linked `POST_GAME_SETTLEMENT` snapshot (§10) —
never from anything passed into `run_candidate_replay()` itself.

## 7. Replay modes: CANDIDATE_MODEL vs HISTORICAL_PRODUCTION

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

This is also why `replayFidelity` never reaches `LEVEL_3_CODE_PINNED`
today: `productionModelCommitSha` (copied from the snapshot's own
`productionCommitSha`) is always `null` — no upstream artifact in this
repository records its own producing commit (a documented gap, same one
`SNAPSHOT_ARCHITECTURE.md` already notes) — so bit-for-bit historical
reproduction can never be honestly claimed yet.

**Candidate commit identity (maintainer review finding, item 4):**
`candidateModelCommitSha` is `git rev-parse HEAD` at execution time —
but a bare commit SHA would misleadingly label a dirty working tree's
output with a commit whose *committed* content isn't what actually ran.
`lib.edgelab.replay._candidate_model_commit_identity()` checks `git diff
--quiet HEAD` and suffixes the SHA `-dirty` (standard git convention)
whenever tracked files differ from `HEAD`, recording
`CANDIDATE_WORKING_TREE_DIRTY` in `limitationReasons`. Since
`candidateModelCommitSha` is part of `replayRunId`'s identity
(`lib/edgelab/ids.py:build_replay_run_id`), a dirty run can never
collide with, or be silently confused with, a clean run at the same
commit. Verified by `tests/edgelab/test_replay.py::TestCandidateCommitIdentity`.

## 8. Why replay does not duplicate model math

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

## 9. Decision comparison semantics

For every evaluated market row, `classify_comparison()` compares
original vs replayed `modelProb`, edge (`calibratedEdgeVsExecutable` if
present, else `edge`), recommendation status, and tier, and returns
exactly one of:

`UNCHANGED`, `PROBABILITY_CHANGED_ONLY`, `EDGE_CHANGED`,
`RECOMMENDATION_ADDED`, `RECOMMENDATION_REMOVED`, `TIER_UPGRADE`,
`TIER_DOWNGRADE`, `EXPRESSION_CHANGED` (reserved, never emitted this
milestone), `NOT_COMPARABLE`, `ORIGINAL_DATA_MISSING`.

**Three-way null-probability split (maintainer review finding, item 6):**
a naive two-way check ("do both sides have a probability?") would
collapse three materially different situations into one bucket. This
engine distinguishes:

- **Original row missing entirely, or exists but never produced a
  `modelProb`** (e.g. classified `"Missing Data"` at capture time) →
  `ORIGINAL_DATA_MISSING`. There is no real historical baseline to
  compare against — this must never be reported as
  `PROBABILITY_CHANGED_ONLY`/`RECOMMENDATION_ADDED`, which would
  misrepresent "we have no original to compare" as "the decision
  changed."
- **Neither side ever produces a probability** (e.g. `RL_Away`/`RL_Home`
  — a market this pipeline structurally never prices, on either the
  original or replayed side) → `NOT_COMPARABLE`. Confirmed against real
  2026-08-01/08-02 data: 85 such rows exist and correctly stay
  `NOT_COMPARABLE` under this rule.
- **Original has a valid probability but replay itself fails to produce
  one** → `NOT_COMPARABLE` with a distinct reason
  (`REPLAY_DID_NOT_PRODUCE_A_PROBABILITY`) — a real, replay-side finding,
  never mislabeled as an archived-data problem.

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

## 10. Settlement, CLV, and scoring

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
quote's yesBid/yesAsk minus the replayed row's own executable entry
price — `executableMarketProb`, falling back to `kalshiVF` only when a
row never had a real executable price at all). This is deliberately
distinct from `lib.edgelab.clv`'s placed-bet CLV, since a replayed
decision may never have been placed as a real bet with a real entry.

**Original market-price fidelity (maintainer review finding, item 8):**
`originalMarketPrice`/`replayedMarketPrice` on `ReplayResult` are
`kalshiVF` — the vig-free **midpoint**, copied verbatim, never a later
registry snapshot, closing price, last trade, or reconstructed
complement. This is display parity with the original artifact, **not**
the price that actually gated the recommendation. The price that did is
exposed separately: `original/replayedExecutablePriceUsed` (cents) and
`original/replayedExecutableMarketProb` (probability scale) — copied
verbatim from the row's own `executablePriceUsed`/`executableMarketProb`
fields, which `evaluate_game()` derives purely from odds already
embedded in the frozen `NORMALIZED_SLATE` game dict (§2), never a live
price feed. Both are `null` when the row never had a real executable
price to begin with — never silently substituted with the midpoint or
anything else. Verified end-to-end by
`tests/edgelab/test_replay.py::TestExecutablePriceFidelity`.

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

## 11. Walk-forward integrity

No fitted model exists yet in this repository, so there is nothing that
could "see" future data during a fit. The safeguards already active:

- **Structural leakage prevention** (§6): postgame/closing data can
  never enter `run_candidate_replay()`, enforced twice independently.
- **Chronological processing**: `sorted_snapshot_dates()` always returns
  dates oldest-first; `scripts/replay_eligibility_report.py` processes
  every date (and every run-key within a date) in that order, never
  discovery order.
- **No cross-run leakage of settled data**: settlement/CLV linkage reads
  strictly from the *same date's* linked postgame snapshot, never a
  cache spanning multiple dates.
- **No wall-clock fallback** (maintainer review finding, item 9/10):
  `apply_tt_safety()`/`apply_portfolio_rules()` default to the real
  wall clock (`datetime.now()`) via `check_game_status()` when their
  `now_ts` argument is `None` — a determinism hole if `run_candidate_replay()`
  ever ran with a missing `productionRunId`, since the frozen decision-time
  reference is what's passed as `now_ts`. `run_candidate_replay()` now
  raises `ReplayError` up front if `manifest.productionRunId` is falsy,
  rather than silently letting the game-skip decision depend on when
  replay happens to run. Every `ELIGIBLE` `PRE_GAME_DECISION` snapshot has
  a real `productionRunId` by construction (it requires `RECOMMENDATION_OUTPUT`
  to be `AVAILABLE`, which implies a real `recommendations.json` existed
  to derive the run key from) — this is belt-and-suspenders, not a change
  to any real snapshot's eligibility.

Not yet relevant (will become so only once a future milestone introduces
a fitted model, per this milestone's explicit scope boundary — no model
fitting, uncertainty estimation, Kelly sizing, or portfolio optimization
is implemented here): candidate-parameter training-cutoff identification,
and cache-invalidation-by-date for a model whose parameters could
themselves encode future information.

## 12. CLI usage

`scripts/run_replay.py` (see its own docstring for the full flag list):

```
# Dry run: assess eligibility only, never execute or write output.
python3 scripts/run_replay.py --date 2026-08-01 --eligibility-only

# Replay the latest PRE_GAME_DECISION run for a date (refuses if only
# Level-1-eligible, per the no-silent-downgrade rule below).
python3 scripts/run_replay.py --date 2026-08-01

# Replay a specific run key, or an exact snapshotId.
python3 scripts/run_replay.py --date 2026-08-01 --production-run-key 2026-08-01T22:10:49Z
python3 scripts/run_replay.py --snapshot-id <sha1 hex>

# Explicitly permit an ELIGIBLE_LEVEL_1_ONLY snapshot to run at Level 1
# fidelity -- never silent, must be requested.
python3 scripts/run_replay.py --date 2026-07-30 --allow-approximate

# Always rejected honestly this milestone (see §7), never a fabricated result.
python3 scripts/run_replay.py --date 2026-08-01 --mode HISTORICAL_PRODUCTION
```

The CLI refuses to run an ineligible Level 2 replay unless
`--allow-approximate` is explicitly passed (`runStatus=REJECTED_INELIGIBLE`,
exit code 1) — confirmed against real data in §17's per-snapshot table
(2026-07-30/07-31 are refused without the flag). Output is validated
against `replay_run.schema.json`/`replay_result.schema.json`
(`lib.edgelab.schema.validate_record`) before being reported; schema
warnings are printed to stderr but never block a completed run from
being written (a validation gap is itself useful signal, not a reason to
silently drop real replay output).

`scripts/replay_eligibility_report.py` runs the same eligibility
assessment across every existing snapshot, chronologically
(`sorted_snapshot_dates`, §11), and executes actual Level 2 replay only
for snapshots that assess `ELIGIBLE_LEVEL_2` — it never passes
`--allow-approximate` on a caller's behalf, so Level-1-only snapshots are
always classified-only in the unattended batch report, never silently
promoted or auto-approximated.

## 13. Known schema-validator gotcha: nullable-but-required fields

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

## 14. Output structure

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

## 15. Sample-size policy

Same three-tier gate as `lib.edgelab.calibration`
(`INSUFFICIENT_SAMPLE`/`DESCRIPTIVE_ONLY`/`CALIBRATED` at n<20/n<100/n>=100)
— applied to `performance`'s settlement-resolved sample, never a separate
scheme invented for replay.

## 16. Why this remains research-only

Nothing in `lib/edgelab/replay.py`, `scripts/run_replay.py`, or
`scripts/replay_eligibility_report.py` writes to `data/slate.json`,
`data/bets.json`, `BET_LOG.md`, `config/rules.json`, or any file a
production workflow reads. Every production function replay calls is
invoked read-only against an in-memory copy of frozen snapshot data
(`copy.deepcopy(normalized_games)` before mutation) — the live
production pipeline's own artifacts are never touched. This is verified
directly by `tests/edgelab/test_replay.py::TestResearchLiveDataIsolation`,
including a test that writes real sentinel content to `bets.json`/
`BET_LOG.md`/`slate.json` before running replay and proves every byte is
unchanged after.

**Loader isolation (maintainer review finding, item 3):**
`lib.edgelab.snapshot.load_frozen_component()` reads exclusively from
each component's `snapshotPath` (the frozen copy physically committed
under `data/edgelab/snapshots/`), never `sourcePath` (the live
location) — confirmed by direct inspection, and by
`tests/edgelab/test_replay.py::TestResearchLiveDataIsolation::test_replay_ignores_poisoned_live_source_files_after_snapshot_capture`,
which overwrites every live source file a snapshot's components were
frozen from with obviously-wrong sentinel values *after* capture, then
proves the replayed output is byte-for-byte unaffected. A second test,
`TestPostgameLeakagePrevention::test_real_enticing_postgame_data_present_in_linked_snapshot_does_not_change_replay_output`,
goes further than the structural `AVAILABLE`-flag guards (§6): it places
real settlement/CLV data in a genuinely linked `POST_GAME_SETTLEMENT`
snapshot and proves `run_candidate_replay()`'s own numeric output is
unchanged by its mere presence, while confirming the full
`execute_replay()` orchestration *does* pick it up for comparison/scoring
— proving the postgame data is genuinely reachable, not simply
disconnected.

## 17. Known gaps blocking full historical reproduction

- **`HISTORICAL_PRODUCTION` mode is unsupported** (§7) — no snapshot
  today can be replayed against the exact historical commit that
  produced it.
- **`productionCommitSha` is always `null`** — no upstream artifact
  records its own producing commit, so `LEVEL_3_CODE_PINNED` fidelity can
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
- **`roi` is always `null`** (§10) — no staking/ledger link exists for a
  replayed (never-necessarily-placed) decision.
- **Settlement coverage is currently 0%** across every executed Level 2
  replay run to date — the linked `POST_GAME_SETTLEMENT` snapshots for
  the dates replayed so far have no resolved settlement records yet (CLV
  coverage is non-zero and works correctly independent of settlement).

## 18. Recommendation for the next milestone

The natural next milestone (backtesting/uncertainty) should build
directly on this replay engine's `CANDIDATE_MODEL` mode and
`ReplayResult` output rather than re-deriving historical
probability/edge data from scratch — this milestone's `performance`
scoring (Brier/log-loss/calibration error) is the natural foundation for
a future model-fitting evaluation harness, once a real training-cutoff
and walk-forward discipline (§11) is layered on top. Recommend
prioritizing (a) a real `productionCommitSha` capture path (unblocks
`LEVEL_3_CODE_PINNED` and eventually `HISTORICAL_PRODUCTION` mode) and
(b) expanding the historical snapshot sample size before any fitted
model's calibration claims could be taken seriously (`CALIBRATED` needs
n>=100 resolved decisions; today's real sample is far below that).
