#!/usr/bin/env python3
"""
scripts/edgelab/run_hitter_prospective_snapshots.py
=========================================================
Hitter Projection Checkpoint Scheduling milestone: CLI entry point
invoked on a recurring schedule (see
.github/workflows/hitter-snapshot-scheduler.yml) during the MLB pregame
window. Thin I/O wrapper around
lib.research.hitter_prospective_snapshot.run_hitter_prospective_snapshot_cycle
-- all orchestration/eligibility/scheduling logic lives there and is
unit tested without any network access or real Monte Carlo simulation;
this script only supplies the real production functions and real data
sources, mirroring scripts/edgelab/run_prospective_snapshots.py's own
split for the game-level system.

READ-ONLY with respect to every file this repository's production
betting behavior depends on:
  - never reads or writes data/slate.json (independently resolves
    schedule/lineups via scripts.fetch_standalone_pregame_context, the
    SAME independent source the existing manual hitter research entry
    point already uses -- see docs/HITTER_SIMULATION_ENGINE.md Sec.15.3)
  - reads the latest already-committed, regularly-scheduled Kalshi
    snapshot (data/kalshi_registry_snapshots/kalshi_search_<date>_<HHMM>.json,
    produced independently by .github/workflows/capture-snapshots-scheduled.yml)
    -- never makes its own live Kalshi fetch, so this script's own
    failure domain can never include a Kalshi API problem
  - WRITES ONLY data/edgelab/hitter_projection_snapshots/<date>.jsonl
    (append-only, idempotent, via lib.edgelab.storage.append_records),
    data/edgelab/research_runs/<date>.jsonl (a run-metadata record, same
    convention run_prospective_snapshots.py already uses), and
    run-scoped filtered-slate files under
    data/pipeline/<date>/<runId>/ -- never
    data/pipeline/<date>/hitter_projection_board.json (every
    scripts.build_hitter_projection_board.main() call here passes
    dry_run=True)
  - never touches data/edgelab/recommendations/, data/edgelab/bets/, or
    any bankroll file; never calls scripts/risk_gate.py or
    scripts/write_pending_bets.py

A failure anywhere in this script (a bad game, a lineup-fetch network
error, a missing schedule, a hitter-simulation exception) must never
raise past main() uncaught -- this is a best-effort, non-blocking
collector that must never be able to fail a workflow run other
workflows depend on. See module docstring of
lib/research/hitter_prospective_snapshot.py for the full safety
contract.

Usage:
    python3 scripts/edgelab/run_hitter_prospective_snapshots.py
    python3 scripts/edgelab/run_hitter_prospective_snapshots.py --date 2026-08-19 --dry-run
"""
import argparse
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import ids, storage
from lib.research.hitter_prospective_snapshot import (
    HITTER_CORE_CHECKPOINTS,
    run_hitter_prospective_snapshot_cycle,
    write_filtered_hitter_slate,
)
from scripts.build_hitter_projection_board import DEFAULT_N_SIMS
from scripts.build_hitter_projection_board import main as build_hitter_projection_board_main
from scripts.fetch_lineups import fetch_lineup_for_game, load_batter_woba, load_team_woba
from scripts.fetch_standalone_pregame_context import main as fetch_standalone_pregame_context_main

DEFAULT_KALSHI_SNAPSHOT_DIR = os.path.join("data", "kalshi_registry_snapshots")
HITTER_SNAPSHOTS_ENTITY = "hitter_projection_snapshots"

# Bounded-fallback-policy wiring (see
# lib.research.hitter_prospective_snapshot.HITTER_FALLBACK_WORST_CASE_SECONDS's
# own docstring, and docs/HITTER_SCHEDULER_RUNTIME_HARDENING.md): must match
# .github/workflows/hitter-snapshot-scheduler.yml's own `timeout-minutes: 30`
# -- kept as a separate constant here (not imported from the workflow YAML,
# which this script has no reason to parse) so a future change to either
# needs a human to update both, deliberately, rather than silently drifting.
# NON_SCRIPT_OVERHEAD_SECONDS_BUDGET reserves headroom for the OTHER steps
# that also count against the job's own timeout-minutes (checkout, Python
# setup, the post-script commit/artifact-upload steps) -- this script's own
# job_timeout_seconds budget is intentionally smaller than the job's full
# timeout, never equal to it.
JOB_TIMEOUT_MINUTES = 30
NON_SCRIPT_OVERHEAD_SECONDS_BUDGET = 120
DEFAULT_JOB_TIMEOUT_SECONDS_FOR_SCRIPT = JOB_TIMEOUT_MINUTES * 60 - NON_SCRIPT_OVERHEAD_SECONDS_BUDGET


def compute_run_status(evaluated_count, genuine_failure_count):
    """Pure. Same honest three/four-way status scheme run_prospective_snapshots.compute_run_status established -- never 'success' merely because the process reached the end."""
    if genuine_failure_count and not evaluated_count:
        return "failed"
    if genuine_failure_count:
        return "partial"
    if evaluated_count:
        return "success"
    return "no_op"


def latest_dated_kalshi_snapshot(date, snapshot_dir=DEFAULT_KALSHI_SNAPSHOT_DIR):
    """
    The most recent already-committed, regularly-scheduled Kalshi
    snapshot for `date` (kalshi_search_<date>_<HHMM>.json -- the
    "timestamped" naming lib/snapshot_retention.py already recognizes,
    produced independently by capture-snapshots-scheduled.yml). Never
    the bare kalshi_search_<date>.json (that "dated" file is a single
    end-of-day snapshot, not necessarily the freshest available at
    cycle time) and never a `*_standalone.json` file (those are
    workflow_dispatch-only manual captures, not part of this scheduled
    system's own regular cadence). Returns None if no such file exists
    yet today -- never falls back to a different date's file.
    """
    candidates = sorted(glob.glob(os.path.join(snapshot_dir, f"kalshi_search_{date}_[0-9][0-9][0-9][0-9].json")))
    return candidates[-1] if candidates else None


def _live_status_by_team_pair(date):
    """Best-effort, identical pattern to run_prospective_snapshots._live_status_by_team_pair -- returns {} on any failure, never a hard block."""
    try:
        from lib.edgelab import mlb_schedule
        schedule_json = mlb_schedule.fetch_schedule(date)
        if schedule_json is None:
            return {}
        parsed = mlb_schedule.parse_schedule_games(schedule_json)
        context, _warnings = mlb_schedule.build_schedule_game_context(parsed)
        return {pair: entry["status"] for pair, entry in context.items()}
    except Exception as exc:
        print(f"[run_hitter_prospective_snapshots] WARNING: live schedule status unavailable: {exc}", file=sys.stderr)
        return {}


def _aggregate_batch_runtimes(run_log):
    """{checkpoint: elapsedSeconds} for each of checkpointBatchElapsedSeconds/boardBuildElapsedSeconds,
    derived from the run_log entries lib.research.hitter_prospective_snapshot.run_hitter_prospective_snapshot_cycle
    already annotates -- every game in the same checkpoint's batch carries the SAME batch-level
    elapsed value, so the first entry seen for a checkpoint is authoritative (dict.setdefault dedupes
    the rest for free). Pure; used both by main() and directly by tests."""
    checkpoint_batch_runtime_seconds = {}
    board_build_runtime_seconds = {}
    for entry in run_log:
        checkpoint = entry.get("checkpoint")
        if not checkpoint:
            continue
        if entry.get("checkpointBatchElapsedSeconds") is not None:
            checkpoint_batch_runtime_seconds.setdefault(checkpoint, entry["checkpointBatchElapsedSeconds"])
        if entry.get("boardBuildElapsedSeconds") is not None:
            board_build_runtime_seconds.setdefault(checkpoint, entry["boardBuildElapsedSeconds"])
    return checkpoint_batch_runtime_seconds, board_build_runtime_seconds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="Slate date YYYY-MM-DD (default: today, UTC)")
    parser.add_argument("--checkpoints", default=None, help="Comma-separated checkpoint targets (default: the 5 core hitter checkpoints)")
    parser.add_argument("--n-sims", type=int, default=DEFAULT_N_SIMS, help="Monte Carlo simulations per hitter (default: scripts.build_hitter_projection_board.DEFAULT_N_SIMS)")
    parser.add_argument("--dry-run", action="store_true", help="Compute and print what would be written, without writing anything")
    parser.add_argument("--job-timeout-seconds", type=int, default=DEFAULT_JOB_TIMEOUT_SECONDS_FOR_SCRIPT,
                         help="Bounded-fallback-policy budget: must safely fit within .github/workflows/"
                              "hitter-snapshot-scheduler.yml's own timeout-minutes (default: derived from "
                              "JOB_TIMEOUT_MINUTES minus a fixed non-script-overhead buffer). Pass 0 to disable "
                              "the bounded-fallback check entirely (always attempt fallback, matching this "
                              "script's pre-fix behavior).")
    args = parser.parse_args()
    job_timeout_seconds = args.job_timeout_seconds or None

    main_started_at = time.time()
    now = ids.utc_now_iso()
    date = args.date or now[:10]
    target_checkpoints = tuple(args.checkpoints.split(",")) if args.checkpoints else HITTER_CORE_CHECKPOINTS

    kalshi_search_path = latest_dated_kalshi_snapshot(date)
    if kalshi_search_path is None:
        print(f"[run_hitter_prospective_snapshots] no committed Kalshi snapshot found yet for {date} -- nothing to do this cycle.")
        return 0

    run_id = ids.new_run_id("HITTER_PROSPECTIVE_SNAPSHOT", github_run_id=os.environ.get("GITHUB_RUN_ID"))

    context_path = os.path.join("data", "pipeline", date, run_id, "standalone_pregame_context.json")
    try:
        pregame_context = fetch_standalone_pregame_context_main(date_str=date, output_path=context_path)
    except Exception as exc:
        print(f"[run_hitter_prospective_snapshots] WARNING: standalone pregame context fetch failed: {exc}", file=sys.stderr)
        return 0  # a schedule-fetch failure is a no-op cycle, never a hard failure

    games = pregame_context.get("games") or []
    if not games:
        print(f"[run_hitter_prospective_snapshots] no games found for {date} -- nothing to do.")
        return 0

    print(f"[run_hitter_prospective_snapshots] slate date={date} totalGamesConsidered={len(games)}", flush=True)

    existing_rows = list(storage.read_records(storage.partition_path(HITTER_SNAPSHOTS_ENTITY, date)))
    batter_woba_map = load_batter_woba()
    team_woba_map = load_team_woba()
    live_status = _live_status_by_team_pair(date)

    new_rows, run_log = run_hitter_prospective_snapshot_cycle(
        date, games, existing_rows,
        now=now, target_checkpoints=target_checkpoints, live_status_by_team_pair=live_status,
        lineup_fetch_fn=fetch_lineup_for_game, batter_woba_map=batter_woba_map, team_woba_map=team_woba_map,
        build_board_main_fn=build_hitter_projection_board_main, write_filtered_slate_fn=write_filtered_hitter_slate,
        kalshi_search_path=kalshi_search_path, n_sims=args.n_sims, run_id=run_id,
        job_timeout_seconds=job_timeout_seconds,
    )

    evaluated = [r for r in run_log if r["action"] == "EVALUATED"]
    skipped = [r for r in run_log if r["action"] == "SKIPPED"]
    missed = [r for r in run_log if r["action"] == "MISSED"]
    skip_reason_counts = {}
    for entry in skipped:
        skip_reason_counts[entry["reason"]] = skip_reason_counts.get(entry["reason"], 0) + 1
    checkpoint_counts = {}
    for entry in evaluated:
        checkpoint_counts[entry["checkpoint"]] = checkpoint_counts.get(entry["checkpoint"], 0) + 1
    missed_checkpoint_counts = {}
    for entry in missed:
        missed_checkpoint_counts[entry["checkpoint"]] = missed_checkpoint_counts.get(entry["checkpoint"], 0) + 1

    genuine_failures = [
        r for r in skipped if isinstance(r.get("reason"), str) and r["reason"].startswith("hitter board build raised")
    ]
    lineup_poll_attempts = sum(1 for r in run_log if r.get("lineupPollAttempted"))
    lineup_poll_successes = sum(1 for r in run_log if r.get("lineupNewlyConfirmed"))
    lineup_poll_failures = sum(1 for r in run_log if r.get("lineupPollFailed"))

    print(
        f"[run_hitter_prospective_snapshots] date={date} now={now} games={len(games)} "
        f"evaluated={len(evaluated)} skipped={len(skipped)} missed={len(missed)} newRows={len(new_rows)} "
        f"kalshiSnapshot={kalshi_search_path}"
    )
    if missed:
        print(f"[run_hitter_prospective_snapshots] WARNING: {len(missed)} checkpoint(s) definitively missed this cycle (window closed, never captured): {missed_checkpoint_counts}", file=sys.stderr)
    for entry in run_log:
        print(f"  {entry['gameId']}: {entry['action']} checkpoint={entry['checkpoint']} reason={entry['reason']} warnings={entry['warnings']}")

    checkpoint_batch_runtime_seconds, board_build_runtime_seconds = _aggregate_batch_runtimes(run_log)

    if args.dry_run:
        total_runtime_seconds = round(time.time() - main_started_at, 2)
        print(f"[run_hitter_prospective_snapshots] --dry-run: not writing anything. totalRuntimeSeconds={total_runtime_seconds}")
        return 0

    print("[run_hitter_prospective_snapshots] persistence starting", flush=True)
    written, skipped_dup = 0, 0
    if new_rows:
        written, skipped_dup = storage.append_records(
            storage.partition_path(HITTER_SNAPSHOTS_ENTITY, date), new_rows, "hitterProjectionSnapshotId",
        )
    print(f"[run_hitter_prospective_snapshots] persistence complete written={written} skippedDuplicate={skipped_dup}", flush=True)

    total_runtime_seconds = round(time.time() - main_started_at, 2)
    print(f"[run_hitter_prospective_snapshots] cycle total elapsedSeconds={total_runtime_seconds}", flush=True)

    run_status = compute_run_status(len(evaluated), len(genuine_failures))

    run_record = {
        "schemaVersion": "1",
        "runId": run_id,
        "runType": "HITTER_PROSPECTIVE_SNAPSHOT",
        "startedAt": now,
        "completedAt": ids.utc_now_iso(),
        "status": run_status,
        "date": date,
        "sourceWorkflow": os.environ.get("GITHUB_WORKFLOW"),
        "githubRunId": os.environ.get("GITHUB_RUN_ID"),
        "sourceCapturePath": kalshi_search_path,
        "standalonePregameContextPath": context_path,
        "inputFiles": [kalshi_search_path, context_path],
        "outputFiles": [storage.partition_path(HITTER_SNAPSHOTS_ENTITY, date)],
        "counts": {
            "gamesConsidered": len(games),
            "gamesEvaluated": len(evaluated),
            "gamesSkipped": len(skipped),
            "gamesSkippedByReason": skip_reason_counts,
            "gamesEvaluatedByCheckpoint": checkpoint_counts,
            "checkpointsMissed": len(missed),
            "checkpointsMissedByLabel": missed_checkpoint_counts,
            "hitterProjectionSnapshotsWritten": written,
            "hitterProjectionSnapshotsSkippedDuplicate": skipped_dup,
            "lineupPollAttempts": lineup_poll_attempts,
            "lineupPollSuccesses": lineup_poll_successes,
            "lineupPollFailures": lineup_poll_failures,
            "totalRuntimeSeconds": total_runtime_seconds,
            "checkpointBatchRuntimeSeconds": checkpoint_batch_runtime_seconds,
            "boardBuildRuntimeSeconds": board_build_runtime_seconds,
        },
        "errors": [r["reason"] for r in genuine_failures],
        "warnings": [w for entry in run_log for w in entry["warnings"]],
        "createdAt": now,
        "provenance": {
            "sourceSystem": "edgelab_hitter_prospective_snapshot",
            "sourceFile": __file__,
            "sourceKey": date,
            "capturedAt": now,
            "ingestedAt": ids.utc_now_iso(),
        },
    }
    storage.append_records(storage.partition_path("research_runs", date), [run_record], "runId")

    return 0


if __name__ == "__main__":
    sys.exit(main())
