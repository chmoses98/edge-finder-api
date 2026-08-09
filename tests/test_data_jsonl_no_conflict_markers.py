#!/usr/bin/env python3
"""
tests/test_data_jsonl_no_conflict_markers.py
=================================================
Lightweight, repo-wide validation (runs as part of the normal `pytest
tests/` sweep already wired into .github/workflows/pr-ci.yml, so it
needs no separate workflow step): no tracked JSONL file under data/
may contain a literal, unresolved git conflict marker
(<<<<<<<, =======, >>>>>>> at line start).

This is the belt-and-suspenders half of the git-conflict-marker fix
(scripts/ci/git_data_commit.py is the actual root-cause fix, in the
automated commit path itself) -- this test instead guards the
COMMITTED STATE of the repository at any point in time: it would catch
a broken file that ever slipped past the commit-time check for any
reason (a manual commit, a future workflow that doesn't yet use the
shared safe path, a git version's own edge case), on the very next PR,
rather than letting it sit undetected in data/edgelab/research_runs/
until an unrelated PR's CI happens to fail because of it -- which is
exactly how this bug was originally discovered.
"""
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")

_MARKERS = (b"<<<<<<< ", b"=======", b">>>>>>> ")


def _iter_jsonl_files():
    for dirpath, _dirnames, filenames in os.walk(DATA_DIR):
        for name in filenames:
            if name.endswith(".jsonl"):
                yield os.path.join(dirpath, name)


def _find_conflict_marker_lines(path):
    hits = []
    with open(path, "rb") as f:
        for i, line in enumerate(f, start=1):
            if line.startswith(_MARKERS[0]) or line.rstrip(b"\n") == _MARKERS[1] or line.startswith(_MARKERS[2]):
                hits.append(i)
    return hits


@pytest.mark.parametrize("path", sorted(_iter_jsonl_files()), ids=lambda p: os.path.relpath(p, ROOT))
def test_jsonl_file_has_no_unresolved_conflict_markers(path):
    hits = _find_conflict_marker_lines(path)
    assert not hits, (
        f"{os.path.relpath(path, ROOT)} contains unresolved git conflict marker(s) "
        f"at line(s) {hits} -- an automated commit landed broken data; see "
        f"scripts/ci/git_data_commit.py"
    )


def test_at_least_one_jsonl_file_was_actually_scanned():
    """Guards against this test silently checking nothing if data/'s
    layout ever changes (e.g. an empty checkout, or every *.jsonl
    renamed) -- a parametrized test with zero cases still reports as
    zero failures, which looks identical to "all clean" unless
    something else notices the count is zero."""
    assert len(list(_iter_jsonl_files())) > 0
