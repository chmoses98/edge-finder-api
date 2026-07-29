#!/usr/bin/env python3
"""
tests/research/test_three_way_projection.py
================================================
Model Performance Phase 1 -- required synthetic test matrix (mission
Part 5) for lib/research/three_way_projection.py.

All inputs here are synthetic (hand-constructed run-projection pairs),
never real historical data, never real Kalshi prices -- this test
suite proves the MATH is correct and side-effect-free; it does not
prove calibration or historical accuracy (out of scope for Phase 1,
explicitly deferred to a backtest phase per the mission).
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lib.research.three_way_projection import (
    poisson_pmf,
    three_way_result_probs,
    three_way_result_probs_for_horizon,
    assert_probabilities_valid,
    HORIZON_INNINGS,
    canonical_inning_result_probs,
    legacy_conditional_probs,
    f5_migration_safe_output,
    CANONICAL_METHOD,
)


class TestCanonicalInningResultProbs:
    """Model Performance Phase 2A Part 7 -- canonical schema wrapper."""

    @pytest.mark.parametrize("horizon", ["F3", "F5", "F7", "full_game"])
    def test_schema_fields_present(self, horizon):
        r = canonical_inning_result_probs(4.5, 4.3, horizon)
        for key in ("awayLeadProb", "tieProb", "homeLeadProb", "probabilitySum",
                    "truncationMass", "method", "horizon", "horizonInnings"):
            assert key in r

    @pytest.mark.parametrize("horizon", ["F3", "F5", "F7", "full_game"])
    def test_probability_sum_equals_components(self, horizon):
        r = canonical_inning_result_probs(4.5, 4.3, horizon)
        assert r["probabilitySum"] == pytest.approx(
            r["awayLeadProb"] + r["tieProb"] + r["homeLeadProb"]
        )

    def test_probability_sum_within_tolerance_of_one(self):
        r = canonical_inning_result_probs(4.5, 4.3, "F5")
        assert abs(r["probabilitySum"] - 1.0) < 1e-6

    def test_method_is_documented_constant(self):
        r = canonical_inning_result_probs(4.5, 4.3, "F5")
        assert r["method"] == CANONICAL_METHOD

    def test_no_renormalization_tie_retained(self):
        """A high-tie-probability environment must show tieProb > 0 and
        awayLeadProb+homeLeadProb < 1 -- never renormalized away."""
        r = canonical_inning_result_probs(3.0, 3.0, "F5")
        assert r["tieProb"] > 0.1
        assert r["awayLeadProb"] + r["homeLeadProb"] < 1.0

    def test_deterministic(self):
        r1 = canonical_inning_result_probs(4.5, 4.3, "F5")
        r2 = canonical_inning_result_probs(4.5, 4.3, "F5")
        assert r1 == r2


class TestLegacyConditionalProbs:
    """Model Performance Phase 2A Part 8 -- legacy migration-safety formula."""

    def test_matches_documented_formula(self):
        canonical = canonical_inning_result_probs(4.5, 4.3, "F5")
        legacy = legacy_conditional_probs(canonical)
        away, home = canonical["awayLeadProb"], canonical["homeLeadProb"]
        assert legacy["awayLeadGivenNoTieProb"] == pytest.approx(away / (away + home))
        assert legacy["homeLeadGivenNoTieProb"] == pytest.approx(home / (away + home))

    def test_legacy_probs_sum_to_one(self):
        canonical = canonical_inning_result_probs(5.0, 3.5, "F5")
        legacy = legacy_conditional_probs(canonical)
        assert legacy["awayLeadGivenNoTieProb"] + legacy["homeLeadGivenNoTieProb"] == pytest.approx(1.0)

    def test_zero_projection_never_divides_by_zero(self):
        canonical = {"awayLeadProb": 0.0, "tieProb": 1.0, "homeLeadProb": 0.0}
        legacy = legacy_conditional_probs(canonical)
        assert legacy["awayLeadGivenNoTieProb"] is None
        assert legacy["homeLeadGivenNoTieProb"] is None

    def test_deterministic(self):
        canonical = canonical_inning_result_probs(4.5, 4.3, "F5")
        assert legacy_conditional_probs(canonical) == legacy_conditional_probs(canonical)


class TestF5MigrationSafeOutput:
    """Model Performance Phase 2A Part 8 -- combined f5ThreeWay/f5LegacyConditional schema."""

    def test_both_versions_present_with_unambiguous_names(self):
        r = f5_migration_safe_output(4.5, 4.3)
        assert set(r.keys()) == {"f5ThreeWay", "f5LegacyConditional"}
        assert set(r["f5ThreeWay"].keys()) == {"awayLeadProb", "tieProb", "homeLeadProb"}
        assert set(r["f5LegacyConditional"].keys()) == {"awayLeadGivenNoTieProb", "homeLeadGivenNoTieProb"}

    def test_no_ambiguous_f5winprob_field(self):
        r = f5_migration_safe_output(4.5, 4.3)
        blob = str(r)
        assert "f5WinProb" not in blob

    def test_legacy_derived_from_three_way_not_recomputed_independently(self):
        r = f5_migration_safe_output(4.5, 4.3)
        away, home = r["f5ThreeWay"]["awayLeadProb"], r["f5ThreeWay"]["homeLeadProb"]
        assert r["f5LegacyConditional"]["awayLeadGivenNoTieProb"] == pytest.approx(away / (away + home))

    def test_deterministic(self):
        assert f5_migration_safe_output(4.5, 4.3) == f5_migration_safe_output(4.5, 4.3)


class TestNormalization:

    @pytest.mark.parametrize("away,home", [
        (4.5, 4.3),      # evenly matched, moderate total
        (2.0, 1.9),      # evenly matched, low total
        (6.5, 6.2),      # evenly matched, high total
        (7.0, 2.0),      # large away favorite
        (2.0, 7.0),      # large home favorite
        (0.01, 0.01),    # near-zero projections
        (15.0, 15.0),    # extreme projections
        (3.2, 3.15),     # near-symmetric decimals
    ])
    def test_probabilities_sum_to_one(self, away, home):
        result = three_way_result_probs(away, home)
        assert_probabilities_valid(result)

    def test_zero_projection_both_teams(self):
        """
        Degenerate edge case: both teams projected to score exactly 0.
        This must resolve to a certain tie (P(Tie)=1), not a crash and
        not an arbitrary Away/Home split.
        """
        result = three_way_result_probs(0.0, 0.0)
        assert result["tieProb"] == pytest.approx(1.0, abs=1e-9)
        assert result["awayWinProb"] == pytest.approx(0.0, abs=1e-9)
        assert result["homeWinProb"] == pytest.approx(0.0, abs=1e-9)
        assert_probabilities_valid(result)

    def test_none_projection_treated_as_zero(self):
        result = three_way_result_probs(None, None)
        assert result["tieProb"] == pytest.approx(1.0, abs=1e-9)


class TestTieNeverRenormalizedAway:

    def test_tie_never_treated_as_push_or_discarded(self):
        """
        The single most important behavioral contract this module
        exists to satisfy: tieProb must appear in the output and
        contribute to the sum-to-1 identity, never silently folded
        into away/home the way production's F5 renormalization does
        today (documented, not fixed, in this phase).
        """
        result = three_way_result_probs(4.0, 4.0)
        assert result["tieProb"] > 0
        total = result["awayWinProb"] + result["tieProb"] + result["homeWinProb"]
        assert total == pytest.approx(1.0, abs=1e-9)

    def test_away_home_not_renormalized_after_tie_removed(self):
        """
        A direct differential proof: compute the "wrong" renormalized
        version (dividing away/home by (1 - tie), the exact production
        anti-pattern) and confirm the two are NOT equal whenever
        tieProb > 0 -- proving this module's away/home values are the
        raw joint-distribution shares, not tie-adjusted ones.
        """
        result = three_way_result_probs(4.5, 4.0)
        tie = result["tieProb"]
        assert tie > 0
        wrong_away = result["awayWinProb"] / (1 - tie)
        wrong_home = result["homeWinProb"] / (1 - tie)
        assert wrong_away != pytest.approx(result["awayWinProb"], abs=1e-9)
        assert wrong_home != pytest.approx(result["homeWinProb"], abs=1e-9)
        # The renormalized (wrong) values must themselves still sum to
        # 1 with each other (sanity check on the comparison itself),
        # confirming the difference is specifically the tie handling.
        assert wrong_away + wrong_home == pytest.approx(1.0, abs=1e-6)


class TestHighAndLowTieEnvironments:

    def test_high_tie_environment_low_scoring_symmetric(self):
        """Low, symmetric projections produce a materially higher tie probability than a high-scoring symmetric game."""
        low_scoring = three_way_result_probs(1.5, 1.5)
        high_scoring = three_way_result_probs(6.0, 6.0)
        assert low_scoring["tieProb"] > high_scoring["tieProb"]

    def test_low_tie_environment_large_mismatch(self):
        """A large projected mismatch produces a low tie probability relative to a symmetric game at the same total."""
        mismatched = three_way_result_probs(7.5, 1.5)
        symmetric = three_way_result_probs(4.5, 4.5)
        assert mismatched["tieProb"] < symmetric["tieProb"]

    def test_asymmetric_run_means_favorite_has_higher_win_prob(self):
        result = three_way_result_probs(6.0, 3.0)
        assert result["awayWinProb"] > result["homeWinProb"]
        assert result["awayWinProb"] > result["tieProb"]


class TestTruncationSensitivity:

    def test_truncation_mass_small_for_realistic_projections(self):
        result = three_way_result_probs(4.5, 4.3, max_runs=40)
        assert result["truncationMass"] < 1e-6

    def test_truncation_mass_shrinks_as_max_runs_increases(self):
        small = three_way_result_probs(5.0, 5.0, max_runs=10)
        large = three_way_result_probs(5.0, 5.0, max_runs=40)
        assert large["truncationMass"] <= small["truncationMass"]

    def test_extreme_projection_still_normalizes_via_truncation_correction(self):
        """
        An extreme projection (e.g. 15 runs/game) pushes real mass
        beyond even a max_runs=40 grid -- the proportional truncation
        correction must still restore an exact sum-to-1, not merely an
        approximate one, even though truncationMass itself is
        materially larger here than for a realistic projection.
        """
        result = three_way_result_probs(15.0, 15.0, max_runs=40)
        assert_probabilities_valid(result)


class TestDeterminism:

    def test_repeated_calls_produce_identical_output(self):
        r1 = three_way_result_probs(4.5, 4.3)
        r2 = three_way_result_probs(4.5, 4.3)
        assert r1 == r2

    def test_poisson_pmf_deterministic(self):
        assert poisson_pmf(3, 4.5) == poisson_pmf(3, 4.5)

    def test_no_argument_mutation(self):
        away, home = 4.5, 4.3
        three_way_result_probs(away, home)
        assert away == 4.5 and home == 4.3


class TestHorizonSeparation:

    @pytest.mark.parametrize("horizon", ["F3", "F5", "F7", "full_game"])
    def test_each_horizon_produces_valid_three_way_probs(self, horizon):
        result = three_way_result_probs_for_horizon(9.0, 8.6, horizon)
        assert_probabilities_valid(result)
        assert result["horizon"] == horizon
        assert result["horizonInnings"] == HORIZON_INNINGS[horizon]

    def test_starter_dominant_f5_environment_lower_scoring_than_full_game(self):
        """
        F5 (starters only, typically stronger than a game's full
        bullpen-inclusive average) should project a materially lower
        total than the full 9-inning game under the naive linear-scale
        placeholder -- this test locks in that ordering, not a specific
        numeric value.
        """
        full = three_way_result_probs_for_horizon(9.0, 8.5, "full_game")
        f5 = three_way_result_probs_for_horizon(9.0, 8.5, "F5")
        assert f5["awayProj"] < full["awayProj"]
        assert f5["homeProj"] < full["homeProj"]

    def test_bullpen_heavy_f7_environment_between_f5_and_full_game(self):
        f5 = three_way_result_probs_for_horizon(9.0, 8.5, "F5")
        f7 = three_way_result_probs_for_horizon(9.0, 8.5, "F7")
        full = three_way_result_probs_for_horizon(9.0, 8.5, "full_game")
        assert f5["awayProj"] < f7["awayProj"] < full["awayProj"]

    def test_extremely_low_scoring_f3_environment(self):
        result = three_way_result_probs_for_horizon(2.0, 2.0, "F3")
        assert_probabilities_valid(result)
        assert result["tieProb"] > 0.3, "a low-scoring F3 environment should be tie-heavy"

    def test_unknown_horizon_raises(self):
        with pytest.raises(ValueError):
            three_way_result_probs_for_horizon(4.5, 4.3, "F9")

    def test_custom_scale_fn_is_used_when_provided(self):
        """
        Proves the injectable scale_fn seam works -- a caller can plug
        in a production-realistic (e.g. starter-workload-based) scaling
        function instead of the naive linear-fraction placeholder.
        """
        def flat_scale(away_full, home_full, innings):
            return 3.0, 2.5  # ignores inputs entirely, for this test's purposes

        result = three_way_result_probs_for_horizon(9.0, 8.5, "F5", scale_fn=flat_scale)
        assert result["awayProj"] == 3.0
        assert result["homeProj"] == 2.5


class TestPurity:

    def test_no_forbidden_imports_at_module_scope(self):
        import ast
        import inspect
        import lib.research.three_way_projection as mod
        tree = ast.parse(inspect.getsource(mod))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in ("os", "sys", "requests", "json", "subprocess"), (
                        f"unexpected I/O-adjacent import at module scope: {alias.name}"
                    )
