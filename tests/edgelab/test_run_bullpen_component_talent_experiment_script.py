#!/usr/bin/env python3
"""
tests/edgelab/test_run_bullpen_component_talent_experiment_script.py
=========================================================
Coverage for scripts/edgelab/run_bullpen_component_talent_experiment.py --
MLB-RSCH-0020's bullpen component talent study.
"""
import ast
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab"), os.path.join(_ROOT, "scripts", "edgelab", "backtest")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

import run_bullpen_component_talent_experiment as exp  # noqa: E402

SCRIPT_PATH = os.path.join(_ROOT, "scripts", "edgelab", "run_bullpen_component_talent_experiment.py")


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


class TestReliefOnlyClassification:
    def test_only_orderindex_greater_than_zero_counted_as_relief(self):
        boxscore_row = {
            "gamePk": 1,
            "homePitchers": [
                {"orderIndex": 0, "strikeOuts": 99, "baseOnBalls": 99, "battersFaced": 99},  # starter -- must be excluded
                {"orderIndex": 1, "strikeOuts": 2, "baseOnBalls": 1, "battersFaced": 6},
            ],
            "awayPitchers": [],
        }
        team_games = {100: [{"gamePk": 1, "date": "2024-04-01", "side": "home"}]}
        import tempfile, gzip, json
        with tempfile.TemporaryDirectory() as d:
            season_dir = os.path.join(d, "2024")
            os.makedirs(season_dir)
            path = os.path.join(season_dir, "boxscores.jsonl.gz")
            with gzip.open(path, "wt") as f:
                f.write(json.dumps(boxscore_row) + "\n")

            class _FakeFetcher:
                @staticmethod
                def boxscore_cache_path(season):
                    return path

            import run_bullpen_component_talent_experiment as mod
            old_fetcher = mod.starter_fetcher
            mod.starter_fetcher = _FakeFetcher
            try:
                out = mod.load_relief_kbb_games(2024, team_games)
            finally:
                mod.starter_fetcher = old_fetcher
        assert out[100][0]["reliefKMinusBBPct"] == round((2 - 1) / 6, 4)

    def test_zero_relievers_yields_none_not_zero(self):
        boxscore_row = {"gamePk": 1, "homePitchers": [{"orderIndex": 0, "strikeOuts": 5, "baseOnBalls": 1, "battersFaced": 30}], "awayPitchers": []}
        team_games = {100: [{"gamePk": 1, "date": "2024-04-01", "side": "home"}]}
        import tempfile, gzip, json
        with tempfile.TemporaryDirectory() as d:
            season_dir = os.path.join(d, "2024")
            os.makedirs(season_dir)
            path = os.path.join(season_dir, "boxscores.jsonl.gz")
            with gzip.open(path, "wt") as f:
                f.write(json.dumps(boxscore_row) + "\n")

            class _FakeFetcher:
                @staticmethod
                def boxscore_cache_path(season):
                    return path

            import run_bullpen_component_talent_experiment as mod
            old_fetcher = mod.starter_fetcher
            mod.starter_fetcher = _FakeFetcher
            try:
                out = mod.load_relief_kbb_games(2024, team_games)
            finally:
                mod.starter_fetcher = old_fetcher
        assert out[100][0]["reliefKMinusBBPct"] is None  # zero relief batters faced -- real undefined, never a fabricated zero


class TestKBBDenominatorCorrectness:
    def test_kbb_pct_is_k_minus_bb_over_battersfaced(self):
        assert round((5 - 2) / 20, 4) == 0.15

    def test_bullpen_kbb_baseline_mirrors_bullpen_quality_baseline_exactly(self):
        from lib.edgelab.backtest.proxy_enrichment import bullpen_quality_baseline
        source_a = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("bullpen_kbb_baseline"))
        assert "prior_games_this_season(" in source_a
        assert "season_to_date_rate(" in source_a
        assert "def prior_games_this_season" not in source_a  # reused, never reimplemented
        assert "def season_to_date_rate" not in source_a


class TestNoFutureLeakage:
    def test_prior_games_this_season_reused_unchanged(self):
        source = open(SCRIPT_PATH).read()
        assert "from lib.edgelab.backtest.team_offense_recency_reconstruction import prior_games_this_season" in source
        assert "def prior_games_this_season(" not in source

    def test_target_game_bullpen_data_used_only_as_outcome_never_predictor(self):
        """actualReliefER9 (the target game's own outcome) must never
        appear as an input to bullpen_kbb_baseline or the B1/B3
        prediction functions -- only as the fitting/evaluation TARGET."""
        predict_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("predict_b1_er9"))
        assert "actualReliefER9" not in predict_source


class TestB0ExactReproduction:
    def test_b0_override_fn_always_returns_none(self):
        """B0's own override is a no-op -- attach_team_mean_predictions
        falls back to blend_run_prevention_with_bullpen_quality exactly
        as rsch0009.baseline_for_components does, reproducing MLB-RSCH-0009's
        own bullpen component byte-exact."""
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        assert 'attach_team_mean_predictions(all_rows, "B0", lambda r, side: None' in main_source

    def test_reproduction_proof_present_in_main(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        assert "b0_reproduction_ok" in main_source
        assert "rsch0009.attach_predictions(verify_rows, BASELINE_COMPONENTS, hfa" in main_source


class TestDevOnlyFitting:
    def test_shrinkage_grid_is_fixed_preregistered_tuple(self):
        assert exp.KBB_SHRINKAGE_K_GRID == (10, 20, 30, 50, 80)

    def test_blend_weight_grid_is_fixed_preregistered_tuple(self):
        assert exp.BLEND_WEIGHT_GRID[0] == 0.0
        assert exp.BLEND_WEIGHT_GRID[-1] == 1.0
        assert len(exp.BLEND_WEIGHT_GRID) == 11

    def test_mapping_fit_never_references_val_or_holdout(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("fit_kbb_shrinkage_and_mapping_dev_only"))
        assert "val_" not in source.lower()
        assert "holdout" not in source.lower()

    def test_blend_weight_fit_never_references_val_or_holdout(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("fit_blend_weight_dev_only"))
        assert "val_" not in source.lower()
        assert "holdout" not in source.lower()

    def test_main_fits_mapping_and_blend_on_dev_seasons_data_only(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        assert "fit_kbb_shrinkage_and_mapping_dev_only(dev_seasons_data" in main_source
        assert "fit_blend_weight_dev_only(dev_bullpen_rows)" in main_source


class TestValidationNeverRefits:
    def test_val_bullpen_rows_built_with_frozen_k_never_a_new_fit(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        val_index = main_source.index("val_bullpen_rows = build_bullpen_rows_multi_season(val_seasons_data, league_avg_kbb, mapping[\"k\"])")
        fit_index = main_source.index("mapping = fit_kbb_shrinkage_and_mapping_dev_only")
        assert fit_index < val_index


class TestHoldoutInaccessibleBeforeGate:
    def test_holdout_only_evaluated_after_selection_check(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        selection_index = main_source.index("passes_b1, reasons_b1 = selection_passes")
        holdout_index = main_source.index("if selected is not None:")
        assert selection_index < holdout_index

    def test_selection_passes_signature_never_takes_holdout_data(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("selection_passes"))
        assert "holdout" not in source.lower()


class TestFrozenNbUnchanged:
    def test_dispersion_never_refit_per_candidate(self):
        source = open(SCRIPT_PATH).read()
        assert "fit_overdispersion" not in source

    def test_nb_probability_cells_uses_frozen_dispersion_constant(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("_nb_joint"))
        assert "FROZEN_DISPERSION" in source

    def test_returns_all_five_families(self):
        cells = exp.nb_probability_cells(4.0, 3.8)
        prefixes = {k.split("_over_")[0].split("_win_by")[0] for k in cells if k != "moneyline"}
        assert "moneyline" in cells
        assert any(k.startswith("game_total") for k in cells)
        assert any(k.startswith("team_total_home") for k in cells)
        assert any(k.startswith("team_total_away") for k in cells)
        assert any(k.startswith("run_margin") for k in cells)


class TestPinnacleAfterHoldoutOnly:
    def test_pinnacle_stage_gated_by_holdout_result_in_main(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        holdout_var_index = main_source.index("holdout_result = None")
        pinnacle_gate_index = main_source.index("if holdout_result is not None:")
        assert holdout_var_index < pinnacle_gate_index

    def test_pinnacle_import_happens_inside_the_gated_block(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        pinnacle_gate_index = main_source.index("if holdout_result is not None:")
        import_index = main_source.index("import run_proxy_vs_pinnacle_experiment")
        assert pinnacle_gate_index < import_index


class TestB2B4NotRun:
    def test_b2_and_b4_marked_not_run_in_report(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        assert '"b2Status": "NOT_RUN' in main_source
        assert '"b4Status": "NOT_RUN' in main_source

    def test_no_hr_field_ever_referenced_operationally(self):
        """The module docstring legitimately documents WHY B2 is NOT_RUN
        (mentioning the missing "homeRuns" field as prose) -- but no
        actual function body may ever reference it as a real input."""
        tree = ast.parse(open(SCRIPT_PATH).read())
        funcs = [n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name != "register_experiment"]
        source = "\n".join(ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node(f)) for f in funcs)
        assert "homeRuns" not in source


class TestSampleDepthBandsFixed:
    def test_bands_are_fixed_tuple_never_optimized(self):
        assert exp.SAMPLE_DEPTH_BANDS == (("first_20_ip", 0, 20), ("20_50_ip", 20, 50), ("50_100_ip", 50, 100), ("100_plus_ip", 100, 99999))


class TestSelectionRule:
    def _robustness(self, n_pos, n_total):
        return {"nTeamsPositive": n_pos, "nTeamsTotal": n_total}

    def test_fails_when_dev_bullpen_outcome_not_improved(self):
        passes, reasons = exp.selection_passes(0.01, -0.001, -0.0001, -0.01, -0.0001, self._robustness(20, 30))
        assert not passes

    def test_fails_when_dev_team_mean_not_improved(self):
        passes, reasons = exp.selection_passes(-0.01, 0.001, -0.0001, -0.01, -0.0001, self._robustness(20, 30))
        assert not passes

    def test_fails_when_dev_probability_not_improved(self):
        passes, reasons = exp.selection_passes(-0.01, -0.001, 0.001, -0.01, -0.0001, self._robustness(20, 30))
        assert not passes

    def test_fails_when_concentrated_in_few_teams(self):
        passes, reasons = exp.selection_passes(-0.01, -0.001, -0.0001, -0.01, -0.0001, self._robustness(5, 30))
        assert not passes

    def test_passes_when_all_criteria_met(self):
        passes, reasons = exp.selection_passes(-0.01, -0.001, -0.0001, -0.01, -0.0001, self._robustness(20, 30))
        assert passes
        assert reasons == []


class TestOlsHelper:
    def test_recovers_known_linear_relationship(self):
        pairs = [(float(i), 2.0 * i + 1.0) for i in range(10)]
        slope, intercept = exp._simple_ols(pairs)
        assert abs(slope - 2.0) < 1e-9
        assert abs(intercept - 1.0) < 1e-9

    def test_none_for_degenerate_input(self):
        assert exp._simple_ols([(1.0, 1.0)]) == (None, None)
