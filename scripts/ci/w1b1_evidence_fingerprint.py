#!/usr/bin/env python3
"""
scripts/ci/w1b1_evidence_fingerprint.py
=======================================
Prints one `<sha256>  <path>` line per canonical evidence file, sorted.

Used by the W1-B1 read-only rehearsal to prove, by CONTENT rather than by git
status alone, that a shadow run changed nothing that matters: the workflow takes
a fingerprint before the run and after it, and diffs the two.

The file list is imported from lib/canonical_evidence.py rather than restated
here. Two lists would drift, and the one that drifted would be this one -- the
copy nobody edits when the guarded set grows. An earlier version imported
tests/conftest.py directly for the same reason, which shared the list correctly
but also dragged in `import pytest`; pytest is not in requirements-ci.txt, so
the rehearsal died before running a single check. The shared module is standard
library only.

Content hashing is affordable at this scope because it covers the small, hot
artifacts (the wager ledger, the bet log, the market registry, the analytics
manifests). The very large partition trees -- settlements, recommendations,
model_evaluations, snapshots -- are deliberately NOT hashed here: at 74.6 MB and
2,000+ files that is seconds per invocation, and the workflow already covers
them with a whole-tree `git status --porcelain` check, which is strictly broader
(it reports modifications, deletions AND untracked creations) and effectively
free.
"""

import hashlib
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib import canonical_evidence  # noqa: E402


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(root=ROOT):
    """[(path, sha256_or_ABSENT)] for every canonical evidence file, sorted."""
    paths = set(canonical_evidence.CANONICAL_EVIDENCE)
    for directory in canonical_evidence.CANONICAL_EVIDENCE_DIRS:
        full = os.path.join(root, directory)
        if not os.path.isdir(full):
            continue
        for dirpath, _dirnames, filenames in os.walk(full):
            for name in filenames:
                paths.add(os.path.relpath(os.path.join(dirpath, name), root))

    rows = []
    for rel in sorted(paths):
        full = os.path.join(root, rel)
        # ABSENT is recorded rather than skipped: a file that DISAPPEARS during
        # a run has to show up in the diff, and a skipped path would not.
        rows.append((rel, _sha256(full) if os.path.isfile(full) else "ABSENT"))
    return rows


def main():
    for rel, digest in fingerprint():
        print("%s  %s" % (digest, rel))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
