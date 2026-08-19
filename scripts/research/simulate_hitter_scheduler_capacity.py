#!/usr/bin/env python3
"""
scripts/research/simulate_hitter_scheduler_capacity.py
=============================================================
RESEARCH-ONLY. Deterministic scheduler-CAPACITY simulation for
lib.research.hitter_prospective_snapshot's checkpoint scheduler --
distinct from, and a required complement to,
scripts/research/simulate_hitter_checkpoint_coverage.py (which models
ONLY minute-of-hour cron alignment, always assuming every scheduled tick
executes instantly and independently). This module additionally models
what a real GitHub Actions `concurrency:` group actually does when a
cycle's own RUNTIME exceeds the cadence between ticks:

  - the workflow's `concurrency: { group: edgelab-hitter-snapshot,
    cancel-in-progress: false }` block, verified (2026-08-19, via GitHub's
    own current documentation) to mean: at most ONE run is ever RUNNING
    in the group; at most ONE additional run is ever PENDING (this is
    GitHub's own DEFAULT "single-pending-slot" queue behavior -- no
    `queue:` key configured); `cancel-in-progress: false` controls ONLY
    whether the currently RUNNING job gets cancelled when a new run
    arrives (it does not -- it always finishes) -- it has NO EFFECT on
    the separate, always-on PENDING-SLOT-REPLACEMENT rule: a new run
    entering the group while one is already pending CANCELS that older
    pending run and takes its place, regardless of cancel-in-progress.
  - GitHub Actions GA'd (per the GitHub Changelog, 2026-05-07) an
    explicit `concurrency: { queue: max }` option: up to 100 runs may
    wait in true FIFO order instead of the newest evicting the oldest
    pending one. `queue: max` is REJECTED by GitHub's own workflow
    validation when combined with `cancel-in-progress: true` (not
    applicable here, since this workflow already uses
    `cancel-in-progress: false`).

simulate_concurrency_group() is a pure, general timeline simulator for
either mode. simulate_checkpoint_coverage_under_load() combines that
timeline with the REAL, unmodified
lib.research.hitter_prospective_snapshot.determine_due_hitter_checkpoint/
compute_missed_hitter_checkpoints/lib.edgelab.prospective_snapshot.classify_game_eligibility
functions (never a second, reimplemented model of them) -- every
executed cycle's checkpoint evaluation uses that cycle's REAL,
actually-observed execution instant (never the nominal cron trigger
instant it was originally scheduled for), so a cycle delayed by queueing
can never fabricate an on-time capture it didn't really make; a
checkpoint whose window has definitively closed by the time a delayed
cycle finally runs is reported MISSED via the same
compute_missed_hitter_checkpoints mechanism the production scheduler
itself uses -- never silently dropped and never backdated.

Usage:
    python3 scripts/research/simulate_hitter_scheduler_capacity.py
    python3 scripts/research/simulate_hitter_scheduler_capacity.py --queue-mode max
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.prospective_snapshot import classify_game_eligibility
from lib.research import hitter_prospective_snapshot as hps

GAME_DAY = datetime(2026, 8, 18, tzinfo=timezone.utc)
TARGET_LABELS = ("T_MINUS_90", "T_MINUS_60", "T_MINUS_30", "LINEUP_CONFIRMATION", hps.HITTER_CLOSING_WINDOW)

EXECUTED = "EXECUTED"
CANCELED_WHILE_PENDING = "CANCELED_WHILE_PENDING"
REJECTED_QUEUE_FULL = "REJECTED_QUEUE_FULL"


def simulate_concurrency_group(cron_times, runtime_minutes_fn, *, queue_mode="single", queue_max=100):
    """
    Pure. `cron_times`: sorted iterable of nominal trigger instants
    (minutes, numeric, strictly increasing) a `*/N`-minute GitHub Actions
    cron would fire at this concurrency group. `runtime_minutes_fn(t)`:
    the wall-clock runtime (minutes) that cycle would take IF it runs.
    `queue_mode`: "single" (GitHub's default -- see module docstring) or
    "max" (the `queue: max` FIFO option, capped at `queue_max`).

    Returns {cron_time: {"outcome", "actualStart", "actualEnd",
    "delayMinutes"}} for EVERY cron_time given -- "EXECUTED" entries
    carry real actualStart/actualEnd/delayMinutes (delayMinutes = how
    much later than its own nominal trigger instant it actually started,
    0 for an on-time start); "CANCELED_WHILE_PENDING" (single mode only)
    and "REJECTED_QUEUE_FULL" (max mode only, exceptionally rare given
    the 100-slot cap) entries carry actualStart=actualEnd=None -- that
    cycle never ran at all.

    Models a single-runner concurrency group (real GitHub Actions
    concurrency groups execute at most one job at a time by definition)
    with an event-driven timeline: a pending job dispatches the INSTANT
    the runner frees, which can fall strictly between two cron ticks --
    never only re-checked at the next tick's own instant.
    """
    cron_times = sorted(cron_times)
    results = {}
    runner_busy_until = None  # None = idle; else the instant the running job finishes
    pending_single = None  # queue_mode == "single": at most one waiting cron_time, or None
    pending_fifo = []  # queue_mode == "max": FIFO list of waiting cron_times

    def _dispatch(job, start):
        runtime = runtime_minutes_fn(job)
        end = start + runtime
        results[job] = {
            "outcome": EXECUTED, "actualStart": start, "actualEnd": end,
            "delayMinutes": start - job,
        }
        return end

    def _drain_pending_up_to(t):
        nonlocal runner_busy_until, pending_single
        while runner_busy_until is not None and runner_busy_until <= t:
            if queue_mode == "single":
                if pending_single is None:
                    break
                job, pending_single = pending_single, None
            else:
                if not pending_fifo:
                    break
                job = pending_fifo.pop(0)
            runner_busy_until = _dispatch(job, runner_busy_until)

    for t in cron_times:
        _drain_pending_up_to(t)
        is_busy = runner_busy_until is not None and runner_busy_until > t
        if not is_busy:
            runner_busy_until = _dispatch(t, t)
            continue

        if queue_mode == "single":
            if pending_single is not None:
                results[pending_single] = {
                    "outcome": CANCELED_WHILE_PENDING, "actualStart": None, "actualEnd": None, "delayMinutes": None,
                }
            pending_single = t
        else:
            if len(pending_fifo) >= queue_max:
                results[t] = {
                    "outcome": REJECTED_QUEUE_FULL, "actualStart": None, "actualEnd": None, "delayMinutes": None,
                }
            else:
                pending_fifo.append(t)

    # Drain whatever is still pending after the last cron tick -- these
    # cycles still genuinely execute (just later), they are not dropped
    # merely because no further tick arrived to observe it.
    if queue_mode == "single":
        if pending_single is not None:
            start = max(runner_busy_until, pending_single) if runner_busy_until is not None else pending_single
            runner_busy_until = _dispatch(pending_single, start)
            pending_single = None
    else:
        while pending_fifo:
            job = pending_fifo.pop(0)
            start = max(runner_busy_until, job) if runner_busy_until is not None else job
            runner_busy_until = _dispatch(job, start)

    return results


def _minutes_to_iso(minute_offset):
    return (GAME_DAY + timedelta(minutes=minute_offset)).strftime("%Y-%m-%dT%H:%M:%SZ")


def simulate_checkpoint_coverage_under_load(
    game_start_minute, *, cadence_minutes=15, runtime_minutes, queue_mode="single",
    tolerance_minutes=hps.HITTER_CHECKPOINT_TOLERANCE_MINUTES, closing_window_minutes=hps.HITTER_CLOSING_WINDOW_MINUTES,
    horizon_before_start=180, horizon_after_start=30, num_heavy_cycles=None,
):
    """
    Simulates one synthetic game's checkpoint capture across a full
    scheduler timeline that includes REAL concurrency-group contention,
    using the REAL determine_due_hitter_checkpoint/
    compute_missed_hitter_checkpoints/classify_game_eligibility functions
    -- never a reimplemented model of them.

    `runtime_minutes`: the cycle runtime (minutes) applied to the first
    `num_heavy_cycles` cron ticks (default: ALL ticks, i.e. every cycle
    in the window is this expensive -- the worst-case "extended heavy
    slate" scenario the task requires modeling); every cycle after that
    runs effectively instantaneously (0 minutes), matching a slate that
    returns to normal load after `num_heavy_cycles` consecutive expensive
    cycles.

    Returns {"ticks": {cron_time: {...outcome...}}, "capturedLabels":
    {label: True/False}, "capturedAtMinutesToStart": {label: float},
    "missedLabels": [label, ...]} -- `missedLabels` comes directly from
    compute_missed_hitter_checkpoints evaluated at the REAL execution
    time of each executed tick (never the nominal trigger time), so a
    cycle that only finally executes after a target's window has
    definitively closed correctly reports that target as missed rather
    than fabricating a late on-time capture.
    """
    scheduled_start = GAME_DAY + timedelta(minutes=game_start_minute)
    start = game_start_minute - horizon_before_start
    remainder = start % cadence_minutes
    if remainder:
        start += (cadence_minutes - remainder)
    end = game_start_minute + horizon_after_start
    cron_times = list(range(start, end + 1, cadence_minutes))

    heavy_count = len(cron_times) if num_heavy_cycles is None else num_heavy_cycles

    def _runtime_fn(t):
        idx = cron_times.index(t)
        return runtime_minutes if idx < heavy_count else 0

    timeline = simulate_concurrency_group(cron_times, _runtime_fn, queue_mode=queue_mode)

    game = {
        "gameId": "SIM", "startTime": scheduled_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "away": {"abbr": "AAA"}, "home": {"abbr": "BBB"},
        "awayTeamStats": {"lineupConfirmedOfficial": False},
        "homeTeamStats": {"lineupConfirmedOfficial": False},
    }

    captured = {}
    captured_at_minutes_to_start = {}
    missed_labels = set()

    for t in cron_times:
        entry = timeline[t]
        if entry["outcome"] != EXECUTED:
            continue  # a canceled/rejected cycle never evaluates this game at all
        now_iso = _minutes_to_iso(entry["actualStart"])

        eligible, _reason, _mts = classify_game_eligibility(game, now=now_iso)
        already = set(captured.keys())
        for missed_label in hps.compute_missed_hitter_checkpoints(
            game, now=now_iso, already_captured=already, target_checkpoints=TARGET_LABELS,
            tolerance_minutes=tolerance_minutes,
        ):
            missed_labels.add(missed_label)
        if not eligible:
            continue

        label, minutes_to_start = hps.determine_due_hitter_checkpoint(
            game, now=now_iso, already_captured=already, target_checkpoints=TARGET_LABELS,
            tolerance_minutes=tolerance_minutes, closing_window_minutes=closing_window_minutes,
        )
        if label is not None:
            captured[label] = True
            captured_at_minutes_to_start[label] = minutes_to_start

    return {
        "ticks": timeline,
        "capturedLabels": {label: (label in captured) for label in TARGET_LABELS},
        "capturedAtMinutesToStart": captured_at_minutes_to_start,
        "missedLabels": sorted(missed_labels - {"LINEUP_CONFIRMATION"}),
    }


RUNTIME_SCENARIOS_MINUTES = (5, 10, 15, 20, 25, 30, 40, 45)
HEAVY_CYCLE_COUNTS = (1, 2, 3)


def run_capacity_matrix(*, cadence_minutes=15, queue_mode="single"):
    """
    The required deterministic matrix: cadence=15min x runtime in
    RUNTIME_SCENARIOS_MINUTES x {1,2,3}+ consecutive heavy cycles, for
    ONE representative game (T-90 anchored at a clean cron-aligned
    instant). Reports, per scenario, which cron invocations executed,
    which were canceled/replaced (or rejected under queue:max), how late
    each executed cycle started relative to its own nominal trigger, and
    which of the 4 time/window-based checkpoint targets were captured
    vs. missed as a direct RESULT of that capacity contention (not mere
    minute-alignment -- alignment is held constant/favorable here so the
    only variable under test is runtime-vs-cadence-vs-concurrency).
    """
    # T-90 lands exactly on a cron tick by construction (game starts 90
    # minutes after a cadence-aligned instant) -- isolates the capacity
    # failure mode from the ALREADY-FIXED minute-alignment question
    # scripts/research/simulate_hitter_checkpoint_coverage.py covers.
    game_start_minute = 300  # 05:00 into GAME_DAY, cleanly cadence-aligned
    matrix = []
    for runtime in RUNTIME_SCENARIOS_MINUTES:
        for heavy_cycles in HEAVY_CYCLE_COUNTS:
            result = simulate_checkpoint_coverage_under_load(
                game_start_minute, cadence_minutes=cadence_minutes, runtime_minutes=runtime,
                queue_mode=queue_mode, num_heavy_cycles=heavy_cycles,
            )
            canceled = sorted(t for t, e in result["ticks"].items() if e["outcome"] == CANCELED_WHILE_PENDING)
            rejected = sorted(t for t, e in result["ticks"].items() if e["outcome"] == REJECTED_QUEUE_FULL)
            executed = sorted(t for t, e in result["ticks"].items() if e["outcome"] == EXECUTED)
            max_delay = max((result["ticks"][t]["delayMinutes"] for t in executed), default=0)
            matrix.append({
                "runtimeMinutes": runtime,
                "heavyCycles": heavy_cycles,
                "executedCount": len(executed),
                "canceledCount": len(canceled),
                "rejectedCount": len(rejected),
                "maxStartDelayMinutes": max_delay,
                "capturedLabels": result["capturedLabels"],
                "missedLabels": result["missedLabels"],
            })
    return matrix


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cadence", type=int, default=15)
    parser.add_argument("--queue-mode", choices=["single", "max"], default="single")
    args = parser.parse_args()

    matrix = run_capacity_matrix(cadence_minutes=args.cadence, queue_mode=args.queue_mode)
    print(json.dumps({
        "config": {"cadenceMinutes": args.cadence, "queueMode": args.queue_mode},
        "matrix": matrix,
    }, indent=2))


if __name__ == "__main__":
    main()
