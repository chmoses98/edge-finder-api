#!/usr/bin/env python3
"""
scripts/score_replay.py
==========================
Scored Postgame Replay milestone: research CLI. Reads an already-
completed ReplayRun (data/edgelab/replay_runs/<replayRunId>/, written by
scripts/run_replay.py) and scores it against canonical postgame
settlement/CLV/bet evidence, writing the result under
data/edgelab/scored_replay_runs/<scoredReplayRunId>/. Never touches the
source ReplayRun's own files, and never touches production files
(data/slate.json, bets.json, config/rules.json).

Example
-------
    python3 scripts/score_replay.py --replay-run-id <sha1 hex>
"""
import argparse
import json
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from lib.edgelab import replay  # noqa: E402
from lib.edgelab import schema as edgelab_schema  # noqa: E402
from lib.edgelab import scored_replay  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Scored Postgame Replay CLI (research-only).")
    parser.add_argument("--replay-run-id", required=True, help="The replayRunId to score (must already exist under data/edgelab/replay_runs/).")
    args = parser.parse_args()

    run = replay.load_replay_run(args.replay_run_id)
    if run is None:
        print(f"ERROR: no ReplayRun found with replayRunId={args.replay_run_id!r}", file=sys.stderr)
        sys.exit(1)

    scored_run, scored_results = scored_replay.score_replay_run(args.replay_run_id)
    if scored_run is None:
        print(f"ERROR: ReplayRun {args.replay_run_id!r} has runStatus={run.get('runStatus')!r} -- nothing to score", file=sys.stderr)
        sys.exit(1)

    run_errors = edgelab_schema.validate_record("scored_replay_run", scored_run)
    if run_errors:
        print(f"WARNING: ScoredReplayRun failed schema validation: {run_errors}", file=sys.stderr)
    for r in scored_results:
        errs = edgelab_schema.validate_record("scored_replay_result", r)
        if errs:
            print(f"WARNING: ScoredReplayResult failed schema validation: {errs}", file=sys.stderr)

    write_result = scored_replay.write_scored_replay_outputs(scored_run, scored_results)

    print(json.dumps({
        "scoredReplayRunId": scored_run["scoredReplayRunId"],
        "replayRunId": scored_run["replayRunId"],
        "snapshotId": scored_run["snapshotId"],
        "summary": scored_run["summary"],
        "limitationReasons": scored_run["limitationReasons"],
        "writeOutcome": write_result["outcome"],
        "outputPath": write_result["path"],
    }, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
