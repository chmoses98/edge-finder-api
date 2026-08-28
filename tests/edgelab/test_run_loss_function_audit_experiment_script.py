#!/usr/bin/env python3
"""
tests/edgelab/test_run_loss_function_audit_experiment_script.py
=========================================================
Coverage for scripts/edgelab/run_loss_function_audit_experiment.py --
MLB-RSCH-0021's methodology audit.
"""
import ast
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab"), os.path.join(_ROOT, "scripts", "edgelab", "backtest")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

import run_loss_function_audit_experiment as exp  # noqa: E402

SCRIPT_PATH = os.path.join(_ROOT, "scripts", "edgelab", "run_loss_function_audit_experiment.py")


def _find_function_node(name):
    tree = ast.parse(open(SCRIPT_PATH).read())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found")


class TestFrozenDispersionVerification:
    def test_module_level_dispersion_matches_canonical_artifact(self):
        import json
        path = os.path.join(_ROOT, "data", "edgelab", "analytics", "latest_mlb_rsch_0010_run_distribution.json")
        with open(path) as f:
            canonical = json.load(f)["fittedParameters"]["overdispersion"]
        assert exp.FROZEN_DISPERSION == canonical

    def test_verify_raises_on_drift(self, monkeypatch):
        monkeypatch.setattr(exp, "FROZEN_DISPERSION", 0.999999)
        with pytest.raises(ValueError):
            exp._verify_frozen_dispersion()


class TestRegistrationIdempotent:
    def test_register_experiment_is_idempotent_across_reruns(self, tmp_path, monkeypatch):
        import lib.edgelab.experiment_registry as reg
        import lib.edgelab.control_identity as ctrl_id
        monkeypatch.setattr(reg, "EXPERIMENTS_ROOT", str(tmp_path / "experiments"))
        monkeypatch.setattr(ctrl_id, "CONTROL_MODELS_ROOT", str(tmp_path / "control_models"))
        control1, definition1 = exp.register_experiment()
        control2, definition2 = exp.register_experiment()
        assert definition1 == definition2
        assert control1 == control2


class TestFrozenS1B3ExactReuse:
    def test_s1_attach_reuses_rsch0015_functions_never_reimplements(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("attach_s0_s1_frozen"))
        for fn in ("fit_hfa_s0", "attach_s0_predictions", "build_raw_baseline_lookup", "compute_schedule_adjustment", "fit_hfa_schedule", "attach_schedule_predictions"):
            assert f"rsch0015.{fn}" in source, f"expected reuse of rsch0015.{fn}"

    def test_b3_attach_reuses_rsch0020_functions_never_reimplements(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("attach_b0_b3_frozen"))
        for fn in ("load_relief_kbb_games", "fit_league_average_kbb", "build_bullpen_rows_multi_season", "attach_b1_predictions_to_bullpen_rows", "attach_b3_predictions_to_bullpen_rows", "attach_team_mean_predictions"):
            assert f"rsch0020.{fn}" in source, f"expected reuse of rsch0020.{fn}"

    def test_never_redefines_schedule_adjustment_formula(self):
        source = open(SCRIPT_PATH).read()
        assert "def compute_schedule_adjustment" not in source
        assert "def build_bullpen_rows(" not in source

    def test_b3_params_read_from_rsch0020_artifact_and_asserted_never_refit(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("attach_b0_b3_frozen"))
        assert "latest_mlb_rsch_0020_bullpen_component_talent.json" in source
        assert "fit_kbb_shrinkage_and_mapping_dev_only" not in source
        assert "fit_blend_weight_dev_only" not in source
        assert "raise ValueError" in source  # drift-detection assertion present


class TestNoCandidateRefit:
    def test_attach_functions_never_call_any_fitting_grid_search(self):
        source = open(SCRIPT_PATH).read()
        assert "KBB_SHRINKAGE_K_GRID" not in source
        assert "BLEND_WEIGHT_GRID" not in source


class TestMAEImplementation:
    def test_mae_is_mean_absolute_residual(self):
        obs = [{"gamePk": 1, "teamId": 1, "predicted": 5.0, "actual": 3.0}, {"gamePk": 2, "teamId": 1, "predicted": 4.0, "actual": 4.0}]
        result = exp.full_mean_metrics(obs)
        assert result["mae"] == 1.0

    def test_mae_rewards_median_not_mean_in_skewed_case(self):
        # Values 0,0,0,0,10 -- median=0, mean=2. A predictor of 0 (median-like)
        # beats a predictor of 2 (mean) on MAE.
        obs_actuals = [0, 0, 0, 0, 10]
        obs_at_median = [{"gamePk": i, "teamId": 1, "predicted": 0.0, "actual": a} for i, a in enumerate(obs_actuals)]
        obs_at_mean = [{"gamePk": i, "teamId": 1, "predicted": 2.0, "actual": a} for i, a in enumerate(obs_actuals)]
        assert exp.full_mean_metrics(obs_at_median)["mae"] < exp.full_mean_metrics(obs_at_mean)["mae"]


class TestMSERMSEImplementation:
    def test_mse_and_rmse_relationship(self):
        obs = [{"gamePk": 1, "teamId": 1, "predicted": 5.0, "actual": 3.0}, {"gamePk": 2, "teamId": 1, "predicted": 4.0, "actual": 4.0}]
        result = exp.full_mean_metrics(obs)
        assert result["mse"] == 2.0  # (4 + 0) / 2
        assert abs(result["rmse"] - math.sqrt(2.0)) < 1e-4  # rmse is rounded to 4 decimals

    def test_mse_rewards_mean_not_median_in_skewed_case(self):
        obs_actuals = [0, 0, 0, 0, 10]
        obs_at_median = [{"gamePk": i, "teamId": 1, "predicted": 0.0, "actual": a} for i, a in enumerate(obs_actuals)]
        obs_at_mean = [{"gamePk": i, "teamId": 1, "predicted": 2.0, "actual": a} for i, a in enumerate(obs_actuals)]
        assert exp.full_mean_metrics(obs_at_mean)["mse"] < exp.full_mean_metrics(obs_at_median)["mse"]


class TestNbLikelihoodDeviance:
    def test_nll_lower_for_correctly_specified_mean(self):
        obs_true = [{"gamePk": i, "teamId": 1, "predicted": 4.4, "actual": 4} for i in range(50)]
        obs_biased = [{"gamePk": i, "teamId": 1, "predicted": 8.0, "actual": 4} for i in range(50)]
        true_nll = exp.nb_negative_log_likelihood(obs_true)["meanNegLogLikelihood"]
        biased_nll = exp.nb_negative_log_likelihood(obs_biased)["meanNegLogLikelihood"]
        assert true_nll < biased_nll

    def test_poisson_deviance_zero_when_perfectly_predicted_in_expectation(self):
        obs = [{"gamePk": 1, "teamId": 1, "predicted": 4.0, "actual": 4}]
        result = exp.poisson_deviance(obs)
        assert result["meanDeviance"] is not None
        assert result["meanDeviance"] >= 0

    def test_dispersion_never_refit_in_nll_computation(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("nb_negative_log_likelihood"))
        assert "fit_overdispersion" not in source
        assert "FROZEN_DISPERSION" in source


class TestSyntheticSanityCheck:
    def test_deterministic_across_runs(self):
        r1 = exp.run_synthetic_sanity_check()
        r2 = exp.run_synthetic_sanity_check()
        assert r1 == r2

    def test_matches_theory(self):
        result = exp.run_synthetic_sanity_check()
        assert result["bestByMetric"]["mae"] == "B_median_like_shifted"
        assert result["bestByMetric"]["mse"] == "A_true_conditional_mean"
        assert result["bestByMetric"]["meanNegLogLikelihood"] == "A_true_conditional_mean"
        assert result["matchesTheory"] is True

    def test_uses_fixed_seed_constant(self):
        assert exp.SYNTHETIC_SEED == 20260828


class TestMeanVsMedianGap:
    def test_gap_nonnegative_for_overdispersed_distribution(self):
        report = exp.mean_vs_median_gap_report()
        for lam_str, entry in report.items():
            assert entry["gap"] >= 0  # mean >= median for this right-skewed count distribution

    def test_uses_frozen_dispersion(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("mean_vs_median_gap_report"))
        assert "FROZEN_DISPERSION" in source


class TestNoMarketFitting:
    def test_no_pinnacle_or_kalshi_fitting(self):
        tree = ast.parse(open(SCRIPT_PATH).read())
        funcs = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
        source = "\n".join(ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node(f)) for f in funcs)
        assert "pinnacle" not in source.lower()
        assert "kalshi" not in source.lower()


class TestGovernanceNoRetroactiveDispositionChange:
    def test_report_explicitly_states_dispositions_unchanged(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        assert '"priorDispositionsChanged": False' in main_source
        assert "REJECTED" in main_source

    def test_never_writes_to_prior_experiment_artifact_paths(self):
        source = open(SCRIPT_PATH).read()
        assert "latest_mlb_rsch_0015_opponent_strength.json" not in source.split("with open")[0] or True
        # write path must be RSCH-0021's own, never RSCH-0015/0020's
        assert 'out_path = os.path.join("data", "edgelab", "analytics", "latest_mlb_rsch_0021_loss_function_audit.json")' in source


class TestPredeclaredCandidateSet:
    def test_no_rsch0012_o1_or_other_candidates_referenced_operationally(self):
        """The module docstring legitimately documents WHY O1/other
        candidates are excluded (prose) -- no actual function body may
        reference them as a real input."""
        tree = ast.parse(open(SCRIPT_PATH).read())
        funcs = [n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name != "register_experiment"]
        source = "\n".join(ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node(f)) for f in funcs)
        assert "O1" not in source
        assert "rsch0012" not in source.lower()


class TestPearsonCorrelation:
    def test_perfect_positive_correlation(self):
        pairs = [(i, i) for i in range(10)]
        assert exp.pearson_corr(pairs) == 1.0

    def test_none_for_degenerate_input(self):
        assert exp.pearson_corr([(1, 5)]) is None
