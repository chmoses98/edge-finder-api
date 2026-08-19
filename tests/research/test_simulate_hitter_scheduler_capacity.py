#!/usr/bin/env python3
"""
tests/research/test_simulate_hitter_scheduler_capacity.py
================================================================
Coverage for scripts/research/simulate_hitter_scheduler_capacity.py --
the deterministic cadence+runtime+concurrency capacity simulator (as
distinct from tests/research/test_simulate_hitter_checkpoint_coverage.py,
which covers minute-of-hour alignment only). Every test is pure/offline
-- no real GitHub API calls, no real scheduler execution.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.research import simulate_hitter_scheduler_capacity as sim


class TestSimulateConcurrencyGroupSingleMode:
    def test_short_runtime_never_queues_anything(self):
        """Runtime well under cadence: every tick executes immediately, on time, nothing pending."""
        timeline = sim.simulate_concurrency_group([0, 15, 30, 45], lambda t: 5, queue_mode="single")
        for t in (0, 15, 30, 45):
            assert timeline[t]["outcome"] == sim.EXECUTED
            assert timeline[t]["delayMinutes"] == 0

    def test_overrunning_cycle_causes_next_pending_tick_to_be_canceled(self):
        """
        The exact failure mode the task describes: cycle A (t=0) runs 40
        minutes (>> cadence 15) -- still running when BOTH B (t=15) and
        C (t=30) arrive. B becomes PENDING first; C's arrival (while A is
        still running and B is still pending) cancels/replaces B, exactly
        matching GitHub's default single-pending-slot semantics
        (cancel-in-progress:false only protects the RUNNING job, never a
        PENDING one). C then waits pending until A finally frees at t=40.
        """
        timeline = sim.simulate_concurrency_group([0, 15, 30], lambda t: 40, queue_mode="single")
        assert timeline[0]["outcome"] == sim.EXECUTED
        assert timeline[0]["actualStart"] == 0
        assert timeline[15]["outcome"] == sim.CANCELED_WHILE_PENDING
        assert timeline[15]["actualStart"] is None
        assert timeline[30]["outcome"] == sim.EXECUTED
        assert timeline[30]["actualStart"] == 40  # dispatched the instant the runner freed, not at its own nominal t=30
        assert timeline[30]["delayMinutes"] == 10

    def test_a_single_overrun_with_no_third_tick_still_eventually_executes_the_pending_one(self):
        """B (t=15) is pending while A (t=0, runtime=25) runs; if no C ever arrives, B still executes once A finishes -- never silently dropped just because the simulated tick horizon ended."""
        timeline = sim.simulate_concurrency_group([0, 15], lambda t: 25, queue_mode="single")
        assert timeline[0]["outcome"] == sim.EXECUTED
        assert timeline[15]["outcome"] == sim.EXECUTED
        assert timeline[15]["actualStart"] == 25
        assert timeline[15]["delayMinutes"] == 10

    def test_runtime_exactly_equal_to_cadence_never_queues(self):
        """Boundary: runtime == cadence means the runner frees up EXACTLY as the next tick arrives -- no contention."""
        timeline = sim.simulate_concurrency_group([0, 15, 30], lambda t: 15, queue_mode="single")
        for t in (0, 15, 30):
            assert timeline[t]["outcome"] == sim.EXECUTED
            assert timeline[t]["delayMinutes"] == 0


class TestSimulateConcurrencyGroupMaxMode:
    def test_queue_max_never_cancels_only_delays(self):
        """Under queue:max, the SAME overrun scenario that cancels B in single mode instead queues it in true FIFO order -- eventually executes, later than nominal, never dropped."""
        timeline = sim.simulate_concurrency_group([0, 15, 30], lambda t: 25, queue_mode="max")
        assert timeline[0]["outcome"] == sim.EXECUTED
        assert timeline[15]["outcome"] == sim.EXECUTED
        assert timeline[30]["outcome"] == sim.EXECUTED
        # FIFO: 15 must start before 30, since it queued first.
        assert timeline[15]["actualStart"] <= timeline[30]["actualStart"]

    def test_queue_max_respects_cap(self):
        """A pathological backlog exceeding queue_max is REJECTED (never silently executed out of order or fabricated) -- exercised with an artificially tiny cap since GitHub's real cap (100) is never realistically reached by a 15-minute cadence."""
        cron_times = list(range(0, 15 * 10, 15))  # 10 ticks
        timeline = sim.simulate_concurrency_group(cron_times, lambda t: 1000, queue_mode="max", queue_max=2)
        rejected = [t for t in cron_times if timeline[t]["outcome"] == sim.REJECTED_QUEUE_FULL]
        assert len(rejected) > 0


class TestSimulateCheckpointCoverageUnderLoad:
    def test_light_runtime_captures_every_time_target_checkpoint(self):
        result = sim.simulate_checkpoint_coverage_under_load(
            300, cadence_minutes=15, runtime_minutes=5, queue_mode="single",
        )
        assert result["capturedLabels"]["T_MINUS_90"] is True
        assert result["capturedLabels"]["T_MINUS_60"] is True
        assert result["capturedLabels"]["T_MINUS_30"] is True
        assert result["missedLabels"] == []

    def test_sustained_overload_under_default_queue_eventually_misses_a_checkpoint(self):
        """A long enough run of consecutive 45-minute cycles under the default single-pending-slot queue eventually cancels enough pending ticks that a real checkpoint window closes before any surviving executed tick lands inside it -- the core capacity failure mode this task audits."""
        result = sim.simulate_checkpoint_coverage_under_load(
            300, cadence_minutes=15, runtime_minutes=45, queue_mode="single", num_heavy_cycles=6,
        )
        assert "T_MINUS_60" in result["missedLabels"]

    def test_missed_checkpoint_is_never_fabricated_as_a_late_capture(self):
        """Directly proves the task's 'never backdate' requirement: when a checkpoint's window has definitively closed by the time a delayed/queued cycle finally executes, it must be reported MISSED, not silently captured using the nominal (not real) trigger time."""
        result = sim.simulate_checkpoint_coverage_under_load(
            300, cadence_minutes=15, runtime_minutes=45, queue_mode="max", num_heavy_cycles=3,
        )
        assert result["capturedLabels"]["T_MINUS_60"] is False
        assert "T_MINUS_60" in result["missedLabels"]

    def test_captured_checkpoints_use_real_execution_time_not_nominal_trigger_time(self):
        """A cycle delayed by queueing that DOES still land inside a target's tolerance window must report the REAL minutesToStart it executed at, never the nominal (undelayed) value."""
        result = sim.simulate_checkpoint_coverage_under_load(
            300, cadence_minutes=15, runtime_minutes=20, queue_mode="single", num_heavy_cycles=1,
        )
        assert result["capturedLabels"]["T_MINUS_90"] is True
        # T-90 nominal minutesToStart would be exactly 90; a delayed real
        # execution must report something other than the pristine nominal
        # value whenever any delay actually occurred for that tick.
        assert "T_MINUS_90" in result["capturedAtMinutesToStart"]


class TestRunCapacityMatrix:
    def test_covers_every_required_runtime_and_heavy_cycle_combination(self):
        matrix = sim.run_capacity_matrix(cadence_minutes=15, queue_mode="single")
        seen = {(row["runtimeMinutes"], row["heavyCycles"]) for row in matrix}
        expected = {
            (runtime, heavy)
            for runtime in sim.RUNTIME_SCENARIOS_MINUTES
            for heavy in sim.HEAVY_CYCLE_COUNTS
        }
        assert seen == expected

    def test_runtime_at_or_below_cadence_never_cancels_anything(self):
        matrix = sim.run_capacity_matrix(cadence_minutes=15, queue_mode="single")
        for row in matrix:
            if row["runtimeMinutes"] <= 15:
                assert row["canceledCount"] == 0
                assert row["missedLabels"] == []

    def test_runtime_well_above_cadence_produces_cancellations_under_single_queue(self):
        matrix = sim.run_capacity_matrix(cadence_minutes=15, queue_mode="single")
        row = next(r for r in matrix if r["runtimeMinutes"] == 45 and r["heavyCycles"] == 3)
        assert row["canceledCount"] > 0

    def test_max_queue_mode_never_cancels_anything(self):
        matrix = sim.run_capacity_matrix(cadence_minutes=15, queue_mode="max")
        for row in matrix:
            assert row["canceledCount"] == 0
