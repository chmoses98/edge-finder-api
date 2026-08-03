#!/usr/bin/env python3
"""
scripts/capture_production_provenance.py
=============================================
Forward Replay Corpus and Production Provenance milestone (item 2):
writes data/pipeline/<date>/provenance.json -- the authoritative record
of exactly which git commit, workflow run, and ref checked-out code will
execute this run's model/pricing/recommendation logic.

Positioned in fetch-slate.yml immediately before the model-execution chain
begins (merge_odds.py onward) and after every in-job `git rebase
--autostash origin/main` this workflow performs (Commit Kalshi snapshot,
the not-ready-path snapshot commit, Commit fetch_status.json) -- see the
"Record production provenance" step's comment in that workflow for the
full reasoning (maintainer review of PR #37, item 1). Capturing any
earlier risks a rebase silently advancing HEAD past this recorded SHA
before model execution actually runs; capturing here is the latest point
that still runs strictly before scripts/build_market_ledger.py.

Every field here is read directly from a GitHub Actions environment
variable the runner sets, or from `git rev-parse HEAD` as a local-run
fallback -- never inferred, guessed, or defaulted to something that looks
plausible. A field GitHub Actions doesn't provide (e.g. local/manual
runs) is honestly null.

`commitSha` (the field treated as authoritative -- see
lib/edgelab/snapshot.py's _production_provenance) always prefers the
static GITHUB_SHA env var set once by the Actions runner at job start.
That value never changes mid-job even though HEAD legitimately advances
during the job from routine, code-untouching data commits (the three
rebases above), so GITHUB_SHA remains the right authoritative answer to
"which commit did the Actions runner check out for this job" even at this
later capture position.

`gitHeadShaAtCapture` is a SEPARATE, purely informational field: live
`git rev-parse HEAD` at the moment this script runs. It is never used for
automated ambiguity decisions -- comparing it against commitSha would
produce false positives on ordinary runs, since HEAD can legitimately
differ from GITHUB_SHA by this point (fast-forwarded by the job's own
earlier rebases onto commits this same job authored). It exists purely so
a human auditing provenance.json can see what was actually checked out.

`workingTreeDirty` is the real authenticity signal this module adds: True
if `git diff --quiet HEAD -- scripts/ lib/ config/` reports uncommitted
changes to CODE paths specifically (not data/ -- at this capture position
data/ legitimately has uncommitted changes from earlier fetch steps in
this same job, so a whole-repository dirty check would spuriously flag
every real production run). lib/edgelab/snapshot.py treats
workingTreeDirty=True as AMBIGUOUS, never CAPTURED, because it means the
code that is about to execute does not match any committed commit SHA at
all -- the one case a SHA, however captured, cannot describe honestly.
"""
import os
import subprocess
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

import lib.pipeline_artifacts as pipeline_artifacts  # noqa: E402
from lib.edgelab import ids  # noqa: E402

# Scoped deliberately to CODE paths only -- see module docstring's
# `workingTreeDirty` section for why a whole-repository check would be
# useless (data/ always has legitimate uncommitted changes at this point).
_CODE_PATHS_FOR_DIRTY_CHECK = ("scripts/", "lib/", "config/")


def _local_git_commit_sha():
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def _code_tree_dirty():
    """True/False, or None if git state couldn't be determined (never
    treated as False-by-default -- an unknown dirty state must not be
    silently trusted as clean; see lib/edgelab/snapshot.py's handling of
    None here, which maps it to AMBIGUOUS same as True)."""
    try:
        result = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", *_CODE_PATHS_FOR_DIRTY_CHECK],
            capture_output=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0:
        return False
    if result.returncode == 1:
        return True
    return None


def capture_provenance(date: str) -> dict:
    commit_sha = os.environ.get("GITHUB_SHA") or _local_git_commit_sha()
    payload = {
        "commitSha": commit_sha,
        "gitHeadShaAtCapture": _local_git_commit_sha(),
        "workingTreeDirty": _code_tree_dirty(),
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
