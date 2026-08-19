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
