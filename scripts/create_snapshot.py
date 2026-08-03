#!/usr/bin/env python3
"""
scripts/create_snapshot.py
=============================
Historical Capture Completeness and Immutable Snapshot Foundation
milestone: CLI entrypoint workflows call to create (or verify-as-no-op)
one immutable Snapshot manifest for one (stage, date).

Never touches model probabilities, recommendation logic, thresholds,
staking, or settlement outcomes -- see lib/edgelab/snapshot.py's module
docstring.

WORKFLOW FAILURE POLICY (maintainer review item 1 -- documented tradeoff)
----------------------------------------------------------------------
This script's own exit code stays non-fatal by design when called from
fetch-slate.yml/edgelab-postgame.yml (continue-on-error: true) --
production slate publication and manual bet placement must remain
possible even if snapshot capture breaks. But "non-fatal to the
production workflow" must NOT mean "invisible" or "indistinguishable
from success":

  1. Every invocation (success or failure) writes a machine-readable
     status record to data/edgelab/snapshot_capture_status.json --
     keyed by (stage, date), containing outcome/completenessStatus/
     timestamp/workflowRunId. This is git-committed alongside the
     manifest, so "did capture succeed for date D" is answerable by
     reading one file, not by querying the GitHub Actions API or
     scrolling job logs.
  2. Every invocation writes to $GITHUB_STEP_SUMMARY with a `:warning:`
     banner on failure -- visible on the workflow run's summary page
     even though the step itself is marked continue-on-error.
  3. A SEPARATE, dedicated workflow (.github/workflows/snapshot-capture-check.yml,
     driven by scripts/check_snapshot_capture.py) is the thing that is
     actually ALLOWED to fail (no continue-on-error) when an expected
     snapshot is missing and cannot be safely recovered. This means the
     production workflow's own green checkmark never misrepresents
     "did every daily obligation succeed" -- a real capture gap shows
     up as a RED run of the dedicated check, not a silently-green
     production run.
  4. The dedicated check ALSO attempts safe recovery first: if a
     PRE_GAME_DECISION snapshot is missing for a date whose
     data/pipeline/<date>/recommendations.json (and everything else
     build_snapshot() needs) still exists on disk, it just calls
     build_snapshot() again -- capture is naturally idempotent and safe
     to retry. It never fabricates a snapshot once the underlying
     source data has been overwritten/pruned -- see
     scripts/check_snapshot_capture.py's own docstring.

Usage:
  python3 scripts/create_snapshot.py <STAGE> <DATE> [--workflow-run-id ID]

  STAGE: PRE_GAME_DECISION | POST_GAME_SETTLEMENT | CLOSING_LINE
  DATE:  YYYY-MM-DD
"""
import argparse
import json
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from lib.edgelab import ids  # noqa: E402
from lib.edgelab import snapshot as snap  # noqa: E402

# Deliberately CWD-relative (not ROOT_DIR-based) -- consistent with every
# other path in lib.edgelab.snapshot, and testable via monkeypatch.chdir()
# rather than only ever operating on the real repo checkout regardless of
# working directory.
STATUS_PATH = os.path.join("data", "edgelab", "snapshot_capture_status.json")


def _record_capture_status(stage, date, outcome, extra=None):
    """
    Overwritten-in-place, git-committed, machine-readable record of the
    LAST capture attempt for every (stage, date) key this script has ever
    been invoked with. This is operational telemetry about the CAPTURE
    PROCESS, not a claim about snapshot content -- unlike a manifest, it
    is legitimately fine for this to be revised in place (see
    lib/edgelab/snapshot.py's classification rule: this file is NOT part
    of any manifest and nothing in this milestone freezes or hashes it).
    """
    try:
        with open(STATUS_PATH) as f:
            status = json.load(f)
    except (OSError, json.JSONDecodeError):
        status = {}
    key = f"{date}|{stage}"
    status[key] = {
        "stage": stage, "date": date, "outcome": outcome,
        "recordedAt": ids.utc_now_iso(),
        "workflowRunId": os.environ.get("GITHUB_RUN_ID"),
        **(extra or {}),
    }
    os.makedirs(os.path.dirname(STATUS_PATH), exist_ok=True)
    with open(STATUS_PATH, "w") as f:
        json.dump(status, f, indent=2, sort_keys=True)


def main():
    parser = argparse.ArgumentParser(description="Create an immutable EdgeLab Snapshot manifest.")
    parser.add_argument("stage", choices=sorted(snap.VALID_STAGES))
    parser.add_argument("date", help="YYYY-MM-DD")
    parser.add_argument("--workflow-run-id", default=os.environ.get("GITHUB_RUN_ID"))
    args = parser.parse_args()

    try:
        result = snap.build_snapshot(args.stage, args.date, workflow_run_id=args.workflow_run_id)
    except snap.SnapshotIntegrityError as e:
        print(f"SNAPSHOT INTEGRITY ERROR: {e}", file=sys.stderr)
        _record_capture_status(args.stage, args.date, "integrity_error", {"error": str(e)})
        github_summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if github_summary:
            with open(github_summary, "a") as f:
                f.write(f"\n### :rotating_light: Snapshot capture FAILED (integrity error): {args.stage} {args.date}\n")
                f.write(f"```\n{e}\n```\n")
                f.write("A dedicated capture check will detect this gap and attempt safe recovery on its next run.\n")
        sys.exit(1)

    manifest = result["manifest"]
    # Forward Replay Corpus and Production Provenance milestone (item 4):
    # the workflow summary must report snapshotId, completeness, manifest
    # hash, and (degraded-capture) visibility -- never leave a caller to
    # infer this from job logs alone.
    summary = {
        "outcome": result["outcome"],
        "snapshotId": manifest["snapshotId"],
        "snapshotStage": manifest["snapshotStage"],
        "snapshotDate": manifest["snapshotDate"],
        "productionRunId": manifest.get("productionRunId"),
        "captureMode": manifest["captureMode"],
        "completenessStatus": manifest["completenessStatus"],
        "replayFidelityPotential": manifest["replayFidelityPotential"],
        "manifestHash": manifest["manifestHash"],
        "productionCommitSha": manifest.get("productionCommitSha"),
        "productionProvenanceStatus": (manifest.get("productionProvenance") or {}).get("status"),
        "missingComponents": manifest["missingComponents"],
        "limitationReasons": manifest["limitationReasons"],
    }
    print(json.dumps(summary, indent=2))

    if result["outcome"] in ("created", "noop_verified"):
        _record_capture_status(args.stage, args.date, result["outcome"], {
            "snapshotId": manifest["snapshotId"], "completenessStatus": manifest["completenessStatus"],
            "manifestHash": manifest["manifestHash"], "productionCommitSha": manifest.get("productionCommitSha"),
        })
        github_summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if github_summary:
            degraded = manifest["completenessStatus"] != "COMPLETE_FOR_PRODUCTION_REPLAY"
            with open(github_summary, "a") as f:
                if degraded:
                    f.write(f"\n### :warning: Snapshot capture DEGRADED: {args.stage} {args.date}\n")
                else:
                    f.write(f"\n### Snapshot: {args.stage} {args.date}\n")
                f.write(f"- snapshotId: `{manifest['snapshotId']}`\n")
                f.write(f"- productionRunId: `{manifest.get('productionRunId')}`\n")
                f.write(f"- outcome: `{result['outcome']}`\n")
                f.write(f"- completenessStatus: `{manifest['completenessStatus']}`\n")
                f.write(f"- replayFidelityPotential: `{manifest['replayFidelityPotential']}`\n")
                f.write(f"- manifestHash: `{manifest['manifestHash']}`\n")
                f.write(f"- productionCommitSha: `{manifest.get('productionCommitSha')}`\n")
                f.write(f"- productionProvenanceStatus: `{(manifest.get('productionProvenance') or {}).get('status')}`\n")
                if manifest["missingComponents"]:
                    f.write(f"- missingComponents: {', '.join(manifest['missingComponents'])}\n")
        sys.exit(0)

    _record_capture_status(args.stage, args.date, result["outcome"])
    print(f"SNAPSHOT {result['outcome'].upper()} for {args.stage} {args.date}", file=sys.stderr)
    if "conflictEvidencePath" in result:
        print(f"Diagnostic evidence preserved at: {result['conflictEvidencePath']}", file=sys.stderr)
    if "diagnostics" in result:
        print(f"Diagnostics: {result['diagnostics']}", file=sys.stderr)
    github_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if github_summary:
        with open(github_summary, "a") as f:
            f.write(f"\n### :rotating_light: Snapshot capture FAILED ({result['outcome']}): {args.stage} {args.date}\n")
            f.write("Existing snapshot preserved untouched; see diagnostics in job logs. ")
            f.write("A dedicated capture check will detect this gap and attempt safe recovery on its next run.\n")
    sys.exit(1)


if __name__ == "__main__":
    main()
