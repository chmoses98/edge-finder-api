#!/usr/bin/env python3
"""
tests/edgelab/test_run_hitter_prospective_snapshots_script.py
===================================================================
Coverage for scripts/edgelab/run_hitter_prospective_snapshots.py's own
pure, testable helpers -- compute_run_status() (identical contract to
run_prospective_snapshots.compute_run_status, see
tests/edgelab/test_run_prospective_snapshots_script.py) and
latest_dated_kalshi_snapshot() (new: finds the most recent already-
committed, regularly-scheduled Kalshi snapshot for a date, never a
`*_standalone.json` manual-capture file and never a stale different-
date file). The rest of the script is a thin I/O wrapper around
lib.research.hitter_prospective_snapshot.run_hitter_prospective_snapshot_cycle,
already covered by tests/research/test_hitter_prospective_snapshot.py.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.edgelab.run_hitter_prospective_snapshots import (
    compute_run_status,
    latest_dated_kalshi_snapshot,
)


class TestComputeRunStatus:
    def test_no_op_when_nothing_evaluated_and_no_failures(self):
        assert compute_run_status(evaluated_count=0, genuine_failure_count=0) == "no_op"

    def test_success_when_something_evaluated_and_no_failures(self):
        assert compute_run_status(evaluated_count=3, genuine_failure_count=0) == "success"

    def test_partial_when_some_succeeded_and_some_genuinely_failed(self):
        assert compute_run_status(evaluated_count=11, genuine_failure_count=1) == "partial"

    def test_failed_when_attempted_but_nothing_succeeded(self):
        assert compute_run_status(evaluated_count=0, genuine_failure_count=1) == "failed"

    def test_never_returns_success_merely_because_process_reached_the_end(self):
        for evaluated in (0, 1, 5, 100):
            assert compute_run_status(evaluated_count=evaluated, genuine_failure_count=1) != "success"


class TestLatestDatedKalshiSnapshot:
    def test_returns_none_when_no_files_exist(self, tmp_path):
        assert latest_dated_kalshi_snapshot("2026-08-16", snapshot_dir=str(tmp_path)) is None

    def test_picks_the_latest_timestamped_file_for_the_date(self, tmp_path):
        for name in ("kalshi_search_2026-08-16_0051.json", "kalshi_search_2026-08-16_1926.json",
                     "kalshi_search_2026-08-16_2326.json"):
            (tmp_path / name).write_text("{}")
        result = latest_dated_kalshi_snapshot("2026-08-16", snapshot_dir=str(tmp_path))
        assert result.endswith("kalshi_search_2026-08-16_2326.json")

    def test_ignores_standalone_manual_capture_files(self, tmp_path):
        (tmp_path / "kalshi_search_2026-08-16_192143_standalone.json").write_text("{}")
        (tmp_path / "kalshi_search_2026-08-16_1830.json").write_text("{}")
        result = latest_dated_kalshi_snapshot("2026-08-16", snapshot_dir=str(tmp_path))
        assert result.endswith("kalshi_search_2026-08-16_1830.json")

    def test_ignores_bare_end_of_day_dated_file(self, tmp_path):
        """kalshi_search_<date>.json (no time component) is a different, single end-of-day snapshot -- never confused with the freshest available intraday capture."""
        (tmp_path / "kalshi_search_2026-08-16.json").write_text("{}")
        (tmp_path / "kalshi_search_2026-08-16_0500.json").write_text("{}")
        result = latest_dated_kalshi_snapshot("2026-08-16", snapshot_dir=str(tmp_path))
        assert result.endswith("kalshi_search_2026-08-16_0500.json")

    def test_never_falls_back_to_a_different_date(self, tmp_path):
        (tmp_path / "kalshi_search_2026-08-15_2300.json").write_text("{}")
        assert latest_dated_kalshi_snapshot("2026-08-16", snapshot_dir=str(tmp_path)) is None


class TestModuleSafety:
    def test_module_never_imports_forbidden_scripts(self):
        import ast
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                             "scripts", "edgelab", "run_hitter_prospective_snapshots.py")
        with open(path) as f:
            tree = ast.parse(f.read())
        imported_names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_names.append(node.module)
        for forbidden in ("write_pending_bets", "risk_gate", "protect_slate", "validate_slate_final"):
            assert not any(forbidden in imported for imported in imported_names), f"forbidden import: {forbidden}"
