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
    _aggregate_batch_runtimes,
    compute_remaining_cycle_budget_seconds,
    compute_run_status,
    latest_dated_kalshi_snapshot,
)


class TestComputeRemainingCycleBudgetSeconds:
    """True-remaining-timeout-budget fix: the cycle must only ever be told
    how much of the SCRIPT's own job_timeout_seconds is ACTUALLY left after
    preprocessing (pregame-context fetch, existing-row load, wOBA loads,
    live-status fetch) has already consumed some of it -- never the full,
    un-adjusted original budget."""

    def test_no_preprocessing_elapsed_returns_full_budget(self):
        assert compute_remaining_cycle_budget_seconds(1680, 0) == 1680

    def test_substantial_preprocessing_reduces_the_budget(self):
        """The exact scenario this fix targets: real preprocessing time (e.g. a
        slow standalone pregame-context fetch) must actually come OFF the
        budget the cycle is told it has -- not be silently ignored."""
        assert compute_remaining_cycle_budget_seconds(1680, 400) == 1280

    def test_preprocessing_consuming_the_entire_budget_floors_at_zero(self):
        """Preprocessing alone consuming (or exceeding) the whole script budget
        must never produce a negative remaining budget -- the cycle must be
        told exactly 0, which correctly declines any risky fallback."""
        assert compute_remaining_cycle_budget_seconds(1680, 1680) == 0
        assert compute_remaining_cycle_budget_seconds(1680, 2000) == 0

    def test_near_zero_remaining_budget(self):
        assert compute_remaining_cycle_budget_seconds(1680, 1679) == 1

    def test_none_job_timeout_seconds_passes_through_as_none(self):
        """job_timeout_seconds=None means 'no configured bound to check against'
        (--job-timeout-seconds 0 at the CLI) -- the bounded-fallback-policy check
        stays fully disabled regardless of how much preprocessing time elapsed."""
        assert compute_remaining_cycle_budget_seconds(None, 400) is None
        assert compute_remaining_cycle_budget_seconds(None, 0) is None


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


class TestAggregateBatchRuntimes:
    """Coverage for _aggregate_batch_runtimes -- derives the run manifest's
    checkpointBatchRuntimeSeconds/boardBuildRuntimeSeconds (runtime-hardening
    fix, see docs/HITTER_SCHEDULER_RUNTIME_HARDENING.md) from the run_log
    lib.research.hitter_prospective_snapshot.run_hitter_prospective_snapshot_cycle
    already annotates per checkpoint batch."""

    def test_empty_run_log_produces_empty_dicts(self):
        batch, build = _aggregate_batch_runtimes([])
        assert batch == {}
        assert build == {}

    def test_entries_without_a_checkpoint_are_ignored(self):
        run_log = [{"checkpoint": None, "action": "SKIPPED", "reason": "STARTED"}]
        batch, build = _aggregate_batch_runtimes(run_log)
        assert batch == {}
        assert build == {}

    def test_single_checkpoint_batch_timing_extracted(self):
        run_log = [
            {"gameId": "1", "checkpoint": "T_MINUS_90", "action": "EVALUATED",
             "boardBuildElapsedSeconds": 12.5, "checkpointBatchElapsedSeconds": 13.1},
        ]
        batch, build = _aggregate_batch_runtimes(run_log)
        assert batch == {"T_MINUS_90": 13.1}
        assert build == {"T_MINUS_90": 12.5}

    def test_multiple_games_in_the_same_batch_deduplicate_to_one_entry(self):
        """Every game in the same checkpoint's batch carries the SAME batch-level elapsed value (the Monte Carlo evaluate step is invoked once per BATCH, not once per game) -- the aggregation must not double-count or diverge across games sharing a batch."""
        run_log = [
            {"gameId": "1", "checkpoint": "T_MINUS_90", "action": "EVALUATED",
             "boardBuildElapsedSeconds": 12.5, "checkpointBatchElapsedSeconds": 13.1},
            {"gameId": "2", "checkpoint": "T_MINUS_90", "action": "EVALUATED",
             "boardBuildElapsedSeconds": 12.5, "checkpointBatchElapsedSeconds": 13.1},
        ]
        batch, build = _aggregate_batch_runtimes(run_log)
        assert batch == {"T_MINUS_90": 13.1}
        assert build == {"T_MINUS_90": 12.5}

    def test_multiple_distinct_checkpoint_groups_stay_independent(self):
        run_log = [
            {"gameId": "1", "checkpoint": "T_MINUS_90", "action": "EVALUATED",
             "boardBuildElapsedSeconds": 12.5, "checkpointBatchElapsedSeconds": 13.1},
            {"gameId": "2", "checkpoint": "LINEUP_CONFIRMATION", "action": "EVALUATED",
             "boardBuildElapsedSeconds": 4.0, "checkpointBatchElapsedSeconds": 4.2},
        ]
        batch, build = _aggregate_batch_runtimes(run_log)
        assert batch == {"T_MINUS_90": 13.1, "LINEUP_CONFIRMATION": 4.2}
        assert build == {"T_MINUS_90": 12.5, "LINEUP_CONFIRMATION": 4.0}

    def test_a_failed_batchs_timing_is_still_captured(self):
        """A checkpoint group whose board build raised still ran for some time before failing -- that must still show up here, not be silently dropped just because it never produced a row."""
        run_log = [
            {"gameId": "1", "checkpoint": "LINEUP_CONFIRMATION", "action": "SKIPPED",
             "reason": "hitter board build raised: boom",
             "boardBuildElapsedSeconds": 3.4, "checkpointBatchElapsedSeconds": 3.6},
        ]
        batch, build = _aggregate_batch_runtimes(run_log)
        assert batch == {"LINEUP_CONFIRMATION": 3.6}
        assert build == {"LINEUP_CONFIRMATION": 3.4}


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
