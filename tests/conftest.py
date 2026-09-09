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
    yield
    after = _snapshot()

    drifted = []
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
