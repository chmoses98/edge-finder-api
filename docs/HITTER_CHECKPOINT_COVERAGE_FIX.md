# Hitter Checkpoint Scheduling — Coverage Fix

Status: **infrastructure fix, found and fixed before PR #92 merged**. No
hitter projection formula, weight, prior, calibration, edge threshold,
ranking logic, or production betting logic was changed. This document
is the mathematical audit and fix writeup for the scheduling-coverage
flaw identified in review of `lib/research/hitter_prospective_snapshot.py`
and `.github/workflows/hitter-snapshot-scheduler.yml`.

## 1. Root cause

The scheduler determines whether a checkpoint (`T_MINUS_90`,
`T_MINUS_60`, `T_MINUS_30`) is "due" by calling
`lib.edgelab.checkpoints.classify_checkpoint(now, scheduled_start)`,
which labels the current instant as the checkpoint whose nominal target
(90/60/30 minutes before first pitch) is within `tolerance_minutes`
(default 7.5) of the actual elapsed time. The scheduler itself only runs
when its own GitHub Actions cron fires — originally every 30 minutes.

**Two independently-reasonable numbers, combined, do not guarantee
coverage.** For a periodic sampling grid of period `P` (the scheduler's
own cadence) and a fixed target instant, the *worst-case* distance from
the target to the nearest sample is `P/2`. At `P = 30`, that worst case
is **15 minutes** — twice the classifier's 7.5-minute tolerance. Any
game whose start-time minute-of-hour offset places a checkpoint target
in the "dead zone" between two ticks (more than 7.5 minutes from
either) silently never gets that checkpoint captured, and — because
`minutesToStart` only ever decreases as real time passes — it can
**never** become due again once missed.

**Reported example, reproduced exactly** (see
`tests/research/test_hitter_checkpoint_coverage_simulation.py::TestPreFixConfigurationDocumentsTheRealBug::test_reported_7_10_pm_example_reproduces_exactly`):
a 7:10 PM game's `T_MINUS_90` target is 5:40 PM. The old 30-minute-cadence
ticks land at 5:30 and 6:00 — 10 and 20 minutes away respectively, both
outside the ±7.5-minute tolerance. `T_MINUS_90` is silently never
captured for that game.

`HITTER_CLOSING_WINDOW` (the final-pregame-snapshot checkpoint) has an
even more direct version of the same problem: it doesn't use
`classify_checkpoint`'s tolerance at all — it fires whenever
`0 < minutesToStart <= HITTER_CLOSING_WINDOW_MINUTES` (originally 12).
For a periodic grid of period `P` to be *guaranteed* to contain at
least one sample inside *any* window of width `W`, `W` must be `>= P`.
The old configuration had `W=12 < P=30`, so the entire closing window
could — and, per the simulation, regularly did — fall completely
between two consecutive ticks.

## 2. Coverage audit — before the fix

Exhaustive simulation across every possible game start-minute-of-hour
offset (`:00` through `:59`), using the real, unmodified
`determine_due_hitter_checkpoint`/`classify_game_eligibility` functions
(never a separate model of them —
`scripts/research/simulate_hitter_checkpoint_coverage.py`), at the
original configuration (30-minute cadence, 7.5-minute tolerance,
12-minute closing window):

| Checkpoint | Covered / 60 | Missed / 60 | Coverage rate |
|---|---:|---:|---:|
| `T_MINUS_90` | 30 | 30 | **50.0%** |
| `T_MINUS_60` | 30 | 30 | **50.0%** |
| `T_MINUS_30` | 30 | 30 | **50.0%** |
| `HITTER_CLOSING_WINDOW` | 24 | 36 | **40.0%** |

These numbers match the closed-form prediction exactly:
`2 × tolerance / cadence = 15/30 = 50%` for the time-target checkpoints,
and `window / cadence = 12/30 = 40%` for the closing window — confirming
the failure is structural, not incidental, and reproducible for any
30-minute-cadence configuration regardless of which specific dates were
ever archived. `T_MINUS_90`/`T_MINUS_60`/`T_MINUS_30` share an *identical*
set of missed offsets: because 90, 60, and 30 are all multiples of the
30-minute cadence, a game's coverage status for all three checkpoints is
determined by the exact same phase relationship to the tick grid.

Reproducible: `python3 scripts/research/simulate_hitter_checkpoint_coverage.py --cadence 30 --tolerance 7.5 --closing-window 12`.

## 3. The fix

Two independent, additive changes
(`lib/research/hitter_prospective_snapshot.py`):

1. **`HITTER_SCHEDULER_CADENCE_MINUTES` tightened from 30 to 15** —
   halves the worst-case on-time gap to 7.5 minutes, the mathematical
   minimum for the classifier's *default* tolerance to guarantee
   coverage under perfectly on-time execution. Matches
   `model-snapshot-scheduler.yml`'s own already-proven cadence in this
   codebase, rather than introducing an untested third value.
2. **`HITTER_CHECKPOINT_TOLERANCE_MINUTES` widened from the classifier's
   default 7.5 to 12** — buffers realistic GitHub Actions scheduling
   delay on top of the 15-minute cadence's own worst-case gap, while
   staying comfortably under 15 (half the 30-minute spacing between
   adjacent `T_MINUS_X` targets), so a late capture can never become
   ambiguous between two different targets. This widens
   `classify_checkpoint`'s own existing `tolerance_minutes` parameter
   for this specific caller's known cadence — the SAME nearest-target-
   within-tolerance function every other checkpoint-aware system in this
   repository already trusts, never a second, competing time-bucketing
   scheme, and never a fabricated label: a capture landing outside even
   the widened 12-minute tolerance is honestly reported as
   `"INTERMEDIATE"` by `classify_checkpoint` itself, never force-labeled
   as the nearest target (verified directly:
   `TestExactToleranceBoundary::test_just_past_tolerance_boundary_is_not_covered_as_t90`).
3. **`HITTER_CLOSING_WINDOW_MINUTES` widened from 12 to 20** — satisfies
   the `W >= P` guarantee (`P` = the new 15-minute cadence) with a
   5-minute margin, so the closing window can never fall entirely
   between two consecutive ticks.

Neither cadence nor tolerance/window alone is sufficient — verified
directly by running the simulation with each change in isolation before
settling on the combination above.

## 4. Coverage audit — after the fix

Same exhaustive simulation, same real scheduling functions, new
configuration (15-minute cadence, 12-minute tolerance, 20-minute
closing window):

| Checkpoint | Covered / 60 | Missed / 60 | Coverage rate |
|---|---:|---:|---:|
| `T_MINUS_90` | 60 | 0 | **100%** |
| `T_MINUS_60` | 60 | 0 | **100%** |
| `T_MINUS_30` | 60 | 0 | **100%** |
| `HITTER_CLOSING_WINDOW` | 60 | 0 | **100%** |

Reproducible: `python3 scripts/research/simulate_hitter_checkpoint_coverage.py --cadence 15 --tolerance 12 --closing-window 20` (also the module's own defaults, so a bare invocation of the real scheduler functions reproduces this).

**This is a guarantee under on-time execution, not an unconditional
one.** Also tested:

- **Realistic GitHub Actions delay** (a systematic per-run delay of up
  to 10 minutes, simulated via `--systematic-delay`): coverage remains
  100% for all four checkpoints. (Mathematical note: a *systematic*
  delay is a phase shift of the tick grid, which is already one of the
  60 alignments the exhaustive sweep above covers — this test confirms,
  it does not add new information beyond the sweep, and the writeup
  says so rather than implying it does.)
- **A genuinely skipped scheduled run** (simulating ~33% of runs never
  firing, e.g. an extended outage): coverage measurably degrades (the
  closing window in particular drops well below 100%) — this is an
  **honest, expected limitation**, not a regression. No fixed-cadence
  polling design can guarantee against unbounded delay or a dropped
  run. This is precisely why §5 exists.

## 5. Explicit missed-checkpoint recording

`compute_missed_hitter_checkpoints(game, now, already_captured, ...)`
detects, every cycle, any `T_MINUS_90`/`60`/`30` target whose window has
*definitively* closed (`minutesToStart` has dropped below
`target - tolerance` — since `minutesToStart` only ever decreases,
once this happens the target can never become due again) and was never
captured. `run_hitter_prospective_snapshot_cycle` logs this as an
explicit `"MISSED"` run-log entry
(`reason: "CHECKPOINT_WINDOW_CLOSED_NEVER_CAPTURED"`), aggregated into
the run manifest's `counts.checkpointsMissed` /
`counts.checkpointsMissedByLabel`
(`scripts/edgelab/run_hitter_prospective_snapshots.py`). This is never a
fabricated data row in the append-only snapshot store — only real,
successfully-captured projections are ever written there — it is a
metadata record of an honest gap, mirroring this repository's existing
`errors`/`warnings` run-status conventions.

`HITTER_CLOSING_WINDOW` and `LINEUP_CONFIRMATION` are not covered by
this mechanism: the closing window's "expiry" is equivalent to the game
starting, already handled by `classify_game_eligibility`'s `POST_START`
exclusion; `LINEUP_CONFIRMATION` is event-driven with no fixed window to
close (if a lineup is never confirmed before first pitch, the game
simply stops being eligible — not a scheduling-cadence failure).

A missed checkpoint **never blocks a later checkpoint** for the same
game, in the same cycle or a later one — each checkpoint's due-ness is
tracked independently via `already_captured_hitter_checkpoints`, and
this is directly tested
(`TestMissedCheckpointIntegrationInCycle::test_missed_checkpoint_does_not_block_a_later_checkpoint_same_cycle`,
`::test_missed_checkpoint_does_not_block_a_later_cycle_capturing_the_next_target`).

## 6. What is guaranteed to never happen, and why

- **No backdating / no false relabeling**: every captured checkpoint's
  stored `checkpoint` label comes directly from
  `lib.edgelab.checkpoints.classify_checkpoint`'s own honest nearest-
  target-within-tolerance classification — it is never assigned by this
  scheduler independently, and a capture outside even the widened
  tolerance is labeled `"INTERMEDIATE"`, never coerced to a nominal
  target. `snapshotGeneratedAt`/`marketObservedAt`/the real captured
  `minutesToStart` are always the genuine values from that exact cycle
  — never the nominal 90/60/30 (verified:
  `TestPostFixConfigurationGuaranteesCoverage::test_captured_minutes_to_start_are_never_fabricated_as_exact_targets`).
- **No post-first-pitch capture**: unchanged —
  `classify_game_eligibility`'s `POST_START` exclusion (reused,
  unmodified from the game-level system) still hard-excludes any game
  whose clock-time has passed scheduled start, regardless of cadence.
- **No overwrite of an earlier observation by a later one**: unchanged
  — `already_captured_hitter_checkpoints` still gates each checkpoint to
  at most one capture per game, ever, and storage remains append-only,
  ID-keyed (`lib.edgelab.storage.append_records`), idempotent.
- **`LINEUP_CONFIRMATION` remains event/context-driven**: entirely
  unaffected by the cadence/tolerance changes (it never used
  `classify_checkpoint`'s time-distance logic) — a lineup confirmed
  between two scheduler cycles is captured on the next eligible cycle
  that observes it, and an earlier, already-stored T-90/T-60/T-30 row
  is never retroactively mutated to reflect a later lineup confirmation
  (verified:
  `TestLineupConfirmationBetweenRunsAtNewCadence::test_never_retroactively_labels_the_earlier_capture_as_lineup_confirmed`).

## 7. Cost / concurrency impact

Doubling the cadence (30 → 15 minutes) doubles the *scheduler's own
run-count/overhead*, but **per-cycle compute cost stays bounded exactly
as before**: `run_hitter_prospective_snapshot_cycle` only ever invokes
the expensive Monte Carlo evaluate step
(`scripts.build_hitter_projection_board.main`) for games whose
checkpoint is actually due *this* cycle (via the filtered-slate
mechanism, unchanged by this fix) — never the whole day's slate. A
no-op cycle (the common steady state between checkpoint windows) costs
only the cheap eligibility/schedule bookkeeping, independent of cadence.
The workflow retains its own dedicated `edgelab-hitter-snapshot`
concurrency group, shares no job/step/concurrency-group with any
production capture workflow, places no bets, mutates no bankroll, and
never touches recommendation/risk-gate logic — none of that changed.

**Why not a fully dynamic, per-game wake scheduler** (a theoretically
tighter-cost alternative — computing each game's exact target instants
and waking only then)? GitHub Actions' native scheduling primitive is
cron, which cannot be reconfigured per-run from within a workflow; a
true dynamic scheduler would require new infrastructure (e.g. a
self-rescheduling job using `repository_dispatch` or an external timer
service) well beyond the scope of a coverage bug fix, and would
introduce a new, unproven failure surface for this correctness-critical
system. 15-minute periodic polling with the tolerance/window widening
above is the more conservative choice: it reuses this repository's own
already-battle-tested cadence and scheduling primitive, and its
guarantee is now exhaustively proven rather than assumed. A dynamic
scheduler remains a reasonable future enhancement, not attempted here.

## 8. Reproducing this audit

```bash
# Before-fix configuration (documents the bug, permanently pinned by
# tests/research/test_hitter_checkpoint_coverage_simulation.py's
# TestPreFixConfigurationDocumentsTheRealBug):
python3 scripts/research/simulate_hitter_checkpoint_coverage.py --cadence 30 --tolerance 7.5 --closing-window 12

# After-fix configuration (the module's own real defaults):
python3 scripts/research/simulate_hitter_checkpoint_coverage.py --cadence 15 --tolerance 12 --closing-window 20

# Realistic GitHub Actions delay buffer check:
python3 scripts/research/simulate_hitter_checkpoint_coverage.py --cadence 15 --tolerance 12 --closing-window 20 --systematic-delay 10

# Full regression suite for this fix:
python3 -m pytest tests/research/test_hitter_checkpoint_coverage_simulation.py tests/research/test_hitter_prospective_snapshot.py tests/edgelab/test_hitter_snapshot_scheduler_workflow.py tests/edgelab/test_run_hitter_prospective_snapshots_script.py -v
```

---

## 9. A second, separate bug: daily operating-window coverage

Everything in §1–§8 proves **cadence/alignment coverage**: given that the
scheduler's cron is actively running, every possible game start-*minute*
alignment is captured. It says nothing about whether the cron is running
at all at the *hour* a given game needs it to be. This section documents
a second, independently-found bug in that separate dimension, its audit,
and its fix. **Never conflate the two** — a system can have perfect
minute-of-hour alignment coverage (§4) and still silently miss every
checkpoint for an early game, which is exactly what happened here.

### 9.1 Root cause

`hitter-snapshot-scheduler.yml`'s cron originally ran only during
`16:00–23:45 UTC` and `00:00–05:45 UTC` — completely inactive outside
those hours. GitHub Actions cron hour-lists have no "nearest active hour"
fallback: an hour outside the list simply never fires, at all, ever, for
that day. For an early MLB day game — a real 12:10 PM ET game, for
example — the checkpoint targets are:

| Checkpoint | ET | UTC (EDT) |
|---|---|---|
| `T_MINUS_90` | 10:40 AM | 14:40 |
| `T_MINUS_60` | 11:10 AM | 15:10 |
| `T_MINUS_30` | 11:40 AM | 15:40 |

All three fall **before** the scheduler's first run of the day at 16:00
UTC (noon ET). They are silently never captured — not because of any
minute-alignment issue (§1–§8's fix is unrelated and insufficient here),
but because the cron itself was not yet running.

The prior exhaustive `:00`–`:59` minute-of-hour simulation (§2, §4)
could never have caught this: it always places the simulated game at a
representative hour deep inside the operating window by construction
(`run_coverage_table`'s own docstring now says so explicitly). Catching
this bug requires sweeping the **hour** dimension too, not just the
minute.

### 9.2 Coverage audit — full day, before the fix

New harness: `run_full_day_coverage_table`
(`scripts/research/simulate_hitter_checkpoint_coverage.py --full-day`),
sweeping all `24 × 60 = 1,440` possible (hour, minute) UTC start-time
combinations, at the already-fixed §3 cadence/tolerance/window
(15/12/20), varying *only* the daily operating-window gating — isolating
this bug from the one already fixed in §1–§8.

| Checkpoint | Covered / 1,440 | Missed / 1,440 | Coverage rate |
|---|---:|---:|---:|
| `T_MINUS_90` | 850 | 590 | 59.0% |
| `T_MINUS_60` | 850 | 590 | 59.0% |
| `T_MINUS_30` | 845 | 595 | 58.7% |
| `HITTER_CLOSING_WINDOW` | 845 | 595 | 58.7% |

Confirmed directly: `16:10` UTC (the 12:10 PM ET example) is in
`T_MINUS_90`'s missed list. The missed range spans UTC hours 06:00
through 17:59 — i.e. every start time from roughly 2:00 AM ET through
1:59 PM ET was affected to some degree, including real, common MLB
first-pitch times.

Reproducible:
`python3 scripts/research/simulate_hitter_checkpoint_coverage.py --cadence 15 --tolerance 12 --closing-window 20 --full-day --operating-hours old`
(also pinned permanently by
`tests/research/test_hitter_checkpoint_coverage_simulation.py::TestDailyOperatingWindowFullDayCoverage::test_old_operating_window_has_real_full_day_gaps`).

### 9.3 Deriving the fix (not an arbitrary choice)

The window's **end** (`23:45 UTC` / `00:00–05:45 UTC`) was left
unchanged — it already comfortably covers the latest realistic West
Coast night games' full checkpoint sequence (verified in §9.4).

The window's **start** was derived as follows:

1. **Earliest realistic MLB first-pitch time.** Common day-game starts
   begin around 12:05 PM local; rare "businessman's special" games
   occasionally start as early as ~11:00 AM local. A conservative
   earliest-supported floor of **11:00 AM ET** was chosen — below the
   common case, with margin for the rare early case. (No live network
   access was available in this environment to empirically verify MLB's
   published schedule grid; this is reasoned from general public
   knowledge of MLB scheduling conventions, stated here explicitly
   rather than presented as directly verified.)
2. **Required scheduler lead time for `T_MINUS_90`.** The scheduler must
   already be running by `T_MINUS_90` at the very latest — for an 11:00
   AM ET floor game, that's 9:30 AM ET.
3. **Cadence safety margin.** One full cadence period (15 minutes) of
   margin below that lands the scheduler's required active-by time at
   9:15 AM ET; rounding down to a clean boundary gives **9:00 AM ET**.
4. **DST-safe UTC anchor.** 9:00 AM **EDT** (summer, when almost the
   entire MLB season is played) is **13:00 UTC**. This is the UTC hour
   used for the fixed cron window (§9.5 explains why anchoring to the
   EDT-equivalent hour, rather than EST, is the safe choice).

Result: cron window start moved from `16:00 UTC` to **`13:00 UTC`** in
both `hitter-snapshot-scheduler.yml` and `model-snapshot-scheduler.yml`
(§10). "If the cleanest solution is simply extending the scheduled
hours, do that" — no other architectural change was needed or made.

### 9.4 Coverage audit — full day, after the fix

Same harness, same isolated variable, corrected window
(`13:00–23:45 UTC` + `00:00–05:45 UTC`):

| Checkpoint | Covered / 1,440 | Missed / 1,440 | Missed range (UTC) |
|---|---:|---:|---|
| `T_MINUS_90` | 1,030 | 410 | 07:28–14:17 |
| `T_MINUS_60` | 1,030 | 410 | 06:58–13:47 |
| `T_MINUS_30` | 1,025 | 415 | 06:28–13:22 |
| `HITTER_CLOSING_WINDOW` | 1,025 | 415 | 06:06–13:00 |

Every remaining missed start time falls in the genuine overnight ET dead
zone (roughly 2:00 AM–10:20 AM ET) where **no real MLB game is ever
scheduled** — e.g. `07:28 UTC = 3:28 AM ET`. `15:00 UTC` (11:00 AM ET,
the originally-targeted floor) is **not** in any missed list. The
binding constraint is actually `T_MINUS_90`'s missed range ending at
`14:17 UTC`, meaning the fix's *true* earliest-fully-covered start time
is `14:18 UTC = 10:18 AM ET` — earlier (more permissive) than the
originally-targeted 11:00 AM ET floor, i.e. the chosen window has
healthy margin rather than being a tight fit.

Reproducible:
`python3 scripts/research/simulate_hitter_checkpoint_coverage.py --cadence 15 --tolerance 12 --closing-window 20 --full-day --operating-hours new`
(pinned by
`TestDailyOperatingWindowFullDayCoverage::test_new_operating_window_closes_the_gap_substantially`
and `::test_all_remaining_new_window_misses_are_in_the_overnight_dead_zone`).

Additional realistic-scenario tests (all in
`TestDailyOperatingWindowFullDayCoverage`), each reproducing the real
production functions for a specific realistic first-pitch time: the
reported 12:10 PM ET example (before *and* after the fix), 1:05 PM ET,
1:10 PM ET, an ~11:05 AM ET "businessman's special" near the earliest-
supported floor, a standard 7:10 PM ET evening game, a late West Coast
night game crossing the UTC date boundary (10:10 PM ET), a getaway-
day/doubleheader-nightcap-style 11:40 PM ET start also crossing the UTC
date boundary, an EDT-vs-EST comparison for the same ET wall-clock time,
a delayed-execution case layered on top of the new window, and an
explicit genuine-miss case for a start time still before the window
opens (proving the harness reports a real gap rather than silently
hiding it).

### 9.5 DST handling

GitHub Actions cron has no per-locale DST primitive — it is UTC-only,
always. `13:00 UTC` corresponds to:

- **9:00 AM EDT** (UTC−4) — in effect for almost the entire MLB regular
  season (roughly mid-March through early November).
- **8:00 AM EST** (UTC−5) — in effect only for any early-spring
  exhibition/tune-up activity or November postseason games outside
  daylight time.

Anchoring the fixed UTC window to the **EDT**-equivalent hour (13:00,
the smaller/earlier UTC hour of the two) is the safe choice: during EST
periods, the *same* fixed UTC instant corresponds to an **earlier**
local ET clock time than during EDT (8:00 AM EST vs. 9:00 AM EDT) — so
EST periods automatically get **more** margin, never less. A single
fixed UTC cron window is therefore safe across the whole season without
needing separate summer/winter cron entries, provided (as done here) it
is anchored to the tighter (EDT) case. Verified directly:
`TestDailyOperatingWindowFullDayCoverage::test_dst_est_winter_12_10_pm_et_gets_strictly_more_margin_than_edt`.

### 9.6 Precise coverage guarantee (do not overstate this)

- **Alignment coverage** (§1–§8, unchanged by this section's fix): given
  the scheduler is running, on-time execution guarantees all 60/60
  minute-of-hour alignments for every checkpoint.
- **Daily operating-window coverage** (this section): every realistic
  MLB first pitch at or after ~10:20 AM ET (with margin below the
  ~11:00 AM ET conservative floor, itself below the ~12:05 PM ET common
  earliest real start) through the latest real West Coast night games
  receives all four checkpoints — verified by exhaustive full-day
  simulation, not assumed. Overnight UTC hours with no real MLB game
  (roughly 2:00 AM–10:00 AM ET) remain outside the window **by design**
  and are never claimed as covered.
- **Platform delay / outage limitations** (§4, unchanged): a genuinely
  skipped scheduled run or extended GitHub Actions outage can still
  cause a real miss — no fixed-cadence polling design can guarantee
  against unbounded delay. Handled, as before, by explicit `"MISSED"`
  logging (§5), never a silent gap and never a fabricated late capture.
- **This is not a claim of "every game, unconditionally."** It is a
  claim of "every realistic MLB game, given the scheduler is not itself
  down for an extended period" — the two are different, and this
  document (and both workflow files' own header comments) says so
  explicitly.

## 10. The game-level scheduler had the identical bug

`.github/workflows/model-snapshot-scheduler.yml` (the pre-existing
game-level prospective-model scheduler, `lib/edgelab/prospective_snapshot.py`)
used the **exact same** `16:00–23:45 UTC` + `00:00–05:45 UTC` cron
window — confirmed by direct comparison of both files' cron lines before
this fix. Its cadence was already 15 minutes (never 30), so it never had
the §1–§8 minute-alignment bug — but it shared the identical §9 daily-
operating-window blind spot, for the identical reason: the classifier
tolerance/cadence math in `lib/edgelab/prospective_snapshot.py`
(`DEFAULT_TOLERANCE_MINUTES=7.5`, `CLOSING_WINDOW_MINUTES=12`, already
correct at a 15-minute cadence) says nothing about whether the cron is
running at the hour a given game needs it.

Fixed identically and only at the scheduling level: cron window start
moved from `16:00` to `13:00 UTC`
(`'*/15 13,14,15,16,17,18,19,20,21,22,23 * * *'`), matching
`hitter-snapshot-scheduler.yml` exactly, with the same derivation (§9.3)
and the same DST reasoning (§9.5) documented in that file's own header
comment. **No change was made to `lib/edgelab/prospective_snapshot.py`
itself** — no model-evaluation logic, probability calibration, or
recommendation code path was touched; this remained a
scheduling/infrastructure-only change, mirroring the isolation already
required of the hitter-side fix.

Regression tests:
`tests/edgelab/test_prospective_snapshot.py::test_operating_window_starts_at_13_utc_not_16`,
`::test_overnight_window_unchanged`,
`::test_documentation_no_longer_overclaims_coverage_from_minute_only_simulation`.

`capture-snapshots-scheduled.yml` was noted to have a similar-but-not-
identical window (`'0,30 16,...,23 * * *'` / `'0,30 0,...,5 * * *'`)
during this audit — explicitly **out of scope** for this fix (raw Kalshi
price capture, not a model/checkpoint scheduler) and left untouched.

## 11. Updated reproduction commands

```bash
# Full-day audit, before the daily-operating-window fix (permanently
# pinned by TestDailyOperatingWindowFullDayCoverage::test_old_operating_window_has_real_full_day_gaps):
python3 scripts/research/simulate_hitter_checkpoint_coverage.py --cadence 15 --tolerance 12 --closing-window 20 --full-day --operating-hours old

# Full-day audit, after the fix (the module's own real defaults + the
# corrected window):
python3 scripts/research/simulate_hitter_checkpoint_coverage.py --cadence 15 --tolerance 12 --closing-window 20 --full-day --operating-hours new

# Full regression suite for both the minute-cadence fix (§1-§8) and the
# daily-operating-window fix (§9-§10):
python3 -m pytest tests/research/test_hitter_checkpoint_coverage_simulation.py tests/research/test_hitter_prospective_snapshot.py tests/edgelab/test_hitter_snapshot_scheduler_workflow.py tests/edgelab/test_run_hitter_prospective_snapshots_script.py tests/edgelab/test_prospective_snapshot.py -v
```
