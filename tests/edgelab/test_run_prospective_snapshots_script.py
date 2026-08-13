#!/usr/bin/env python3
"""
tests/edgelab/test_run_prospective_snapshots_script.py
============================================================
Coverage for scripts/edgelab/run_prospective_snapshots.py -- currently
just its pure, testable compute_run_status() helper (reliability pass,
spec section 9). The rest of the script is a thin I/O wrapper around
lib.edgelab.prospective_snapshot.run_prospective_snapshot_cycle, already
covered by tests/edgelab/test_prospective_snapshot.py.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.edgelab.run_prospective_snapshots import compute_run_status


def test_no_op_when_nothing_evaluated_and_no_failures():
    assert compute_run_status(evaluated_count=0, genuine_failure_count=0) == "no_op"


def test_success_when_something_evaluated_and_no_failures():
    assert compute_run_status(evaluated_count=3, genuine_failure_count=0) == "success"


def test_partial_when_some_succeeded_and_some_genuinely_failed():
    assert compute_run_status(evaluated_count=11, genuine_failure_count=1) == "partial"


def test_failed_when_attempted_but_nothing_succeeded():
    assert compute_run_status(evaluated_count=0, genuine_failure_count=1) == "failed"


def test_never_returns_success_merely_because_process_reached_the_end():
    """A run with genuine failures must never be reported 'success', regardless of how many other games succeeded."""
    for evaluated in (0, 1, 5, 100):
        assert compute_run_status(evaluated_count=evaluated, genuine_failure_count=1) != "success"
