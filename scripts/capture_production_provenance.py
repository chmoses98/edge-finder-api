#!/usr/bin/env python3
"""
scripts/capture_production_provenance.py
=============================================
Forward Replay Corpus and Production Provenance milestone (item 2):
writes data/pipeline/<date>/provenance.json -- the authoritative record
of exactly which git commit, workflow run, and ref checked-out code will
execute this run's model/pricing/recommendation logic.

Deliberately the FIRST script fetch-slate.yml runs after checkout, before
any model-execution step (merge_odds.py, enrich_data.py,
build_market_ledger.py, risk_gate.py). This is the earliest possible
capture point, so lib.edgelab.snapshot's later PRODUCTION_PROVENANCE
component freeze can prove the commit SHA was recorded BEFORE model
execution ran -- never reconstructed afterward from whatever HEAD happens
to be at snapshot-creation time (see lib/edgelab/snapshot.py's
_production_provenance_component docstring for the cross-check this
enables).

Every field here is read directly from a GitHub Actions environment
variable the runner sets, or from `git rev-parse HEAD` as a local-run
fallback -- never inferred, guessed, or defaulted to something that looks
plausible. A field GitHub Actions doesn't provide (e.g. local/manual
runs) is honestly null.
"""
import os
import subprocess
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

import lib.pipeline_artifacts as pipeline_artifacts  # noqa: E402
from lib.edgelab import ids  # noqa: E402


def _local_git_commit_sha():
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def capture_provenance(date: str) -> dict:
    commit_sha = os.environ.get("GITHUB_SHA") or _local_git_commit_sha()
    payload = {
        "commitSha": commit_sha,
        "workflowRunId": os.environ.get("GITHUB_RUN_ID"),
        "workflowRunAttempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "ref": os.environ.get("GITHUB_REF"),
        "refName": os.environ.get("GITHUB_REF_NAME"),
        "repository": os.environ.get("GITHUB_REPOSITORY"),
        "workflow": os.environ.get("GITHUB_WORKFLOW"),
        "job": os.environ.get("GITHUB_JOB"),
        "eventName": os.environ.get("GITHUB_EVENT_NAME"),
        "capturedAt": ids.utc_now_iso(),
    }
    pipeline_artifacts.write_stage_artifact(
        "provenance", date, payload, produced_by="scripts/capture_production_provenance.py",
    )
    return payload


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 scripts/capture_production_provenance.py <DATE>", file=sys.stderr)
        sys.exit(1)
    date = sys.argv[1]
    payload = capture_provenance(date)
    if not payload["commitSha"]:
        print("WARNING: no commit SHA available (neither GITHUB_SHA nor `git rev-parse HEAD` resolved)", file=sys.stderr)
    print(f"Production provenance captured for {date}: commitSha={payload['commitSha']}")


if __name__ == "__main__":
    main()
