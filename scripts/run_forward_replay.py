#!/usr/bin/env python3
"""
scripts/run_forward_replay.py
=================================
Forward Replay Corpus and Production Provenance milestone (item 6):
automatically runs a research-only CANDIDATE_MODEL replay against the
PRE_GAME_DECISION snapshot fetch-slate.yml just created, so the
trustworthy replay corpus grows every production day without manual
intervention.

Deliberately the step immediately after "Create immutable PRE_GAME_DECISION
snapshot" in fetch-slate.yml, and deliberately continue-on-error at the
workflow level for the exact same reason snapshot capture itself is
(see scripts/create_snapshot.py's WORKFLOW FAILURE POLICY docstring):
this is new, additive, research-only infrastructure that must never be
able to block production slate publication or manual bet placement.

Never writes to data/slate.json, bets.json, config/rules.json, or any
production file -- lib.edgelab.replay already structurally guarantees
this (see docs/REPLAY_ENGINE.md). Output lives exclusively under
data/edgelab/replay_runs/.

Refuses to silently downgrade fidelity: if today's snapshot is only
ELIGIBLE_LEVEL_1_ONLY (or worse), this script records that honestly
(REJECTED_INELIGIBLE) rather than requesting --allow-approximate on a
caller's behalf -- exactly like scripts/replay_eligibility_report.py's
unattended batch mode already does for historical dates.

Usage:
  python3 scripts/run_forward_replay.py <DATE> [--workflow-run-id ID]
"""
import argparse
import json
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from lib.edgelab import replay  # noqa: E402
from lib.edgelab import schema as edgelab_schema  # noqa: E402
from lib.edgelab import snapshot as snap  # noqa: E402

STATUS_PATH = os.path.join("data", "edgelab", "forward_replay_status.json")


def _record_status(date, outcome, extra=None):
    """Same convention as scripts/create_snapshot.py's _record_capture_status:
    overwritten-in-place, machine-readable, keyed by date -- operational
    telemetry about the replay ATTEMPT, not a claim about replay content."""
    try:
        with open(STATUS_PATH) as f:
            status = json.load(f)
    except (OSError, json.JSONDecodeError):
        status = {}
    from lib.edgelab import ids
    status[date] = {
        "date": date, "outcome": outcome, "recordedAt": ids.utc_now_iso(),
        "workflowRunId": os.environ.get("GITHUB_RUN_ID"),
        **(extra or {}),
    }
    os.makedirs(os.path.dirname(STATUS_PATH), exist_ok=True)
    with open(STATUS_PATH, "w") as f:
        json.dump(status, f, indent=2, sort_keys=True)


def _write_summary(lines):
    github_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if github_summary:
        with open(github_summary, "a") as f:
            f.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Automatic forward CANDIDATE_MODEL replay (research-only).")
    parser.add_argument("date", help="YYYY-MM-DD")
    parser.add_argument("--workflow-run-id", default=os.environ.get("GITHUB_RUN_ID"))
    args = parser.parse_args()

    manifest = snap.load_latest_pregame_manifest(args.date)
    if manifest is None:
        print(f"No PRE_GAME_DECISION snapshot exists yet for {args.date} -- nothing to replay.", file=sys.stderr)
        _record_status(args.date, "no_snapshot")
        _write_summary([
            f"### :warning: Forward replay SKIPPED: {args.date}",
            "No PRE_GAME_DECISION snapshot exists for this date -- snapshot capture itself must have failed or not yet run.",
        ])
        sys.exit(0)  # never fail the workflow -- surfaced via status file + job summary only

    try:
        run, results = replay.execute_replay(
            manifest, replay_mode=replay.MODE_CANDIDATE, allow_level_1=False,
            workflow_run_id=args.workflow_run_id,
        )
    except Exception as e:  # noqa: BLE001 -- research-only automation must never crash the production workflow
        print(f"UNEXPECTED FORWARD REPLAY ERROR: {e!r}", file=sys.stderr)
        _record_status(args.date, "unexpected_error", {"error": repr(e)})
        _write_summary([
            f"### :rotating_light: Forward replay FAILED (unexpected error): {args.date}",
            f"```\n{e!r}\n```",
            "Snapshot capture and production slate publication are unaffected -- this is research-only automation.",
        ])
        sys.exit(0)

    run_errors = edgelab_schema.validate_record("replay_run", run)
    if run_errors:
        print(f"WARNING: ReplayRun failed schema validation: {run_errors}", file=sys.stderr)
    result_errors = [e for r in results for e in edgelab_schema.validate_record("replay_result", r)]
    if result_errors:
        print(f"WARNING: {len(result_errors)} ReplayResult schema validation errors", file=sys.stderr)

    write_result = replay.write_replay_outputs(run, results)

    summary = {
        "date": args.date,
        "snapshotId": manifest.get("snapshotId"),
        "replayRunId": run["replayRunId"],
        "runStatus": run["runStatus"],
        "eligibilityStatus": run["eligibilityStatus"],
        "replayFidelity": run["replayFidelity"],
        "summary": run["summary"],
        "limitationReasons": run["limitationReasons"],
        "writeOutcome": write_result["outcome"],
        "outputPath": write_result["path"],
    }
    print(json.dumps(summary, indent=2))

    outcome = "completed" if run["runStatus"] == replay.RUN_STATUS_COMPLETED else run["runStatus"].lower()
    _record_status(args.date, outcome, {
        "replayRunId": run["replayRunId"], "eligibilityStatus": run["eligibilityStatus"],
        "runStatus": run["runStatus"], "writeOutcome": write_result["outcome"],
    })

    if run["runStatus"] == replay.RUN_STATUS_COMPLETED:
        _write_summary([
            f"### Forward replay: {args.date}",
            f"- replayRunId: `{run['replayRunId']}`",
            f"- eligibilityStatus: `{run['eligibilityStatus']}`",
            f"- replayFidelity: `{run['replayFidelity']}`",
            f"- writeOutcome: `{write_result['outcome']}`",
            f"- marketsEvaluated: {run['summary']['marketsEvaluated']}, "
            f"decisionsChanged: {run['summary']['decisionsChanged']}",
        ])
    else:
        _write_summary([
            f"### :warning: Forward replay NOT COMPLETED: {args.date}",
            f"- runStatus: `{run['runStatus']}`",
            f"- eligibilityStatus: `{run['eligibilityStatus']}`",
            f"- limitationReasons: {', '.join(run['limitationReasons']) or '(none)'}",
            "This is an honest, non-fatal outcome (e.g. today's snapshot is only Level-1-eligible) -- "
            "never a silent fidelity downgrade. See docs/PRODUCTION_PROVENANCE.md.",
        ])

    sys.exit(0)  # never fail the workflow -- research-only, must never block production


if __name__ == "__main__":
    main()
