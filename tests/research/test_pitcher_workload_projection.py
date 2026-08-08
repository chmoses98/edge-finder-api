#!/usr/bin/env python3
"""
tests/research/test_pitcher_workload_projection.py
=======================================================
Regression coverage for lib/research/pitcher_workload_projection.py --
the joint starter workload/K/outs distribution model.

All inputs here are synthetic (hand-constructed pitcher-profile
parameters), never real historical data -- this suite proves the MATH
behaves coherently (monotonic responses to worsening workload risk,
distribution-based threshold probabilities, explicit missing-data
states); it is not a claim of calibration against real outcomes (the
task explicitly warns against overfitting to the Aug 3-6 postmortems --
those are used here only as documented failure-mode context, never as
target numbers to hit).
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lib.research.pitcher_workload_projection import (
    DEFAULT_MAX_OUTS,
    binomial_pmf,
    expected_batters_faced,
    expected_outs,
    p_outs_at_least,
    p_strikeouts_at_least,
    project_pitcher_workload,
    survival_curve,
)


def _baseline(**overrides):
    kwargs = dict(avg_ip_per_start=6.0, k_pct=22.0, bb_pct=8.5)
    kwargs.update(overrides)
    return project_pitcher_workload(**kwargs)


# ── Missing-data / explicit unavailable states (Requirement 6) ──────────

class TestMissingWorkloadData:
    def test_missing_avg_ip_per_start_is_insufficient_not_a_guess(self):
        result = project_pitcher_workload(avg_ip_per_start=None, k_pct=22.0)
        assert result["insufficientWorkloadData"] is True
        assert result["perOutSurvival"] is None
        assert result["expectedOuts"] is None
        assert result["expectedStrikeouts"] is None
        assert result["pOutsAtLeast"](19) is None
        assert result["pStrikeoutsAtLeast"](6) is None

    def test_missing_k_pct_leaves_strikeouts_null_but_outs_still_computed(self):
        """A pitcher can have a real workload projection (outs) even when kPct is unavailable -- K-specific outputs alone go null, never the whole result."""
        result = _baseline(k_pct=None)
        assert result["insufficientWorkloadData"] is False
        assert result["expectedOuts"] is not None
        assert result["expectedStrikeouts"] is None
        assert result["pStrikeoutsAtLeast"](6) is None
        assert result["pOutsAtLeast"](19) is not None

    def test_optional_inputs_report_their_own_availability_flags(self):
        _, diagnostics = survival_curve(6.0, bb_pct=8.5)
        assert diagnostics["ttoDataAvailable"] is False
        assert diagnostics["recentWorkloadDataAvailable"] is False
        assert diagnostics["opponentStrengthDataAvailable"] is False

        _, diagnostics = survival_curve(6.0, bb_pct=8.5, tto_split=0.8, tto_risk=True,
                                         recent_workload_restricted=True, opponent_wrc_plus=110)
        assert diagnostics["ttoDataAvailable"] is True
        assert diagnostics["recentWorkloadDataAvailable"] is True
        assert diagnostics["opponentStrengthDataAvailable"] is True

    def test_never_fabricates_tto_penalty_when_tto_risk_flag_missing_even_with_a_split_value(self):
        """ttoSplit alone (without an explicit tto_risk verdict) must never silently trigger the penalty -- both must be supplied."""
        with_split_only, _ = survival_curve(6.0, bb_pct=8.5, tto_split=1.2, tto_risk=None)
        neutral, _ = survival_curve(6.0, bb_pct=8.5)
        assert with_split_only == neutral


# ── Requirement 7-i: reduced probability when workload/hook risk worsens ──

class TestWorseningWorkloadReducesProbability:
    def test_tto_risk_reduces_outs_threshold_probability(self):
        baseline = _baseline()
        worse = _baseline(tto_split=1.5, tto_risk=True)
        assert worse["pOutsAtLeast"](19) < baseline["pOutsAtLeast"](19)

    def test_opener_status_drastically_reduces_outs_threshold_probability(self):
        baseline = _baseline()
        opener = _baseline(opener=True)
        assert opener["pOutsAtLeast"](19) < baseline["pOutsAtLeast"](19)
        assert opener["pOutsAtLeast"](19) < 0.01

    def test_recent_workload_restriction_reduces_outs_threshold_probability(self):
        baseline = _baseline()
        restricted = _baseline(recent_workload_restricted=True)
        assert restricted["pOutsAtLeast"](19) < baseline["pOutsAtLeast"](19)

    def test_tougher_opponent_offense_reduces_outs_threshold_probability(self):
        baseline = _baseline()
        tough_opponent = _baseline(opponent_wrc_plus=140)
        assert tough_opponent["pOutsAtLeast"](19) < baseline["pOutsAtLeast"](19)

    def test_easier_opponent_offense_increases_outs_threshold_probability(self):
        baseline = _baseline()
        easy_opponent = _baseline(opponent_wrc_plus=75)
        assert easy_opponent["pOutsAtLeast"](19) > baseline["pOutsAtLeast"](19)


# ── Requirement 7-ii: 19+ outs requires a genuine 6 1/3-inning probability ──

class TestOutsThresholdIsADistributionNotAPointComparison:
    def test_nineteen_plus_outs_is_a_real_probability_strictly_between_zero_and_one(self):
        result = _baseline(avg_ip_per_start=19 / 3)  # a pitcher who averages exactly 6 1/3 IP
        p19 = result["pOutsAtLeast"](19)
        assert 0.0 < p19 < 1.0

    def test_nineteen_outs_threshold_equals_six_and_a_third_innings_exactly(self):
        """19 outs / 3 outs-per-inning == 6.333... innings -- the exact boundary Requirement 2 names, not an approximation."""
        assert 19 / 3 == pytest.approx(6.3333333, rel=1e-6)

    def test_probability_of_reaching_nineteen_outs_increases_monotonically_with_workload(self):
        low = _baseline(avg_ip_per_start=5.0)["pOutsAtLeast"](19)
        mid = _baseline(avg_ip_per_start=6.0)["pOutsAtLeast"](19)
        target = _baseline(avg_ip_per_start=19 / 3)["pOutsAtLeast"](19)
        high = _baseline(avg_ip_per_start=7.0)["pOutsAtLeast"](19)
        assert low < mid < target < high

    def test_p_outs_at_least_is_a_genuine_survival_function_not_a_hard_cutoff(self):
        """A distribution-based model must show SOME mass below AND above every real threshold -- never a deterministic 0/1 step at the mean."""
        result = _baseline(avg_ip_per_start=6.0)
        assert 0.0 < result["pOutsAtLeast"](18) < 1.0
        assert 0.0 < result["pOutsAtLeast"](19) < 1.0
        assert 0.0 < result["pOutsAtLeast"](20) < 1.0
        # Monotonic non-increasing in the threshold, as any survival function must be.
        assert result["pOutsAtLeast"](17) >= result["pOutsAtLeast"](18) >= result["pOutsAtLeast"](19) >= result["pOutsAtLeast"](20)

    def test_p_outs_at_least_zero_is_certain(self):
        result = _baseline()
        assert result["pOutsAtLeast"](0) == 1.0


# ── Requirement 7-iii: walk/efficiency effects on the K ceiling ─────────

class TestWalkRateShapesTheStrikeoutCeiling:
    def test_higher_walk_rate_lowers_expected_strikeouts(self):
        efficient = _baseline(bb_pct=5.0)
        wild = _baseline(bb_pct=15.0)
        assert wild["expectedStrikeouts"] < efficient["expectedStrikeouts"]

    def test_higher_walk_rate_lowers_strikeout_threshold_probability(self):
        efficient = _baseline(bb_pct=5.0)
        wild = _baseline(bb_pct=15.0)
        assert wild["pStrikeoutsAtLeast"](7) < efficient["pStrikeoutsAtLeast"](7)

    def test_higher_walk_rate_also_shortens_the_outs_projection(self):
        """The K-ceiling effect flows through the SAME shortened outing -- not an independently-invented penalty."""
        efficient = _baseline(bb_pct=5.0)
        wild = _baseline(bb_pct=15.0)
        assert wild["expectedOuts"] < efficient["expectedOuts"]


# ── Requirement 7-iv: opponent K-rate effects ────────────────────────────

class TestOpponentStrikeoutTendency:
    def test_higher_opponent_k_rate_raises_strikeout_probability(self):
        vs_average = _baseline()
        vs_high_k_lineup = _baseline(opponent_k_pct=28.0)
        assert vs_high_k_lineup["pStrikeoutsAtLeast"](6) > vs_average["pStrikeoutsAtLeast"](6)

    def test_lower_opponent_k_rate_reduces_strikeout_probability(self):
        vs_average = _baseline()
        vs_contact_lineup = _baseline(opponent_k_pct=16.0)
        assert vs_contact_lineup["pStrikeoutsAtLeast"](6) < vs_average["pStrikeoutsAtLeast"](6)

    def test_opponent_k_rate_never_affects_the_outs_projection(self):
        """Opponent contact tendency is a strikeout-specific signal -- it must never leak into how long the pitcher is projected to stay in the game."""
        vs_average = _baseline()
        vs_high_k_lineup = _baseline(opponent_k_pct=28.0)
        assert vs_high_k_lineup["expectedOuts"] == vs_average["expectedOuts"]

    def test_opponent_k_rate_omitted_leaves_k_rate_unadjusted(self):
        neutral = p_strikeouts_at_least(20, 22.0, 6)
        explicit_league_average = p_strikeouts_at_least(20, 22.0, 6, opponent_k_pct=22.0)
        assert neutral == pytest.approx(explicit_league_average, rel=1e-9)


# ── Requirement 7-v: early-exit tail risk ────────────────────────────────

class TestEarlyExitTailRisk:
    def test_meaningful_probability_mass_below_three_innings_even_at_baseline(self):
        """A distribution-based model must carry real (non-vanishing) probability of a short outing even for an average workload projection -- a point-estimate model would implicitly treat this as ~0."""
        result = _baseline()
        p_early_exit = 1.0 - result["pOutsAtLeast"](9)  # fewer than 9 outs == didn't complete 3 innings
        assert p_early_exit > 0.05

    def test_early_exit_risk_grows_with_factors_that_apply_from_the_first_out(self):
        """Opener status and a tougher opponent offense both shape the WHOLE start, so they raise the risk of not even completing 3 innings."""
        baseline = 1.0 - _baseline()["pOutsAtLeast"](9)
        with_tough_opponent = 1.0 - _baseline(opponent_wrc_plus=140)["pOutsAtLeast"](9)
        with_opener = 1.0 - _baseline(opener=True)["pOutsAtLeast"](9)
        assert baseline < with_tough_opponent
        assert baseline < with_opener

    def test_third_time_through_risk_grows_late_tail_exit_probability_specifically(self):
        """TTO risk is a late-game phenomenon (third time through the order) -- it must raise the probability of NOT reaching 7 innings (21 outs) without affecting the very-early (sub-3-inning) tail at all, since a pitcher doesn't face TTO risk until he's deep into the start."""
        baseline_early = 1.0 - _baseline()["pOutsAtLeast"](9)
        with_tto_early = 1.0 - _baseline(tto_split=1.5, tto_risk=True)["pOutsAtLeast"](9)
        assert with_tto_early == pytest.approx(baseline_early, rel=1e-12)

        baseline_late = 1.0 - _baseline()["pOutsAtLeast"](21)
        with_tto_late = 1.0 - _baseline(tto_split=1.5, tto_risk=True)["pOutsAtLeast"](21)
        assert with_tto_late > baseline_late


# ── Requirement 7-vi: coherent K/outs response to the SAME workload change ──

class TestCoherentJointResponse:
    def test_tto_risk_reduces_both_outs_and_strikeout_probabilities_together(self):
        baseline = _baseline()
        worse = _baseline(tto_split=1.5, tto_risk=True)
        assert worse["pOutsAtLeast"](19) < baseline["pOutsAtLeast"](19)
        assert worse["pStrikeoutsAtLeast"](6) < baseline["pStrikeoutsAtLeast"](6)

    def test_tougher_opponent_offense_reduces_both_outs_and_strikeout_probabilities_together(self):
        baseline = _baseline()
        worse = _baseline(opponent_wrc_plus=140)
        assert worse["pOutsAtLeast"](19) < baseline["pOutsAtLeast"](19)
        assert worse["pStrikeoutsAtLeast"](6) < baseline["pStrikeoutsAtLeast"](6)

    def test_opener_status_reduces_both_outs_and_strikeout_probabilities_together(self):
        baseline = _baseline()
        worse = _baseline(opener=True)
        assert worse["pOutsAtLeast"](19) < baseline["pOutsAtLeast"](19)
        assert worse["pStrikeoutsAtLeast"](6) < baseline["pStrikeoutsAtLeast"](6)

    def test_recent_workload_restriction_reduces_both_outs_and_strikeout_probabilities_together(self):
        baseline = _baseline()
        worse = _baseline(recent_workload_restricted=True)
        assert worse["pOutsAtLeast"](19) < baseline["pOutsAtLeast"](19)
        assert worse["pStrikeoutsAtLeast"](6) < baseline["pStrikeoutsAtLeast"](6)

    def test_outs_and_strikeouts_share_the_same_expected_batters_faced(self):
        """Both stats are derived from the identical expectedBattersFaced figure -- proof they're jointly modeled, not two independent point estimates."""
        result = _baseline()
        recomputed_bf = expected_batters_faced(result["expectedOuts"], 8.5)
        assert result["expectedBattersFaced"] == pytest.approx(recomputed_bf, rel=1e-4)
        recomputed_k = recomputed_bf * (22.0 / 100.0)
        assert result["expectedStrikeouts"] == pytest.approx(recomputed_k, rel=1e-3)


# ── Pure-function unit coverage for the building blocks ─────────────────

class TestSurvivalCurveBuildingBlocks:
    def test_curve_length_matches_max_outs(self):
        curve, _ = survival_curve(6.0, bb_pct=8.5, max_outs=10)
        assert len(curve) == 10

    def test_default_max_outs_is_generous(self):
        assert DEFAULT_MAX_OUTS >= 30  # at least 10 innings of support

    def test_p_outs_at_least_matches_manual_product(self):
        curve, _ = survival_curve(6.0, bb_pct=8.5)
        manual = 1.0
        for s in curve[:5]:
            manual *= s
        assert p_outs_at_least(curve, 5) == pytest.approx(manual, rel=1e-12)

    def test_expected_outs_matches_target_when_curve_is_flat(self):
        """A pure constant-survival curve (no TTO/opener/workload adjustments) must reproduce the classic geometric-mean identity exactly, once the tail-correction accounts for the truncated support."""
        curve, diagnostics = survival_curve(6.0, bb_pct=8.5)
        assert expected_outs(curve) == pytest.approx(diagnostics["targetOuts"], rel=1e-6)


class TestBinomialPmfAndStrikeoutTail:
    def test_binomial_pmf_matches_hand_computed_small_case(self):
        # P(2 successes in 3 trials, p=0.5) = C(3,2) * 0.5^2 * 0.5^1 = 3 * 0.125 = 0.375
        assert binomial_pmf(2, 3, 0.5) == pytest.approx(0.375, rel=1e-12)

    def test_binomial_pmf_out_of_range_is_zero(self):
        assert binomial_pmf(-1, 10, 0.3) == 0.0
        assert binomial_pmf(11, 10, 0.3) == 0.0

    def test_strikeout_tail_sums_to_one_across_full_support(self):
        total = sum(binomial_pmf(k, 20, 0.22) for k in range(0, 21))
        assert total == pytest.approx(1.0, rel=1e-9)

    def test_p_strikeouts_at_least_zero_is_certain(self):
        assert p_strikeouts_at_least(20, 22.0, 0) == 1.0

    def test_p_strikeouts_at_least_none_when_inputs_missing(self):
        assert p_strikeouts_at_least(None, 22.0, 6) is None
        assert p_strikeouts_at_least(20, None, 6) is None
