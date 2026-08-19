# Hitter Snapshot Scheduler — Runtime Hardening & Observability

Status: **infrastructure/observability hardening only**. No hitter
projection formula, weight, prior, probability calibration, `n_sims`,
edge threshold, ranking logic, or production betting logic was changed.
This document is the audit/derivation writeup for the timeout and
logging changes described here, mirroring
`docs/HITTER_CHECKPOINT_COVERAGE_FIX.md`'s format for the (separate,
already-resolved) scheduling-coverage work.

## 1. The incident

Workflow run
[`32189380616`](https://github.com/chmoses98/edge-finder-api/actions/runs/32189380616)
(`hitter-snapshot-scheduler.yml`, dry-run smoke test dispatched
2026-08-18 21:45:34 UTC against a real 15-game MLB slate) was
**cancelled** by the job's then-configured `timeout-minutes: 25` at
~22:10:57 UTC, mid-execution, with **zero log output** between the
script invocation (21:45:52 UTC) and the cancellation — the only lines
in the job log before `##[error]The operation was canceled.` are the
checkout/setup-python steps; the "Run hitter prospective snapshot
cycle" step itself never printed anything.

## 2. Root cause: two separate, compounding issues

**(a) Genuine, legitimate multi-checkpoint-group cost, not a hang.**
`run_hitter_prospective_snapshot_cycle` (`lib/research/hitter_prospective_snapshot.py`)
groups this cycle's due games **by checkpoint** and issues one separate
call into the hitter engine's Monte Carlo evaluate step
(`scripts.build_hitter_projection_board.main`, `n_sims=1500` by
default) **per checkpoint group** — the due-game filtering that bounds
each individual call's game count does *not* bound how many such calls
happen in one 15-minute cycle. At 2026-08-18 21:45–22:10 UTC (5:45–6:10
PM ET), the 15-game slate that day had games clustered at several
different start times (6:35–6:40 PM ET and 7:10–7:40 PM ET among them),
so multiple checkpoint groups (at minimum a `T_MINUS_60`-ish cluster and
a `T_MINUS_90`-ish cluster; plausibly `LINEUP_CONFIRMATION` for still
more games) were very likely simultaneously due — several separate,
real Monte Carlo evaluate calls, not one. `docs/HITTER_SIMULATION_ENGINE.md`
Sec.11 documents a real archived **full-slate, single-call** run at
1,213 seconds (~20.2 min); multiple separate calls in one cycle can
easily exceed that individually-smaller-but-additively-larger total.

**(b) No progress visibility to distinguish (a) from an actual hang.**
Before this change, `run_hitter_prospective_snapshot_cycle` produced
**no output at all** until it returned — no per-game, per-checkpoint, or
per-batch signal — and the CLI wrapper
(`scripts/edgelab/run_hitter_prospective_snapshots.py`) only prints its
own summary line *after* the cycle function returns. A run that was
genuinely making healthy forward progress through several expensive but
legitimate Monte Carlo batches was therefore **indistinguishable in the
Actions log from a silently hung process** — this is the actual
observability gap this document's changes close.

## 3. Confirmed: no corrupt or partial persisted state

- This specific run was dispatched with `dry_run: 'true'`. The CLI
  script's own `if args.dry_run: ...; return 0` gate sits *before*
  every persistence call
  (`scripts/edgelab/run_hitter_prospective_snapshots.py`), and the
  process was killed while still deep inside
  `run_hitter_prospective_snapshot_cycle` — it never reached that
  return, let alone the persistence calls after it.
- Independently, the workflow's own "Commit new hitter projection
  snapshots" step is gated on
  `if: github.event.inputs.dry_run != 'true'`, so even a hypothetical
  future non-dry-run cancellation at this exact point would still never
  reach a commit — GitHub Actions never runs a later step after a job
  is cancelled mid-step.
- No `data/edgelab/hitter_projection_snapshots/*.jsonl`,
  `data/edgelab/research_runs/*.jsonl`, or any other tracked file was
  touched by this run (confirmed: the job's own "Post job cleanup" log
  shows a clean working tree, and no commit exists on `main` for this
  run's `githubRunId`).
- **Conclusion: zero risk to append-only/idempotent storage from this
  incident** — the failure mode was "no visible progress + no
  completion," never "partial write."

## 4. Timeout derivation (not arbitrary)

Old: `timeout-minutes: 25`. New: **`timeout-minutes: 45`**.

Derivation, from the one real reference point this repository has
(`docs/HITTER_SIMULATION_ENGINE.md` Sec.11's 1,213s full-slate figure):

1. A full-slate, single-call run at `n_sims=1500` took **1,213 seconds**
   (~20.2 min).
2. The cancelled run's own slate (15 games, multiple distinct start
   times) makes **up to ~2 simultaneously-due checkpoint groups** a
   realistic peak within one cycle (verified plausible for that
   specific slate — see §2a).
3. Estimated realistic worst case: `2 × 1,213s = 2,426s` (~40.4 min).
4. Add **~20%** margin for GitHub Actions runner variance and any
   as-yet-unobserved 3-group cycle: `2,426s × 1.2 ≈ 2,911s` (~48.5 min).
5. Round to a clean **45 minutes** — slightly below the padded estimate
   (deliberately not padding away all the way to 60, to keep some
   pressure toward catching a genuinely runaway job), comfortably above
   the derived realistic worst case, and far short of "effectively
   unlimited" (GitHub Actions' own job-level default is 360 minutes) —
   a genuinely hung job still fails visibly well before that.

This is a first-pass derivation from the best evidence currently
available (one full-slate timing figure, one real multi-group
incident) — the new runtime metrics (§6) are recorded specifically so
this can be revisited from real, multi-cycle, multi-group evidence once
the archive has accumulated more samples, rather than guessed again
from scratch.

## 5. Progress logging added

`lib/research/hitter_prospective_snapshot.py`'s
`run_hitter_prospective_snapshot_cycle` and
`scripts/edgelab/run_hitter_prospective_snapshots.py`'s `main()` now
print (all `flush=True`, matching this repository's existing plain-`print()`
convention — no `logging` module or `PYTHONUNBUFFERED`/`python -u`
precedent exists elsewhere in this codebase to match instead):

- Cycle start (date, run ID, target checkpoints, games considered)
- Slate date / total games considered (CLI layer, before the cycle call)
- Checkpoints due this cycle, with game count per checkpoint
- Each checkpoint batch starting, with its game count
- Hitter-board build starting (checkpoint, game count, `n_sims`)
- Hitter-board build complete (elapsed seconds, row count) — or FAILED,
  with elapsed seconds and the exception, on the existing per-batch
  failure-isolation path
- Checkpoint batch complete (elapsed seconds)
- Cycle complete (checkpoint batch count, new row count, total elapsed)
- Persistence starting / persistence complete (written / skipped-duplicate
  counts, CLI layer)
- Cycle total elapsed (CLI layer)

Deliberately **one line per checkpoint batch**, never one line per game
or per Monte Carlo simulation (a batch can contain many games, each
with ~9 hitters × `n_sims`≈1500 simulations — per-simulation logging
would be many orders of magnitude too noisy).

## 6. Runtime metrics added

`data/edgelab/research_runs/<date>.jsonl`'s `HITTER_PROSPECTIVE_SNAPSHOT`
records now additionally carry, inside the existing `counts` object (no
schema change needed — `research_run.schema.json`'s `counts` field is
already an open `{"type": "object"}`, so this is purely additive data,
never a new required field or enum value):

- `totalRuntimeSeconds` — the whole `main()` invocation, start to finish
  (including pregame-context fetch, lineup polling, every checkpoint
  batch, and persistence)
- `checkpointBatchRuntimeSeconds` — `{checkpoint: elapsedSeconds}`, the
  whole per-checkpoint batch (slate-file write + board build)
- `boardBuildRuntimeSeconds` — `{checkpoint: elapsedSeconds}`, just the
  Monte Carlo evaluate call itself (a subset of the batch time above;
  kept separate so a future slate-file-write regression would be
  visible rather than hidden inside the batch total)

`gamesEvaluated` and `hitterProjectionSnapshotsWritten` (the
"projection rows written" figure) already existed in this record before
this change and are reused as-is, per the task's own "avoid schema
churn unless needed" guidance — not duplicated under new names.

These are recorded specifically so a future analysis can determine
whether runtime scales with due-game count, checkpoint-group count, or
something else, from real accumulated data rather than the single
incident this document is based on.

## 7. Redundant-work audit (item 5) — no safe optimization implemented

Audited `scripts/build_hitter_projection_board.py`'s `main()` (the
function invoked once per checkpoint-group batch) for cross-batch
redundant work. Found one real candidate: `_weather_by_team(weather_path)`
and `_savant_batters(savant_team_path)` re-read and re-parse
`data/weather.json`/`data/savant_team.json` from disk on **every**
call — if 2–3 checkpoint groups are due in one cycle, these two small
static files get parsed 2–3 times each, once per group.

**Not implemented in this pass**, for two reasons:

1. **Expected impact is small relative to the dominant cost.** These
   are small, already-local JSON files; re-parsing them a handful of
   extra times per cycle is negligible next to the Monte Carlo
   simulation cost that actually drove the incident in §1–§2 (n_sims≈1500
   per hitter, ~9 hitters per lineup, per due game).
2. **Not safely testable without a disproportionate, riskier change.**
   `build_board_main_fn` is an *injected* callable
   (`run_hitter_prospective_snapshot_cycle`'s own parameter) — the real
   production value is `scripts.build_hitter_projection_board.main`,
   but ~20 existing tests in
   `tests/research/test_hitter_prospective_snapshot.py` inject a *fake*
   with a fixed keyword-only signature
   (`date_str, slate_path, weather_path, savant_team_path,
   kalshi_search_path, n_sims, research_run_id, dry_run, emit_rows`).
   Adding new keyword arguments (e.g. a pre-loaded `weather_lookup=`/
   `savant_data=` cache) to the real call site would require touching
   every one of those fakes' signatures to avoid `TypeError`s — a
   change disproportionate to the negligible expected savings, and
   exactly the kind of change item 5's own instructions say to skip
   ("Only implement optimizations if they are semantics-preserving and
   easily testable... If no safe performance optimization is obvious,
   simply document that").

No other redundant work was found: `_load_by_batter`,
`_load_bat_tracking`, `defenseByTeam`, `sprintSpeedByBatter`,
`catcherFramingByCatcher`, and `umpireByGame` are all scoped to the
filtered slate's own confirmed batters/teams/games — since each
checkpoint group's due games are disjoint (a game has exactly one
checkpoint due per cycle), these never redundantly recompute the same
entity across groups within one cycle.

## 8. Explicitly unchanged

- `n_sims` (still `DEFAULT_N_SIMS = 1500`, still caller-overridable, no
  default change)
- Every projection formula, distribution, shrinkage rule, and
  probability calculation in `lib/research/hitter_board_builder.py` /
  `lib/research/hitter_feature_context.py`
- The checkpoint-coverage math and constants from
  `docs/HITTER_CHECKPOINT_COVERAGE_FIX.md`
  (`HITTER_SCHEDULER_CADENCE_MINUTES`, `HITTER_CHECKPOINT_TOLERANCE_MINUTES`,
  `HITTER_CLOSING_WINDOW_MINUTES`) — this document is about wall-clock
  job capacity and observability, not checkpoint scheduling
- Failure isolation: one game's failure still never corrupts another's
  result; one checkpoint group's exception still never erases another
  group's already-collected rows or aborts the cycle (the existing
  `try/except` around each `build_board_main_fn` call, now additionally
  timed and logged, not restructured)
- No bet placement, bankroll mutation, or production recommendation
  logic anywhere in this change
- Append-only/idempotent storage (`lib.edgelab.storage.append_records`)
  and the recoverable-artifact-on-persistence-failure pattern in
  `hitter-snapshot-scheduler.yml` — untouched
