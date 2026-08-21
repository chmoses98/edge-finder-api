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


# ModelEvaluation Prospective Coverage Reliability mission: data/slate.json
# is a SINGLE, non-date-partitioned file, refreshed only by .github/workflows/
# fetch-slate.yml -- which has NO cron schedule of its own (workflow_dispatch/
# push-to-.fetch-trigger only). Root cause of a real, confirmed 2026-08-11
# through 2026-08-15 gap in EVALUATED ModelEvaluation records: fetch-slate.yml
# was not triggered for 6 days (last refresh 2026-08-10, next 2026-08-16),
# so every 15-minute model-snapshot-scheduler.yml cycle during that window
# kept reading the SAME stale (real, not fabricated) 2026-08-10 slate,
# correctly classified its games as already STARTED via
# lib.edgelab.prospective_snapshot.classify_game_eligibility, and correctly
# reported "no_op" -- indistinguishable from the ordinary, harmless
# steady-state "nothing due yet this cycle" outcome that legitimately fires
# dozens of times a day. A GENUINE multi-day operational outage was
# therefore invisible in this script's own output for 6 straight days.
#
# Two-tier staleness measurement, deliberately never a same-day string-
# equality check on the bare "date" field (US-Eastern-dated per
# fetch-slate.yml's own TZ='America/New_York' convention, while `now` is
# UTC -- a naive equality check would false-positive across every UTC/ET
# day-boundary crossing):
#   1. PREFERRED: "executionSlipGeneratedAt", a real UTC ISO timestamp
#      fetch-slate.yml stamps at the moment of generation -- gives the
#      slate's EXACT age with no anchor ambiguity, so a tight threshold
#      comfortably above the normal ~19-29h day-to-day fetch-slate.yml
#      cadence observed in this repository's own commit history (see
#      PR body / mission audit) safely catches staleness on the very
#      first missed refresh cycle without false-positiving on ordinary
#      day-to-day timing drift.
#   2. FALLBACK (that field absent/unparseable): the bare "date" field is
#      only calendar-day-granular and anchored at UTC midnight, which
#      alone can look up to ~24h "older" than the same real gap the
#      precise-timestamp path would report -- so this path instead counts
#      whole UTC calendar days between slate_date and now's date, and
#      only flags staleness at 2+ full calendar days back, which safely
#      exceeds any single-missed-day gap regardless of time-of-day.
STALE_SLATE_THRESHOLD_HOURS = 36
STALE_SLATE_FALLBACK_THRESHOLD_DAYS = 2


def slate_staleness_reason(slate, now_iso):
    """
    Pure. Returns None if `slate` looks fresh enough to safely drive a
    prospective-evaluation cycle, or an explicit human-readable reason
    string if it does not. Deliberately does NOT compare filesystem mtime
    (a fresh CI checkout stamps every file's mtime as "now" regardless of
    its real content age -- the exact bug already found and fixed for
    scripts/prune_kalshi_snapshots.py) -- only the slate's own embedded
    timestamp/date fields, genuinely written once per real fetch-slate.yml
    run and therefore a trustworthy content-age signal.
    """
    if not slate:
        return "data/slate.json is missing or empty"
    try:
        now_dt = datetime.fromisoformat(str(now_iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)

    generated_at = slate.get("executionSlipGeneratedAt")
    if generated_at:
        try:
            generated_dt = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
            if generated_dt.tzinfo is None:
                generated_dt = generated_dt.replace(tzinfo=timezone.utc)
            age_hours = (now_dt - generated_dt).total_seconds() / 3600.0
            if age_hours >= STALE_SLATE_THRESHOLD_HOURS:
                return (
                    f"data/slate.json executionSlipGeneratedAt={generated_at!r} is {age_hours:.1f}h older than "
                    f"now={now_iso!r} (>= {STALE_SLATE_THRESHOLD_HOURS}h threshold) -- fetch-slate.yml likely "
                    f"did not run recently"
                )
            return None
        except ValueError:
            pass  # fall through to the coarser date-field check below

    slate_date = slate.get("date")
    if not slate_date:
        return "data/slate.json has no 'date' or 'executionSlipGeneratedAt' field"
    try:
        slate_dt = datetime.strptime(slate_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return f"data/slate.json has an unparseable 'date' field: {slate_date!r}"
    days_diff = (now_dt.date() - slate_dt.date()).days
    if days_diff >= STALE_SLATE_FALLBACK_THRESHOLD_DAYS:
        return (
            f"data/slate.json date={slate_date!r} is {days_diff} calendar day(s) behind now={now_iso!r} "
            f"(>= {STALE_SLATE_FALLBACK_THRESHOLD_DAYS}-day fallback threshold) -- fetch-slate.yml likely "
            f"did not run recently"
        )
    return None


def compute_run_status(evaluated_count, genuine_failure_count):
    """
    Pure. Honest run-level status (reliability pass, spec section 9) --
    never "success" merely because the process reached the end:
      no_op:   zero genuine failures AND zero games actually evaluated
               (the common steady-state -- most 15-minute cycles have
               nothing due yet)
      success: zero genuine failures AND at least one game evaluated
      partial: at least one game genuinely failed (evaluate_game raised)
               AND at least one OTHER game still succeeded
      failed:  at least one game was attempted and genuinely failed, and
               NONE succeeded
    A per-game SKIP for an ineligible/not-yet-due reason (STARTED,
    POSTPONED, NO_CHECKPOINT_DUE, a failed lineup poll that correctly
    left the game unconfirmed, etc.) is never counted as a "failure"
    here -- those are expected, honest outcomes, not errors. Describes
    THIS SCRIPT's own execution outcome only; a separate later
    git-persistence failure (the calling workflow's own exit code, see
    .github/workflows/model-snapshot-scheduler.yml) is not folded in.
    """
    if genuine_failure_count and not evaluated_count:
        return "failed"
    if genuine_failure_count:
        return "partial"
    if evaluated_count:
        return "success"
    return "no_op"


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

    # An explicit --date is a deliberate operator override (backfill/dry-run/
    # test against a specific date) -- the staleness check only guards the
    # auto-resolved production path, where `date` came FROM the slate itself
    # and so could never be compared against itself meaningfully.
    if args.date is None:
        staleness_reason = slate_staleness_reason(slate, now)
        if staleness_reason:
            print(f"[run_prospective_snapshots] STALE SLATE: {staleness_reason}", file=sys.stderr)
            if not args.dry_run:
                # Filed under TODAY's UTC calendar date, deliberately NOT
                # under the stale slate's own `date` -- the whole point of
                # this record is to be found by an operator investigating
                # "why does today look empty", and a record filed under a
                # 5-day-old date could sit undiscovered exactly as long as
                # the original silent gap did. Precision to the exact ET
                # slate day doesn't matter for a record whose only job is
                # to be visible, never consumed as real game data.
                today_utc = now[:10]
                stale_run_record = {
                    "schemaVersion": "1",
                    "runId": ids.new_run_id("PROSPECTIVE_SNAPSHOT", github_run_id=os.environ.get("GITHUB_RUN_ID")),
                    "runType": "PROSPECTIVE_SNAPSHOT",
                    "startedAt": now,
                    "completedAt": ids.utc_now_iso(),
                    "status": "stale_slate",
                    "sourceWorkflow": os.environ.get("GITHUB_WORKFLOW"),
                    "githubRunId": os.environ.get("GITHUB_RUN_ID"),
                    "inputFiles": ["data/slate.json"],
                    "outputFiles": [],
                    "counts": {
                        "gamesConsidered": 0, "gamesEvaluated": 0, "gamesSkipped": 0,
                        "gamesSkippedByReason": {}, "gamesEvaluatedByCheckpoint": {},
                        "modelEvaluationsWritten": 0, "modelEvaluationsSkippedDuplicate": 0,
                        "lineupPollAttempts": 0, "lineupPollSuccesses": 0, "lineupPollFailures": 0,
                    },
                    "errors": [staleness_reason],
                    "warnings": [],
                    "createdAt": now,
                    "provenance": {
                        "sourceSystem": "edgelab_prospective_snapshot",
                        "sourceFile": __file__,
                        "sourceKey": today_utc,
                        "capturedAt": now,
                        "ingestedAt": ids.utc_now_iso(),
                    },
                }
                storage.append_records(storage.partition_path("research_runs", today_utc), [stale_run_record], "runId")
            return 1

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

    genuine_failures = [
        r for r in skipped if isinstance(r.get("reason"), str) and r["reason"].startswith("evaluate_game raised")
    ]
    lineup_poll_attempts = sum(1 for r in run_log if r.get("lineupPollAttempted"))
    lineup_poll_successes = sum(1 for r in run_log if r.get("lineupNewlyConfirmed"))
    lineup_poll_failures = sum(1 for r in run_log if r.get("lineupPollFailed"))

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

    run_status = compute_run_status(len(evaluated), len(genuine_failures))

    run_record = {
        "schemaVersion": "1",
        "runId": ids.new_run_id("PROSPECTIVE_SNAPSHOT", github_run_id=os.environ.get("GITHUB_RUN_ID")),
        "runType": "PROSPECTIVE_SNAPSHOT",
        "startedAt": now,
        "completedAt": ids.utc_now_iso(),
        "status": run_status,
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
            "lineupPollAttempts": lineup_poll_attempts,
            "lineupPollSuccesses": lineup_poll_successes,
            "lineupPollFailures": lineup_poll_failures,
        },
        "errors": [r["reason"] for r in genuine_failures],
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
