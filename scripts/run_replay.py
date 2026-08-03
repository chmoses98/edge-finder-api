#!/usr/bin/env python3
"""
scripts/run_replay.py
========================
Level 2 Historical Replay Engine milestone: research CLI. Loads an
immutable Snapshot, assesses replay eligibility, and (unless
--eligibility-only) executes CANDIDATE_MODEL replay and writes results
under data/edgelab/replay_runs/<replayRunId>/. Never touches
data/slate.json, bets.json, config/rules.json, or any other production
file.

Examples
--------
Check eligibility only, no execution:
    python3 scripts/run_replay.py --date 2026-08-01 --eligibility-only

Replay a specific PRE_GAME_DECISION run (Level 2, refuses if ineligible):
    python3 scripts/run_replay.py --date 2026-08-01 --production-run-key 2026-08-01T22:10:49Z

Replay the latest PRE_GAME_DECISION run for a date:
    python3 scripts/run_replay.py --date 2026-08-01

Replay by exact snapshotId:
    python3 scripts/run_replay.py --snapshot-id <sha1 hex>

Force an approximate Level 1 replay even though the snapshot is only
Level-1-eligible (never silent -- must be requested explicitly):
    python3 scripts/run_replay.py --date 2026-07-30 --allow-approximate

Attempt HISTORICAL_PRODUCTION mode (always rejected this milestone,
reported honestly rather than silently downgraded):
    python3 scripts/run_replay.py --date 2026-08-01 --mode HISTORICAL_PRODUCTION
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


def _resolve_manifest(args):
    if args.snapshot_id:
        manifest = snap.find_manifest_by_id(args.snapshot_id)
        if manifest is None:
            print(f"ERROR: no snapshot found with snapshotId={args.snapshot_id!r}", file=sys.stderr)
            sys.exit(1)
        return manifest
    if not args.date:
        print("ERROR: must supply --snapshot-id or --date", file=sys.stderr)
        sys.exit(1)
    if args.production_run_key:
        manifest = snap.load_manifest(snap.STAGE_PRE_GAME_DECISION, args.date, run_key=args.production_run_key)
        if manifest is None:
            print(f"ERROR: no PRE_GAME_DECISION snapshot for date={args.date!r} runKey={args.production_run_key!r}", file=sys.stderr)
            sys.exit(1)
        return manifest
    manifest = snap.load_latest_pregame_manifest(args.date)
    if manifest is None:
        print(f"ERROR: no PRE_GAME_DECISION snapshot exists for date={args.date!r}", file=sys.stderr)
        sys.exit(1)
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Level 2 Historical Replay Engine CLI (research-only).")
    parser.add_argument("--snapshot-id", default=None, help="Exact PRE_GAME_DECISION snapshotId to replay.")
    parser.add_argument("--date", default=None, help="Slate date (YYYY-MM-DD) to replay.")
    parser.add_argument("--production-run-key", default=None, help="Specific productionRunKey for --date (defaults to the latest run for that date).")
    parser.add_argument("--candidate-model-version", default=None, help="Optional human label for this candidate run.")
    parser.add_argument("--mode", choices=sorted(replay.VALID_MODES), default=replay.MODE_CANDIDATE)
    parser.add_argument("--allow-approximate", action="store_true",
                         help="Explicitly permit an ELIGIBLE_LEVEL_1_ONLY snapshot to run at Level 1 fidelity. Without this flag, an ineligible-for-Level-2 snapshot is refused, never silently downgraded.")
    parser.add_argument("--eligibility-only", action="store_true", help="Dry run: assess and print eligibility, never execute or write output.")
    parser.add_argument("--output-path", default=None, help="Override output directory (default: data/edgelab/replay_runs/<replayRunId>/).")
    args = parser.parse_args()

    manifest = _resolve_manifest(args)

    if args.eligibility_only:
        eligibility = replay.assess_replay_eligibility(manifest)
        print(json.dumps({
            "snapshotId": manifest.get("snapshotId"),
            "snapshotDate": manifest.get("snapshotDate"),
            "productionRunId": manifest.get("productionRunId"),
            **eligibility,
        }, indent=2))
        sys.exit(0)

    run, results = replay.execute_replay(
        manifest, replay_mode=args.mode, allow_level_1=args.allow_approximate,
        candidate_model_version=args.candidate_model_version,
        workflow_run_id=os.environ.get("GITHUB_RUN_ID"),
    )

    run_errors = edgelab_schema.validate_record("replay_run", run)
    if run_errors:
        print(f"WARNING: ReplayRun failed schema validation: {run_errors}", file=sys.stderr)
    for r in results:
        errs = edgelab_schema.validate_record("replay_result", r)
        if errs:
            print(f"WARNING: ReplayResult failed schema validation: {errs}", file=sys.stderr)

    if args.output_path:
        # Explicit override -- still write-once (see lib.edgelab.replay.write_replay_outputs
        # for the default path); a caller-specified path is used verbatim as the run dir.
        os.makedirs(args.output_path, exist_ok=True)
        run_path = os.path.join(args.output_path, "replay_run.json")
        results_path = os.path.join(args.output_path, "replay_results.jsonl")
        with open(run_path, "w") as f:
            json.dump(run, f, indent=2, sort_keys=True)
        with open(results_path, "w") as f:
            for r in results:
                f.write(json.dumps(r, sort_keys=True) + "\n")
        write_result = {"outcome": "created", "path": args.output_path}
    else:
        write_result = replay.write_replay_outputs(run, results)

    print(json.dumps({
        "replayRunId": run["replayRunId"],
        "runStatus": run["runStatus"],
        "eligibilityStatus": run["eligibilityStatus"],
        "replayFidelity": run["replayFidelity"],
        "summary": run["summary"],
        "limitationReasons": run["limitationReasons"],
        "writeOutcome": write_result["outcome"],
        "outputPath": write_result["path"],
    }, indent=2))

    if run["runStatus"] in (replay.RUN_STATUS_REJECTED_INELIGIBLE, replay.RUN_STATUS_REJECTED_UNSUPPORTED_MODE, replay.RUN_STATUS_FAILED):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
