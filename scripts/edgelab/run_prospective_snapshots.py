#!/usr/bin/env python3
"""
scripts/edgelab/run_prospective_snapshots.py
==================================================
EdgeLab Prospective Model Snapshots milestone: CLI entry point invoked
on a recurring schedule (see .github/workflows/model-snapshot-scheduler.yml)
during the MLB pregame window. Thin I/O wrapper around
lib.edgelab.prospective_snapshot.run_prospective_snapshot_cycle -- all
orchestration/eligibility/scheduling logic lives there and is unit
tested without any network access; this script only supplies the real
production functions and real data sources.

READ-ONLY with respect to every file this repository's production
betting behavior depends on:
  - reads data/slate.json (never writes it)
  - reads data/edgelab/observations/<date>.jsonl.gz,
    data/edgelab/model_evaluations/<date>.jsonl (for idempotency)
  - WRITES ONLY data/edgelab/model_evaluations/<date>.jsonl (append-only,
    idempotent, via lib.edgelab.storage.append_records) and
    data/edgelab/research_runs/<date>.jsonl (a run-metadata record,
    same convention scripts/edgelab/build_recommendations.py already
    uses)
  - never touches data/edgelab/recommendations/, data/edgelab/bets/,
    or any bankroll file; never calls scripts/risk_gate.py or
    scripts/write_pending_bets.py

A failure anywhere in this script (a bad game, a lineup-fetch network
error, a missing schedule) must never raise past main() uncaught --
this is a best-effort, non-blocking collector that must never be able
to fail a workflow run other workflows depend on. See module docstring
of lib/edgelab/prospective_snapshot.py for the full safety contract.

Usage:
    python3 scripts/edgelab/run_prospective_snapshots.py
    python3 scripts/edgelab/run_prospective_snapshots.py --date 2026-08-13 --dry-run
"""
import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import ids, storage
from lib.edgelab.prospective_snapshot import CORE_CHECKPOINTS, run_prospective_snapshot_cycle
from scripts.build_market_ledger import compute_game_projection_context, evaluate_game
from scripts.fetch_lineups import fetch_lineup_for_game, load_batter_woba, load_team_woba


def _load_slate(path="data/slate.json"):
    import json
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def _live_status_by_team_pair(date):
    """
    Best-effort: returns {(awayAbbr, homeAbbr): detailedState} for
    today's MLB schedule, or {} on ANY failure (network error, malformed
    response) -- lib.edgelab.prospective_snapshot.classify_game_eligibility
    already treats a missing entry as "proceed on clock-time alone, flag
    ambiguous" rather than a hard exclusion, so a schedule-fetch failure
    degrades gracefully instead of blacking out the whole run.
    """
    try:
        from lib.edgelab import mlb_schedule
        schedule_json = mlb_schedule.fetch_schedule(date)
        if schedule_json is None:
            return {}
        parsed = mlb_schedule.parse_schedule_games(schedule_json)
        context, _warnings = mlb_schedule.build_schedule_game_context(parsed)
        return {pair: entry["status"] for pair, entry in context.items()}
    except Exception as exc:
        print(f"[run_prospective_snapshots] WARNING: live schedule status unavailable: {exc}", file=sys.stderr)
        return {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="Slate date YYYY-MM-DD (default: data/slate.json's own date)")
    parser.add_argument("--checkpoints", default=None, help="Comma-separated checkpoint targets (default: the 5 core checkpoints)")
    parser.add_argument("--dry-run", action="store_true", help="Compute and print what would be written, without writing anything")
    args = parser.parse_args()

    slate = _load_slate()
    if not slate or not slate.get("games"):
        print("[run_prospective_snapshots] no data/slate.json games available -- nothing to do.")
        return 0

    date = args.date or slate.get("date")
    if not date:
        print("[run_prospective_snapshots] could not determine slate date -- nothing to do.", file=sys.stderr)
        return 1

    target_checkpoints = tuple(args.checkpoints.split(",")) if args.checkpoints else CORE_CHECKPOINTS
    now = ids.utc_now_iso()

    games = slate["games"]
    existing_evaluations = list(storage.read_records(storage.partition_path("model_evaluations", date)))
    observations = list(storage.read_records(storage.partition_path("observations", date, compressed=True)))
    batter_woba_map = load_batter_woba()
    team_woba_map = load_team_woba()
    live_status = _live_status_by_team_pair(date)

    new_records, run_log = run_prospective_snapshot_cycle(
        date, games, existing_evaluations, observations,
        now=now, target_checkpoints=target_checkpoints, live_status_by_team_pair=live_status,
        evaluate_game_fn=evaluate_game, compute_projection_context_fn=compute_game_projection_context,
        lineup_fetch_fn=fetch_lineup_for_game, batter_woba_map=batter_woba_map, team_woba_map=team_woba_map,
    )

    evaluated = [r for r in run_log if r["action"] == "EVALUATED"]
    skipped = [r for r in run_log if r["action"] == "SKIPPED"]
    skip_reason_counts = {}
    for entry in skipped:
        skip_reason_counts[entry["reason"]] = skip_reason_counts.get(entry["reason"], 0) + 1
    checkpoint_counts = {}
    for entry in evaluated:
        checkpoint_counts[entry["checkpoint"]] = checkpoint_counts.get(entry["checkpoint"], 0) + 1
    print(
        f"[run_prospective_snapshots] date={date} now={now} games={len(games)} "
        f"evaluated={len(evaluated)} skipped={len(skipped)} newRecords={len(new_records)}"
    )
    for entry in run_log:
        print(f"  {entry['gameId']}: {entry['action']} checkpoint={entry['checkpoint']} reason={entry['reason']} warnings={entry['warnings']}")

    if args.dry_run:
        print("[run_prospective_snapshots] --dry-run: not writing anything.")
        return 0

    written, skipped_dup = 0, 0
    if new_records:
        written, skipped_dup = storage.append_records(
            storage.partition_path("model_evaluations", date), new_records, "modelEvaluationId",
        )

    run_record = {
        "schemaVersion": "1",
        "runId": ids.new_run_id("PROSPECTIVE_SNAPSHOT", github_run_id=os.environ.get("GITHUB_RUN_ID")),
        "runType": "PROSPECTIVE_SNAPSHOT",
        "startedAt": now,
        "completedAt": ids.utc_now_iso(),
        "status": "success",
        "sourceWorkflow": os.environ.get("GITHUB_WORKFLOW"),
        "githubRunId": os.environ.get("GITHUB_RUN_ID"),
        "inputFiles": ["data/slate.json", storage.partition_path("observations", date, compressed=True)],
        "outputFiles": [storage.partition_path("model_evaluations", date)],
        "counts": {
            "gamesConsidered": len(games),
            "gamesEvaluated": len(evaluated),
            "gamesSkipped": len(skipped),
            "gamesSkippedByReason": skip_reason_counts,
            "gamesEvaluatedByCheckpoint": checkpoint_counts,
            "modelEvaluationsWritten": written,
            "modelEvaluationsSkippedDuplicate": skipped_dup,
        },
        "errors": [],
        "warnings": [w for entry in run_log for w in entry["warnings"]],
        "createdAt": now,
        "provenance": {
            "sourceSystem": "edgelab_prospective_snapshot",
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
