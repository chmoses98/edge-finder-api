#!/usr/bin/env python3
"""
tests/edgelab/test_hitter_shrinkage_confirmation_experiment.py
==============================================================
Coverage for MLB-RSCH-0030's shrinkage confirmation.

The load-bearing guarantees are CONFIRMATORY DISCIPLINE: alpha is fitted
on DEVELOPMENT only and frozen, it is never fitted to economics, it is
never forced positive, and the fair midpoint and executable ask are never
interchanged.
"""
import ast
import math
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab")):
    if p not in sys.path:
        sys.path.insert(0, p)

import run_hitter_shrinkage_confirmation_experiment as exp  # noqa: E402

SCRIPT = os.path.join(_ROOT, "scripts", "edgelab", "run_hitter_shrinkage_confirmation_experiment.py")
SOURCE = open(SCRIPT).read()


def _fn(name):
    for node in ast.parse(SOURCE).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(SOURCE, node)
    raise AssertionError(f"{name}() not found")


class TestCandidateForm:
    def test_alpha_zero_returns_the_market_exactly(self):
        for m in (0.05, 0.3, 0.5, 0.85):
            assert abs(exp.shrunk_probability(0.9, m, 0.0) - m) < 1e-9

    def test_alpha_one_returns_the_model_exactly(self):
        for mp in (0.05, 0.3, 0.5, 0.85):
            assert abs(exp.shrunk_probability(mp, 0.4, 1.0) - mp) < 1e-9

    def test_partial_alpha_lands_between_market_and_model(self):
        m, mp = 0.30, 0.60
        p = exp.shrunk_probability(mp, m, 0.5)
        assert m < p < mp

    def test_negative_alpha_moves_away_from_the_model(self):
        m, mp = 0.30, 0.60
        assert exp.shrunk_probability(mp, m, -0.5) < m

    def test_output_always_in_unit_interval(self):
        for a in (-1.0, 0.0, 0.5, 1.0, 2.0):
            for mp in (0.001, 0.5, 0.999):
                p = exp.shrunk_probability(mp, 0.4, a)
                assert 0.0 < p < 1.0

    def test_logit_sigmoid_roundtrip(self):
        for p in (0.01, 0.25, 0.5, 0.75, 0.99):
            assert abs(exp.sigmoid(exp.logit(p)) - p) < 1e-9

    def test_sigmoid_is_numerically_stable_at_extremes(self):
        assert 0.0 <= exp.sigmoid(-800) < 1e-300
        assert abs(exp.sigmoid(800) - 1.0) < 1e-12


class TestAlphaRecovery:
    """Synthetic worlds where the true alpha is known."""

    def _rows(self, true_alpha, n=1200, seed=11):
        import random
        rng = random.Random(seed)
        rows = []
        for i in range(n):
            market = rng.uniform(0.08, 0.92)
            model = min(0.98, max(0.02, market + rng.uniform(-0.25, 0.25)))
            true_p = exp.shrunk_probability(model, market, true_alpha)
            rows.append({"modelP": model, "marketP": market,
                         "outcome": 1 if rng.random() < true_p else 0,
                         "playerGameKey": f"P{i // 4}", "gameId": f"G{i // 20}",
                         "date": "2026-08-20", "marketFamily": "hitter_hits"})
        return rows

    def test_recovers_zero_when_model_adds_nothing(self):
        fit = exp.fit_alpha(self._rows(0.0))
        assert abs(fit["alpha"]) < 0.35

    def test_recovers_one_when_model_is_truth(self):
        fit = exp.fit_alpha(self._rows(1.0))
        assert 0.6 < fit["alpha"] < 1.45

    def test_recovers_a_partial_shrinkage(self):
        fit = exp.fit_alpha(self._rows(0.5))
        assert 0.15 < fit["alpha"] < 0.9

    def test_can_return_a_negative_alpha(self):
        """alpha is never forced positive -- an anti-signal must be expressible."""
        fit = exp.fit_alpha(self._rows(-0.8, seed=5))
        assert fit["alpha"] < 0.0

    def test_is_deterministic(self):
        rows = self._rows(0.5)
        assert exp.fit_alpha(rows) == exp.fit_alpha(rows)

    def test_respects_preregistered_bounds(self):
        fit = exp.fit_alpha(self._rows(5.0, seed=3))
        assert exp.ALPHA_BOUNDS[0] <= fit["alpha"] <= exp.ALPHA_BOUNDS[1]

    def test_bounds_allow_anti_signal_and_over_trust(self):
        assert exp.ALPHA_BOUNDS[0] < 0.0 and exp.ALPHA_BOUNDS[1] > 1.0

    def test_returns_none_below_the_floor(self):
        assert exp.fit_alpha(self._rows(0.5, n=10)) is None


class TestFittingObjectiveIsNeverEconomics:
    def test_fit_alpha_never_references_price_fees_or_roi(self):
        src = _fn("fit_alpha") + _fn("nll")
        for banned in ("taker_fee", "yesAsk", "roi", "Roi", "pnl", "netEv", "net_ev"):
            assert banned not in src, f"fitting objective references {banned}"

    def test_nll_is_the_only_objective(self):
        assert "nll(rows" in _fn("fit_alpha")

    def test_artifact_declares_alpha_not_fitted_to_economics(self):
        assert '"alphaFittedOnEconomics": False' in _fn("main")


class TestBenchmarkAndExecutionNeverSwap:
    def test_scoring_never_reads_the_ask(self):
        for name in ("score_candidates", "paired_s1_minus_s0_brier", "nll"):
            assert "yesAsk" not in _fn(name), f"{name} must not use the executable ask"

    def test_economics_enters_at_the_ask_not_the_mid(self):
        src = _fn("executable_economics")
        assert 'r.get("yesAsk")' in src
        assert "taker_fee(" in src

    def test_economics_applies_fees_to_net_ev(self):
        src = _fn("executable_economics")
        assert "- fee" in src


class TestConfirmatoryDiscipline:
    def test_alpha_is_fitted_on_development_only(self):
        main = _fn("main")
        assert "fit = fit_alpha(dev)" in main
        assert "fit_alpha(rows)" not in main, "alpha must never be fitted on the full corpus"

    def test_validation_is_scored_with_the_frozen_alpha(self):
        assert "val_scores = score_candidates(val, alpha," in _fn("main")

    def test_family_validation_uses_the_global_alpha_not_the_family_alpha(self):
        src = _fn("family_alphas")
        assert "score_candidates(v, global_alpha" in src

    def test_dev_val_split_is_inherited_not_recut(self):
        import run_hitter_prop_validity_experiment as r28
        assert exp.DEV_DATE_MAX == r28.DEV_DATE_MAX

    def test_buckets_and_bands_are_inherited_from_prior_experiments(self):
        import run_hitter_edge_decomposition_experiment as r29
        assert exp.SIGNAL_BUCKETS == r29.SIGNAL_BUCKETS
        assert exp.PROBABILITY_BANDS == r29.PROBABILITY_BANDS

    def test_forward_thresholds_are_constants_chosen_in_advance(self):
        assert exp.FORWARD_MIN_KEYS == 100
        assert exp.FORWARD_MIN_GAMES == 30
        assert exp.FORWARD_MIN_DATES == 7


class TestSuccessRule:
    def _val(self, wins, ece_s0=0.02, ece_s1=0.02, keys=60, games=12):
        return {"S1_beats_S0_bothMetrics": wins, "playerGameKeys": keys,
                "independentGames": games,
                "S0_market": {"ece": ece_s0}, "S1_shrunk": {"ece": ece_s1}}

    def _lodo(self, wins, total):
        return {"folds": [{"S1_beats_S0_bothMetrics": i < wins} for i in range(total)]}

    def _fams(self, n_win, n_elig=4):
        return [{"meetsFloor": i < n_elig,
                 "validationUnderGlobalAlpha": {"S1_beats_S0_bothMetrics": i < n_win}}
                for i in range(4)]

    def test_all_five_required(self):
        assert "c1 and c2 and c3 and c4 and c5" in _fn("evaluate_success")

    def test_fails_when_s1_loses_validation(self):
        r = exp.evaluate_success(self._val(False), self._lodo(7, 7), self._fams(4), [])
        assert not r["allRequired"]

    def test_fails_on_a_bare_minority_of_dates(self):
        r = exp.evaluate_success(self._val(True), self._lodo(3, 7), self._fams(4), [])
        assert r["2_direction_holds_on_majority_of_held_out_dates"] is False

    def test_fails_when_confined_to_one_family(self):
        r = exp.evaluate_success(self._val(True), self._lodo(7, 7), self._fams(1), [])
        assert r["3_not_concentrated_in_one_family"] is False

    def test_fails_on_calibration_degradation(self):
        r = exp.evaluate_success(self._val(True, ece_s1=0.09), self._lodo(7, 7), self._fams(4), [])
        assert r["4_no_material_calibration_degradation"] is False

    def test_fails_on_thin_independent_sample(self):
        r = exp.evaluate_success(self._val(True, keys=10, games=2), self._lodo(7, 7), self._fams(4), [])
        assert r["5_sufficient_independent_sample"] is False


class TestGovernance:
    def test_maximum_disposition_is_level_1(self):
        main = _fn("main")
        assert '"maximumDisposition": "LEVEL_1_SHADOW_CANDIDATE"' in main
        assert '"productionActivationAuthorized": False' in main
        assert "PRODUCTION_APPROVED" not in SOURCE
        assert "LEVEL_2" not in main

    def test_rsch0029_coefficient_is_treated_as_hypothesis(self):
        assert '"rsch0029CoefficientTreatedAsHypothesisNotPrior": True' in _fn("main")

    def test_no_user_wagers_or_bet_inference(self):
        main = _fn("main")
        assert '"usesUserConfirmedWagers": False' in main
        assert '"impliesRecommendationsWereBet": False' in main

    def test_materiality_block_is_labelled_post_hoc(self):
        main = _fn("main")
        assert '"preregistered": False' in main
        assert "does NOT change the mechanical verdict" in main

    def test_frozen_artifact_only_written_when_the_rule_passed(self):
        main = _fn("main")
        assert "if shadow:" in main
        assert '"noRefitRule"' in main

    def test_frozen_artifact_is_not_production_active(self):
        assert '"productionActive": False' in _fn("main")


class TestClusteredUncertainty:
    def test_alpha_ci_refits_within_each_resample(self):
        src = _fn("alpha_clustered_ci")
        assert "fit_alpha(resampled)" in src, "the CI must refit, not reuse a point estimate"

    def test_alpha_ci_resamples_whole_player_games(self):
        src = _fn("alpha_clustered_ci")
        assert "cluster_key=CLUSTER_KEY" in src or "by[c]" in src

    def test_primary_cluster_unit_is_player_game(self):
        assert exp.CLUSTER_KEY == "playerGameKey"
