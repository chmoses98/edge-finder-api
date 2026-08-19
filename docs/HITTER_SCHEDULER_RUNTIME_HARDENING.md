# Hitter Snapshot Scheduler — Runtime Hardening & Observability

Status: **infrastructure/scheduling-architecture hardening only**. No
hitter projection formula, weight, prior, probability calibration,
`n_sims`, edge threshold, ranking logic, or production betting logic was
changed. This document is the audit/derivation writeup for the timeout,
logging, and (§9 onward) scheduler-capacity/concurrency/board-build
consolidation changes described here, mirroring
`docs/HITTER_CHECKPOINT_COVERAGE_FIX.md`'s format for the (separate,
already-resolved) scheduling-coverage work.

**§4's original 45-minute timeout derivation contained a math error**
(caught in a follow-up review, before merge) and has been **superseded
by §13** below, which also corrects the underlying architecture rather
than just the timeout number. §4 is left intact as the historical
record of the first-pass reasoning and exactly what was wrong with it —
see §13 for the corrected derivation and §9-§12 for the deeper
capacity-architecture fix that makes the corrected, lower timeout safe.

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

## 9. GitHub Actions concurrency semantics (verified)

A follow-up review before merge found that §4's fix (raising the
timeout) treated only the symptom: it never asked what happens to a
scheduled run that becomes queued behind a long-running one under this
workflow's `concurrency: { group: edgelab-hitter-snapshot,
cancel-in-progress: false }` block. Verified against GitHub's current
documentation (2026-08-19):

- **Default queue (no `queue:` key configured, this workflow's original
  state):** at most **one** run may be **RUNNING** in a concurrency
  group at a time, and at most **one** additional run may be
  **PENDING**. There is no third slot: if a new run enters the group
  while one is running and one is already pending, the previously
  pending run is **cancelled and replaced** by the new one.
- **`cancel-in-progress: false` affects ONLY the currently RUNNING job.**
  It does not, and cannot, protect a PENDING run from being replaced —
  that replacement rule is unconditional and independent of
  `cancel-in-progress`. This was the misreading this task's review
  caught: `cancel-in-progress: false` was assumed to mean "every
  scheduled run eventually gets its turn," which is false under the
  default queue.
- **`queue: max`** (GitHub Actions GA, 2026-05-07): an explicit
  concurrency option allowing up to **100** runs to wait in true FIFO
  order instead of the default single-pending-slot replacement — nothing
  is cancelled merely because a newer run arrived while queued.
  `queue: max` is rejected by GitHub's workflow validation when combined
  with `cancel-in-progress: true` (not applicable here — this workflow
  already uses `cancel-in-progress: false`).
- **Queued runs preserve their own scheduled/trigger metadata** (e.g.
  `github.event.schedule`, the run's creation timestamp) but do not
  execute until dispatched — a queued run's *actual* execution instant
  can be materially later than its nominal cron trigger instant. This
  scheduler already treats `now` as the real wall-clock instant a cycle
  runs at (never the nominal trigger instant) for exactly this reason —
  see §11's "never backdate" note.

## 10. The capacity failure mode this enables, and the simulation that proves it

Sequence (concrete, from the default single-pending-slot queue):
cycle **A** (T+0) runs longer than the 15-minute cadence → cycle **B**
(T+15) arrives and becomes **PENDING** while A is still running → cycle
**C** (T+30) arrives while A is *still* running and B is *still*
pending → **B is cancelled/replaced by C**. Any checkpoint opportunity
that only B's specific `now` would have captured (because C's later
`now` has moved past that target's tolerance window, or a lineup
confirmed in the gap between B and C's nominal times) is never evaluated
at all — not delayed, not missed-and-logged, just silently absent from
that cycle's `due_games_by_checkpoint`. This would undermine the
prospective checkpoint coverage guarantee even though the pure
cadence/alignment math (`docs/HITTER_CHECKPOINT_COVERAGE_FIX.md`) is
unaffected — that document proves alignment coverage GIVEN every tick
executes; it never modeled tick cancellation.

`scripts/research/simulate_hitter_scheduler_capacity.py` is a new,
deterministic simulator built specifically to model cadence + runtime +
concurrency **together** (not just minute alignment, which the existing
`simulate_hitter_checkpoint_coverage.py` already covers). It reuses the
real, unmodified `determine_due_hitter_checkpoint` /
`compute_missed_hitter_checkpoints` / `classify_game_eligibility`
functions — never a second, reimplemented model of the scheduling logic
— combined with a pure event-driven timeline simulator
(`simulate_concurrency_group`) for both the default single-pending-slot
queue and `queue: max`.

`run_capacity_matrix()` sweeps cadence=15min × runtime ∈
{5,10,15,20,25,30,40,45} min × {1,2,3} consecutive heavy cycles.
Representative findings (single-pending-slot / default queue, one
synthetic game, T-90 aligned to a clean cron tick):

| Runtime (min) | Heavy cycles | Executed | Cancelled | Max start delay (min) |
|---|---|---|---|---|
| 5–15 | 1–3 | 15/15 | 0 | 0 |
| 20 | 1–3 | 15/15 | 0 | 5–15 |
| 25–30 | 1 | 15/15 | 0 | 10–15 |
| 25–45 | 2–3 | 12–14/15 | 1–3 | 10–15 |

At the realistic 1–3-heavy-cycle scale the task asks for, cancellations
appear once per-cycle runtime exceeds roughly the cadence (15 min) by a
meaningful margin, but — for a single game with the existing 12-minute
checkpoint tolerance — enough OTHER (surviving, non-cancelled) ticks
still land inside each target's tolerance window that no checkpoint is
actually lost in these specific scenarios. Extending the same simulation
to a sustained run of **6 consecutive** 45-minute cycles (a deliberately
extreme stress case, well beyond the realistic 1–3-cycle scope the task
asks for) does produce a genuine miss — `T_MINUS_60` — proving the
failure mode is real once the number of consecutive overruns grows large
enough, and that the simulator correctly detects it
(`tests/research/test_simulate_hitter_scheduler_capacity.py::TestSimulateCheckpointCoverageUnderLoad::test_sustained_overload_under_default_queue_eventually_misses_a_checkpoint`).

**Could a scheduled run previously be cancelled/replaced? Yes** — this
is the confirmed, real behavior of the default queue this workflow used
before this fix, for exactly the sequence described above.

## 11. The fix: consolidated board-build calls, not just a bigger number

Per §2a and §7, the root amplifier was never runtime-per-game — it was
**one board-build call per checkpoint group**, uncapped in count within
a cycle. `lib/research/hitter_prospective_snapshot.py`'s
`run_hitter_prospective_snapshot_cycle` now evaluates every due game —
regardless of which checkpoint it belongs to — with **one consolidated
board-build call per cycle** (the "optimistic batch" happy path). A game
has at most one due checkpoint per cycle
(`determine_due_hitter_checkpoint` returns a single label), so the due
games combined across every checkpoint group are always a **subset** of
the day's full slate: total Monte Carlo cost per cycle is bounded by the
**same** figure as a full-slate rebuild (`docs/HITTER_SIMULATION_ENGINE.md`
Sec.11's 1,213s), never a multiple of it — this corrects the flawed
"N × 1,213s" compounding assumption underlying §4's original derivation.

Each resulting row is mapped back to its owning game's already-determined
checkpoint via the row's own `gameId` — **never** via the matchup string
(`"AWAY @ HOME"`) the old per-checkpoint-group loop used, which is
ambiguous for doubleheaders (see §12). If the consolidated call raises
for **any** reason, the cycle falls back to the **original**
per-checkpoint-group call loop (byte-identical to the pre-fix code,
preserved as `_run_per_checkpoint_group_fallback`), so a single bad game
or a transient failure of the combined call never aborts an entire
cycle's evaluation — failure isolation is unchanged, just relocated to a
fallback path instead of being the only path.

**Trade-off, disclosed:** `build_game_contract_coverage`
(`lib/research/hitter_board_builder.py`) seeds each hitter's Monte Carlo
draw via `seed = seed_base + i`, where `seed_base` starts at 0 and
increments per game **within one `main()` call**. Consolidating multiple
checkpoint groups' games into one call therefore assigns different seeds
than the old multi-call design would have for any game after the first
in the combined list — a **different Monte Carlo noise draw from the
same unbiased estimator, formula, and `n_sims`**, not a changed model,
and bounded by each row's own already-disclosed `monteCarloStderr`. The
common case (only one checkpoint group due this cycle) is unaffected —
it trivially reduces to the same single call as before, with identical
seeds. The fallback path (triggered only on a real exception) is also
unaffected — it reproduces the exact pre-fix seed assignment.

**Call-count comparison, representative heavy cycle** (2 simultaneously
due checkpoint groups, e.g. the 32189380616 incident's own slate
profile): **before** — 2 separate `build_board_main_fn` calls, each
re-paying weather/savant file load and model init overhead, with the
total Monte Carlo cost still bounded by the same full-slate figure but
split across 2 calls. **after** — **1** consolidated call in the common
(happy-path) case; up to 2 (this scenario) in the rare fallback case,
identical to before. See
`tests/research/test_hitter_prospective_snapshot.py::TestRunHitterProspectiveSnapshotCycle::test_multiple_checkpoints_consolidated_into_one_call`
and `::test_consolidated_call_falls_back_to_per_checkpoint_group_on_failure`.

**Never backdated:** every checkpoint decision — in both the
consolidated and fallback paths — is made using `now`, which is always
the cycle's real, current wall-clock invocation instant (this was
already true before this fix and remains unchanged). Combined with
`queue: max` (§14), a cycle delayed by queueing evaluates checkpoints
using its real, delayed execution instant; if a target's window has
definitively closed by then, `compute_missed_hitter_checkpoints` reports
it `MISSED_CHECKPOINT_WINDOW_CLOSED`, never a fabricated on-time
capture. Verified directly by
`scripts/research/simulate_hitter_scheduler_capacity.py`'s
`test_missed_checkpoint_is_never_fabricated_as_a_late_capture`.

## 12. Doubleheader / game-identity safety

Audited `scripts/build_hitter_projection_board.py`'s
`_raw_markets_for_game`, which previously matched Kalshi hitter markets
to a game by a bare substring check (`f"{away}{home}"` inside the
market's `event_ticker`) — a **real, pre-existing bug**, independent of
the consolidation work above: two doubleheader legs between the same two
teams on the same date share an identical away/home-abbreviation ticker
suffix, so this check alone cannot distinguish them.

Fixed using this repository's own already-established disambiguation
pattern from `scripts/discover_kalshi_mlb_markets.py`
(`_et_time_str`/`build_slate_index`/`resolve_game_match` — mirrored, not
imported, to avoid a script-to-script coupling for one small pure
helper): every game's scheduled start is converted to its ET "HHMM"
string (matching Kalshi's own ticker time encoding, e.g.
`KXMLBHIT-26AUG181940ATHKC` embeds `1940` = 7:40 PM ET); when more than
one real game shares an (away, home) abbreviation pair on the slate, a
matched market is assigned to whichever candidate game's scheduled ET
time is closest to the market ticker's own embedded time. The overwhelming
common case (exactly one real game for a given away/home pair) is
byte-identical to the original bare-substring behavior.

Every hitter projection row now also carries the game's real `gameId`
(previously absent — rows only carried the ambiguous `matchup` label),
stamped in `build_hitter_projection_board.py`'s `main()`. This is what
makes both the board-builder-level fix and the scheduler-level
consolidation (§11) safe: rows are always attributed to a game via a
stable, unambiguous identifier, never a matchup string that two
different real games can share.

Regression coverage:
`tests/test_hitter_phase5_orchestration.py::test_doubleheader_same_matchup_label_distinct_game_ids_never_collide`
(board-builder level — two real games sharing a matchup label, distinct
gameIds, distinct ET-time-embedding tickers, asserts no cross-attribution)
and `hitter_projection_snapshot.schema.json`'s existing `gameId`
required field / `additionalProperties: true` permission (no schema
change needed — the field already existed and was already required on
the final stored row; this fix ensures it is now always correctly
populated upstream, including under consolidation).

## 13. Corrected timeout derivation

**§4's original derivation contained a compounding error**: it assumed
`N` simultaneously-due checkpoint groups multiply the full-slate Monte
Carlo cost by `N` (`2 × 1,213s ≈ 40.4 min`, `+20% ≈ 48.5 min`). This is
wrong — as established in §11, due games across every checkpoint group
combined are always a **subset** of the full slate (a game has at most
one due checkpoint per cycle), so total Monte Carlo cost per cycle is
bounded by the **same** ~1,213s figure regardless of how many checkpoint
groups happen to be due, whether evaluated as 1 consolidated call or (in
the rare fallback case) split across up to 5 separate calls
(`HITTER_CORE_CHECKPOINTS` has 5 labels, the theoretical maximum).

Corrected derivation:

1. Full-slate Monte Carlo cost bound (`n_sims=1500`): **1,213 seconds**
   (~20.2 min) — unchanged reference figure, now correctly understood as
   a bound on the WHOLE cycle, not per checkpoint group.
2. Worst case is the **fallback** path (consolidated call raised,
   reverted to per-checkpoint-group calls): total Monte Carlo cost is
   still bounded by the same ~1,213s (games are still a partition of the
   same subset), plus per-call overhead (weather/savant file load, model
   init — small relative to Monte Carlo cost, estimated ~30s/call) for
   up to 5 calls: `1,213s + (5 × 30s) = 1,363s` (~22.7 min).
3. Add **~20%** margin for GitHub Actions runner variance:
   `1,363s × 1.2 ≈ 1,635s` (~27.3 min).
4. Round up to a clean **30 minutes** — comfortably above the derived
   worst case, framed explicitly as a **runaway-safety bound** (something
   has genuinely hung), never the expected normal-operation envelope. A
   normal peak-slate cycle now completes within roughly the 15-minute
   cadence itself in the common (single consolidated call) case.

**Timeout: `timeout-minutes: 45` → `30`.**

**§13's own derivation above STILL contained a gap** (caught in a
SECOND follow-up review, before merge): step 2's `1,363s` fallback
figure is the fallback LOOP's own cost -- it silently omits however
long the FAILED consolidated attempt itself already ran before raising.
A late-failing consolidated call (which the 1,213s full-slate bound
does not rule out -- see §16) followed by a full fallback recovery could
therefore genuinely need up to `1,213s + 1,363s ≈ 2,576s` (~43 min)
before variance margin, not the `1,363s` this section budgeted for. See
§17 for the corrected fix (a bounded-fallback POLICY, not a further
timeout increase) -- §13's 30-minute timeout value is UNCHANGED and
remains correct, but only because §17's policy now actively prevents
the fallback loop from ever being started in a scenario that would
require the larger, ~43-minute figure.

## 14. `queue: max` added

`concurrency.queue: max` added alongside the existing
`cancel-in-progress: false` (compatible — the invalid combination is
specifically `queue: max` + `cancel-in-progress: true`, not applicable
here). This is a strictly-better safety net than the default
single-pending-slot behavior for this workflow: combined with §11's
consolidation fix (which keeps normal cycles fast enough to rarely need
to queue at all), it ensures that on the rare occasion a cycle does run
long, a subsequent scheduled cycle is delayed rather than silently
discarded — and, per §11's "never backdate" note, a delayed cycle still
correctly reports genuinely-closed checkpoint windows as MISSED rather
than fabricating a late capture as on-time. `queue: max` is **not by
itself a complete fix** for the underlying capacity question (a
sufficiently long backlog can still cause real misses via delay alone,
as demonstrated by
`test_missed_checkpoint_is_never_fabricated_as_a_late_capture`'s
`queue_mode="max"` scenario) — the consolidation fix in §11 is what
actually keeps normal cycles inside the 15-minute cadence; `queue: max`
is the safety net for the residual tail, not the primary fix.

## 15. Performance target: is peak runtime now under 15 minutes?

**Common case (single checkpoint group due, or the consolidated happy
path succeeds): yes.** The consolidated call's Monte Carlo cost is
bounded by the same games-actually-due subset as before — for any cycle
where that subset is meaningfully smaller than a full 15-game slate
(the normal case outside of rare multi-checkpoint-group collisions), the
call comfortably completes within the 15-minute cadence.

**Worst case (full-slate-sized due-game count in one cycle, or the rare
fallback path): not guaranteed under 15 minutes** — the 1,213s
(~20.2 min) full-slate reference figure itself exceeds the 15-minute
cadence. This residual limitation is inherent to `n_sims=1500`'s
Monte Carlo cost for a full-slate-sized cycle and is **not** solved by
this fix (per the task's own explicit instruction: do not reduce
`n_sims` to hit the target). The safest available architecture given
that constraint is what this fix implements: keep the common case fast
(consolidation), bound the worst case with a correctly-derived timeout
(§13) rather than an inflated one, and use `queue: max` (§14) plus
real-time-based MISSED accounting so a worst-case cycle degrades to
"delayed and explicitly logged as missed where genuinely unreachable,"
never to "silently cancelled" or "fabricated as on-time."

---

# Final correctness review (second pass, before merge)

A second review pass, after §1-§15 above were already implemented, found
four further correctness issues: a real math bug in the doubleheader
time-distance calculation (present in TWO independent resolvers), a
"guess the earliest candidate" fallback for a genuinely ambiguous
doubleheader market that this research system must never do, the
fallback-timeout gap flagged in §13's own correction note above, and a
snapshot-timestamp integrity gap the new fallback architecture opened
up. §16-§20 document each fix. Nothing in this section changes any
hitter projection formula, weight, prior, `n_sims`, edge threshold, or
production betting logic.

## 16. Doubleheader time-distance bug (both resolvers)

**Root cause.** `_raw_markets_for_game`'s original doubleheader
disambiguation (§12) picked the closest candidate game via
`abs(int(candidate_hhmm) - int(ticker_hhmm))` — treating a 4-digit
`'HHMM'` ticker-time string as a plain integer and subtracting. This is
mathematically wrong across an hour boundary: `'1255'` (12:55) and
`'1305'` (13:05) are 10 real minutes apart, but
`int('1305') - int('1255') == 50`. Worse, a MORE DISTANT same-hour
candidate could be preferred over the true closest one — e.g. a 13:30
candidate has a raw integer difference of `|1305-1330| = 25` from a
13:05 ticker time, which is LESS than the wrong 50 computed for the
true-closest 12:55 candidate, so the old code would have incorrectly
picked 13:30 over 12:55 despite 12:55 being the real closer game (10
true minutes away vs. 25).

**Generic Kalshi discovery had the identical bug.**
`scripts/discover_kalshi_mlb_markets.py::resolve_game_match` performed
the exact same raw-integer-subtraction (`abs(int(e["time_str"]) -
int(contract_time))`) for its own doubleheader disambiguation — an
independent implementation with the identical defect, confirming this
was a shared conceptual bug, not a hitter-board-specific one.

**Canonical corrected implementation.** Extracted as ONE shared helper,
`lib/kalshi_ticker_time.py`:

- `hhmm_to_minutes(hhmm)` — `'HHMM'` → minutes-since-midnight (0-1439).
- `hhmm_distance_minutes(a, b)` — true elapsed clock-minutes between two
  `'HHMM'` strings (linear, not circular/wraparound — see the module's
  own docstring for why a genuine midnight rollover is not a real
  scenario either caller needs to handle: both candidates in every real
  comparison are always two games on the SAME calendar date).
- `closest_by_hhmm(target, candidates, key=...)` — returns
  `(best_candidate, is_unique_closest_bool)`; `is_unique_closest_bool`
  is `False` whenever the target time is missing/unparseable OR
  two-or-more candidates are genuinely tied for closest — never
  silently resolved by an arbitrary tie-break.

Both `scripts/build_hitter_projection_board.py`'s
`_resolve_doubleheader_market` and
`scripts/discover_kalshi_mlb_markets.py`'s `resolve_game_match` now call
this SAME helper, rather than maintaining two subtly different
implementations. `resolve_game_match`'s own pre-existing "fall back to
the earliest candidate when the contract's own time is missing" policy
is unchanged (that script's generic-discovery scope, always attributing
a matched contract to some real game, is intentionally different from
the hitter board's own stricter "never guess" policy — see §17 below).

Regression coverage: `tests/test_kalshi_ticker_time.py` (20 tests, pure
math), `tests/test_doubleheader_resolver_consistency.py` (both
resolvers agree on the same scenario, including the exact bug-report
example), and
`tests/test_hitter_phase5_orchestration.py::test_cross_hour_boundary_correctly_prefers_the_true_closest_leg`
(full board-build, end-to-end, using the exact reported scenario).

## 17. Never guess an ambiguous doubleheader match

`_raw_markets_for_game` previously attributed a market whose ticker time
couldn't be extracted to the earliest candidate game — an implicit
guess. Per this review, that is not acceptable for a research system
whose whole purpose is provenance-honest, never-fabricated data.

**Fix.** `_resolve_doubleheader_market` now returns `None` (never a
guessed gameId) whenever a market's ticker time is missing/unparseable
OR the closest-candidate computation is a genuine tie — in either case
the market is excluded from EVERY game's own `_raw_markets_for_game`
result (never guessed into the earliest candidate, never silently
merged into the wrong game). `find_ambiguous_doubleheader_markets` scans
the full market list once per cycle to recover exactly these excluded
markets, and `scripts/build_hitter_projection_board.py`'s `main()`
appends one explicit `AMBIGUOUS_TICKER_MATCH` row per such market
(`lib.research.hitter_board_builder.build_ambiguous_doubleheader_row`,
reusing the SAME status this module already uses for a different kind
of ambiguity — multiple confirmed hitters matching one contract's
player name — since both are "this contract's owning entity cannot be
determined without guessing" cases) with `gameId=None`. **Complete
market preservation is maintained**: the market is never dropped, just
never misattributed.

Regression coverage:
`test_ambiguous_ticker_time_market_preserved_not_guessed`,
`test_ambiguous_tied_ticker_time_market_preserved_not_guessed`, and
`test_ambiguous_market_never_assigned_to_either_doubleheader_leg` in
`tests/test_hitter_phase5_orchestration.py`.

## 18. Bounded fallback policy (corrected fallback-timeout accounting)

Per §13's own correction note: a naive fix would have inflated the job
timeout to cover BOTH a near-worst-case failed consolidated attempt
(bounded by the same ~1,213s full-slate figure — `main()` has no
internal try/except around the per-game loop itself, only around each
individual hitter's own Monte Carlo call, so an uncaught exception from
malformed input or a board-assembly bug could in principle surface as
late as just before the call would otherwise have finished) AND a full
fallback recovery (~1,363s), pushing the timeout back toward the
original, too-permissive ~48-50 minute territory this whole fix exists
to correct.

**Chosen design: Option B, a bounded fallback policy** (not a bigger
timeout). `run_hitter_prospective_snapshot_cycle` now accepts
`job_timeout_seconds` (production: `scripts/edgelab/run_hitter_prospective_snapshots.py`'s
`JOB_TIMEOUT_MINUTES = 30` — kept in lock-step with the workflow's own
`timeout-minutes`, checked by
`test_cli_job_timeout_budget_stays_consistent_with_this_workflows_own_timeout`
— minus a fixed non-script-overhead buffer for checkout/setup/commit
steps that also count against the job's timeout). If the consolidated
call raises, the cycle computes how much time has already elapsed
(`elapsed_before_fallback`) and compares
`elapsed_before_fallback + HITTER_FALLBACK_WORST_CASE_SECONDS` (1,363s,
the fallback LOOP's own bound from §13) against `job_timeout_seconds`.
If the fallback could not possibly complete in time, it is **never
started** — every due game/checkpoint entry is recorded
`SKIPPED_INSUFFICIENT_TIME_FOR_SAFE_FALLBACK` (never silently dropped,
never fabricated), and the cycle ends cleanly. Any checkpoint genuinely
still reachable is naturally retried by the next scheduled cycle; a
checkpoint whose window has since closed is caught by the existing
`compute_missed_hitter_checkpoints` mechanism on that later cycle, same
as any other missed checkpoint. `job_timeout_seconds=None` (the default,
used by every pre-existing test) preserves this function's original
behavior exactly — fallback is always attempted.

**This keeps §13's 30-minute timeout correctly derived** rather than
re-inflating it: the timeout only ever needs to cover a scenario this
policy has already pre-approved as fitting inside the remaining budget.

Regression coverage: `TestBoundedFallbackPolicy` in
`tests/research/test_hitter_prospective_snapshot.py` (3 tests: declined
when insufficient time remains, attempted when sufficient time remains,
`job_timeout_seconds=None` always attempts fallback regardless of
elapsed time).

## 19. Snapshot timestamp integrity

**Audit finding.** Before this fix, `generated_at` (feeding every row's
`snapshotGeneratedAt`) was computed ONCE, immediately after
due-checkpoint determination — BEFORE either the consolidated attempt or
the fallback loop had even started. A row produced by a SLOW fallback
(especially one that only started after a failed consolidated attempt
had already consumed substantial time) would therefore be stamped with
a `snapshotGeneratedAt` that could be materially EARLIER than when it
was actually computed — a backdating bug.

**Fix.** `snapshotGeneratedAt` is now computed fresh, immediately AFTER
the specific board-build call that produced a given row returns —
separately for the consolidated happy path and for EACH checkpoint
group in the fallback loop (a strict precision improvement over the
fallback loop's own pre-fix behavior too, where multiple groups
previously shared one early timestamp). The computation uses
`_advance_iso(now, real_elapsed_seconds)` — the cycle's own `now`
(real in production, a fixed simulated instant in tests/the capacity
simulator) advanced by REAL wall-clock seconds measured via
`time.time()` — deliberately NOT a direct call to `ids.utc_now_iso()`
(the actual system clock), which would silently break every
test/simulation that injects a fictional `now` by anchoring the
timestamp to a completely different timeline than the rest of the
cycle's own scheduling decisions. In production, where `now` already
originates from `ids.utc_now_iso()` at cycle start, this is
mathematically identical to the real completion instant.

**Post-first-pitch masquerading protection.** Every row is additionally
re-validated against `classify_game_eligibility(game, now=generated_at)`
— the SAME real completion instant — before being accepted. A
computation that finishes AFTER its own game's first pitch (by real
elapsed time, regardless of how long ago the checkpoint was originally
determined due) is discarded and recorded as
`SKIPPED_COMPUTED_AFTER_GAME_START` in `run_log`, never persisted as if
it were still a normal, fresh pregame snapshot.

**Provenance separation (already correct, now explicitly verified).**
Three distinct timestamps on every row, never conflated:
`marketObservedAt`/`sourceCapturePath` (the FROZEN PREGAME INPUT — the
Kalshi snapshot's own immutable capture instant, stamped by
`scripts/build_hitter_projection_board.py` and passed through
unchanged), `projectionGeneratedAt` (the underlying board row's own
per-call generation timestamp, likewise passed through unchanged — an
existing, pre-this-PR field), and `snapshotGeneratedAt` (this module's
own orchestration-level COMPUTATION COMPLETION instant, now corrected
per this section). No new field was needed — the schema
(`data/edgelab/schema_v1/hitter_projection_snapshot.schema.json`)
already documented `snapshotGeneratedAt` as "the instant this
checkpoint's evaluation cycle produced this row"; this fix makes the
implementation actually match that documented meaning.

Regression coverage: `TestSnapshotTimestampIntegrity` in
`tests/research/test_hitter_prospective_snapshot.py` (4 tests:
`snapshotGeneratedAt` reflects real completion time not cycle start; a
computation finishing after first pitch is discarded; a normal fast
computation is kept; `marketObservedAt`/`sourceCapturePath` remain the
separate frozen-input provenance).

## 20. Monte Carlo seed / reproducibility — verified and documented

Re-verified, not re-derived (§11 already disclosed this trade-off; this
section is the explicit confirmation the final review asked for):

- **Same formulas, same distributions, same `n_sims`.** Zero diff to
  `lib/research/hitter_board_builder.py`,
  `lib/research/hitter_market_distributions.py`, or any pricing/
  calibration code in this PR. Only WHICH specific pseudo-random seed
  integer a given hitter's simulation receives can differ between the
  consolidated happy path and the (byte-identical-to-pre-fix) fallback
  path — never the formula, the distribution family, or the sample
  count applied to that seed.
- **Differences bounded by existing `monteCarloStderr`.** A different
  seed is a different SAMPLE from the same unbiased estimator, not a
  different estimator — exactly the situation `monteCarloStderr`
  (already computed and disclosed on every row) exists to quantify.
- **Batching order cannot create a systematic bias.** Each hitter's
  Monte Carlo draw is statistically independent of which specific
  integer seed it receives, and independent of that hitter's position
  in whatever list happened to be passed to `main()` — a different seed
  assignment changes WHICH sample path is drawn, never introduces a
  directional skew correlated with call composition, game order, or
  consolidated-vs-fallback routing. The common case (a single checkpoint
  group due, or the consolidated call's own first game) is unaffected
  either way — it reduces to the exact same seed assignment as before
  this fix.
- **A game-stable deterministic seed (e.g. `hash(gameId)`-derived,
  rather than a running per-call counter) is a viable, low-risk
  candidate for a FUTURE, narrowly-scoped follow-up** — it would make a
  given game's hitters receive the identical seed regardless of which
  call evaluates them. Deliberately **not implemented in this PR**: it
  would touch `scripts/build_hitter_projection_board.py`'s shared,
  multi-caller `seed_base` derivation (used by every existing production
  and manual-research caller of this script, not just this scheduler),
  which is a broader-blast-radius change than this correctness-focused
  PR's own scope justifies, per this task's own explicit "do not
  introduce a risky seed-system rewrite solely for this PR" instruction.
