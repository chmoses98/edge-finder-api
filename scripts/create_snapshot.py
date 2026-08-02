#!/usr/bin/env python3
"""
scripts/create_snapshot.py
=============================
Historical Capture Completeness and Immutable Snapshot Foundation
milestone: CLI entrypoint workflows call to create (or verify-as-no-op)
one immutable Snapshot manifest for one (stage, date).

Never touches model probabilities, recommendation logic, thresholds,
staking, or settlement outcomes -- see lib/edgelab/snapshot.py's module
docstring. Designed to be safe to call with `continue-on-error: true` /
non-fatal wrapping in a workflow: exits 0 on created/noop_verified,
exits 1 (loudly, with a clear message) on conflict/existing_manifest_corrupted
so a workflow step can choose how to react (see
.github/workflows/fetch-slate.yml and edgelab-postgame.yml for the
"safest behavior" decision this milestone documents in
docs/SNAPSHOT_ARCHITECTURE.md: snapshot failures are reported as a
visible warning in the job summary but never fail the overall workflow,
since this is new, additive capture infrastructure that must never be
able to block production betting/handicapping).

Usage:
  python3 scripts/create_snapshot.py <STAGE> <DATE> [--workflow-run-id ID] [--production-run-id ID]

  STAGE: PRE_GAME_DECISION | POST_GAME_SETTLEMENT | CLOSING_LINE
  DATE:  YYYY-MM-DD
"""
import argparse
import json
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from lib.edgelab import snapshot as snap  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Create an immutable EdgeLab Snapshot manifest.")
    parser.add_argument("stage", choices=sorted(snap.VALID_STAGES))
    parser.add_argument("date", help="YYYY-MM-DD")
    parser.add_argument("--workflow-run-id", default=os.environ.get("GITHUB_RUN_ID"))
    parser.add_argument("--production-run-id", default=None)
    args = parser.parse_args()

    try:
        result = snap.build_snapshot(
            args.stage, args.date,
            workflow_run_id=args.workflow_run_id,
            production_run_id=args.production_run_id,
        )
    except snap.SnapshotIntegrityError as e:
        print(f"SNAPSHOT INTEGRITY ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    manifest = result["manifest"]
    summary = {
        "outcome": result["outcome"],
        "snapshotId": manifest["snapshotId"],
        "snapshotStage": manifest["snapshotStage"],
        "snapshotDate": manifest["snapshotDate"],
        "completenessStatus": manifest["completenessStatus"],
        "replayFidelityPotential": manifest["replayFidelityPotential"],
        "missingComponents": manifest["missingComponents"],
        "limitationReasons": manifest["limitationReasons"],
    }
    print(json.dumps(summary, indent=2))

    if result["outcome"] in ("created", "noop_verified"):
        github_summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if github_summary:
            with open(github_summary, "a") as f:
                f.write(f"\n### Snapshot: {args.stage} {args.date}\n")
                f.write(f"- outcome: `{result['outcome']}`\n")
                f.write(f"- completenessStatus: `{manifest['completenessStatus']}`\n")
                f.write(f"- replayFidelityPotential: `{manifest['replayFidelityPotential']}`\n")
                if manifest["missingComponents"]:
                    f.write(f"- missingComponents: {', '.join(manifest['missingComponents'])}\n")
        sys.exit(0)

    print(f"SNAPSHOT {result['outcome'].upper()} for {args.stage} {args.date}", file=sys.stderr)
    if "conflictEvidencePath" in result:
        print(f"Diagnostic evidence preserved at: {result['conflictEvidencePath']}", file=sys.stderr)
    if "diagnostics" in result:
        print(f"Diagnostics: {result['diagnostics']}", file=sys.stderr)
    github_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if github_summary:
        with open(github_summary, "a") as f:
            f.write(f"\n### :warning: Snapshot {result['outcome']}: {args.stage} {args.date}\n")
            f.write("Existing snapshot preserved untouched; see diagnostics in job logs.\n")
    sys.exit(1)


if __name__ == "__main__":
    main()
