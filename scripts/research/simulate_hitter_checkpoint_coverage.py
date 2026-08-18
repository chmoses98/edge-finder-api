#!/usr/bin/env python3
"""
scripts/research/simulate_hitter_checkpoint_coverage.py
=============================================================
RESEARCH-ONLY. Deterministic coverage simulation for
lib.research.hitter_prospective_snapshot's checkpoint scheduler.

For every possible game-start minute-of-hour offset (:00 through :59),
simulates a full scheduler run (a series of ticks spaced `cadence_minutes`
apart, aligned to :00/:MM past each hour exactly as GitHub Actions cron
`*/N` fires) against ONE synthetic game, using the REAL, unmodified
lib.research.hitter_prospective_snapshot.determine_due_hitter_checkpoint
and lib.edgelab.prospective_snapshot.classify_game_eligibility functions
(never a separate reimplemented model of them -- this simulation must
reflect exactly what the real scheduler does).

Reports, per target checkpoint, how many of the 60 minute-offsets are
captured vs. missed under a given (cadence, tolerance, closing-window)
configuration. Used to (a) prove the pre-fix 30-minute/7.5-tolerance/
12-minute-window configuration has real coverage gaps (Part 1 of the
required audit), and (b) prove the post-fix configuration closes them.

Usage:
    python3 scripts/research/simulate_hitter_checkpoint_coverage.py
    python3 scripts/research/simulate_hitter_checkpoint_coverage.py --cadence 15 --tolerance 12 --closing-window 20
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
TIME_TARGET_LABELS = ("T_MINUS_90", "T_MINUS_60", "T_MINUS_30")

# The scheduler's ACTUAL cron only fires during listed UTC hours (GitHub
# Actions `*/15 16,17,...,23 * * *` + `*/15 0,...,5 * * *` never fires
# outside those hours at all -- unlike tolerance/cadence, there is no
# "nearest tick" fallback for an hour the cron simply never runs in).
# These two named windows let the simulation reproduce that exactly:
# OLD_OPERATING_HOURS is the pre-full-day-fix window (16:00-23:45 UTC,
# 00:00-05:45 UTC) both schedulers originally shared; NEW_OPERATING_HOURS
# is the corrected window (see docs/HITTER_CHECKPOINT_COVERAGE_FIX.md
# Sec.9 for the derivation). `operating_hours=None` (the default for
# simulate_one_game/run_coverage_table, preserving every existing
# caller's behavior) means "always on" -- no daily-window restriction at
# all, i.e. tests ONLY the minute-of-hour alignment question the
# original coverage fix addressed.
OLD_OPERATING_HOURS = frozenset(list(range(16, 24)) + list(range(0, 6)))
NEW_OPERATING_HOURS = frozenset(list(range(13, 24)) + list(range(0, 6)))


def _tick_times(scheduled_start, cadence_minutes, window_start_offset_minutes=180, window_end_offset_minutes=30,
                 operating_hours=None):
    """
    Every aligned tick instant a `*/cadence_minutes` GitHub Actions cron
    would fire, from `window_start_offset_minutes` before THIS game's own
    scheduled_start through `window_end_offset_minutes` after it -- wide
    enough to comfortably bracket every checkpoint target (T-90 is the
    earliest) and the game's own start (POST_START cutoff), anchored to
    the actual game being simulated rather than a fixed reference point
    (a fixed-reference window would only cover the games whose start
    time happens to fall inside it -- a real bug caught during this
    simulation's own development, see the module's test coverage).
    Ticks are aligned to minute-of-hour multiples of cadence_minutes
    (e.g. :00/:15/:30/:45 for a 15-minute cadence), matching real cron
    `*/N` semantics exactly -- never offset to conveniently align with
    any target. `window_start_offset_minutes` (180 = 3 hours) comfortably
    exceeds even the furthest target (T-90) with 90 minutes of margin,
    which is enough range for `operating_hours` gating to correctly
    prove a boundary case too (a completely-inactive 180-minute window
    correctly yields zero candidate ticks, proving a miss).

    `operating_hours`, when given, is a set of UTC hours (0-23) the
    scheduler actually runs in -- any candidate tick whose UTC hour
    isn't in this set is dropped BEFORE due-checkpoint evaluation,
    exactly reproducing a real GitHub Actions cron's hour-list gating
    (there is no "nearest active hour" fallback in real cron -- an hour
    outside the list simply never fires, at all).
    """
    start = scheduled_start - timedelta(minutes=window_start_offset_minutes)
    # Snap to the next aligned tick at or after `start`, aligned to
    # minute-of-HOUR (not minute-of-day) multiples of cadence_minutes,
    # exactly matching cron `*/N * * * *` semantics.
    remainder = start.minute % cadence_minutes
    if remainder:
        start += timedelta(minutes=(cadence_minutes - remainder))
    start = start.replace(second=0, microsecond=0)
    end = scheduled_start + timedelta(minutes=window_end_offset_minutes)
    ticks = []
    t = start
    while t <= end:
        if operating_hours is None or t.hour in operating_hours:
            ticks.append(t)
        t += timedelta(minutes=cadence_minutes)
    return ticks


def simulate_one_game(start_minute_offset, *, cadence_minutes, tolerance_minutes, closing_window_minutes,
                       lineup_confirmed_at_tick_index=None, systematic_delay_minutes=0, operating_hours=None):
    """
    Simulates a full scheduler run for a synthetic game whose scheduled
    start is GAME_DAY + start_minute_offset minutes. Returns
    {label: captured_bool} for every target checkpoint, plus the exact
    minutesToStart each captured checkpoint actually fired at (never
    assumed to equal the nominal target).
    """
    scheduled_start = GAME_DAY + timedelta(minutes=start_minute_offset)
    scheduled_start_iso = scheduled_start.strftime("%Y-%m-%dT%H:%M:%SZ")
    game = {
        "gameId": "SIM", "startTime": scheduled_start_iso,
        "away": {"abbr": "AAA"}, "home": {"abbr": "BBB"},
        "awayTeamStats": {"lineupConfirmedOfficial": False},
        "homeTeamStats": {"lineupConfirmedOfficial": False},
    }

    ticks = _tick_times(scheduled_start, cadence_minutes, operating_hours=operating_hours)
    if systematic_delay_minutes:
        # Simulates every scheduled run firing `systematic_delay_minutes`
        # late (a realistic, documented GitHub Actions behavior --
        # scheduled workflow runs are not guaranteed to fire at the
        # exact cron instant) -- the aligned GRID itself is unaffected
        # (the next tick's nominal time doesn't shift because a prior
        # one ran late), only the INSTANT each tick's logic actually
        # executes.
        ticks = [t + timedelta(minutes=systematic_delay_minutes) for t in ticks]
    captured = {}
    captured_at_minutes_to_start = {}

    for i, tick in enumerate(ticks):
        now_iso = tick.strftime("%Y-%m-%dT%H:%M:%SZ")

        if lineup_confirmed_at_tick_index is not None and i >= lineup_confirmed_at_tick_index:
            game["awayTeamStats"]["lineupConfirmedOfficial"] = True
            game["homeTeamStats"]["lineupConfirmedOfficial"] = True

        eligible, _reason, _mts = classify_game_eligibility(game, now=now_iso)
        if not eligible:
            continue

        already = set(captured.keys())
        label, minutes_to_start = hps.determine_due_hitter_checkpoint(
            game, now=now_iso, already_captured=already, target_checkpoints=TARGET_LABELS,
            tolerance_minutes=tolerance_minutes, closing_window_minutes=closing_window_minutes,
        )
        if label is not None:
            captured[label] = True
            captured_at_minutes_to_start[label] = minutes_to_start

    return {label: (label in captured) for label in TARGET_LABELS}, captured_at_minutes_to_start


def run_coverage_table(*, cadence_minutes, tolerance_minutes, closing_window_minutes, systematic_delay_minutes=0,
                        operating_hours=None):
    """{label: {"coveredCount": int, "missedOffsets": [int, ...]}} across every minute-of-hour offset 0-59, for a game start hour comfortably inside `operating_hours` (or, when operating_hours is None, an always-on scheduler) -- tests ONLY minute-of-hour alignment, matching the original coverage fix's own scope. See run_full_day_coverage_table for the hour-of-day sweep."""
    per_label_missed = {label: [] for label in TARGET_LABELS}
    for minute_offset in range(60):
        # Game "starts" at GAME_DAY + 3 hours + minute_offset minutes --
        # the +3h just keeps the whole T-90..closing window comfortably
        # inside a single simulated day; only the MINUTE alignment
        # (mod cadence) actually matters for coverage.
        start_total_offset = 180 + minute_offset
        result, _ = simulate_one_game(
            start_total_offset, cadence_minutes=cadence_minutes, tolerance_minutes=tolerance_minutes,
            closing_window_minutes=closing_window_minutes, systematic_delay_minutes=systematic_delay_minutes,
            operating_hours=operating_hours,
        )
        for label in TARGET_LABELS:
            if label == "LINEUP_CONFIRMATION":
                continue  # event-driven, not a time-alignment concern -- excluded from this table
            if not result[label]:
                per_label_missed[label].append(minute_offset)

    return {
        label: {
            "coveredCount": 60 - len(missed),
            "missedCount": len(missed),
            "missedOffsets": missed,
        }
        for label, missed in per_label_missed.items() if label != "LINEUP_CONFIRMATION"
    }


def run_full_day_coverage_table(*, cadence_minutes, tolerance_minutes, closing_window_minutes,
                                 operating_hours=None, systematic_delay_minutes=0):
    """
    {label: {"coveredCount": int, "missedCount": int, "missedStartTimes": ["HH:MM", ...]}}
    across EVERY possible game start time in a full UTC day (24 hours x
    60 minutes = 1,440 combinations) -- unlike run_coverage_table (minute-
    of-hour only, a fixed representative hour), this sweeps the hour
    dimension too, so it can prove (or disprove) coverage for early-day
    games, late-night games, and the UTC-date-boundary games the
    original minute-only simulation could never have exercised (it
    always tested at a game-start hour deep inside the operating window
    by construction). This is the harness that actually answers "can
    every realistic MLB first pitch receive its checkpoints" -- not just
    "is the minute-grid alignment correct once the scheduler happens to
    already be running."
    """
    per_label_missed = {label: [] for label in TARGET_LABELS}
    for hour in range(24):
        for minute in range(60):
            start_total_offset = hour * 60 + minute
            result, _ = simulate_one_game(
                start_total_offset, cadence_minutes=cadence_minutes, tolerance_minutes=tolerance_minutes,
                closing_window_minutes=closing_window_minutes, systematic_delay_minutes=systematic_delay_minutes,
                operating_hours=operating_hours,
            )
            for label in TARGET_LABELS:
                if label == "LINEUP_CONFIRMATION":
                    continue
                if not result[label]:
                    per_label_missed[label].append(f"{hour:02d}:{minute:02d}")

    total = 24 * 60
    return {
        label: {
            "coveredCount": total - len(missed),
            "missedCount": len(missed),
            "totalCombinations": total,
            "missedStartTimesUTC": missed,
        }
        for label, missed in per_label_missed.items() if label != "LINEUP_CONFIRMATION"
    }


def et_wall_time_to_utc_offset_minutes(hour_et, minute_et, *, is_dst):
    """
    Minutes-from-UTC-midnight for a given Eastern wall-clock time on
    GAME_DAY -- EDT is UTC-4, EST is UTC-5. Used only to build readable
    example start times (e.g. "12:10 PM ET") for the CLI/tests; the
    simulation itself always operates in UTC.
    """
    utc_offset_hours = 4 if is_dst else 5
    total_minutes_et = hour_et * 60 + minute_et
    return (total_minutes_et + utc_offset_hours * 60) % (24 * 60)


_OPERATING_HOURS_PRESETS = {
    "old": OLD_OPERATING_HOURS,
    "new": NEW_OPERATING_HOURS,
    "none": None,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cadence", type=int, default=30)
    parser.add_argument("--tolerance", type=float, default=7.5)
    parser.add_argument("--closing-window", type=int, default=12)
    parser.add_argument("--systematic-delay", type=float, default=0, help="Simulates every scheduled run firing this many minutes late (realistic GitHub Actions delay).")
    parser.add_argument("--full-day", action="store_true", help="Sweep every (hour, minute) start time in a full UTC day, not just minute-of-hour at a fixed representative hour.")
    parser.add_argument("--operating-hours", choices=["old", "new", "none"], default="none",
                         help="'old' = original 16:00-23:45/00:00-05:45 UTC window, 'new' = corrected 13:00-23:45/00:00-05:45 UTC window, 'none' = always-on (default).")
    args = parser.parse_args()

    operating_hours = _OPERATING_HOURS_PRESETS[args.operating_hours]

    if args.full_day:
        table = run_full_day_coverage_table(
            cadence_minutes=args.cadence, tolerance_minutes=args.tolerance, closing_window_minutes=args.closing_window,
            systematic_delay_minutes=args.systematic_delay, operating_hours=operating_hours,
        )
    else:
        table = run_coverage_table(
            cadence_minutes=args.cadence, tolerance_minutes=args.tolerance, closing_window_minutes=args.closing_window,
            systematic_delay_minutes=args.systematic_delay, operating_hours=operating_hours,
        )
    print(json.dumps({
        "config": {"cadenceMinutes": args.cadence, "toleranceMinutes": args.tolerance,
                   "closingWindowMinutes": args.closing_window, "systematicDelayMinutes": args.systematic_delay,
                   "operatingHours": args.operating_hours, "fullDay": args.full_day},
        "coverage": table,
    }, indent=2))


if __name__ == "__main__":
    main()
