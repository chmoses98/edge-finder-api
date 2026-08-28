#!/usr/bin/env python3
"""
tests/edgelab/test_run_mean_calibration_experiment_script.py
=========================================================
Coverage for scripts/edgelab/run_mean_calibration_experiment.py --
MLB-RSCH-0014's expected-run mean calibration experiment.
"""
import ast
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab"), os.path.join(_ROOT, "scripts", "edgelab", "backtest")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

import run_mean_calibration_experiment as exp  # noqa: E402

SCRIPT_PATH = os.path.join(_ROOT, "scripts", "edgelab", "run_mean_calibration_experiment.py")


def _find_function_node(name):
    tree = ast.parse(open(SCRIPT_PATH).read())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found")


def _call_names_in_order(func_node):
    names = []

    def _visit(node):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                names.append(f.id)
            elif isinstance(f, ast.Attribute):
                names.append(f.attr)
        for child in ast.iter_child_nodes(node):
            _visit(child)

    _visit(func_node)
    return names


class TestFrozenDispersionVerification:
    def test_module_level_dispersion_matches_canonical_artifact(self):
        import json
        path = os.path.join(_ROOT, "data", "edgelab", "analytics", "latest_mlb_rsch_0010_run_distribution.json")
        with open(path) as f:
            canonical = json.load(f)["fittedParameters"]["overdispersion"]
        assert exp.FROZEN_DISPERSION == canonical

    def test_verify_frozen_dispersion_raises_on_drift(self, monkeypatch):
        monkeypatch.setattr(exp, "FROZEN_DISPERSION", 0.999999)
        with pytest.raises(ValueError):
            exp._verify_frozen_dispersion()


class TestRegistrationOrdering:
    def test_main_registers_before_building_corpus(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        names = _call_names_in_order(_find_function_node("main"))
        registration_index = names.index("register_experiment")
        for call in ("build_corpus", "fit_hfa_c0", "attach_c0_predictions"):
            occurrences = [i for i, n in enumerate(names) if n == call]
            assert occurrences, f"expected main() to call {call!r}"
            assert min(occurrences) > registration_index

    def test_register_experiment_is_idempotent_across_reruns(self, tmp_path, monkeypatch):
        import lib.edgelab.experiment_registry as reg
        import lib.edgelab.control_identity as ctrl_id
        monkeypatch.setattr(reg, "EXPERIMENTS_ROOT", str(tmp_path / "experiments"))
        monkeypatch.setattr(ctrl_id, "CONTROL_MODELS_ROOT", str(tmp_path / "control_models"))
        control1, definition1 = exp.register_experiment()
        control2, definition2 = exp.register_experiment()
        assert definition1 == definition2
        assert control1 == control2


class TestC0ExactReproduction:
    def test_c0_uses_offense_and_bullpen_components_only(self):
        assert exp.C0_COMPONENTS == frozenset({"offense", "bullpen"})

    def test_c0_matches_rsch0009_direct_computation(self):
        """Proves attach_c0_predictions produces IDENTICAL values to
        calling rsch0009's own baseline_for_components/expected_runs
        directly with the SAME components -- no reimplementation drift."""
        import run_proxy_ablation_experiment as rsch0009
        from lib.edgelab.backtest.proxy_model import expected_runs

        row = {
            "gamePk": 1, "date": "2023-06-01", "gameNumber": 1, "homeTeamId": 1, "awayTeamId": 2, "season": 2023,
            "homeBaselineRaw": {"offenseRunsPerGame": 4.5, "runPreventionRunsAllowedPerGame": 4.0, "priorGamesThisSeason": 30},
            "awayBaselineRaw": {"offenseRunsPerGame": 4.2, "runPreventionRunsAllowedPerGame": 4.3, "priorGamesThisSeason": 28},
            "homeOffenseStabilized": 4.4, "awayOffenseStabilized": 4.25,
            "homeBullpenStabilized": 3.9, "awayBullpenStabilized": 4.1,
            "actualHomeRuns": 5, "actualAwayRuns": 3,
        }
        hfa = 0.0114
        exp.attach_c0_predictions([row], hfa)

        hb = rsch0009.baseline_for_components(row["homeBaselineRaw"], row["homeOffenseStabilized"], row["homeBullpenStabilized"], exp.C0_COMPONENTS)
        ab = rsch0009.baseline_for_components(row["awayBaselineRaw"], row["awayOffenseStabilized"], row["awayBullpenStabilized"], exp.C0_COMPONENTS)
        eh_expected, ea_expected = expected_runs(hb, ab, home_field_adjustment=hfa)

        assert row["homeExpectedRuns_C0"] == eh_expected
        assert row["awayExpectedRuns_C0"] == ea_expected


class TestCalibrateValue:
    def test_c1_global_affine(self):
        params = {"a": 0.5, "b": 0.9}
        assert exp.calibrate_value(4.0, exp.C1, params) == round(0.5 + 0.9 * 4.0, 4)

    def test_c2_home_away_uses_correct_side(self):
        params = {"a_h": 0.2, "b_h": 1.1, "a_a": -0.1, "b_a": 0.95}
        home_val = exp.calibrate_value(4.0, exp.C2, params, side="home")
        away_val = exp.calibrate_value(4.0, exp.C2, params, side="away")
        assert home_val == round(0.2 + 1.1 * 4.0, 4)
        assert away_val == round(-0.1 + 0.95 * 4.0, 4)
        assert home_val != away_val

    def test_c3_quadratic(self):
        params = {"a": 1.0, "b": 0.5, "c": 0.05}
        assert exp.calibrate_value(4.0, exp.C3, params) == round(1.0 + 0.5 * 4.0 + 0.05 * 16.0, 4)

    def test_none_raw_returns_none(self):
        assert exp.calibrate_value(None, exp.C1, {"a": 0.5, "b": 0.9}) is None

    def test_none_params_returns_raw_unchanged(self):
        assert exp.calibrate_value(4.0, exp.C1, None) == 4.0

    def test_floored_at_calibration_floor_never_nonpositive(self):
        params = {"a": -100.0, "b": 0.01}
        result = exp.calibrate_value(4.0, exp.C1, params)
        assert result == exp.CALIBRATION_FLOOR
        assert result > 0

    def test_unrecognized_kind_raises(self):
        with pytest.raises(ValueError):
            exp.calibrate_value(4.0, "C99_bogus", {"a": 0, "b": 1})

    def test_deterministic(self):
        params = {"a": 0.5, "b": 0.9}
        assert exp.calibrate_value(4.0, exp.C1, params) == exp.calibrate_value(4.0, exp.C1, params)


class TestSimpleOls:
    def test_recovers_exact_line_noiseless(self):
        rows = [{"x": 0.0, "y": 2.0}, {"x": 1.0, "y": 5.0}, {"x": 2.0, "y": 8.0}]
        slope, intercept = exp._simple_ols(rows, "x", "y")
        assert round(slope, 4) == 3.0
        assert round(intercept, 4) == 2.0

    def test_none_for_degenerate_x(self):
        rows = [{"x": 4.0, "y": 1.0}, {"x": 4.0, "y": 2.0}, {"x": 4.0, "y": 3.0}]
        slope, intercept = exp._simple_ols(rows, "x", "y")
        assert slope is None and intercept is None

    def test_none_for_too_few_rows(self):
        assert exp._simple_ols([{"x": 1.0, "y": 1.0}], "x", "y") == (None, None)


class TestOlsFitQuadratic:
    def test_recovers_exact_quadratic_noiseless(self):
        # y = 1 + 2*x + 0.5*x^2
        rows = []
        for x in (0.0, 1.0, 2.0, 3.0, 4.0):
            rows.append({"x": x, "xsq": x * x, "y": 1 + 2 * x + 0.5 * x * x})
        coeffs = exp._ols_fit(rows, ["x", "xsq"], "y")
        assert coeffs is not None
        assert round(coeffs["intercept"], 2) == 1.0
        assert round(coeffs["x"], 2) == 2.0
        assert round(coeffs["xsq"], 2) == 0.5

    def test_none_when_underdetermined(self):
        rows = [{"x": 1.0, "xsq": 1.0, "y": 1.0}]
        assert exp._ols_fit(rows, ["x", "xsq"], "y") is None


class TestFitC1DevOnly:
    def test_none_on_degenerate_dev_data(self):
        obs = [{"predictedC0": 4.0, "actual": 3.0, "side": "home"}] * 5
        params, diag = exp.fit_c1_global_affine_dev_only(obs)
        assert params is None
        assert "fallback" in diag

    def test_fits_pooled_home_and_away(self):
        obs = []
        for i in range(20):
            x = 3.5 + 0.1 * i
            obs.append({"predictedC0": x, "actual": 1.0 + 0.8 * x, "side": "home" if i % 2 == 0 else "away"})
        params, diag = exp.fit_c1_global_affine_dev_only(obs)
        assert params is not None
        assert round(params["b"], 1) == 0.8
        assert round(params["a"], 1) == 1.0


class TestFitC2DevOnly:
    def test_fits_separate_home_away_params(self):
        obs = []
        for i in range(20):
            x = 3.5 + 0.1 * i
            obs.append({"predictedC0": x, "actual": 0.5 + 1.0 * x, "side": "home"})
            obs.append({"predictedC0": x, "actual": -0.5 + 1.2 * x, "side": "away"})
        params, diag = exp.fit_c2_home_away_affine_dev_only(obs)
        assert params is not None
        assert round(params["b_h"], 1) == 1.0
        assert round(params["b_a"], 1) == 1.2
        assert params["b_h"] != params["b_a"]

    def test_none_when_one_side_degenerate(self):
        obs = [{"predictedC0": 4.0, "actual": 4.0, "side": "home"}] * 10 + [
            {"predictedC0": 3.5 + 0.1 * i, "actual": 4.0 + 0.5 * i, "side": "away"} for i in range(10)
        ]
        params, diag = exp.fit_c2_home_away_affine_dev_only(obs)
        assert params is None


class TestFitC3DevOnly:
    def test_never_examines_validation_or_holdout(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("fit_c3_quadratic_dev_only"))
        assert "val_" not in source.lower().replace("value", "")
        assert "holdout" not in source.lower()

    def test_none_on_insufficient_data(self):
        obs = [{"predictedC0": 4.0, "actual": 4.0, "side": "home"}]
        params, diag = exp.fit_c3_quadratic_dev_only(obs)
        assert params is None


class TestTeamObservationsAndPairedDelta:
    def _rows(self):
        return [{
            "gamePk": 1, "homeTeamId": 10, "awayTeamId": 20, "season": 2023, "gameNumber": 1,
            "homeBaselineRaw": {"priorGamesThisSeason": 25}, "awayBaselineRaw": {"priorGamesThisSeason": 30},
            "homeExpectedRuns_C0": 4.0, "awayExpectedRuns_C0": 3.8,
            "homeExpectedRuns_C1": 4.2, "awayExpectedRuns_C1": 3.6,
            "actualHomeRuns": 5, "actualAwayRuns": 3,
        }]

    def test_two_observations_per_row(self):
        obs = exp.team_observations(self._rows(), "C1")
        assert len(obs) == 2
        sides = {o["side"] for o in obs}
        assert sides == {"home", "away"}

    def test_predictedc0_always_carries_control_value_for_banding(self):
        obs = exp.team_observations(self._rows(), "C1")
        home_obs = next(o for o in obs if o["side"] == "home")
        assert home_obs["predictedC0"] == 4.0
        assert home_obs["predicted"] == 4.2

    def test_paired_delta_negative_means_improvement(self):
        obs_c0 = exp.team_observations(self._rows(), "C0")
        obs_c1 = exp.team_observations(self._rows(), "C1")
        result = exp.paired_mean_mae_delta(obs_c0, obs_c1)
        # home: C0 err=1.0, C1 err=0.8 -> improved; away: C0 err=0.8, C1 err=0.6 -> improved
        assert result["maeDelta"] < 0


class TestCalibrationBandBreakdown:
    def test_bands_keyed_by_c0_value_not_candidate_value(self):
        obs_c0 = [{"gamePk": 1, "teamId": 1, "predictedC0": 2.5, "predicted": 2.5, "actual": 3.0}]
        obs_c1 = [{"gamePk": 1, "teamId": 1, "predictedC0": 2.5, "predicted": 6.5, "actual": 3.0}]
        result = exp.calibration_band_breakdown(obs_c0, obs_c1)
        # even though C1's OWN predicted value (6.5) would fall in "6_0_plus",
        # this row must be counted in "lt_3_0" (C0's own value governs banding)
        assert result["lt_3_0"]["n"] == 1
        assert result["6_0_plus"]["n"] == 0


class TestSelectionRule:
    def test_fails_when_dev_not_improved(self):
        passes, reasons = exp.selection_passes(dev_mae_delta=0.01, dev_nb_primary_delta=-0.001, val_mae_delta=-0.01, val_nb_primary_delta=-0.001, band_deltas={})
        assert not passes

    def test_fails_when_dev_probability_not_improved(self):
        passes, reasons = exp.selection_passes(dev_mae_delta=-0.01, dev_nb_primary_delta=0.001, val_mae_delta=-0.01, val_nb_primary_delta=-0.001, band_deltas={})
        assert not passes

    def test_fails_when_validation_mean_degrades_beyond_tolerance(self):
        passes, reasons = exp.selection_passes(dev_mae_delta=-0.02, dev_nb_primary_delta=-0.001, val_mae_delta=0.2, val_nb_primary_delta=-0.001, band_deltas={})
        assert not passes

    def test_fails_when_validation_probability_degrades_beyond_tolerance(self):
        passes, reasons = exp.selection_passes(dev_mae_delta=-0.02, dev_nb_primary_delta=-0.001, val_mae_delta=-0.01, val_nb_primary_delta=0.02, band_deltas={})
        assert not passes

    def test_fails_when_improvement_confined_to_one_band(self):
        band_deltas = {b: {"maeDelta": None} for b, _, _ in exp.CALIBRATION_BANDS}
        first_band = exp.CALIBRATION_BANDS[0][0]
        band_deltas[first_band] = {"maeDelta": -0.05}
        passes, reasons = exp.selection_passes(dev_mae_delta=-0.02, dev_nb_primary_delta=-0.001, val_mae_delta=-0.01, val_nb_primary_delta=-0.001, band_deltas=band_deltas)
        assert not passes
        assert any("band" in r for r in reasons)

    def test_passes_when_all_criteria_met(self):
        band_deltas = {b: {"maeDelta": -0.01} for b, _, _ in exp.CALIBRATION_BANDS}
        passes, reasons = exp.selection_passes(dev_mae_delta=-0.02, dev_nb_primary_delta=-0.001, val_mae_delta=-0.01, val_nb_primary_delta=-0.001, band_deltas=band_deltas)
        assert passes
        assert reasons == []

    def test_never_examines_holdout_or_pinnacle_in_its_own_signature(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("selection_passes"))
        assert "holdout" not in source.lower()
        assert "pinnacle" not in source.lower()


class TestHoldoutInaccessibleDuringSelection:
    def test_evaluate_candidate_dev_val_never_touches_holdout(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("evaluate_candidate_dev_val"))
        assert "holdout" not in source.lower()

    def test_holdout_only_evaluated_after_selection_in_main(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        selection_index = main_source.index("passing = [")
        holdout_index = main_source.index("evaluate_frozen_winner_holdout")
        assert selection_index < holdout_index

    def test_pinnacle_stage_runs_after_holdout_unlock_in_main(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        holdout_index = main_source.index("evaluate_frozen_winner_holdout")
        pinnacle_index = main_source.index("Pinnacle secondary stage")
        assert holdout_index < pinnacle_index

    def test_only_frozen_winner_ever_reaches_holdout_function(self):
        """Proves main() calls evaluate_frozen_winner_holdout with the
        SAME frozen_winner_key selection already produced -- never with
        a rejected candidate."""
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        call_index = main_source.index("evaluate_frozen_winner_holdout(")
        call_line = main_source[call_index:call_index + 120]
        assert "frozen_winner_key" in call_line


class TestNbProbabilityCells:
    def test_includes_run_margin_family(self):
        cells = exp.nb_probability_cells(4.0, 3.8)
        assert cells is not None
        for m in exp.MARGIN_THRESHOLDS:
            assert f"run_margin_win_by_at_least_{m}" in cells
            assert f"run_margin_lose_by_at_least_{m}" in cells

    def test_none_for_nonpositive_means(self):
        assert exp.nb_probability_cells(None, 3.8) is None
        assert exp.nb_probability_cells(0.0, 3.8) is None
        assert exp.nb_probability_cells(-1.0, 3.8) is None

    def test_deterministic(self):
        assert exp.nb_probability_cells(4.0, 3.8) == exp.nb_probability_cells(4.0, 3.8)

    def test_probabilities_are_valid_range(self):
        cells = exp.nb_probability_cells(4.0, 3.8)
        for k, v in cells.items():
            assert 0.0 <= v <= 1.0, f"{k}={v} out of range"


class TestOutcomesForActual:
    def test_margin_outcomes_match_actual_difference(self):
        outcomes = exp._outcomes_for_actual(actual_home=6, actual_away=3)
        assert outcomes["run_margin_win_by_at_least_2"] == 1
        assert outcomes["run_margin_win_by_at_least_3"] == 1
        assert outcomes["run_margin_lose_by_at_least_2"] == 0

    def test_away_margin_outcomes(self):
        outcomes = exp._outcomes_for_actual(actual_home=1, actual_away=4)
        assert outcomes["run_margin_lose_by_at_least_2"] == 1
        assert outcomes["run_margin_lose_by_at_least_3"] == 1
        assert outcomes["run_margin_win_by_at_least_2"] == 0


class TestFrozenDispersionNeverRefitInNbEval:
    def test_nb_probability_cells_uses_module_constant(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("_nb_joint"))
        assert "FROZEN_DISPERSION" in source


class TestEarlySeasonFeasibilityDiagnostic:
    def test_never_builds_a_candidate_inside_diagnostic(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("early_season_feasibility_diagnostic"))
        assert "fit_c" not in source.lower()
        assert "calibrate_value" not in source

    def test_reports_inherited_floor_unchanged(self):
        rows_by_season = {
            2022: [{"homeBaselineRaw": {"priorGamesThisSeason": 25}}, {"homeBaselineRaw": {"priorGamesThisSeason": 40}}],
        }
        result = exp.early_season_feasibility_diagnostic(rows_by_season)
        assert result["inheritedEligibilityFloor"] == 20
        assert result["minPriorGamesObservedInCorpus"] == 25
        assert result["gamesOneToTwentyPresentInCorpus"] is False


class TestProductionMappingReadOnly:
    def test_no_production_files_referenced_for_writing(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("production_mapping_notes"))
        assert "open(" not in source
        assert "write" not in source.lower()
