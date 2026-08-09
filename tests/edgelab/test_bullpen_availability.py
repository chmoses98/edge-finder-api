#!/usr/bin/env python3
"""
tests/edgelab/test_bullpen_availability.py
================================================
Coverage for lib/edgelab/bullpen_availability.py -- the pure,
conservative bullpen-availability (recent workload) adjustment built on
top of PR #51's bullpen.recentUsage block.

Scope: this module never reads season-long bullpen quality (xFIP) --
only the multiplier it produces to be applied to that quality elsewhere.
These tests cover the multiplier in isolation: missing-data safety
(never a "rested" bonus), each of the four required workload signals
individually, combined-signal capping, and determinism.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.bullpen_availability import (
    MAX_TOTAL_PENALTY,
    compute_bullpen_workload_adjustment,
)


def _recent_usage(**overrides):
    base = {
        "dataAvailable": True,
        "unavailableReason": None,
        "asOfDate": "2026-08-07",
        "gamesConsidered": 2,
        "relieversUsedLastGame": [],
        "backToBackRelievers": [],
        "recentPitchCounts": [],
        "highLeverageRecentUsage": [],
        "handednessMix": {"L": 0, "R": 0, "unknown": 0},
        "teamPitchCountLastGame": 50,
        "teamPitchCountWindow": 100,
    }
    base.update(overrides)
    return base


class TestMissingDataNeverGuessesRested:

    def test_none_recent_usage_yields_no_adjustment(self):
        result = compute_bullpen_workload_adjustment(None)
        assert result["multiplier"] == 1.0
        assert result["adjustmentApplied"] is False
        assert result["dataAvailable"] is False
        assert result["unavailableReason"] == "no_recent_usage_data"

    def test_empty_dict_yields_no_adjustment(self):
        result = compute_bullpen_workload_adjustment({})
        assert result["multiplier"] == 1.0
        assert result["dataAvailable"] is False

    def test_explicit_data_unavailable_yields_no_adjustment_not_a_bonus(self):
        result = compute_bullpen_workload_adjustment({
            "dataAvailable": False,
            "unavailableReason": "no_completed_games_in_window",
        })
        assert result["multiplier"] == 1.0
        assert result["adjustmentApplied"] is False
        assert result["dataAvailable"] is False
        assert result["unavailableReason"] == "no_completed_games_in_window"

    def test_unavailable_reason_is_never_fabricated_when_absent(self):
        """dataAvailable=False with no unavailableReason string still
        surfaces SOME reason (never silently None), but never claims the
        bullpen is rested."""
        result = compute_bullpen_workload_adjustment({"dataAvailable": False})
        assert result["multiplier"] == 1.0
        assert result["unavailableReason"] is not None


class TestRestedBullpenGetsNoFabricatedBonus:

    def test_zero_workload_data_available_stays_at_neutral_multiplier(self):
        usage = _recent_usage(
            teamPitchCountLastGame=0, teamPitchCountWindow=0,
            gamesConsidered=2,
        )
        result = compute_bullpen_workload_adjustment(usage)
        assert result["multiplier"] == 1.0
        assert result["adjustmentApplied"] is False

    def test_below_baseline_workload_never_goes_below_1(self):
        """A bullpen that threw noticeably FEWER pitches than the generic
        baseline must not receive a multiplier below 1.0 -- there is no
        'bonus' path in this module, only a 'penalty' path."""
        usage = _recent_usage(teamPitchCountLastGame=20, teamPitchCountWindow=40)
        result = compute_bullpen_workload_adjustment(usage)
        assert result["multiplier"] == 1.0

    def test_multiplier_is_never_below_one_across_many_light_usage_profiles(self):
        for last_game in (0, 10, 20, 30):
            for window in (0, 20, 40, 60):
                usage = _recent_usage(teamPitchCountLastGame=last_game, teamPitchCountWindow=window)
                result = compute_bullpen_workload_adjustment(usage)
                assert result["multiplier"] >= 1.0


class TestBackToBackRelievers:

    def test_back_to_back_relievers_increase_multiplier(self):
        rested = compute_bullpen_workload_adjustment(_recent_usage(backToBackRelievers=[]))
        taxed = compute_bullpen_workload_adjustment(_recent_usage(
            backToBackRelievers=[{"playerId": "1", "name": "A"}, {"playerId": "2", "name": "B"}],
        ))
        assert taxed["multiplier"] > rested["multiplier"]
        assert taxed["components"]["backToBackCount"] == 2
        assert taxed["components"]["backToBackPenalty"] > 0

    def test_back_to_back_penalty_is_capped(self):
        many = compute_bullpen_workload_adjustment(_recent_usage(
            backToBackRelievers=[{"playerId": str(i), "name": f"P{i}"} for i in range(10)],
        ))
        from lib.edgelab.bullpen_availability import MAX_BACK_TO_BACK_PENALTY
        assert many["components"]["backToBackPenalty"] <= MAX_BACK_TO_BACK_PENALTY + 1e-9


class TestRecentPitchWorkload:

    def test_heavily_used_individual_relievers_increase_multiplier(self):
        rested = compute_bullpen_workload_adjustment(_recent_usage(recentPitchCounts=[
            {"playerId": "1", "name": "A", "totalPitches": 10, "appearances": 1},
        ]))
        taxed = compute_bullpen_workload_adjustment(_recent_usage(recentPitchCounts=[
            {"playerId": "1", "name": "A", "totalPitches": 45, "appearances": 2},
            {"playerId": "2", "name": "B", "totalPitches": 40, "appearances": 2},
        ]))
        assert taxed["multiplier"] > rested["multiplier"]
        assert taxed["components"]["heavilyUsedRelieverCount"] == 2

    def test_low_pitch_counts_below_threshold_do_not_penalize(self):
        usage = _recent_usage(recentPitchCounts=[
            {"playerId": "1", "name": "A", "totalPitches": 15, "appearances": 1},
            {"playerId": "2", "name": "B", "totalPitches": 20, "appearances": 1},
        ])
        result = compute_bullpen_workload_adjustment(usage)
        assert result["components"]["heavilyUsedRelieverCount"] == 0
        assert result["components"]["recentPitchWorkloadPenalty"] == 0.0


class TestHighLeverageRecentUsage:

    def test_taxed_high_leverage_arm_increases_multiplier(self):
        rested = compute_bullpen_workload_adjustment(_recent_usage(highLeverageRecentUsage=[]))
        taxed = compute_bullpen_workload_adjustment(_recent_usage(highLeverageRecentUsage=[
            {"playerId": "1", "name": "Closer", "saves": 1, "holds": 0, "totalPitches": 28},
        ]))
        assert taxed["multiplier"] > rested["multiplier"]
        assert taxed["components"]["taxedHighLeverageArmCount"] == 1

    def test_high_leverage_arm_with_light_workload_does_not_penalize(self):
        usage = _recent_usage(highLeverageRecentUsage=[
            {"playerId": "1", "name": "Closer", "saves": 1, "holds": 0, "totalPitches": 12},
        ])
        result = compute_bullpen_workload_adjustment(usage)
        assert result["components"]["taxedHighLeverageArmCount"] == 0
        assert result["multiplier"] == 1.0


class TestOverallRecentWorkload:

    def test_heavy_team_window_workload_increases_multiplier(self):
        rested = compute_bullpen_workload_adjustment(_recent_usage(
            teamPitchCountWindow=100, teamPitchCountLastGame=50, gamesConsidered=2,
        ))
        heavy = compute_bullpen_workload_adjustment(_recent_usage(
            teamPitchCountWindow=220, teamPitchCountLastGame=90, gamesConsidered=2,
        ))
        assert heavy["multiplier"] > rested["multiplier"]
        assert heavy["components"]["teamWorkloadRatio"] > 1.0

    def test_single_heavy_last_game_alone_can_trigger_penalty(self):
        """Even if the two-game window average looks normal, an
        extremely heavy single most-recent game should still register."""
        usage = _recent_usage(teamPitchCountWindow=110, teamPitchCountLastGame=95, gamesConsidered=2)
        result = compute_bullpen_workload_adjustment(usage)
        assert result["components"]["overallWorkloadPenalty"] > 0


class TestCombinedSignalsAreCapped:

    def test_worst_case_profile_never_exceeds_max_total_penalty(self):
        worst = _recent_usage(
            backToBackRelievers=[{"playerId": str(i), "name": f"B{i}"} for i in range(5)],
            recentPitchCounts=[{"playerId": str(i), "name": f"P{i}", "totalPitches": 60, "appearances": 3} for i in range(5)],
            highLeverageRecentUsage=[{"playerId": str(i), "name": f"H{i}", "saves": 1, "holds": 0, "totalPitches": 40} for i in range(3)],
            teamPitchCountWindow=400, teamPitchCountLastGame=200, gamesConsidered=2,
        )
        result = compute_bullpen_workload_adjustment(worst)
        assert result["multiplier"] <= round(1.0 + MAX_TOTAL_PENALTY, 4) + 1e-9

    def test_worst_case_profile_multiplier_is_meaningfully_above_neutral(self):
        worst = _recent_usage(
            backToBackRelievers=[{"playerId": str(i), "name": f"B{i}"} for i in range(5)],
            recentPitchCounts=[{"playerId": str(i), "name": f"P{i}", "totalPitches": 60, "appearances": 3} for i in range(5)],
            highLeverageRecentUsage=[{"playerId": str(i), "name": f"H{i}", "saves": 1, "holds": 0, "totalPitches": 40} for i in range(3)],
            teamPitchCountWindow=400, teamPitchCountLastGame=200, gamesConsidered=2,
        )
        result = compute_bullpen_workload_adjustment(worst)
        assert result["adjustmentApplied"] is True
        assert result["multiplier"] > 1.05


class TestDeterminismAndPurity:

    def test_same_input_yields_same_output(self):
        usage = _recent_usage(backToBackRelievers=[{"playerId": "1", "name": "A"}])
        r1 = compute_bullpen_workload_adjustment(usage)
        r2 = compute_bullpen_workload_adjustment(usage)
        assert r1 == r2

    def test_does_not_mutate_input(self):
        import copy
        usage = _recent_usage(backToBackRelievers=[{"playerId": "1", "name": "A"}])
        before = copy.deepcopy(usage)
        compute_bullpen_workload_adjustment(usage)
        assert usage == before
