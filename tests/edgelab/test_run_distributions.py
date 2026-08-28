#!/usr/bin/env python3
"""tests/edgelab/test_run_distributions.py"""
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_SCRIPTS_DIR = os.path.join(_ROOT, "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from lib.edgelab.backtest.run_distributions import (
    negative_binomial_pmf,
    independent_joint_pmf,
    bivariate_poisson_joint_pmf,
    home_win_and_push_prob,
    total_over_prob,
    margin_at_least_prob,
    team_total_over_prob,
    joint_pmf_sums_to_one,
    fit_overdispersion_dev_only,
    fit_correlation_dev_only,
    empirical_mean_variance,
    empirical_correlation,
    empirical_tail_frequency,
    poisson_implied_tail_frequency,
    MAX_RUNS,
)
from build_market_ledger import poisson_pmf, p_team_wins, p_over_total  # noqa: E402


class TestNegativeBinomialPmf:
    def test_sums_to_approximately_one(self):
        total = sum(negative_binomial_pmf(k, 4.5, 0.05) for k in range(60))
        assert abs(total - 1.0) < 1e-6

    def test_degenerates_to_poisson_at_zero_dispersion(self):
        for k in range(15):
            assert abs(negative_binomial_pmf(k, 4.5, 0.0) - poisson_pmf(k, 4.5)) < 1e-9

    def test_none_or_negative_dispersion_falls_back_to_poisson(self):
        assert negative_binomial_pmf(3, 4.5, None) == poisson_pmf(3, 4.5)
        assert negative_binomial_pmf(3, 4.5, -0.1) == poisson_pmf(3, 4.5)

    def test_mean_matches_input_mean(self):
        mean = 4.5
        computed_mean = sum(k * negative_binomial_pmf(k, mean, 0.05) for k in range(80))
        assert abs(computed_mean - mean) < 1e-4

    def test_variance_exceeds_poisson_variance_when_overdispersed(self):
        mean, dispersion = 4.5, 0.05
        computed_mean = sum(k * negative_binomial_pmf(k, mean, dispersion) for k in range(80))
        computed_var = sum((k - computed_mean) ** 2 * negative_binomial_pmf(k, mean, dispersion) for k in range(80))
        expected_var = mean + dispersion * mean ** 2
        assert abs(computed_var - expected_var) < 1e-3
        assert computed_var > mean  # strictly overdispersed relative to Poisson (var == mean)

    def test_zero_or_negative_mean_returns_zero(self):
        assert negative_binomial_pmf(3, 0, 0.05) == 0.0
        assert negative_binomial_pmf(3, -1, 0.05) == 0.0

    def test_negative_k_returns_zero(self):
        assert negative_binomial_pmf(-1, 4.5, 0.05) == 0.0


class TestIndependentJointPmfMatchesProductionExactly:
    """D0 (independent Poisson) reproduces scripts/build_market_ledger.py's
    own p_team_wins/p_over_total EXACTLY -- proves this module's generic
    joint-based derivation is a faithful, not merely similar, reuse."""

    def _poisson_joint(self, lam_h, lam_a):
        return independent_joint_pmf(lambda k: poisson_pmf(k, lam_h), lambda k: poisson_pmf(k, lam_a))

    def test_home_win_prob_matches_p_team_wins(self):
        lam_h, lam_a = 4.2, 3.8
        joint = self._poisson_joint(lam_h, lam_a)
        pw, pp = home_win_and_push_prob(joint)
        pw_ref, pp_ref = p_team_wins(lam_h, lam_a)
        assert abs(pw - pw_ref) < 1e-6
        assert abs(pp - pp_ref) < 1e-6

    def test_total_over_prob_matches_p_over_total(self):
        lam_h, lam_a = 4.2, 3.8
        joint = self._poisson_joint(lam_h, lam_a)
        result = total_over_prob(joint, 8.5)
        ref = p_over_total(lam_h + lam_a, 8.5)
        assert abs(result - ref) < 1e-6

    def test_joint_sums_to_one(self):
        joint = self._poisson_joint(4.2, 3.8)
        assert joint_pmf_sums_to_one(joint)


class TestBivariatePoissonMarginalsMatchD0Structurally:
    def test_marginal_x_equals_poisson_home_regardless_of_correlation(self):
        lam_h, lam_a = 4.2, 3.8
        for lam_c in (0.0, 0.5, 1.5, 3.0):
            joint = bivariate_poisson_joint_pmf(lam_h, lam_a, lam_c)
            for k in range(10):
                marginal = sum(joint(k, a) for a in range(MAX_RUNS + 1))
                assert abs(marginal - poisson_pmf(k, lam_h)) < 1e-6

    def test_marginal_y_equals_poisson_away_regardless_of_correlation(self):
        lam_h, lam_a = 4.2, 3.8
        for lam_c in (0.0, 0.5, 1.5, 3.0):
            joint = bivariate_poisson_joint_pmf(lam_h, lam_a, lam_c)
            for k in range(10):
                marginal = sum(joint(h, k) for h in range(MAX_RUNS + 1))
                assert abs(marginal - poisson_pmf(k, lam_a)) < 1e-6

    def test_joint_sums_to_one(self):
        joint = bivariate_poisson_joint_pmf(4.2, 3.8, 1.5)
        assert joint_pmf_sums_to_one(joint)

    def test_lambda_c_zero_reduces_to_independent_poisson(self):
        lam_h, lam_a = 4.2, 3.8
        bp_joint = bivariate_poisson_joint_pmf(lam_h, lam_a, 0.0)
        indep_joint = independent_joint_pmf(lambda k: poisson_pmf(k, lam_h), lambda k: poisson_pmf(k, lam_a))
        for h in range(8):
            for a in range(8):
                assert abs(bp_joint(h, a) - indep_joint(h, a)) < 1e-9

    def test_lambda_c_clamped_to_never_exceed_min_lambda(self):
        # lambda_c requested far larger than either mean -- must not
        # produce a negative Poisson rate internally (would raise/NaN).
        joint = bivariate_poisson_joint_pmf(2.0, 1.5, lambda_c=100.0)
        assert joint_pmf_sums_to_one(joint)

    def test_negative_lambda_c_clamped_to_zero(self):
        joint = bivariate_poisson_joint_pmf(4.2, 3.8, lambda_c=-5.0)
        indep_joint = independent_joint_pmf(lambda k: poisson_pmf(k, 4.2), lambda k: poisson_pmf(k, 3.8))
        assert abs(joint(3, 3) - indep_joint(3, 3)) < 1e-9

    def test_positive_correlation_raises_probability_of_matching_high_scores(self):
        # With shared environment (lambda_c > 0), both teams scoring
        # high together should be MORE likely than under independence.
        lam_h, lam_a = 4.0, 4.0
        indep = independent_joint_pmf(lambda k: poisson_pmf(k, lam_h), lambda k: poisson_pmf(k, lam_a))
        correlated = bivariate_poisson_joint_pmf(lam_h, lam_a, lambda_c=2.0)
        assert correlated(8, 8) > indep(8, 8)


class TestMarginAndTeamTotal:
    def test_margin_prob_symmetric_for_equal_means(self):
        lam = 4.0
        joint = independent_joint_pmf(lambda k: poisson_pmf(k, lam), lambda k: poisson_pmf(k, lam))
        p_home_by_2 = margin_at_least_prob(joint, 2)
        p_away_by_2 = margin_at_least_prob(joint, -2)  # home - away <= -2, i.e. away wins by 2+ is NOT what this computes
        # margin_at_least_prob(joint, -2) computes P(home-away >= -2), which
        # by symmetry of equal means equals P(home-away <= 2) = 1 - P(home-away>2)... instead
        # directly verify complement identity: P(margin>=2) should equal P(margin<=-2) for equal means.
        p_lose_by_2_or_more = sum(
            joint(h, a) for h in range(31) for a in range(31) if (h - a) <= -2
        )
        assert abs(p_home_by_2 - p_lose_by_2_or_more) < 1e-6

    def test_margin_at_least_zero_equals_win_or_push(self):
        lam_h, lam_a = 4.2, 3.8
        joint = independent_joint_pmf(lambda k: poisson_pmf(k, lam_h), lambda k: poisson_pmf(k, lam_a))
        pw, pp = home_win_and_push_prob(joint)
        assert abs(margin_at_least_prob(joint, 0) - (pw + pp)) < 1e-6

    def test_team_total_over_prob_matches_manual_poisson_tail_sum(self):
        lam = 4.5
        pmf = lambda k: poisson_pmf(k, lam)
        result = team_total_over_prob(pmf, 3.5)
        manual = sum(poisson_pmf(k, lam) for k in range(4, MAX_RUNS + 1))
        assert abs(result - manual) < 1e-9

    def test_team_total_complement_sums_correctly(self):
        lam = 4.5
        pmf = lambda k: poisson_pmf(k, lam)
        over = team_total_over_prob(pmf, 3.5)
        under_or_equal = sum(pmf(k) for k in range(4))
        # over + under_or_equal should sum to ~1 (truncation at MAX_RUNS aside)
        assert abs(over + under_or_equal - 1.0) < 1e-6


class TestFittingIsDevOnlyClosedForm:
    def test_overdispersion_zero_when_data_matches_poisson_exactly(self):
        # actual == lambda for every pair -> zero excess variance -> phi should be near/below 0, floored at 0
        pairs = [(4, 4.0), (5, 5.0), (3, 3.0)]
        assert fit_overdispersion_dev_only(pairs) == 0.0

    def test_overdispersion_positive_when_variance_exceeds_poisson(self):
        pairs = [(10, 4.5), (0, 4.5), (9, 4.5), (1, 4.5)]  # wildly overdispersed relative to a Poisson(4.5)
        phi = fit_overdispersion_dev_only(pairs)
        assert phi > 0.0

    def test_overdispersion_ignores_invalid_pairs(self):
        pairs = [(5, None), (None, 4.5), (5, 0), (5, -1)]
        assert fit_overdispersion_dev_only(pairs) == 0.0

    def test_overdispersion_empty_input_returns_zero(self):
        assert fit_overdispersion_dev_only([]) == 0.0

    def test_correlation_positive_when_residuals_move_together(self):
        # home and away residuals both positive/negative together -> positive covariance
        triples = [(6, 4.0, 6, 4.0), (2, 4.0, 2, 4.0), (6, 4.0, 6, 4.0), (2, 4.0, 2, 4.0)]
        lam_c = fit_correlation_dev_only(triples)
        assert lam_c > 0.0

    def test_correlation_zero_when_no_real_covariance(self):
        # residuals uncorrelated by construction (alternating signs, symmetric) -> near zero, floored
        triples = [(6, 4.0, 2, 4.0), (2, 4.0, 6, 4.0), (6, 4.0, 2, 4.0), (2, 4.0, 6, 4.0)]
        lam_c = fit_correlation_dev_only(triples)
        assert lam_c == 0.0  # negative covariance floored to 0

    def test_correlation_ignores_incomplete_tuples(self):
        triples = [(6, None, 6, 4.0), (None, 4.0, 6, 4.0)]
        assert fit_correlation_dev_only(triples) == 0.0

    def test_correlation_empty_input_returns_zero(self):
        assert fit_correlation_dev_only([]) == 0.0


class TestProbabilityMassConservation:
    def test_nb_joint_sums_to_one(self):
        lam_h, lam_a, dispersion = 4.2, 3.8, 0.05
        pmf_h = lambda k: negative_binomial_pmf(k, lam_h, dispersion)
        pmf_a = lambda k: negative_binomial_pmf(k, lam_a, dispersion)
        joint = independent_joint_pmf(pmf_h, pmf_a)
        assert joint_pmf_sums_to_one(joint)

    def test_moneyline_complement_consistency_across_all_candidates(self):
        lam_h, lam_a = 4.2, 3.8
        candidates = {
            "D0": independent_joint_pmf(lambda k: poisson_pmf(k, lam_h), lambda k: poisson_pmf(k, lam_a)),
            "D1": independent_joint_pmf(
                lambda k: negative_binomial_pmf(k, lam_h, 0.05), lambda k: negative_binomial_pmf(k, lam_a, 0.05)
            ),
            "D2": bivariate_poisson_joint_pmf(lam_h, lam_a, 1.0),
        }
        for name, joint in candidates.items():
            pw, pp = home_win_and_push_prob(joint)
            p_away = sum(
                joint(h, a) for h in range(MAX_RUNS + 1) for a in range(MAX_RUNS + 1) if a > h
            )
            assert abs((pw + pp + p_away) - 1.0) < 1e-6, name

    def test_total_over_under_complement_consistency_across_all_candidates(self):
        lam_h, lam_a = 4.2, 3.8
        line = 8.5
        candidates = {
            "D0": independent_joint_pmf(lambda k: poisson_pmf(k, lam_h), lambda k: poisson_pmf(k, lam_a)),
            "D1": independent_joint_pmf(
                lambda k: negative_binomial_pmf(k, lam_h, 0.05), lambda k: negative_binomial_pmf(k, lam_a, 0.05)
            ),
            "D2": bivariate_poisson_joint_pmf(lam_h, lam_a, 1.0),
        }
        for name, joint in candidates.items():
            over = total_over_prob(joint, line)
            under = sum(
                joint(h, a) for h in range(MAX_RUNS + 1) for a in range(MAX_RUNS + 1) if (h + a) < line
            )
            assert abs((over + under) - 1.0) < 1e-6, name


class TestDeterminism:
    def test_repeated_calls_produce_identical_results(self):
        joint = bivariate_poisson_joint_pmf(4.2, 3.8, 1.5)
        first = [joint(h, a) for h in range(10) for a in range(10)]
        second = [joint(h, a) for h in range(10) for a in range(10)]
        assert first == second


class TestEmpiricalMeanVariance:
    def test_known_values(self):
        mean, var = empirical_mean_variance([2, 4, 4, 4, 5, 5, 7, 9])
        assert mean == 5.0
        assert var == 4.0  # population variance of this classic textbook example

    def test_ignores_none(self):
        mean, _ = empirical_mean_variance([4, None, 6])
        assert mean == 5.0

    def test_empty_returns_none(self):
        assert empirical_mean_variance([]) == (None, None)
        assert empirical_mean_variance([None, None]) == (None, None)


class TestEmpiricalCorrelation:
    def test_perfect_positive_correlation(self):
        pairs = [(1, 1), (2, 2), (3, 3), (4, 4)]
        assert empirical_correlation(pairs) == 1.0

    def test_perfect_negative_correlation(self):
        pairs = [(1, 4), (2, 3), (3, 2), (4, 1)]
        assert empirical_correlation(pairs) == -1.0

    def test_no_correlation_when_one_variable_constant(self):
        pairs = [(1, 5), (2, 5), (3, 5)]
        assert empirical_correlation(pairs) is None  # zero variance in y -- undefined, not 0.0

    def test_ignores_incomplete_pairs(self):
        pairs = [(1, 1), (None, 2), (3, None), (2, 2)]
        # only (1,1) and (2,2) are valid -- n=2, perfectly correlated
        assert empirical_correlation(pairs) == 1.0

    def test_fewer_than_two_valid_pairs_returns_none(self):
        assert empirical_correlation([(1, 1)]) is None
        assert empirical_correlation([]) is None


class TestTailFrequency:
    def test_empirical_at_least(self):
        values = [0, 1, 5, 7, 10, 3]
        assert empirical_tail_frequency(values, 5, mode="at_least") == round(3 / 6, 4)  # 5,7,10

    def test_empirical_exactly(self):
        values = [0, 0, 1, 5]
        assert empirical_tail_frequency(values, 0, mode="exactly") == 0.5

    def test_ignores_none(self):
        values = [0, None, 5]
        assert empirical_tail_frequency(values, 5, mode="at_least") == 0.5

    def test_empty_returns_none(self):
        assert empirical_tail_frequency([], 5) is None

    def test_unknown_mode_raises(self):
        import pytest as _pytest
        with _pytest.raises(ValueError):
            empirical_tail_frequency([1, 2], 1, mode="bogus")

    def test_poisson_implied_matches_manual_poisson_sum(self):
        from build_market_ledger import poisson_pmf
        lambdas = [4.0, 5.0]
        result = poisson_implied_tail_frequency(lambdas, 5, mode="at_least")
        manual = sum(sum(poisson_pmf(k, lam) for k in range(5, MAX_RUNS + 1)) for lam in lambdas) / 2
        assert abs(result - manual) < 1e-3  # result is rounded to 4dp by the function under test

    def test_poisson_implied_ignores_nonpositive_lambdas(self):
        result = poisson_implied_tail_frequency([4.0, 0, None, -1], 5, mode="at_least")
        result_clean = poisson_implied_tail_frequency([4.0], 5, mode="at_least")
        assert result == result_clean

    def test_poisson_implied_empty_returns_none(self):
        assert poisson_implied_tail_frequency([], 5) is None
        assert poisson_implied_tail_frequency([0, None], 5) is None

    def test_poisson_implied_unknown_mode_raises(self):
        import pytest as _pytest
        with _pytest.raises(ValueError):
            poisson_implied_tail_frequency([4.0], 5, mode="bogus")
