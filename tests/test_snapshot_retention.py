#!/usr/bin/env python3
"""
tests/test_snapshot_retention.py
====================================
Production Reliability and Settlement Recovery milestone: coverage for
lib/snapshot_retention.py and scripts/prune_kalshi_snapshots.py.
"""
import datetime
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.snapshot_retention import (  # noqa: E402
    DEFAULT_RETENTION_DAYS,
    build_retention_plan,
    classify_filename,
)


class TestClassifyFilename:

    def test_dated_file_recognized(self):
        assert classify_filename("kalshi_search_2026-06-08.json") == ("dated", "2026-06-08")

    def test_timestamped_file_recognized(self):
        assert classify_filename("kalshi_search_2026-08-01_2232.json") == ("timestamped", "2026-08-01")

    def test_readme_is_unrecognized(self):
        assert classify_filename("README.md") == (None, None)

    def test_garbage_filename_is_unrecognized(self):
        assert classify_filename("kalshi_search_notadate.json") == (None, None)

    def test_wrong_extension_is_unrecognized(self):
        assert classify_filename("kalshi_search_2026-06-08.json.bak") == (None, None)


def _write_snapshot(directory, filename, content=None):
    path = directory / filename
    path.write_text(json.dumps(content or {"markets": []}))
    return path


class TestBuildRetentionPlan:

    def test_dated_files_always_kept_regardless_of_age(self, tmp_path):
        _write_snapshot(tmp_path, "kalshi_search_2026-01-01.json")
        plan = build_retention_plan(str(tmp_path), datetime.date(2026, 8, 2), retention_days=21)
        assert plan["datedFilesKeptForever"] == 1
        assert plan["timestampedFilesToPrune"] == []

    def test_recent_timestamped_file_kept(self, tmp_path):
        _write_snapshot(tmp_path, "kalshi_search_2026-08-01_2232.json")
        plan = build_retention_plan(str(tmp_path), datetime.date(2026, 8, 2), retention_days=21)
        assert plan["timestampedFilesKept"] == 1
        assert plan["timestampedFilesToPrune"] == []

    def test_old_timestamped_file_pruned(self, tmp_path):
        _write_snapshot(tmp_path, "kalshi_search_2026-06-01_1200.json")
        plan = build_retention_plan(str(tmp_path), datetime.date(2026, 8, 2), retention_days=21)
        assert plan["timestampedFilesToPrune"] == ["kalshi_search_2026-06-01_1200.json"]
        assert plan["timestampedFilesKept"] == 0

    def test_exactly_at_boundary_is_kept_not_pruned(self, tmp_path):
        """age_days == retention_days is the last day still kept (only STRICTLY older than the window is pruned)."""
        _write_snapshot(tmp_path, "kalshi_search_2026-07-12_0000.json")  # exactly 21 days before 2026-08-02
        plan = build_retention_plan(str(tmp_path), datetime.date(2026, 8, 2), retention_days=21)
        assert plan["timestampedFilesToPrune"] == []
        assert plan["timestampedFilesKept"] == 1

    def test_one_day_past_boundary_is_pruned(self, tmp_path):
        _write_snapshot(tmp_path, "kalshi_search_2026-07-11_0000.json")  # 22 days before 2026-08-02
        plan = build_retention_plan(str(tmp_path), datetime.date(2026, 8, 2), retention_days=21)
        assert plan["timestampedFilesToPrune"] == ["kalshi_search_2026-07-11_0000.json"]

    def test_unrecognized_file_never_touched(self, tmp_path):
        (tmp_path / "README.md").write_text("# notes")
        plan = build_retention_plan(str(tmp_path), datetime.date(2026, 8, 2), retention_days=21)
        assert plan["unrecognizedFilesSkipped"] == ["README.md"]
        assert plan["timestampedFilesToPrune"] == []

    def test_projected_bytes_reclaimed_matches_actual_file_sizes(self, tmp_path):
        old_file = _write_snapshot(tmp_path, "kalshi_search_2026-01-01_1200.json", {"markets": ["x"] * 50})
        plan = build_retention_plan(str(tmp_path), datetime.date(2026, 8, 2), retention_days=21)
        assert plan["projectedBytesReclaimed"] == old_file.stat().st_size

    def test_plan_is_deterministic_across_repeated_calls(self, tmp_path):
        _write_snapshot(tmp_path, "kalshi_search_2026-06-01_1200.json")
        _write_snapshot(tmp_path, "kalshi_search_2026-08-01.json")
        plan1 = build_retention_plan(str(tmp_path), datetime.date(2026, 8, 2), retention_days=21)
        plan2 = build_retention_plan(str(tmp_path), datetime.date(2026, 8, 2), retention_days=21)
        assert plan1 == plan2

    def test_default_retention_is_21_days(self):
        assert DEFAULT_RETENTION_DAYS == 21

    def test_mixed_directory_full_scenario(self, tmp_path):
        _write_snapshot(tmp_path, "kalshi_search_2026-08-01.json")           # dated, always kept
        _write_snapshot(tmp_path, "kalshi_search_2026-08-01_1200.json")     # recent, kept
        _write_snapshot(tmp_path, "kalshi_search_2026-05-01_1200.json")     # old, pruned
        _write_snapshot(tmp_path, "kalshi_search_2026-05-01.json")          # dated, always kept
        (tmp_path / "README.md").write_text("# readme")
        plan = build_retention_plan(str(tmp_path), datetime.date(2026, 8, 2), retention_days=21)
        assert plan["datedFilesKeptForever"] == 2
        assert plan["timestampedFilesKept"] == 1
        assert plan["timestampedFilesToPrune"] == ["kalshi_search_2026-05-01_1200.json"]
        assert plan["unrecognizedFilesSkipped"] == ["README.md"]
        assert plan["totalFilesConsidered"] == 5


class TestNeverDeletesUniqueDataSilently:

    def test_never_prunes_a_dated_file_no_matter_how_old(self, tmp_path):
        _write_snapshot(tmp_path, "kalshi_search_2020-01-01.json")
        plan = build_retention_plan(str(tmp_path), datetime.date(2026, 8, 2), retention_days=1)
        assert plan["datedFilesKeptForever"] == 1
        assert plan["timestampedFilesToPrune"] == []


class TestCLI:

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "prune_kalshi_snapshots.py"), *args],
            capture_output=True, text=True,
        )

    def test_dry_run_default_never_deletes_files(self, tmp_path):
        old = _write_snapshot(tmp_path, "kalshi_search_2026-01-01_1200.json")
        result = self._run("--snapshot-dir", str(tmp_path), "--today", "2026-08-02", "--retention-days", "21")
        assert result.returncode == 0
        assert old.exists()
        plan = json.loads(result.stdout)
        assert plan["mode"] == "DRY_RUN"
        assert plan["timestampedFilesToPrune"] == ["kalshi_search_2026-01-01_1200.json"]

    def test_execute_actually_deletes_pruned_files(self, tmp_path):
        old = _write_snapshot(tmp_path, "kalshi_search_2026-01-01_1200.json")
        kept = _write_snapshot(tmp_path, "kalshi_search_2026-08-01.json")
        result = self._run("--snapshot-dir", str(tmp_path), "--today", "2026-08-02", "--retention-days", "21", "--execute")
        assert result.returncode == 0
        assert not old.exists()
        assert kept.exists()

    def test_execute_with_nothing_to_prune_deletes_nothing(self, tmp_path):
        kept = _write_snapshot(tmp_path, "kalshi_search_2026-08-01_1200.json")
        result = self._run("--snapshot-dir", str(tmp_path), "--today", "2026-08-02", "--retention-days", "21", "--execute")
        assert result.returncode == 0
        assert kept.exists()

    def test_missing_snapshot_dir_errors_cleanly(self, tmp_path):
        result = self._run("--snapshot-dir", str(tmp_path / "does_not_exist"), "--today", "2026-08-02")
        assert result.returncode == 1
        assert "does not exist" in result.stderr

    def test_plan_out_writes_same_plan_as_stdout(self, tmp_path):
        _write_snapshot(tmp_path, "kalshi_search_2026-01-01_1200.json")
        plan_out = tmp_path / "plan.json"
        result = self._run(
            "--snapshot-dir", str(tmp_path), "--today", "2026-08-02", "--retention-days", "21",
            "--plan-out", str(plan_out),
        )
        stdout_plan = json.loads(result.stdout)
        file_plan = json.loads(plan_out.read_text())
        assert stdout_plan == file_plan

    def test_repeated_dry_runs_are_idempotent(self, tmp_path):
        _write_snapshot(tmp_path, "kalshi_search_2026-01-01_1200.json")
        result1 = self._run("--snapshot-dir", str(tmp_path), "--today", "2026-08-02", "--retention-days", "21")
        result2 = self._run("--snapshot-dir", str(tmp_path), "--today", "2026-08-02", "--retention-days", "21")
        assert json.loads(result1.stdout) == json.loads(result2.stdout)


class TestWorkflowIntegration:

    def test_capture_snapshots_scheduled_workflow_calls_the_script_not_broken_mtime(self):
        """
        Regression guard for the actual bug this milestone found: `find
        ... -mtime` against a freshly-checked-out git working tree can
        never match anything (checkout resets every mtime to "now"), so
        it silently pruned nothing for months. Confirms the workflow now
        delegates to the filename-date-aware script instead.
        """
        path = os.path.join(ROOT, ".github", "workflows", "capture-snapshots-scheduled.yml")
        with open(path) as f:
            content = f.read()
        assert "prune_kalshi_snapshots.py" in content
        # The explanatory comment above the fix legitimately quotes the
        # old, broken invocation when describing what was wrong with it --
        # what must never reappear is that invocation actually being run.
        assert 'find "$SNAP_DIR"' not in content
