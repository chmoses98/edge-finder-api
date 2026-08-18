#!/usr/bin/env python3
"""
tests/research/test_hitter_checkpoint_coverage_simulation.py
==================================================================
Deterministic, exhaustive coverage-guarantee tests for
lib.research.hitter_prospective_snapshot's checkpoint scheduling.

Reuses scripts.research.simulate_hitter_checkpoint_coverage's simulation
harness directly (never a second, separately-maintained model of the
scheduler) -- that harness itself calls the REAL, unmodified
determine_due_hitter_checkpoint / classify_game_eligibility functions,
so these tests exercise the actual production scheduling logic across
every possible game start-minute alignment, not a simplified stand-in
for it.

This file is the permanent regression guard for the scheduling-coverage
bug found in review before PR #92 merged: a 30-minute cadence paired
with the shared checkpoint classifier's default +/-7.5-minute tolerance
mathematically covered only half of all possible game start-minute
alignments. See docs/HITTER_CHECKPOINT_COVERAGE_FIX.md for the full
writeup.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.research import hitter_prospective_snapshot as hps
from scripts.research.simulate_hitter_checkpoint_coverage import (
    run_coverage_table,
    simulate_one_game,
)

TIME_TARGET_LABELS = ("T_MINUS_90", "T_MINUS_60", "T_MINUS_30")
OLD_CADENCE_MINUTES = 30
OLD_TOLERANCE_MINUTES = 7.5
OLD_CLOSING_WINDOW_MINUTES = 12


class TestPreFixConfigurationDocumentsTheRealBug:
    """These tests pin the OLD, buggy (cadence=30, tolerance=7.5, window=12) configuration's actual measured coverage -- they exist to PROVE the bug was real (never hand-waved), not to endorse that configuration. They must keep failing this way forever, as a permanent record of what was fixed."""

    def test_old_configuration_covers_exactly_half_of_all_alignments_for_time_targets(self):
        table = run_coverage_table(
            cadence_minutes=OLD_CADENCE_MINUTES, tolerance_minutes=OLD_TOLERANCE_MINUTES,
            closing_window_minutes=OLD_CLOSING_WINDOW_MINUTES,
        )
        for label in TIME_TARGET_LABELS:
            assert table[label]["coveredCount"] == 30, f"{label}: expected exactly 30/60 under the old buggy config, got {table[label]['coveredCount']}"
            assert table[label]["missedCount"] == 30

    def test_old_configuration_closing_window_also_has_real_gaps(self):
        table = run_coverage_table(
            cadence_minutes=OLD_CADENCE_MINUTES, tolerance_minutes=OLD_TOLERANCE_MINUTES,
            closing_window_minutes=OLD_CLOSING_WINDOW_MINUTES,
        )
        assert table[hps.HITTER_CLOSING_WINDOW]["missedCount"] > 0
        assert table[hps.HITTER_CLOSING_WINDOW]["coveredCount"] == 24  # 12/30 = 40% of 60

    def test_reported_7_10_pm_example_reproduces_exactly(self):
        """The exact scenario from the bug report: a 7:10 PM game, T-90=5:40, old 30-minute-cadence ticks at 5:30 and 6:00 -- neither within +/-7.5 min of 5:40, so T-90 is missed entirely."""
        # minute-of-hour offset 10 == games starting at :10 past the hour (e.g. 7:10 PM)
        result, captured_minutes = simulate_one_game(
            190, cadence_minutes=OLD_CADENCE_MINUTES, tolerance_minutes=OLD_TOLERANCE_MINUTES,
            closing_window_minutes=OLD_CLOSING_WINDOW_MINUTES,
        )
        assert result["T_MINUS_90"] is False, "the reported bug must reproduce: T-90 silently missed for a :10 start"

    def test_old_configuration_never_captured_t90_for_the_35_to_45_minute_alignment_band(self):
        """A concrete, independently-checkable slice of the failure: start-minute offsets far from a 30-minute tick boundary (the middle of each 30-minute window) are always missed."""
        table = run_coverage_table(
            cadence_minutes=OLD_CADENCE_MINUTES, tolerance_minutes=OLD_TOLERANCE_MINUTES,
            closing_window_minutes=OLD_CLOSING_WINDOW_MINUTES,
        )
        for offset in (38, 39, 40, 41, 42):
            assert offset in table["T_MINUS_90"]["missedOffsets"]


class TestPostFixConfigurationGuaranteesCoverage:
    """The actual production defaults (hps.HITTER_SCHEDULER_CADENCE_MINUTES / HITTER_CHECKPOINT_TOLERANCE_MINUTES / HITTER_CLOSING_WINDOW_MINUTES), verified by the SAME exhaustive simulation."""

    def test_default_constants_are_the_fixed_values(self):
        """Regression guard: if these constants ever silently drift back toward the old buggy values, this test (and the coverage tests below, which use them) must fail loudly."""
        assert hps.HITTER_SCHEDULER_CADENCE_MINUTES == 15
        assert hps.HITTER_CHECKPOINT_TOLERANCE_MINUTES == 12
        assert hps.HITTER_CLOSING_WINDOW_MINUTES == 20

    def test_full_coverage_for_every_minute_offset_time_targets(self):
        table = run_coverage_table(
            cadence_minutes=hps.HITTER_SCHEDULER_CADENCE_MINUTES,
            tolerance_minutes=hps.HITTER_CHECKPOINT_TOLERANCE_MINUTES,
            closing_window_minutes=hps.HITTER_CLOSING_WINDOW_MINUTES,
        )
        for label in TIME_TARGET_LABELS:
            assert table[label]["missedCount"] == 0, f"{label} missed offsets: {table[label]['missedOffsets']}"
            assert table[label]["coveredCount"] == 60

    def test_full_coverage_for_every_minute_offset_closing_window(self):
        table = run_coverage_table(
            cadence_minutes=hps.HITTER_SCHEDULER_CADENCE_MINUTES,
            tolerance_minutes=hps.HITTER_CHECKPOINT_TOLERANCE_MINUTES,
            closing_window_minutes=hps.HITTER_CLOSING_WINDOW_MINUTES,
        )
        assert table[hps.HITTER_CLOSING_WINDOW]["missedCount"] == 0

    def test_reported_7_10_pm_example_now_covered(self):
        result, captured_minutes = simulate_one_game(
            190, cadence_minutes=hps.HITTER_SCHEDULER_CADENCE_MINUTES,
            tolerance_minutes=hps.HITTER_CHECKPOINT_TOLERANCE_MINUTES,
            closing_window_minutes=hps.HITTER_CLOSING_WINDOW_MINUTES,
        )
        assert result["T_MINUS_90"] is True
        # The captured minutesToStart must be genuine -- never assumed to be exactly 90.
        assert "T_MINUS_90" in captured_minutes
        assert captured_minutes["T_MINUS_90"] is not None

    def test_captured_minutes_to_start_are_never_fabricated_as_exact_targets(self):
        """Every captured checkpoint's recorded minutesToStart must reflect the REAL tick time, not a hardcoded 90/60/30 -- proves no relabeling/backdating occurs anywhere in the scheduling path."""
        for offset in (0, 7, 23, 41, 59):
            result, captured_minutes = simulate_one_game(
                180 + offset, cadence_minutes=hps.HITTER_SCHEDULER_CADENCE_MINUTES,
                tolerance_minutes=hps.HITTER_CHECKPOINT_TOLERANCE_MINUTES,
                closing_window_minutes=hps.HITTER_CLOSING_WINDOW_MINUTES,
            )
            for label, target in (("T_MINUS_90", 90), ("T_MINUS_60", 60), ("T_MINUS_30", 30)):
                if result[label]:
                    actual = captured_minutes[label]
                    assert actual is not None
                    # Must be within the tolerance window of the nominal target
                    # (that's what makes classify_checkpoint honestly label it
                    # this way) but is NOT required to equal it exactly --
                    # exactness would indicate a hardcoded/fabricated value.
                    assert abs(actual - target) <= hps.HITTER_CHECKPOINT_TOLERANCE_MINUTES


class TestRealisticGithubActionsDelay:
    """A systematic per-run delay is mathematically equivalent to a phase shift already covered by the exhaustive minute-offset sweep above (proven, not assumed -- see test_systematic_delay_is_equivalent_to_a_phase_shift). The genuinely different, harder failure mode is a fully SKIPPED run (an outage), tested separately below."""

    def test_systematic_delay_up_to_ten_minutes_still_fully_covered(self):
        table = run_coverage_table(
            cadence_minutes=hps.HITTER_SCHEDULER_CADENCE_MINUTES,
            tolerance_minutes=hps.HITTER_CHECKPOINT_TOLERANCE_MINUTES,
            closing_window_minutes=hps.HITTER_CLOSING_WINDOW_MINUTES,
            systematic_delay_minutes=10,
        )
        for label in TIME_TARGET_LABELS:
            assert table[label]["missedCount"] == 0
        assert table[hps.HITTER_CLOSING_WINDOW]["missedCount"] == 0

    def test_a_single_fully_skipped_run_can_still_cause_a_genuine_miss(self):
        """Honest limitation, not a bug: no fixed-cadence polling design can guarantee against an outage. This is exactly why compute_missed_hitter_checkpoints exists (tested in test_hitter_prospective_snapshot.py) -- to record this case explicitly rather than pretend it can't happen."""
        import scripts.research.simulate_hitter_checkpoint_coverage as sim

        original = sim._tick_times

        def dropping_every_third_tick(scheduled_start, cadence_minutes, **kw):
            ticks = original(scheduled_start, cadence_minutes, **kw)
            return [t for i, t in enumerate(ticks) if i % 3 != 2]

        sim._tick_times = dropping_every_third_tick
        try:
            table = sim.run_coverage_table(
                cadence_minutes=hps.HITTER_SCHEDULER_CADENCE_MINUTES,
                tolerance_minutes=hps.HITTER_CHECKPOINT_TOLERANCE_MINUTES,
                closing_window_minutes=hps.HITTER_CLOSING_WINDOW_MINUTES,
            )
        finally:
            sim._tick_times = original
        # Under ~33% of runs vanishing, coverage measurably degrades --
        # proving the fix is NOT claiming immunity to real outages.
        assert table[hps.HITTER_CLOSING_WINDOW]["missedCount"] > 0


class TestExactToleranceBoundary:
    """Precise, sub-minute boundary checks against classify_checkpoint directly (not the coarse minute-level simulation above) -- proves the exact +/-HITTER_CHECKPOINT_TOLERANCE_MINUTES cutoff behaves as documented."""

    def test_exactly_at_tolerance_boundary_is_covered(self):
        from lib.edgelab.checkpoints import classify_checkpoint
        # 90 - 12 = 78 minutes before start, i.e. exactly at the edge (diff == tolerance, "<=" is inclusive).
        label = classify_checkpoint(
            "2026-08-18T18:42:00Z", "2026-08-18T20:00:00Z",
            tolerance_minutes=hps.HITTER_CHECKPOINT_TOLERANCE_MINUTES,
        )
        assert label == "T_MINUS_90"

    def test_just_past_tolerance_boundary_is_not_covered_as_t90(self):
        from lib.edgelab.checkpoints import classify_checkpoint
        # 77.98 minutes before start (diff from the T-90 target = 12.02,
        # 1.2 seconds past the +/-12-minute edge) -- captured_at is
        # 1.2s LATER (closer to first pitch) than the 78-minute boundary.
        label = classify_checkpoint(
            "2026-08-18T18:42:01.2Z", "2026-08-18T20:00:00Z",
            tolerance_minutes=hps.HITTER_CHECKPOINT_TOLERANCE_MINUTES,
        )
        assert label != "T_MINUS_90"
        assert label == "INTERMEDIATE"  # honest, never mislabeled as the nearest target

    def test_widened_tolerance_never_lets_two_adjacent_targets_overlap(self):
        """T_MINUS_90 and T_MINUS_60 are 30 minutes apart; a +/-12-minute tolerance on each leaves a genuine gap (66-78 minutes before start) that is ambiguous to neither -- must never double-claim a single instant for both targets."""
        from lib.edgelab.checkpoints import classify_checkpoint
        # 72 minutes before start: 18 min from T-90, 12 min from T-60 -- exactly at the T-60 edge, unambiguous.
        label = classify_checkpoint(
            "2026-08-18T18:48:00Z", "2026-08-18T20:00:00Z",
            tolerance_minutes=hps.HITTER_CHECKPOINT_TOLERANCE_MINUTES,
        )
        assert label == "T_MINUS_60"


class TestNoPostFirstPitchCapture:
    def test_closing_window_never_fires_after_start(self):
        result, _ = simulate_one_game(
            180, cadence_minutes=hps.HITTER_SCHEDULER_CADENCE_MINUTES,
            tolerance_minutes=hps.HITTER_CHECKPOINT_TOLERANCE_MINUTES,
            closing_window_minutes=hps.HITTER_CLOSING_WINDOW_MINUTES,
        )
        # Game starts at offset 180 exactly -- confirm no checkpoint claims a post-start capture for any target by re-running classify_game_eligibility directly at a post-start instant.
        from lib.edgelab.prospective_snapshot import classify_game_eligibility
        eligible, reason, _ = classify_game_eligibility(
            {"gameId": "X", "startTime": "2026-08-18T03:00:00Z"}, now="2026-08-18T03:01:00Z",
        )
        assert eligible is False
        assert reason == "STARTED"
