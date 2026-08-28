#!/usr/bin/env python3
"""
tests/edgelab/test_run_joint_dispersion_experiment_script.py
=========================================================
Coverage for scripts/edgelab/run_joint_dispersion_experiment.py --
MLB-RSCH-0016's joint schedule-adjusted-mean + refit-dispersion experiment.
"""
import ast
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab"), os.path.join(_ROOT, "scripts", "edgelab", "backtest")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

import run_joint_dispersion_experiment as exp  # noqa: E402

SCRIPT_PATH = os.path.join(_ROOT, "scripts", "edgelab", "run_joint_dispersion_experiment.py")


def _find_function_node(name):
    tree = ast.parse(open(SCRIPT_PATH).read())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found")


class TestFrozenOldDispersionVerification:
    def test_module_level_dispersion_matches_canonical_artifact(self):
        import json
        path = os.path.join(_ROOT, "data", "edgelab", "analytics", "latest_mlb_rsch_0010_run_distribution.json")
        with open(path) as f:
            canonical = json.load(f)["fittedParameters"]["overdispersion"]
        assert exp.OLD_FROZEN_DISPERSION == canonical

    def test_verify_raises_on_drift(self, monkeypatch):
        monkeypatch.setattr(exp, "OLD_FROZEN_DISPERSION", 0.999999)
        with pytest.raises(ValueError):
            exp._verify_old_frozen_dispersion()


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


class TestS1FrozenReuse:
    def test_imports_rsch0015_module_never_reimplements(self):
        source = open(SCRIPT_PATH).read()
        assert "import run_opponent_strength_experiment as rsch0015" in source

    def test_build_corpus_and_predictions_calls_rsch0015_functions_only(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("build_corpus_and_predictions"))
        for fn in ("build_corpus", "attach_s0_predictions", "fit_hfa_s0", "build_raw_baseline_lookup",
                   "compute_schedule_adjustment", "fit_hfa_schedule", "attach_schedule_predictions"):
            assert f"rsch0015.{fn}" in source, f"expected reuse of rsch0015.{fn}"

    def test_never_redefines_s1_coefficients_or_formula(self):
        source = open(SCRIPT_PATH).read()
        # the schedule-adjustment formula itself (opponent averaging, shrinkage) must never be reimplemented here
        assert "def compute_schedule_adjustment" not in source
        assert "MIN_PRIOR_GAMES_OPPONENT" not in source or "rsch0015.MIN_PRIOR_GAMES_OPPONENT" in source


class TestResidualDiagnostics:
    def _rows(self, key_prefix, predicted_actual_pairs):
        rows = []
        for i, (predicted, actual) in enumerate(predicted_actual_pairs):
            rows.append({
                "gamePk": i, "homeTeamId": 1, "awayTeamId": 2, "season": 2023, "gameNumber": 1,
                "homeBaselineRaw": {"priorGamesThisSeason": 25}, "awayBaselineRaw": {"priorGamesThisSeason": 25},
                f"homeExpectedRuns_{key_prefix}": predicted, f"awayExpectedRuns_{key_prefix}": predicted,
                "actualHomeRuns": actual, "actualAwayRuns": actual,
            })
        return rows

    def test_zero_mean_residual_for_unbiased_predictions(self):
        pairs = [(4.0, 4), (4.0, 4), (4.0, 4), (4.0, 4)]
        rows = self._rows("X", pairs)
        result = exp.residual_diagnostics(rows, "X")
        assert result["meanResidual"] == 0.0

    def test_fitted_dispersion_is_nonnegative(self):
        import random
        random.seed(1)
        pairs = [(4.0, max(0, 4 + random.randint(-3, 3))) for _ in range(50)]
        rows = self._rows("X", pairs)
        result = exp.residual_diagnostics(rows, "X")
        assert result["fittedNbDispersion"] >= 0.0

    def test_reuses_fit_overdispersion_dev_only_unchanged(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("residual_diagnostics"))
        assert "fit_overdispersion_dev_only(" in source


class TestNbProbabilityCellsParameterizedDispersion:
    def test_different_dispersions_produce_different_cells(self):
        cells_low = exp.nb_probability_cells(4.0, 3.8, 0.1)
        cells_high = exp.nb_probability_cells(4.0, 3.8, 0.5)
        assert cells_low != cells_high

    def test_none_for_nonpositive_means(self):
        assert exp.nb_probability_cells(None, 3.8, 0.28) is None
        assert exp.nb_probability_cells(0.0, 3.8, 0.28) is None

    def test_probabilities_valid_range(self):
        cells = exp.nb_probability_cells(4.0, 3.8, 0.28)
        for k, v in cells.items():
            assert 0.0 <= v <= 1.0, f"{k}={v} out of range"

    def test_includes_run_margin_family(self):
        cells = exp.nb_probability_cells(4.0, 3.8, 0.28)
        for m in exp.MARGIN_THRESHOLDS:
            assert f"run_margin_win_by_at_least_{m}" in cells


class TestJointProbabilityEvalAllowsAsymmetricDispersion:
    def test_signature_accepts_separate_dispersion_per_side(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("joint_probability_eval"))
        assert "dispersion_a" in source and "dispersion_b" in source


class TestSelectionRule:
    def test_dev_gate_fails_when_not_improved(self):
        passes, reasons = exp.selection_passes_dev(dev_primary_delta=0.001)
        assert not passes

    def test_dev_gate_passes_when_improved(self):
        passes, reasons = exp.selection_passes_dev(dev_primary_delta=-0.001)
        assert passes
        assert reasons == []

    def test_dev_gate_fails_on_none(self):
        passes, reasons = exp.selection_passes_dev(dev_primary_delta=None)
        assert not passes

    def test_val_unlock_fails_beyond_tolerance(self):
        passes, reasons = exp.val_unlock_passes(val_primary_delta=exp.VAL_NONINFERIORITY_TOLERANCE + 0.001)
        assert not passes

    def test_val_unlock_passes_within_tolerance(self):
        passes, reasons = exp.val_unlock_passes(val_primary_delta=-0.001)
        assert passes

    def test_val_unlock_passes_at_exact_tolerance_boundary(self):
        passes, reasons = exp.val_unlock_passes(val_primary_delta=exp.VAL_NONINFERIORITY_TOLERANCE)
        assert passes


class TestHoldoutGatedByBothDevAndValInMain:
    def test_holdout_only_evaluated_if_dev_passes_in_main(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        dev_index = main_source.index("dev_passes, dev_reasons = selection_passes_dev")
        val_index = main_source.index("if dev_passes:")
        holdout_index = main_source.index("if unlock_holdout:")
        assert dev_index < val_index < holdout_index

    def test_pinnacle_stage_runs_after_holdout_section_in_main(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        holdout_index = main_source.index("if unlock_holdout:")
        pinnacle_index = main_source.index("Pinnacle secondary stage")
        assert holdout_index < pinnacle_index

    def test_s1_holdout_mean_mae_only_computed_when_unlocked(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        unlock_index = main_source.index("if unlock_holdout:")
        mae_index = main_source.index("holdout_s1_mean_mae = {")
        assert unlock_index < mae_index


class TestTailCalibration:
    def _row(self, key_prefix, home, away, actual_home, actual_away):
        return {f"homeExpectedRuns_{key_prefix}": home, f"awayExpectedRuns_{key_prefix}": away,
                "actualHomeRuns": actual_home, "actualAwayRuns": actual_away}

    def test_builds_joint_grid_once_per_row_not_once_per_predicate(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("tail_calibration"))
        # the grid-building call (_nb_joint) must appear exactly once, outside the per-predicate loop
        assert source.count("_nb_joint(") == 1

    def test_returns_all_five_fixed_tail_checks(self):
        rows = [self._row("X", 4.0, 4.0, 4, 4) for _ in range(5)]
        result = exp.tail_calibration(rows, "X", 0.28)
        assert set(result.keys()) == set(exp.TAIL_CHECKS.keys())

    def test_none_metrics_when_no_eligible_rows(self):
        result = exp.tail_calibration([], "X", 0.28)
        for name, d in result.items():
            assert d["n"] == 0
            assert d["predictedMeanProbability"] is None


class TestPinnacleNeverUsedForFitting:
    def test_pinnacle_import_only_inside_main_after_selection(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        pinnacle_import_index = main_source.index("import run_proxy_vs_pinnacle_experiment")
        dev_gate_index = main_source.index("dev_passes, dev_reasons = selection_passes_dev")
        assert dev_gate_index < pinnacle_import_index

    def test_residual_diagnostics_never_references_pinnacle(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("residual_diagnostics"))
        assert "pinnacle" not in source.lower()
