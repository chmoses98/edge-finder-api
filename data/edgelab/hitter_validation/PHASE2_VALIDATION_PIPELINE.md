# Hitter Projection Validation — Phase 2: Durable Pipeline + Diagnostics

Status: **infrastructure + measurement + analysis only**. Per explicit
instruction, no hitter projection formula, weight, prior, probability
calibration, edge threshold, ranking logic, or production betting logic
was changed in this phase. This document summarizes six workstreams
built on top of the Phase 1 audit (`summary.md`, `recommendations.md`).

## 1. Completed retrospective grading

**Attempted, and honestly blocked by this environment, not by
missing data or missing code.** `scripts/edgelab/settle_markets.py`
(the existing, unmodified, already-merged settlement pipeline — see
§4) was run directly against `--date 2026-08-14`. It executed safely
and correctly: it never fabricated a result, and correctly reported
`settled_or_void=0` because every boxscore/linescore fetch to
`statsapi.mlb.com` failed with a network-policy rejection
(`gateway answered 403 to CONNECT`, confirmed via this session's own
proxy status endpoint — this sandbox's outbound egress to
`statsapi.mlb.com` is policy-blocked, identical in kind to the
Kalshi-API block documented in `docs/research/PROJECTION_AUDIT.md`).

The resulting all-`SETTLEMENT_UNRESOLVED` output (4,563 markets, 0
settled) was **not committed** — it would have added a large (6.7MB),
zero-value file to the repository and could mislead a future reader
into thinking settlement had been attempted-and-failed for a data
reason, when the true cause is this environment's network policy. The
89 unresolved 2026-08-14 hitter rows remain exactly as Phase 1 left
them: `UNRESOLVED` / `NO_SETTLEMENT_RECORD_FOUND`. **No audit rerun
was needed for this reason** — the counts below are unchanged from
Phase 1 for that specific gap; see §6 for what DID change.

**Concrete next step** (not performed here): re-run
`python3 scripts/edgelab/settle_markets.py --date 2026-08-14` from an
environment with real network access (e.g. the actual GitHub Actions
runner — `edgelab-postgame.yml` already runs this exact script,
chained after "Update CLV (Post-Slate Review)"'s `workflow_run`
completion; 2026-08-14's specific miss appears to be a one-off trigger
gap in that chain, not a design defect) — then re-run
`scripts/research/build_hitter_projection_audit.py`, which is fully
idempotent and safe to run repeatedly.

**Re-search for missed hitter projection artifacts**: repeated the
Phase 1 archive search (`find . -iname "*hitter*"`, cross-checked
against `data/pipeline/*/hitter_research_capture.json` run-metadata,
`archive/data/`, and every `data/edgelab/research_runs/*.jsonl` entry
mentioning "hitter"). **Confirmed: no additional prospective hitter
projection artifact exists anywhere in this repository beyond the same
5 dates Phase 1 already found** (2026-08-13 through 2026-08-17). This
phase's new checkpoint scheduler (§3) is what starts growing that set
going forward.

## 2. Snapshot filename/date bug — root cause and fix

**Root cause** (confirmed by direct code inspection, not inferred):
`.github/workflows/kalshi-price-check.yml`'s "Archive an unfiltered
complete-market capture" step computed the snapshot filename's date
component (`DATE`) in `America/New_York` local time (this repository's
consistent MLB-slate-date convention — every other workflow's own
`DATE` variable uses the identical `TZ='America/New_York' date
+%Y-%m-%d`) but computed the filename's TIME component (`TS`) in raw
UTC (`date -u +%H%M%S`) — the **only** place in this repository's
entire workflow set mixing the two timezones for one filename
(verified: every other DATE/timestamp pair in this repo consistently
uses `America/New_York` for both, or UTC for both — grepped across all
`.github/workflows/*.yml`). For any run between roughly 20:00–23:59 ET,
UTC has already rolled to the next calendar day, so the filename's date
and time segments silently encoded two different calendar days. Real
example found during Phase 1: `kalshi_search_2026-08-15_003948_standalone.json`
whose actual capture instant (`fetched_at` inside the file, and the
`marketObservedAt` every downstream row derives from it) is
`2026-08-16T00:39:48Z` — the run was triggered with `--date 2026-08-15`
shortly after UTC midnight (still evening in ET).

**Fix** (`.github/workflows/kalshi-price-check.yml`, one line):
`TS=$(date -u +%H%M%S)` → `TS=$(TZ='America/New_York' date +%H%M%S)` —
`TS` now computed in the same timezone as `DATE`, matching every other
workflow's own convention. `DATE` itself is unchanged (still ET,
still the correct slate-date grouping key). This is a naming/
provenance fix only — it never touches which snapshot data is fetched,
what it's priced against, or how a row's actual `marketObservedAt`/
`projectionGeneratedAt` timestamps (always independently, correctly
UTC — unaffected by this bug) are computed.

**Deterministic tests added**:
`tests/test_kalshi_price_check_workflow.py::TestCorpusArchiveSnapshotFilenameTimezoneConsistency`
(3 tests) — pins the fixed `TZ='America/New_York' date +%H%M%S` line,
asserts `date -u +%H%M%S` is gone, and asserts `DATE` itself is
untouched (this fix must never change which slate date a late-evening
run's snapshot is filed under).

**Verified no similar bug exists elsewhere**: grepped every
`.github/workflows/*.yml` for `TZ='America/New_York'` and
`date -u`/`+%H%M%S` pairings, and every hitter-pipeline Python script's
own timestamp generation (`scripts/build_hitter_projection_board.py`,
`scripts/fetch_standalone_pregame_context.py`,
`scripts/run_standalone_hitter_research.py`). Every other timestamp in
the hitter pipeline is generated by a single Python
`datetime.now(timezone.utc)` call (internally consistent by
construction — one clock, one call) or a single-timezone bash `date`
call. This was an isolated, one-line bug.

**Are any already-archived records ambiguous because of this bug?**
Yes, exactly the 504 rows from the 2026-08-15 board (already
identified and separately flagged in Phase 1's
`provenance_audit.json.snapshotFilenameDateMismatch`, count=504,
`affectedDates: ["2026-08-15"]`). Those rows are **not** leakage-risky
(their real `marketObservedAt`/`projectionGeneratedAt` timestamps are
correct and pregame) — only their `sourceCapturePath` FILENAME
misrepresents its own capture date. Phase 1's provenance-confidence
classifier (`classify_provenance_confidence`) was already built to
never trust the filename for this exact reason — it uses
`marketObservedAt` (a field inside the row, not parsed from a
filename) as the primary contemporaneity signal, with the filename's
mere on-disk EXISTENCE as a secondary corroboration only. No record in
`primaryMetricRowCount` was excluded or is newly excluded because of
this bug; the finding is reported for transparency, not because it
changed any metric.

## 3. Hitter engine scheduling — checkpoint orchestration added

**New**: `lib/research/hitter_prospective_snapshot.py` +
`scripts/edgelab/run_hitter_prospective_snapshots.py` +
`.github/workflows/hitter-snapshot-scheduler.yml` (own dedicated
`edgelab-hitter-snapshot` concurrency group; shares no job/step/group
with `capture-snapshots-scheduled.yml`, `fetch-slate.yml`,
`model-snapshot-scheduler.yml`, or `kalshi-price-check.yml`).

**Checkpoints implemented**: `T_MINUS_90`, `T_MINUS_60`, `T_MINUS_30`,
`LINEUP_CONFIRMATION`, `HITTER_CLOSING_WINDOW` (a distinctly-named
closing-window label — deliberately never reusing
`lib.edgelab.prospective_snapshot.MODEL_CLOSING_WINDOW`, which names
the GAME-LEVEL Poisson model's own closing window, nor the bare
`"CLOSING"` label reserved for the Kalshi market's own closing quote —
three different concepts, three different names, matching this
repository's own established discipline).

**Reuse, not duplication**: the pregame-safety eligibility check
(`classify_game_eligibility`), the lineup live-poll ordering
(`refresh_lineup_fields`), and the checkpoint classifier itself
(`lib.edgelab.checkpoints.classify_checkpoint`) are all imported
UNCHANGED from `lib.edgelab.prospective_snapshot` — the same functions
the game-level scheduler already uses. The actual "evaluate" step
reuses `scripts.build_hitter_projection_board.main()` — the exact same
production hitter engine every manual run has always used — via one
small, additive parameter (`emit_rows=True`, opt-in, changes no
existing caller's return shape) rather than a second implementation.

**Cost containment**: because the hitter engine's Monte Carlo evaluate
step is materially more expensive than the game-level Poisson model's
(a real archived full-slate run took 1,213 seconds), this scheduler
never evaluates the whole day's slate per cycle. It writes a small,
run-and-checkpoint-scoped FILTERED slate file containing only the
games actually due this cycle and points the (otherwise unmodified)
board builder at that filtered file — bounding cost to due games only,
never the full slate, and never touching
`data/pipeline/<date>/hitter_projection_board.json` (every internal
call passes `dry_run=True`).

**Storage — append-only, provenance-safe, never overwrites a prior
capture**: every row goes to
`data/edgelab/hitter_projection_snapshots/<date>.jsonl` via
`lib.edgelab.storage.append_records` (the same idempotent, ID-keyed
append pattern `data/edgelab/model_evaluations/<date>.jsonl` already
uses) — a genuinely new entity, distinct from the legacy single-file
board (which a same-day rerun still silently overwrites — a
pre-existing, documented, UNCHANGED limitation of that older artifact).
Two different checkpoints for the same ticker always produce two
different, both-preserved rows
(`lib.edgelab.ids.build_hitter_projection_snapshot_id =
sha1(runId, marketTicker, checkpoint)`). Idempotency itself comes from
`already_captured_hitter_checkpoints` refusing to re-evaluate an
already-captured checkpoint — the same mechanism (not runId collision)
the game-level system's own idempotency actually relies on.

**Provenance preserved per row**: `gameId`, `checkpoint`,
`researchRunId`, `engineCommitSha` (the repo's own git commit at
capture time — reusing `lib.edgelab.model_evaluation._git_commit_sha`,
the SAME provenance convention `ModelEvaluation.modelCommitSha` already
establishes), `snapshotGeneratedAt`, `sourceCapturePath`,
`marketObservedAt`, `projectionGeneratedAt`, and every existing board-
row field (`modelProbability`, `executableKalshiPrice`, lineup status
via `projectionStatus`, etc.) passed through unchanged. New schema:
`data/edgelab/schema_v1/hitter_projection_snapshot.schema.json`.

**Failure isolation**: one checkpoint group's hitter-engine exception
never erases another group's rows or aborts the cycle (per-checkpoint-
group try/except, tested). The workflow itself shares no job/
concurrency-group with any production capture workflow, so a failure
here structurally cannot block them — this is additionally true of the
workflow's own reliability posture (no `continue-on-error` at the job
level; a whole-run persistence failure still surfaces visibly with a
recoverable backup artifact, mirroring `model-snapshot-scheduler.yml`
exactly).

**Cadence**: every 30 minutes during the MLB window (coarser than the
game-level scheduler's 15 minutes, deliberately, given the higher
per-cycle compute cost) — bounds worst-case overlapping-run risk while
still catching each checkpoint target within
`classify_checkpoint`'s own ±7.5 minute tolerance.

**Status**: research-only, exactly like every other prospective
snapshot system in this repository. No recommendation, staking, or
settlement logic reads from this new entity.

Tests: `tests/research/test_hitter_prospective_snapshot.py` (19),
`tests/edgelab/test_hitter_snapshot_scheduler_workflow.py` (17),
`tests/edgelab/test_run_hitter_prospective_snapshots_script.py` (11),
plus 3 new regression tests on `scripts/build_hitter_projection_board.py`'s
additive `emit_rows=` parameter.

## 4. Issue #43 status

**Already fully implemented and merged** — PR #44, closed
`state_reason: completed`. Verified directly against the live issue via
the GitHub API this session (not assumed from prior documentation):
issue #43's full requested scope (`pitcher_strikeouts`, `pitcher_outs`,
`hitter_hits`, `hitter_total_bases`, `hitter_hits_runs_rbis`,
`hitter_rbis`, `hitter_stolen_bases` — all 7 families) is implemented,
tested, and already producing real settled outcomes in this
repository's archive (43,661+ real hitter-family `Settlement` rows
found and used throughout this whole audit). **The task brief that
requested this work described #43 as not-yet-implemented — that
description was out of date; no new settlement scope needed to be
added.**

What genuinely remains, per the issue's own original design docs
(`docs/PLAYER_PROP_SETTLEMENT.md` §6), is two DELIBERATELY unaddressed
gaps, both correctly left as-is by the original implementation and
correctly left as-is by this phase too:

- **`kalshiOfficialResult` is always `null`** — no ingestion path in
  this repository captures Kalshi's own settlement result for these
  series (requires authenticated Kalshi API access this repository
  does not have, and this sandbox's network policy blocks even
  unauthenticated calls). The conflict-detection code path that would
  use it is implemented and unit-tested, but has nothing to activate it
  from. **Not addressed here** — no safe way to add real ingestion
  without genuine API access.
- **No automatic VOID for a non-participating player** — deliberately
  never implemented, per the original issue's own explicit instruction,
  because no Kalshi rules-text/void-condition evidence exists anywhere
  in this repository's ingestion to support one. **Not addressed
  here** — implementing a guessed VOID rule would directly violate this
  mission's "no guessing on ambiguous markets" requirement and the
  original design's own stated safety reasoning. This is the correct
  call to leave alone, not a gap to close.

**This phase's actual, safe contribution regarding #43**: confirmed
(by directly running it) that the existing, unmodified settlement
pipeline behaves safely under a real network failure (never
fabricates a result — see §1), and that this phase's own new research
artifacts (the checkpoint-scheduler corpus, the audit, the edge
diagnostic) read the EXISTING settlement pipeline's output read-only
and never touch `data/edgelab/bets/bets.jsonl` or any canonical
placed-bet record — keeping research-projection grading and actual
wager settlement exactly as separate as the mission requires. No
settlement-semantics code was changed.

## 5. Edge-inversion diagnostic

See `edge_inversion_diagnostic.md` / `edge_inversion_diagnostic.json`
for the full writeup. Headline finding: the large-edge (≥5pp)
underperformance found in Phase 1 **survives de-correlation** — it is
negative under row-weighting (−18.7% ROI), under (date, player)
cluster-weighting (−12.0%, 58% of 124 clusters net-negative), and under
(date, game) cluster-weighting (−13.1%, 63% of 8 clusters
net-negative). The most concrete, actionable-looking mechanism found:
very cheap (<10¢) longshot-priced large-edge contracts lost −97.4% of
stake (N=39, real, not a single outlier) — a small absolute
calibration error at a long price is proportionally enormous. Also
found: bottom-of-order hitters show the worst large-edge calibration
AND ROI of any lineup slot; `hitter_rbis` is the one family where large
declared edge still looks directionally useful. Analysis only — no
tuning performed or recommended for immediate implementation.

## 6. Continuous validation output

`scripts/research/build_hitter_projection_audit.py` (already the
canonical, idempotent, rerunnable-anytime audit entry point from Phase
1) extended with:

- **Independent-evidence counts** (`independent_evidence_counts`) —
  distinct dates, distinct (date, game) pairs, distinct (date, player)
  pairs — now reported alongside raw row N in `summary.json`, every
  `overall_calibration()` result, and every `roi_simulation()` result,
  everywhere in the report tree. Current corpus: **1,783 raw rows, but
  only 3 distinct dates / 8 distinct game-dates / 125 distinct
  player-dates** — this number is now impossible to miss in any report
  this pipeline produces.
- **Checkpoint-scheduler corpus integration** — `build_full_corpus` now
  discovers and grades `data/edgelab/hitter_projection_snapshots/*.jsonl`
  (the new §3 entity) alongside the legacy single-file board, tagging
  every row's `provenanceSource` (`LEGACY_SINGLE_FILE_BOARD` vs.
  `CHECKPOINT_SCHEDULER`) so the two are never silently blended. This
  is what makes future audit reruns automatically pick up new
  checkpoint-tagged data as §3's scheduler accumulates it — no manual
  wiring needed for the next rerun.
- **Snapshot-timing report** (`snapshot_timing.json`) — calibration/ROI/
  CLV broken out by checkpoint label, ready to become informative the
  moment real multi-checkpoint data exists (today it correctly reports
  one bucket, `LEGACY_NO_CHECKPOINT_LABEL`, honestly reflecting that no
  real checkpoint diversity exists yet).
- **Versioned history** (provenance preservation) — every run now
  additionally archives a complete, timestamped copy of every report to
  `data/edgelab/hitter_validation/history/<UTC-run-timestamp>/` before
  overwriting the top-level "latest" files, mirroring this
  repository's own existing `latest_*.json` + dated-report convention
  (`data/edgelab/analytics/`, `data/edgelab/reports/`). A prior run's
  full output is never silently lost when a later rerun's settlement
  data changes the numbers. `--no-history` skips this for a fast local
  loop.
- **New edge-inversion diagnostic artifact** (§5) — a dedicated,
  separately-rerunnable script
  (`scripts/research/build_edge_inversion_diagnostic.py`), reusing the
  audit's own graded-row corpus rather than a second grading pass.

All of the above is exercised by the existing idempotency tests plus
new dedicated tests (see Testing section of the top-level report).
