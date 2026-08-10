#!/usr/bin/env python3
"""
scripts/score_replay.py
==========================
Scored Postgame Replay milestone: research CLI. Reads an already-
completed ReplayRun (data/edgelab/replay_runs/<replayRunId>/, written by
scripts/run_replay.py or scripts/run_forward_replay.py) and scores it
against canonical postgame settlement/CLV/bet evidence, writing the
result under data/edgelab/scored_replay_runs/<scoredReplayRunId>/.
Never touches the source ReplayRun's own files, and never touches
production files (data/slate.json, bets.json, config/rules.json).

Two resolution modes:

  --replay-run-id <id>   Score one explicit run directly. Manual/direct
                          use -- exits 1 on any failure (run not found,
                          not completed, schema-invalid), matching this
                          repo's normal CLI convention.

  --date <YYYY-MM-DD>    Automated postgame-workflow use (deliberately
                          the step .github/workflows/edgelab-postgame.yml
                          calls). Resolves the day's replayRunId from
                          data/edgelab/forward_replay_status.json (the
                          same status file scripts/run_forward_replay.py
                          already writes each pregame day) and, on
                          success, also writes the date-level coverage
                          report (data/edgelab/reports/scored_replay/
                          <date>.json). Never blocks the workflow --
                          same continue-on-error philosophy as
                          run_forward_replay.py/create_snapshot.py: any
                          gap (no forward replay recorded, run not yet
                          COMPLETED) is recorded honestly to
                          data/edgelab/scored_replay_status.json and the
                          script still exits 0.

Examples
--------
    python3 scripts/score_replay.py --replay-run-id <sha1 hex>
    python3 scripts/score_replay.py --date 2026-08-01
"""
import argparse
import json
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from lib.edgelab import ids  # noqa: E402
from lib.edgelab import replay  # noqa: E402
from lib.edgelab import schema as edgelab_schema  # noqa: E402
from lib.edgelab import scored_replay  # noqa: E402

FORWARD_REPLAY_STATUS_PATH = os.path.join("data", "edgelab", "forward_replay_status.json")
SCORED_REPLAY_STATUS_PATH = os.path.join("data", "edgelab", "scored_replay_status.json")


def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _record_status(date, outcome, extra=None):
    """Same convention as scripts/run_forward_replay.py's own
    _record_status: overwritten-in-place, machine-readable, keyed by
    date -- operational telemetry about the SCORING ATTEMPT, distinct
    from forward_replay_status.json (which is about the replay attempt)
    and from the ScoredReplayRun record itself (which is about scoring
    CONTENT, not this workflow step's own outcome)."""
    status = _load_json(SCORED_REPLAY_STATUS_PATH)
    status[date] = {
        "date": date, "outcome": outcome, "recordedAt": ids.utc_now_iso(),
        "workflowRunId": os.environ.get("GITHUB_RUN_ID"),
        **(extra or {}),
    }
    os.makedirs(os.path.dirname(SCORED_REPLAY_STATUS_PATH), exist_ok=True)
    with open(SCORED_REPLAY_STATUS_PATH, "w") as f:
        json.dump(status, f, indent=2, sort_keys=True)


def _write_summary(lines):
    github_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if github_summary:
        with open(github_summary, "a") as f:
            f.write("\n".join(lines) + "\n")


def _validate_and_warn(scored_run, scored_results):
    run_errors = edgelab_schema.validate_record("scored_replay_run", scored_run)
    if run_errors:
        print(f"WARNING: ScoredReplayRun failed schema validation: {run_errors}", file=sys.stderr)
    result_errors = [e for r in scored_results for e in edgelab_schema.validate_record("scored_replay_result", r)]
    if result_errors:
        print(f"WARNING: {len(result_errors)} ScoredReplayResult schema validation errors", file=sys.stderr)


def _score_and_write(replay_run_id):
    """Shared by both modes: score, validate, write outputs + date
    report. Returns (scored_run, scored_results, write_result,
    report_path)."""
    scored_run, scored_results = scored_replay.score_replay_run(replay_run_id)
    if scored_run is None:
        return None, None, None, None
    _validate_and_warn(scored_run, scored_results)
    write_result = scored_replay.write_scored_replay_outputs(scored_run, scored_results)
    report = scored_replay.build_date_report(scored_run, scored_results)
    report_path = scored_replay.write_scored_replay_date_report(report)
    return scored_run, scored_results, write_result, report_path


def _run_explicit(replay_run_id):
    run = replay.load_replay_run(replay_run_id)
    if run is None:
        print(f"ERROR: no ReplayRun found with replayRunId={replay_run_id!r}", file=sys.stderr)
        sys.exit(1)

    scored_run, scored_results, write_result, report_path = _score_and_write(replay_run_id)
    if scored_run is None:
        print(f"ERROR: ReplayRun {replay_run_id!r} has runStatus={run.get('runStatus')!r} -- nothing to score", file=sys.stderr)
        sys.exit(1)

    print(json.dumps({
        "scoredReplayRunId": scored_run["scoredReplayRunId"],
        "replayRunId": scored_run["replayRunId"],
        "snapshotId": scored_run["snapshotId"],
        "ingestionReadiness": scored_run["ingestionReadiness"],
        "wagerLinkageAvailable": scored_run["wagerLinkageAvailable"],
        "summary": scored_run["summary"],
        "limitationReasons": scored_run["limitationReasons"],
        "writeOutcome": write_result["outcome"],
        "outputPath": write_result["path"],
        "dateReportPath": report_path,
    }, indent=2))
    sys.exit(0)


def _run_for_date(date):
    """
    Automated postgame-workflow entrypoint (requirement 1). Deliberately
    NEVER fails the workflow -- see module docstring's --date section
    and scripts/run_forward_replay.py's identical policy for the exact
    same rationale (research-only automation must never block production
    postgame settlement).
    """
    forward_status = _load_json(FORWARD_REPLAY_STATUS_PATH).get(date)
    if not forward_status or forward_status.get("outcome") != "completed" or not forward_status.get("replayRunId"):
        reason = "no_forward_replay_recorded" if not forward_status else f"forward_replay_outcome_{forward_status.get('outcome')}"
        print(f"No COMPLETED forward replay recorded for {date} ({reason}) -- nothing to score yet.", file=sys.stderr)
        _record_status(date, "skipped_no_replay_run", {"reason": reason})
        _write_summary([
            f"### :warning: Scored replay SKIPPED: {date}",
            f"No COMPLETED forward CANDIDATE_MODEL replay is recorded for this date yet ({reason}). "
            "This is expected if scripts/run_forward_replay.py hasn't run for this date, or its own run "
            "didn't complete -- a later rerun of this step will pick it up once it does.",
        ])
        sys.exit(0)

    replay_run_id = forward_status["replayRunId"]
    scored_run, scored_results, write_result, report_path = _score_and_write(replay_run_id)
    if scored_run is None:
        print(f"ReplayRun {replay_run_id!r} recorded for {date} is no longer COMPLETED -- nothing to score.", file=sys.stderr)
        _record_status(date, "skipped_replay_run_not_completed", {"replayRunId": replay_run_id})
        _write_summary([f"### :warning: Scored replay SKIPPED: {date}", f"ReplayRun `{replay_run_id}` is not COMPLETED."])
        sys.exit(0)

    _record_status(date, "completed", {
        "replayRunId": replay_run_id,
        "scoredReplayRunId": scored_run["scoredReplayRunId"],
        "writeOutcome": write_result["outcome"],
        "wagerLinkageAvailable": scored_run["wagerLinkageAvailable"],
        "ingestionReadiness": scored_run["ingestionReadiness"],
    })

    summary = scored_run["summary"] or {}
    _write_summary([
        f"### Scored replay: {date}",
        f"- scoredReplayRunId: `{scored_run['scoredReplayRunId']}`",
        f"- writeOutcome: `{write_result['outcome']}`",
        f"- wagerLinkageAvailable: `{scored_run['wagerLinkageAvailable']}`",
        f"- ingestionReadiness: `{scored_run['ingestionReadiness']}`",
        f"- predictions scoreable/total: {summary.get('predictionAvailableCount')}/{summary.get('n')}",
        f"- settlement settled/unresolved: {summary.get('settlement', {}).get('settledCount')}/{summary.get('settlement', {}).get('unresolvedCount')}",
        f"- limitationReasons: {', '.join(scored_run['limitationReasons']) or '(none)'}",
        f"- dateReportPath: `{report_path}`",
    ])

    print(json.dumps({
        "date": date,
        "scoredReplayRunId": scored_run["scoredReplayRunId"],
        "replayRunId": replay_run_id,
        "ingestionReadiness": scored_run["ingestionReadiness"],
        "wagerLinkageAvailable": scored_run["wagerLinkageAvailable"],
        "summary": scored_run["summary"],
        "limitationReasons": scored_run["limitationReasons"],
        "writeOutcome": write_result["outcome"],
        "outputPath": write_result["path"],
        "dateReportPath": report_path,
    }, indent=2))
    sys.exit(0)


def main():
    parser = argparse.ArgumentParser(description="Scored Postgame Replay CLI (research-only).")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--replay-run-id", default=None, help="Score one explicit replayRunId directly (manual use; exits 1 on failure).")
    group.add_argument("--date", default=None, help="Resolve and score the day's forward replay run (automated postgame-workflow use; never fails the workflow).")
    args = parser.parse_args()

    if args.replay_run_id:
        _run_explicit(args.replay_run_id)
    else:
        _run_for_date(args.date)


if __name__ == "__main__":
    main()
