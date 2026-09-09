"""
tests/conftest.py
=================
WAVE 0.05A. One job: prove the deterministic test suite does not mutate
canonical repository evidence.

WHY THIS EXISTS
---------------
On 2026-09-08 `tests/edgelab/test_clv_convention.py::test_migration_is_idempotent`
ran `scripts/edgelab/migrate_clv_sign.py --apply` against the REAL
`data/edgelab/bets/bets.jsonl`. The first CI invocation failed AND rewrote the
ledger (sha256 e1016a8a... -> 7f2b9754...); the second invocation then passed,
because the first had already performed the migration. Three tracked files were
modified by pytest:

    data/edgelab/analytics/clv_sign_migration_manifest.json
    data/edgelab/analytics/clv_sign_migration_receipt.json
    data/edgelab/bets/bets.jsonl

A suite that rewrites the evidence base it is asserting against can be made
green by running it twice. That is the failure mode this guard removes.

HOW IT WORKS
------------
`_canonical_evidence_unchanged` is session-scoped and autouse, so it runs for
every invocation of the suite regardless of which tests are selected. It hashes
the canonical artifacts before any test executes and re-hashes them at session
teardown. Any difference fails the session, naming the file.

This is deliberately a hash comparison rather than a `git status` check: it
catches a write-then-restore just as well as a write that is left behind, and
it works in a checkout that has unrelated local modifications.
"""

import hashlib
import os
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Canonical production evidence. A test may READ any of these; none may be
# written by the suite. Paths that do not exist in a given checkout are skipped
# rather than treated as a failure.
CANONICAL_EVIDENCE = (
    "data/edgelab/bets/bets.jsonl",
    "data/edgelab/analytics/clv_sign_migration_manifest.json",
    "data/edgelab/analytics/clv_sign_migration_receipt.json",
    "bets.json",
    "BET_LOG.md",
    "data/kalshi_market_registry.json",
    # WAVE 0 EXIT. Added after the production restore of 2026-09-01..09-06.
    # The research scorecard runner's own test file called that runner's
    # `main()` five times with no output override, so
    # every pytest run rewrote both of these in the working tree. The original
    # list enumerated only the artifacts implicated in the 2026-09-08 incident,
    # so the same defect class in a different file went unseen; the restore
    # surfaced it by changing the numbers the scorer produces (n=329 -> n=1811).
    "docs/EDGELAB_FROZEN_FORWARD_SCORECARD.md",
)

# Whole directories of derived canonical evidence. Enumerating a directory
# rather than individual files is what generalises this guard beyond the
# specific artifacts a past incident happened to touch: a NEW analytics file
# written by the suite is caught as a creation, and there is no list to keep in
# sync. Non-existent directories are skipped.
CANONICAL_EVIDENCE_DIRS = (
    "data/edgelab/analytics",
)

# WAVE 0.07. The restored canonical corpus -- the 29,427 recommendations and
# 28,781 settlements the production restore put on main, plus every other
# durable EdgeLab family. The suite must not be able to modify, delete, or add
# to any of these.
#
# These are guarded by a git-status baseline diff rather than by content
# hashing, because hashing them is the wrong tool at this size. Measured on
# this repository: sha256 over settlements/recommendations/model_evaluations/
# games/health/analytics reads 74.6 MB and takes ~3.5s, and including
# snapshots/ would add another 214 MB across 2,063 files. The same coverage via
# `git status --porcelain` over all of them takes ~0.02s, and is strictly
# BROADER: it reports modifications, deletions AND untracked creations, naming
# each offending path, without reading file contents at all.
#
# The baseline diff is what keeps this from firing on legitimate pre-existing
# state: a workflow-written file that was already dirty when pytest started
# appears identically in the before and after snapshots and is ignored. Only a
# path whose git status CHANGED during the session is reported.
CANONICAL_EVIDENCE_TREES = (
    "data/edgelab/settlements",
    "data/edgelab/recommendations",
    "data/edgelab/model_evaluations",
    "data/edgelab/games",
    "data/edgelab/health",
    "data/edgelab/analytics",
    "data/edgelab/snapshots",
    "data/edgelab/research_runs",
    "data/edgelab/bets",
)


def git_status_map(root, trees):
    """
    Pure-ish. Returns {relative path: git status code} for every path under
    `trees` that git reports as modified, deleted, staged or untracked.

    An empty dict means every one of those trees exactly matches HEAD. A
    missing tree, or a checkout that is not a git repository at all, yields no
    entries rather than an error -- this guard must never be the reason a
    test run cannot start.
    """
    existing = [t for t in trees if os.path.exists(os.path.join(root, t))]
    if not existing:
        return {}
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all", "--"] + existing,
            cwd=root, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return {}
    if proc.returncode != 0:
        return {}

    status = {}
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        code, _, path = line[:2], line[2:3], line[3:]
        # A rename is reported as "old -> new"; the new path is what matters.
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        status[path.strip().strip('"')] = code
    return status


def diff_status(before, after):
    """
    Pure. Returns a sorted list of human-readable descriptions of every path
    whose git status changed between the two snapshots. Paths dirty in both
    snapshots are legitimate pre-existing state and are deliberately ignored.
    """
    drifted = []
    for path, code in sorted(after.items()):
        was = before.get(path)
        if was == code:
            continue
        if was is None:
            drifted.append("%s was created or modified by the test suite (git status %r)"
                           % (path, code.strip() or "??"))
        else:
            drifted.append("%s changed state during the test suite (%r -> %r)"
                           % (path, was.strip(), code.strip()))
    for path in sorted(set(before) - set(after)):
        drifted.append("%s was restored or removed from git's view by the test suite"
                       % path)
    return drifted


def _hash(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _snapshot():
    snap = {}
    for rel in CANONICAL_EVIDENCE:
        full = os.path.join(ROOT, rel)
        if os.path.exists(full):
            snap[rel] = _hash(full)
    for rel_dir in CANONICAL_EVIDENCE_DIRS:
        full_dir = os.path.join(ROOT, rel_dir)
        if not os.path.isdir(full_dir):
            continue
        for dirpath, _dirnames, filenames in os.walk(full_dir):
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, ROOT)
                snap[rel] = _hash(full)
    return snap


@pytest.fixture(scope="session", autouse=True)
def _canonical_evidence_unchanged():
    before = _snapshot()
    tree_before = git_status_map(ROOT, CANONICAL_EVIDENCE_TREES)
    yield
    after = _snapshot()
    tree_after = git_status_map(ROOT, CANONICAL_EVIDENCE_TREES)

    drifted = diff_status(tree_before, tree_after)
    for rel, digest in sorted(before.items()):
        now = after.get(rel)
        if now is None:
            drifted.append("%s was DELETED by the test suite" % rel)
        elif now != digest:
            drifted.append("%s changed: %s -> %s" % (rel, digest[:16], now[:16]))
    for rel in sorted(set(after) - set(before)):
        drifted.append("%s was CREATED by the test suite" % rel)

    assert drifted == [], (
        "the test suite mutated canonical repository evidence:\n  "
        + "\n  ".join(drifted)
        + "\n\nTests must operate on tmp_path copies. A suite that rewrites the "
          "data it asserts against can be turned green by running it twice -- "
          "see this file's module docstring for the 2026-09-08 incident."
    )
