#!/usr/bin/env python3
"""
lib/research/hitter_prospective_snapshot.py
================================================
Hitter Projection Checkpoint Scheduling milestone.

Extends the hitter projection engine (docs/HITTER_SIMULATION_ENGINE.md)
from a single ad hoc snapshot per manual `workflow_dispatch` run (its
only mode of operation historically -- see
data/edgelab/hitter_validation/summary.md Sec.1: 5 runs, ever, across
this repository's entire history) into the same checkpoint-tagged,
append-only, provenance-safe pattern the game-level ModelEvaluation
pipeline already uses (lib/edgelab/prospective_snapshot.py,
docs/EDGELAB_PROSPECTIVE_MODEL_SNAPSHOTS.md). Reuses that module's own
generic pregame-safety/eligibility/lineup-refresh logic directly rather
than duplicating it -- `classify_game_eligibility`, `refresh_lineup_fields`,
`_is_lineup_confirmed`, and `_minutes_to_start` are all imported
unchanged from lib.edgelab.prospective_snapshot; the underlying
checkpoint classifier itself (lib.edgelab.checkpoints.classify_checkpoint)
is the exact same function every other checkpoint-aware system in this
repository already uses.

WHY THIS IS A SEPARATE MODULE, NOT A REUSE OF
run_prospective_snapshot_cycle: that function's "evaluate" step
(scripts.build_market_ledger.evaluate_game) is a cheap, pure-Poisson
computation with no dependency on real player identity, lineup slots, or
Monte Carlo simulation, and it writes ModelEvaluation records -- a
schema hitter props are explicitly NOT part of
(model_evaluation.schema.json's evaluationStatus enum documents
NO_MODEL_SUPPORT "e.g. a player prop"). The hitter engine's own
"evaluate" step (scripts.build_hitter_projection_board.main, reused
UNCHANGED here) is a materially more expensive Monte Carlo simulation
(docs/HITTER_SIMULATION_ENGINE.md Sec.11; a real archived full-slate run
took 1,213 seconds end to end) and produces a different row shape
entirely. Reusing the SAME orchestration function across both would
require it to special-case a completely different evaluate/record
contract for no real code-sharing benefit -- this module instead reuses
every genuinely GENERIC, SHARED piece and adds only its own thin
per-cycle wiring.

COST CONTAINMENT: because the hitter engine's evaluate step is expensive
and scripts.build_hitter_projection_board.main() has no native
"evaluate only these N games" parameter, this module achieves the same
bounded-cost property the game-level scheduler gets for free (at most
one checkpoint evaluated per game per cycle) by writing a small,
run-scoped, FILTERED slate file containing only the games whose
checkpoint is due this cycle (write_filtered_slate_fn, production:
write_filtered_hitter_slate below) and pointing main() at that filtered
file via its existing slate_path= parameter -- never modifying main()'s
internals, never evaluating a game that isn't due. A day with nothing
due (the common steady state between checkpoints) costs nothing beyond
the cheap eligibility/checkpoint bookkeeping below -- main() is never
even invoked.

STORAGE: unlike data/pipeline/<date>/hitter_projection_board.json (a
single-file-per-date artifact a same-day rerun silently overwrites --
a real, documented limitation, see
docs/HITTER_SIMULATION_ENGINE.md Sec.15.5 and this repository's own
hitter-validation audit provenance findings), every row this module
produces is written to data/edgelab/hitter_projection_snapshots/<date>.jsonl
via lib.edgelab.storage.append_records -- the SAME idempotent, ID-keyed,
never-overwrite append pattern data/edgelab/model_evaluations/<date>.jsonl
already uses. Two different checkpoints for the same ticker always
produce two different, both-preserved rows
(lib.edgelab.ids.build_hitter_projection_snapshot_id =
sha1(runId, marketTicker, checkpoint), mirroring
build_model_evaluation_id's exact scheme); a genuine no-op rerun of an
already-captured checkpoint is prevented up front by
already_captured_hitter_checkpoints, exactly mirroring
already_captured_checkpoints's own idempotency guarantee (which itself
does not rely on runId collision -- see that module's docstring).

THIS MODULE NEVER:
  - writes data/pipeline/<date>/hitter_projection_board.json (every call
    into scripts.build_hitter_projection_board.main() here passes
    dry_run=True), data/slate.json, data/edgelab/recommendations/, or
    data/edgelab/bets/bets.jsonl
  - calls scripts/risk_gate.py or scripts/write_pending_bets.py
  - fabricates a lineup, market price, or checkpoint label
  - retroactively relabels a later computation as an earlier prediction
  - changes any hitter projection formula, weight, prior, shrinkage
    level, or threshold -- it calls the EXACT SAME production function
    (scripts.build_hitter_projection_board.main, imported, never
    reimplemented) every manual run already used
"""
import os
import time
from collections import defaultdict
from datetime import datetime, timezone

from lib.edgelab import ids
from lib.edgelab.checkpoints import classify_checkpoint
from lib.edgelab.model_evaluation import _git_commit_sha
from lib.edgelab.prospective_snapshot import (
    _is_lineup_confirmed,
    _minutes_to_start,
    classify_game_eligibility,
    refresh_lineup_fields,
)

# NAMING, deliberately NOT bare "CLOSING" and deliberately NOT reusing
# lib.edgelab.prospective_snapshot.MODEL_CLOSING_WINDOW: that constant
# already names a SPECIFIC concept ("the final targeted snapshot of the
# GAME-LEVEL Poisson model's pregame closing window") -- reusing it here
# for a materially different model (the hitter Monte Carlo engine) risks
# a reader assuming a shared meaning/timing the two systems don't
# actually share (they run independently, on independent schedules, and
# a HITTER_CLOSING_WINDOW capture at T-8 has no relationship to whatever
# MODEL_CLOSING_WINDOW capture the game-level system produced for the
# same game). See lib/edgelab/prospective_snapshot.py's own docstring
# section 7 for why this repository treats bare "CLOSING" as reserved
# for the Kalshi market's own closing QUOTE specifically.
HITTER_CLOSING_WINDOW = "HITTER_CLOSING_WINDOW"

# COVERAGE MATH (scheduling-reliability fix -- see
# docs/HITTER_CHECKPOINT_COVERAGE_FIX.md for the full derivation and the
# exhaustive minute-by-minute simulation this is based on):
#
# The scheduler samples game state on a periodic grid (a GitHub Actions
# `*/N`-minute cron), spaced HITTER_SCHEDULER_CADENCE_MINUTES apart. For
# ANY real number target instant (e.g. "90 minutes before first pitch")
# and a periodic sampling grid of period P, the WORST-CASE distance from
# the target to the nearest sample is P/2 -- this is exactly why
# lib.edgelab.checkpoints.DEFAULT_TOLERANCE_MINUTES (7.5) was originally
# sized for a 10/15-minute cadence (that module's own docstring: "a
# tolerance smaller than half of that would leave real ticks
# unclassified"). The PRIOR version of this scheduler ran on a
# 30-MINUTE cadence while still relying on that same 7.5-minute
# tolerance -- a worst-case gap of 15 minutes against a 7.5-minute
# tolerance, which the exhaustive simulation
# (scripts/research/simulate_hitter_checkpoint_coverage.py) confirms
# misses T_MINUS_90/60/30 for fully HALF of all possible game
# start-minute alignments (e.g. a real 7:10 PM game start: T-90 falls at
# 5:40, but 30-minute-cadence ticks land at 5:30 and 6:00 -- 10 and 20
# minutes away respectively, both outside the old 7.5-minute tolerance,
# so T-90 was silently never captured for that game).
#
# THE FIX has two independent parts:
#   1. HITTER_SCHEDULER_CADENCE_MINUTES tightened to 15 (halves the
#      worst-case on-time gap to 7.5 minutes -- the mathematical minimum
#      needed for classify_checkpoint's OWN default tolerance to
#      guarantee coverage under perfectly on-time execution).
#   2. HITTER_CHECKPOINT_TOLERANCE_MINUTES widened to 12 (> 7.5) to
#      additionally buffer realistic GitHub Actions scheduling delay
#      (documented, real: scheduled workflow runs are not guaranteed to
#      fire at the exact cron instant, and can be delayed, especially
#      under platform load) on top of the 15-minute cadence's own
#      worst-case gap -- while staying comfortably under 15 (half the
#      30-minute spacing BETWEEN adjacent T_MINUS_X targets) so a late
#      capture can never become ambiguous between two different targets.
#      This widens classify_checkpoint's EXISTING tolerance parameter
#      for this caller's own known cadence (the same function, the same
#      nearest-target-within-tolerance logic every other checkpoint-
#      aware system in this repository already trusts -- never a second,
#      competing time-bucketing scheme, and never a fabricated label:
#      a capture that lands outside even the widened 12-minute tolerance
#      is honestly reported by classify_checkpoint as "INTERMEDIATE",
#      not force-labeled as the nearest target).
# Neither change alone is sufficient (verified in the simulation) --
# both together give T_MINUS_90/60/30 full 60/60 minute-offset coverage
# under on-time execution, per
# scripts/research/simulate_hitter_checkpoint_coverage.py's own output.
#
# Residual risk this does NOT eliminate: an extended outage or a fully
# skipped scheduled run near a specific target can still genuinely miss
# it -- no fixed-cadence polling design can guarantee against unbounded
# delay. compute_missed_hitter_checkpoints() below detects exactly this
# case (the target's window has definitively closed -- time only moves
# forward -- and it was never captured) and records it EXPLICITLY in the
# run log, rather than silently doing nothing (the prior design's actual
# failure mode) or fabricating a late capture as if it were on-time.
HITTER_SCHEDULER_CADENCE_MINUTES = 15
HITTER_CHECKPOINT_TOLERANCE_MINUTES = 12

# HITTER_CLOSING_WINDOW coverage math: this checkpoint uses its own
# direct window-membership test (0 < minutesToStart <= WINDOW), not
# classify_checkpoint's nearest-target tolerance, because it targets a
# WINDOW ("as close to first pitch as safely possible"), not a fixed
# point. For a periodic sampling grid of period P to be GUARANTEED to
# contain at least one sample inside ANY window of width W (regardless
# of the window's alignment relative to the grid), W must be >= P --
# otherwise a window can fall entirely between two consecutive samples
# (verified directly: with P=15 and the OLD W=12 < P, two consecutive
# ticks at minutesToStart=14 and minutesToStart=-1 straddle the entire
# (0, 12] window without either one landing inside it -- confirmed by
# the exhaustive simulation). Fixed by widening the window to 20 minutes
# (P=15 plus a 5-minute margin) -- the minimum required guarantee
# (W>=P=15) plus a small buffer for boundary rounding, matching this
# same module's own "tolerance calibrated to this caller's own cadence"
# principle above.
HITTER_CLOSING_WINDOW_MINUTES = 20

HITTER_CORE_CHECKPOINTS = ("T_MINUS_90", "T_MINUS_60", "T_MINUS_30", "LINEUP_CONFIRMATION", HITTER_CLOSING_WINDOW)

SKIPPED_NO_CHECKPOINT_DUE = "NO_CHECKPOINT_DUE"
MISSED_CHECKPOINT_WINDOW_CLOSED = "CHECKPOINT_WINDOW_CLOSED_NEVER_CAPTURED"

# Nominal (target, tolerance) pairs for compute_missed_hitter_checkpoints --
# a time-target checkpoint is definitively unreachable once
# minutesToStart drops below (target - tolerance), since minutesToStart
# only ever decreases as real time advances.
_TIME_TARGET_MINUTES = {"T_MINUS_90": 90, "T_MINUS_60": 60, "T_MINUS_30": 30}


def determine_due_hitter_checkpoint(game, *, now, already_captured, target_checkpoints=HITTER_CORE_CHECKPOINTS,
                                     tolerance_minutes=HITTER_CHECKPOINT_TOLERANCE_MINUTES,
                                     closing_window_minutes=HITTER_CLOSING_WINDOW_MINUTES):
    """
    Pure. Returns (checkpoint_label_or_None, minutes_to_start). Mirrors
    lib.edgelab.prospective_snapshot.determine_due_checkpoint's exact
    priority order (LINEUP_CONFIRMATION > closing window > time-distance
    checkpoints via classify_checkpoint's own nearest-target
    classification -- never a second, competing time-bucketing scheme)
    with HITTER_CLOSING_WINDOW in place of MODEL_CLOSING_WINDOW. Cannot
    call determine_due_checkpoint directly: that function's closing-
    window branch checks the literal imported MODEL_CLOSING_WINDOW
    constant, not a caller-suppliable name, so a differently-named
    closing checkpoint needs this small, separately-tested variant
    rather than a fragile monkeypatch of the shared function.

    `tolerance_minutes`/`closing_window_minutes` default to this
    module's own coverage-fix constants (see the module-level comment
    above) but are caller-overridable -- used directly by
    scripts/research/simulate_hitter_checkpoint_coverage.py to reproduce
    the PRE-fix configuration for the audit, and by tests proving the
    fix at exact boundary values.

    IMPORTANT (mirrors the same rule the game-level system documents and
    enforces): `_is_lineup_confirmed(game)` reads whatever lineup state
    is already on `game` -- callers MUST pass the lineup-REFRESHED game
    copy here whenever LINEUP_CONFIRMATION hasn't already been captured
    (see run_hitter_prospective_snapshot_cycle, which polls live via
    refresh_lineup_fields() before calling this function, identically to
    the game-level cycle's own ordering).
    """
    scheduled_start = game.get("startTime") or game.get("scheduledStart")
    minutes_to_start = _minutes_to_start(now, scheduled_start)

    if "LINEUP_CONFIRMATION" in target_checkpoints and "LINEUP_CONFIRMATION" not in already_captured:
        if _is_lineup_confirmed(game):
            return "LINEUP_CONFIRMATION", minutes_to_start

    if HITTER_CLOSING_WINDOW in target_checkpoints and HITTER_CLOSING_WINDOW not in already_captured:
        if minutes_to_start is not None and 0 < minutes_to_start <= closing_window_minutes:
            return HITTER_CLOSING_WINDOW, minutes_to_start

    if scheduled_start:
        label = classify_checkpoint(now, scheduled_start, tolerance_minutes=tolerance_minutes)
        if label in target_checkpoints and label not in already_captured:
            return label, minutes_to_start

    return None, minutes_to_start


def compute_missed_hitter_checkpoints(game, *, now, already_captured, target_checkpoints=HITTER_CORE_CHECKPOINTS,
                                       tolerance_minutes=HITTER_CHECKPOINT_TOLERANCE_MINUTES):
    """
    Pure. Returns a list of time-target checkpoint labels
    (T_MINUS_90/60/30 only -- LINEUP_CONFIRMATION is event-driven with no
    fixed window to "close", and HITTER_CLOSING_WINDOW's own window
    closing is equivalent to the game starting, already handled by
    classify_game_eligibility's POST_START exclusion) whose capture
    window has DEFINITIVELY closed as of `now` -- i.e. minutesToStart has
    dropped below (target - tolerance), so no future cycle could ever
    legitimately capture this target again (time only moves forward) --
    and which were never captured. This is the explicit-recording
    mechanism this module's docstring promises: a target that becomes
    unreachable is reported here, never silently dropped and never
    fabricated as a late capture. Never mutates `already_captured`.
    """
    scheduled_start = game.get("startTime") or game.get("scheduledStart")
    minutes_to_start = _minutes_to_start(now, scheduled_start)
    if minutes_to_start is None:
        return []

    missed = []
    for label, target in _TIME_TARGET_MINUTES.items():
        if label not in target_checkpoints or label in already_captured:
            continue
        if minutes_to_start < (target - tolerance_minutes):
            missed.append(label)
    return missed


def already_captured_hitter_checkpoints(existing_snapshot_rows, game_id):
    """{checkpoint labels already captured for this game} from prior hitter-snapshot rows written earlier today (any earlier cycle)."""
    return {
        r.get("checkpoint")
        for r in existing_snapshot_rows
        if r.get("gameId") == game_id and r.get("checkpoint")
    }


def write_filtered_hitter_slate(date, run_id, checkpoint, games, *, output_root="data/pipeline"):
    """
    Writes a small, run-and-checkpoint-scoped slate-compatible file
    ({"date":, "games": [...]}) containing ONLY the given games, so
    scripts.build_hitter_projection_board.main() (which has no native
    per-game filter) evaluates exactly this checkpoint's due games and
    nothing else -- the cost-containment mechanism this module's
    docstring describes. Returns the written path. Never touches
    data/slate.json or the canonical data/pipeline/<date>/hitter_projection_board.json.
    """
    import json
    run_dir = os.path.join(output_root, date, run_id)
    os.makedirs(run_dir, exist_ok=True)
    path = os.path.join(run_dir, f"hitter_checkpoint_slate_{checkpoint}.json")
    with open(path, "w") as fh:
        json.dump({"date": date, "games": games}, fh)
    return path


def _matchup_label(game):
    away_abbr = (game.get("away") or {}).get("abbr")
    home_abbr = (game.get("home") or {}).get("abbr")
    return f"{away_abbr} @ {home_abbr}"


def run_hitter_prospective_snapshot_cycle(
    date, games, existing_snapshot_rows, *,
    now=None, target_checkpoints=HITTER_CORE_CHECKPOINTS, live_status_by_team_pair=None,
    lineup_fetch_fn=None, batter_woba_map=None, team_woba_map=None,
    build_board_main_fn=None, write_filtered_slate_fn=None,
    kalshi_search_path=None, weather_path=None, savant_team_path=None,
    n_sims=1500, run_id=None,
):
    """
    The orchestration core -- pure aside from the injected
    build_board_main_fn/write_filtered_slate_fn/lineup_fetch_fn
    (production passes scripts.build_hitter_projection_board.main,
    write_filtered_hitter_slate above, and
    scripts.fetch_lineups.fetch_lineup_for_game respectively; tests
    inject fakes so this function never needs real network access, a
    real Kalshi snapshot, or real Monte Carlo simulation to exercise).

    For every game in `games` (already the day's context -- production
    sources this from scripts.fetch_standalone_pregame_context, the SAME
    independent schedule/lineup source the existing manual hitter
    research run already uses, deliberately never data/slate.json --
    see docs/HITTER_SIMULATION_ENGINE.md Sec.15.3 for why that
    independence matters): determine eligibility
    (classify_game_eligibility, reused unchanged), poll lineups live
    first whenever LINEUP_CONFIRMATION isn't yet captured (identical
    ordering to the game-level cycle, for the identical reason -- a
    stale, hours-old lineup-confirmation flag must never block same-cycle
    discovery), then the due checkpoint. Games with a checkpoint due this
    cycle are grouped BY CHECKPOINT and evaluated in one batched call per
    checkpoint group (not one call per game -- avoids redundant
    weather/savant file I/O for games sharing a checkpoint this cycle).

    Returns (new_rows, run_log): `new_rows` is the list of new hitter
    projection snapshot dicts to append (empty if nothing was due this
    cycle -- the expected common case between checkpoint windows);
    `run_log` has one or more entries per game, mirroring the game-level
    cycle's own shape ({"gameId", "action":
    "EVALUATED"|"SKIPPED"|"DUE"|"MISSED", "checkpoint", "reason",
    "minutesToStart", "warnings", ...}) -- a complete accounting of every
    decision this cycle made, never a silent no-op. "MISSED" entries
    (from compute_missed_hitter_checkpoints, checked for every eligible
    game every cycle) report a time-target checkpoint whose window has
    definitively closed without ever being captured -- an explicit,
    honest record of a genuinely unreachable checkpoint, never a
    fabricated late capture and never a silent gap. Every "EVALUATED"
    (and any board-build-failure "SKIPPED") entry additionally carries
    `boardBuildElapsedSeconds`/`checkpointBatchElapsedSeconds` -- the
    same value shared across every game in that checkpoint's batch,
    since the Monte Carlo evaluate step is invoked once per BATCH, not
    once per game (see the batch loop below). Purely additive: existing
    callers destructuring `(new_rows, run_log)` or reading specific
    known keys off each entry are unaffected by these extra keys.

    RUNTIME OBSERVABILITY (added after a real incident -- workflow run
    32189380616, 2026-08-18, cancelled by its then-configured 25-minute
    job timeout while legitimately still evaluating multiple
    simultaneously-due checkpoint groups on a busy slate, with ZERO
    visible progress in the Actions log the whole time, since this
    function previously produced no output at all until it returned).
    Every `print(..., flush=True)` call below exists so a long-running
    cycle is visibly making forward progress in real time in CI, not
    merely "eventually" via the final summary line the CLI wrapper
    prints. Deliberately ONE line per checkpoint BATCH (never one line
    per game or per Monte Carlo simulation -- see
    docs/HITTER_SIMULATION_ENGINE.md Sec.11 for why per-simulation
    logging would be far too noisy at n_sims~1500/hitter).
    """
    now = now or ids.utc_now_iso()
    run_id = run_id or ids.new_run_id("HITTER_PROSPECTIVE_SNAPSHOT")
    live_status_by_team_pair = live_status_by_team_pair or {}
    engine_commit_sha = _git_commit_sha()

    cycle_started_at = time.time()
    print(
        f"[hitter_prospective_snapshot] cycle start date={date} runId={run_id} "
        f"targetCheckpoints={list(target_checkpoints)} gamesConsidered={len(games)}",
        flush=True,
    )

    run_log = []
    due_games_by_checkpoint = defaultdict(list)
    game_id_by_matchup_and_checkpoint = {}

    for game in games:
        game_id = game.get("gameId")
        away_abbr = (game.get("away") or {}).get("abbr")
        home_abbr = (game.get("home") or {}).get("abbr")
        live_status = live_status_by_team_pair.get((away_abbr, home_abbr))

        eligible, exclusion_reason, minutes_to_start = classify_game_eligibility(game, now=now, live_status=live_status)
        if not eligible:
            run_log.append({
                "gameId": game_id, "action": "SKIPPED", "checkpoint": None,
                "reason": exclusion_reason, "minutesToStart": minutes_to_start, "warnings": [],
                "lineupPollAttempted": False, "lineupPollFailed": False, "lineupNewlyConfirmed": False,
            })
            continue

        captured = already_captured_hitter_checkpoints(existing_snapshot_rows, game_id)
        warnings = []

        lineup_poll_attempted = False
        lineup_poll_failed = False
        lineup_refreshed_game = game
        if (
            "LINEUP_CONFIRMATION" in target_checkpoints
            and "LINEUP_CONFIRMATION" not in captured
            and lineup_fetch_fn is not None
        ):
            lineup_poll_attempted = True
            lineup_refreshed_game, lineup_warning = refresh_lineup_fields(
                game, lineup_fetch_fn=lineup_fetch_fn, batter_woba_map=batter_woba_map or {}, team_woba_map=team_woba_map or {},
            )
            if lineup_warning:
                lineup_poll_failed = True
                warnings.append(lineup_warning)
        was_confirmed_before_poll = _is_lineup_confirmed(game)
        lineup_newly_confirmed = (not was_confirmed_before_poll) and _is_lineup_confirmed(lineup_refreshed_game)

        checkpoint, minutes_to_start = determine_due_hitter_checkpoint(
            lineup_refreshed_game, now=now, already_captured=captured, target_checkpoints=target_checkpoints,
        )

        # Explicit-recording safety net (never silent, never fabricated --
        # see compute_missed_hitter_checkpoints's own docstring): checked
        # for EVERY eligible game, EVERY cycle, regardless of whether a
        # different checkpoint is due this same cycle -- a game due for
        # T_MINUS_60 this cycle may independently have already missed its
        # T_MINUS_90 window in an earlier cycle (e.g. an outage), and that
        # must still be reported even though this cycle DOES capture
        # something for this game.
        for missed_label in compute_missed_hitter_checkpoints(
            lineup_refreshed_game, now=now, already_captured=captured, target_checkpoints=target_checkpoints,
        ):
            run_log.append({
                "gameId": game_id, "action": "MISSED", "checkpoint": missed_label,
                "reason": MISSED_CHECKPOINT_WINDOW_CLOSED, "minutesToStart": minutes_to_start, "warnings": [],
                "lineupPollAttempted": False, "lineupPollFailed": False, "lineupNewlyConfirmed": False,
            })

        if checkpoint is None:
            run_log.append({
                "gameId": game_id, "action": "SKIPPED", "checkpoint": None,
                "reason": SKIPPED_NO_CHECKPOINT_DUE, "minutesToStart": minutes_to_start, "warnings": warnings,
                "lineupPollAttempted": lineup_poll_attempted, "lineupPollFailed": lineup_poll_failed,
                "lineupNewlyConfirmed": lineup_newly_confirmed,
            })
            continue

        # Only the LINEUP_CONFIRMATION checkpoint itself is evaluated
        # against the lineup-refreshed copy -- every other checkpoint uses
        # the original, untouched `game`, identically to the game-level
        # cycle's own rule (a same-cycle lineup confirmation must never
        # leak backward into an earlier T_MINUS_X/closing snapshot).
        eval_game = lineup_refreshed_game if checkpoint == "LINEUP_CONFIRMATION" else game
        due_games_by_checkpoint[checkpoint].append(eval_game)
        game_id_by_matchup_and_checkpoint[(_matchup_label(eval_game), checkpoint)] = game_id
        run_log.append({
            "gameId": game_id, "action": "DUE", "checkpoint": checkpoint,
            "reason": None, "minutesToStart": minutes_to_start, "warnings": warnings,
            "lineupPollAttempted": lineup_poll_attempted, "lineupPollFailed": lineup_poll_failed,
            "lineupNewlyConfirmed": lineup_newly_confirmed,
        })

    if not due_games_by_checkpoint:
        print(
            f"[hitter_prospective_snapshot] cycle complete date={date} runId={run_id} "
            f"noCheckpointsDue elapsedSeconds={round(time.time() - cycle_started_at, 2)}",
            flush=True,
        )
        return [], run_log

    due_summary = {cp: len(g) for cp, g in due_games_by_checkpoint.items()}
    print(f"[hitter_prospective_snapshot] checkpoints due this cycle: {due_summary}", flush=True)

    build_board_main_fn = build_board_main_fn
    write_filtered_slate_fn = write_filtered_slate_fn
    generated_at = ids.utc_now_iso()
    new_rows = []

    for checkpoint, due_games in due_games_by_checkpoint.items():
        batch_started_at = time.time()
        print(
            f"[hitter_prospective_snapshot] checkpoint batch starting checkpoint={checkpoint} games={len(due_games)}",
            flush=True,
        )
        slate_path = write_filtered_slate_fn(date, run_id, checkpoint, due_games)

        board_build_started_at = time.time()
        print(
            f"[hitter_prospective_snapshot] hitter-board build starting checkpoint={checkpoint} "
            f"games={len(due_games)} nSims={n_sims}",
            flush=True,
        )
        try:
            result = build_board_main_fn(
                date_str=date, slate_path=slate_path, weather_path=weather_path,
                savant_team_path=savant_team_path, kalshi_search_path=kalshi_search_path,
                n_sims=n_sims, research_run_id=run_id, dry_run=True, emit_rows=True,
            )
        except Exception as exc:  # one checkpoint group's failure must never erase another's rows or abort the cycle
            board_build_elapsed = round(time.time() - board_build_started_at, 2)
            batch_elapsed = round(time.time() - batch_started_at, 2)
            print(
                f"[hitter_prospective_snapshot] hitter-board build FAILED checkpoint={checkpoint} "
                f"elapsedSeconds={board_build_elapsed} error={exc}",
                flush=True,
            )
            for g in due_games:
                run_log.append({
                    "gameId": g.get("gameId"), "action": "SKIPPED", "checkpoint": checkpoint,
                    "reason": f"hitter board build raised: {exc}", "minutesToStart": None, "warnings": [],
                    "lineupPollAttempted": False, "lineupPollFailed": False, "lineupNewlyConfirmed": False,
                    "boardBuildElapsedSeconds": board_build_elapsed, "checkpointBatchElapsedSeconds": batch_elapsed,
                })
            continue

        board_build_elapsed = round(time.time() - board_build_started_at, 2)
        result_rows = result.get("rows") or []
        print(
            f"[hitter_prospective_snapshot] hitter-board build complete checkpoint={checkpoint} "
            f"elapsedSeconds={board_build_elapsed} rows={len(result_rows)}",
            flush=True,
        )

        for row in result_rows:
            game_id = game_id_by_matchup_and_checkpoint.get((row.get("matchup"), checkpoint))
            new_rows.append(dict(
                row,
                gameId=game_id,
                checkpoint=checkpoint,
                researchRunId=run_id,
                engineCommitSha=engine_commit_sha,
                snapshotGeneratedAt=generated_at,
                hitterProjectionSnapshotId=ids.build_hitter_projection_snapshot_id(run_id, row.get("marketTicker"), checkpoint),
            ))
        batch_elapsed = round(time.time() - batch_started_at, 2)
        for g in due_games:
            for entry in run_log:
                if entry.get("gameId") == g.get("gameId") and entry.get("checkpoint") == checkpoint and entry["action"] == "DUE":
                    entry["action"] = "EVALUATED"
                    entry["recordsWritten"] = sum(1 for r in new_rows if r["gameId"] == g.get("gameId") and r["checkpoint"] == checkpoint)
                    entry["boardBuildElapsedSeconds"] = board_build_elapsed
                    entry["checkpointBatchElapsedSeconds"] = batch_elapsed
        print(
            f"[hitter_prospective_snapshot] checkpoint batch complete checkpoint={checkpoint} "
            f"elapsedSeconds={batch_elapsed}",
            flush=True,
        )

    total_elapsed = round(time.time() - cycle_started_at, 2)
    print(
        f"[hitter_prospective_snapshot] cycle complete date={date} runId={run_id} "
        f"checkpointBatches={len(due_games_by_checkpoint)} newRows={len(new_rows)} "
        f"elapsedSeconds={total_elapsed}",
        flush=True,
    )

    return new_rows, run_log
