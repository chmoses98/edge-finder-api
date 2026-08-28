# Prospective Uncertainty Capture Infrastructure

Status: **COMPLETE. DATA COLLECTION ONLY.**

Wires MLB-RSCH-0019's research-only uncertainty-capture schema
(`lib/edgelab/research/uncertainty_capture_schema.py`, unchanged from
that experiment) into the live prospective-snapshot pipeline, so
structured pregame uncertainty metadata accumulates automatically going
forward. **No production probability, recommendation, confidence,
qualification, edge, Bet Up To, fee, bankroll/staking, market
eligibility, or risk-gate logic is touched.**

## 1. Design

Reuses the exact isolation pattern MLB-RSCH-0011's own shadow step
(`run_shadow_step` in `scripts/edgelab/run_prospective_snapshots.py`)
already established, rather than inventing a second one:

- `lib/edgelab/research/uncertainty_prospective_capture.py` --
  `build_uncertainty_capture_records_for_snapshot_cycle()`: pure, one
  try/except **per game** (a single bad game produces a
  `FAILED_ISOLATED` record with an explicit `failureReason`, never
  aborts the cycle), reuses the SAME `compute_projection_context_fn`
  the core cycle and the RSCH-0011 shadow step already call against the
  same game object (cheap, deterministic, no new expensive computation).
- `run_uncertainty_capture_step()` (new, in `run_prospective_snapshots.py`,
  mirrors `run_shadow_step` exactly) is called **strictly after** the
  core cycle's `new_records` have already been computed AND written to
  `data/edgelab/model_evaluations/`. Writes to its own separate,
  previously-nonexistent partition, `data/edgelab/uncertainty_capture_snapshots/<date>.jsonl`.
  Wrapped in its own outer try/except -- any failure, including an
  import failure of the capture module itself, is caught, logged to
  stderr, and never changes `main()`'s exit code.
- `lib/edgelab/prospective_snapshot.py` (the orchestration core) has
  **zero awareness** of the capture step -- the wiring lives entirely in
  the calling script, one step after the core cycle already returned.

## 2. Captured fields (where genuinely available, never fabricated)

Pulled directly from data the production game object already carries
(zero new network calls, zero new expensive computation):

| Field | Source |
|---|---|
| home/away offense sample depth | `game[side]TeamStats.gamesPlayed` |
| home/away bullpen sample depth | `game[side].bullpen.ip` |
| starter resolved | `game[side].pitcher.id is not None` |
| lineup confirmed | `game[side]TeamStats.lineupConfirmed` |
| mapping resolved | `bool(game.kalshiKey)` |
| stale age (minutes) | `now - game.lineupCheckedAt` |
| unsupported-feature fallback count | `len(projectionContext.missingFields)` |
| probability extremeness | `abs(game.modelProb - 0.5)` (production's own already-computed value) |
| total-projection extremeness | `abs(projectionContext.totalProj - 8.5)` (fixed, disclosed reference line) |
| component disagreement | **NOT_COMPUTED** -- production's projection-context function does not currently expose intermediate offense/run-prevention sub-signals as a separate field; computing it would require duplicating internal model logic, which this infrastructure change deliberately does not do |
| weather data availability | **NOT_COMPUTED** -- no weather field currently exists on the game object |

Every record carries a `fieldStatuses` companion dict (`AVAILABLE` /
`NOT_COMPUTED` / `UNRESOLVED`) alongside the schema's own value fields --
**no field is ever fabricated**; a missing input is always represented by
`None` + an explicit status, never a guessed placeholder.

## 3. Fail-safe isolation

Two independent layers, matching the MLB-RSCH-0011 precedent exactly:
1. Per-game isolation inside `build_uncertainty_capture_records_for_snapshot_cycle`
   (one bad game -> one `FAILED_ISOLATED` record, cycle continues).
2. An outer try/except around the entire step in `run_uncertainty_capture_step`
   (even a broken import of the capture module itself degrades to
   `(0, 0, "<error>")`, never raises into `main()`).

Called only after `storage.append_records(model_evaluations_path, ...)`
has already executed -- structurally cannot affect what was already
written.

## 4. Production equivalence

`tests/edgelab/test_uncertainty_prospective_capture.py::TestProductionEquivalence`
proves `run_prospective_snapshot_cycle`'s own `(new_records, run_log,
evaluated_snapshots)` are **identical** across three scenarios on
otherwise-identical inputs: capture never called ("disabled"), capture
called and succeeding, and capture called and raising internally.
Verified against a real evaluated game (not just an all-skipped cycle).

## 5. Tests

- `tests/edgelab/test_uncertainty_prospective_capture.py` -- 12 tests:
  schema-validity, explicit-missingness (including "no status ever
  claims AVAILABLE for a None value"), per-game fail-safe isolation,
  step-level fail-safe isolation (including a simulated broken import),
  the production-equivalence proof, and a no-recommendation/staking-
  import proof (AST-based, on actual imports only).
- `tests/edgelab/test_uncertainty_capture_schema.py` -- updated: the old
  "never wired into production" invariant is now explicitly the
  post-infrastructure invariant "never wired into **core** production
  betting logic" (market ledger, recommendations, risk gate, bankroll,
  and the orchestration core itself all remain unaware of the capture
  step's existence) while the isolated research-capture path is now a
  legitimate, tested importer.
- Full `tests/edgelab/` suite: **2,983 passed**.
- Live smoke test: `python3 scripts/edgelab/run_prospective_snapshots.py --dry-run`
  runs clean with the new wiring present (dry-run returns before either
  the shadow or uncertainty-capture step, matching prior behavior
  exactly; unaffected by this change).

## 6. Status

**Prospective capture: ACTIVE as of this merge.** Every future
prospective-snapshot cycle that evaluates at least one game will now
also attempt (best-effort, isolated) to append one uncertainty-capture
record per evaluated game to `data/edgelab/uncertainty_capture_snapshots/<date>.jsonl`.
No performance/uncertainty analysis is performed in this change -- this
is deliberately data collection only, to accumulate volume for a future
MLB-RSCH-0019-style Layer B study once enough settled games exist.
