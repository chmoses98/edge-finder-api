#!/usr/bin/env python3
"""
tests/edgelab/test_run_early_season_offense_experiment_script.py
=========================================================
Coverage for scripts/edgelab/run_early_season_offense_experiment.py --
MLB-RSCH-0017's new no-floor early-season offense experiment.
"""
import ast
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab"), os.path.join(_ROOT, "scripts", "edgelab", "backtest")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

import run_early_season_offense_experiment as exp  # noqa: E402

SCRIPT_PATH = os.path.join(_ROOT, "scripts", "edgelab", "run_early_season_offense_experiment.py")


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


class TestPreviousSeasonAverages:
    def test_no_previous_season_for_earliest_cached_season(self):
        result = exp.load_previous_season_full_averages(exp.EARLIEST_CACHED_SEASON)
        assert result == {}

    def test_averages_are_full_season_not_pit_filtered(self):
        """Previous-season averages use the ENTIRE previous season (never
        partial/PIT-filtered) -- that season is completely in the past
        relative to any target-season game, so no leakage is possible."""
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("load_previous_season_full_averages"))
        assert "is_strictly_before" not in source
        assert "prior_games_this_season" not in source


class TestBuildCorpusNoFloor:
    def test_first_game_of_season_is_included_not_excluded(self):
        """Proves this experiment does NOT inherit MLB-RSCH-0009's own
        20-game floor -- game 1 (zero prior games for both teams) must
        appear in the corpus."""
        rows_by_season, _ = exp.build_corpus()
        first_season = exp.ALL_SEASONS[0]
        rows = rows_by_season[first_season]
        min_prior = min(r["homeCurrentRaw"]["priorGamesThisSeason"] for r in rows)
        assert min_prior == 0

    def test_2022_rows_have_no_previous_season_data(self):
        rows_by_season, _ = exp.build_corpus()
        rows_2022 = rows_by_season.get(2022, [])
        if rows_2022:
            assert all(r["homePreviousSeason"] is None for r in rows_2022)

    def test_rows_capped_by_max_prior_games(self):
        rows_by_season, _ = exp.build_corpus()
        for season, rows in rows_by_season.items():
            for r in rows:
                home_pg = r["homeCurrentRaw"]["priorGamesThisSeason"]
                away_pg = r["awayCurrentRaw"]["priorGamesThisSeason"]
                assert home_pg <= exp.MAX_PRIOR_GAMES_CORPUS or away_pg <= exp.MAX_PRIOR_GAMES_CORPUS


class TestE0Component:
    def test_league_average_at_game_one(self):
        assert exp.e0_component(None, 0, 4.4) == 4.4

    def test_league_average_when_raw_is_none_regardless_of_prior_games(self):
        assert exp.e0_component(None, 5, 4.4) == 4.4

    def test_shrinkage_formula_applied_after_game_one(self):
        from lib.edgelab.backtest.proxy_enrichment import stabilized_offense_rate, OFFENSE_SHRINKAGE_K
        result = exp.e0_component(5.0, 10, 4.4)
        expected = stabilized_offense_rate(5.0, 10, 4.4, k=OFFENSE_SHRINKAGE_K)
        assert result == expected


class TestE1Component:
    def test_pure_previous_season_at_game_one(self):
        assert exp.e1_component(None, 0, 4.8, 4.4, k_prior=20) == 4.8

    def test_degrades_to_e0_when_no_previous_season(self):
        result = exp.e1_component(5.0, 10, None, 4.4, k_prior=20)
        expected = exp.e0_component(5.0, 10, 4.4)
        assert result == expected

    def test_blend_moves_toward_current_season_as_games_accumulate(self):
        # previous season much higher than current-season raw -- as prior_games grows,
        # the blended estimate should move DOWN, toward the (lower) current-season raw rate
        early = exp.e1_component(3.0, 2, 6.0, 4.4, k_prior=20)
        late = exp.e1_component(3.0, 40, 6.0, 4.4, k_prior=20)
        assert late < early

    def test_deterministic(self):
        assert exp.e1_component(4.0, 10, 4.8, 4.4, 20) == exp.e1_component(4.0, 10, 4.8, 4.4, 20)


class TestRunPreventionComponentIdenticalAcrossCandidates:
    def test_run_prevention_is_e0_style_regardless_of_offense_candidate(self):
        """Proves the opponent's run-prevention construction never varies
        by candidate -- isolates the offense-prior lever completely."""
        source = open(SCRIPT_PATH).read()
        assert "def run_prevention_component" in source
        rp_source = ast.get_source_segment(source, _find_function_node("run_prevention_component"))
        assert "e0_component(" in rp_source
        assert "k_prior" not in rp_source
        assert "previous_season" not in rp_source.lower()


class TestKPriorFitDevOnly:
    def test_selects_from_fixed_preregistered_grid_only(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("fit_k_prior_dev_only"))
        assert "K_PRIOR_GRID" in source

    def test_never_examines_validation_or_holdout(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("fit_k_prior_dev_only"))
        assert "val_rows" not in source
        assert "holdout" not in source.lower()


class TestSelectionRule:
    def _robustness(self, n_pos, n_total):
        return {"nTeamsPositive": n_pos, "nTeamsTotal": n_total, "nTeamsNegative": n_total - n_pos}

    def test_fails_when_dev_not_improved(self):
        passes, reasons = exp.selection_passes(0.01, -0.001, -0.01, {}, self._robustness(20, 30))
        assert not passes

    def test_fails_when_dev_probability_not_improved(self):
        passes, reasons = exp.selection_passes(-0.01, 0.001, -0.01, {}, self._robustness(20, 30))
        assert not passes

    def test_fails_when_validation_does_not_replicate_direction(self):
        passes, reasons = exp.selection_passes(-0.01, -0.001, 0.01, {}, self._robustness(20, 30))
        assert not passes

    def test_fails_when_concentrated_in_few_teams(self):
        passes, reasons = exp.selection_passes(-0.01, -0.001, -0.01, {}, self._robustness(5, 30))
        assert not passes
        assert any("concentrated" in r for r in reasons)

    def test_passes_when_all_criteria_met(self):
        passes, reasons = exp.selection_passes(-0.01, -0.001, -0.01, {}, self._robustness(20, 30))
        assert passes
        assert reasons == []

    def test_never_examines_holdout_or_pinnacle_in_its_own_signature(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("selection_passes"))
        assert "holdout" not in source.lower()
        assert "pinnacle" not in source.lower()


class TestHoldoutInaccessibleDuringSelection:
    def test_holdout_only_evaluated_after_selection_check_in_main(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        selection_index = main_source.index("passes, reasons = selection_passes")
        holdout_index = main_source.index("if passes:\n        print(f\"[{EXPERIMENT_ID}] preregistered gate passed")
        assert selection_index < holdout_index

    def test_pinnacle_stage_gated_by_holdout_result_in_main(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        assert "if holdout_result is not None:" in main_source
        holdout_var_index = main_source.index("holdout_result = None")
        pinnacle_gate_index = main_source.index("if holdout_result is not None:")
        assert holdout_var_index < pinnacle_gate_index


class TestNbProbabilityCells:
    def test_none_for_nonpositive_means(self):
        assert exp.nb_probability_cells(None, 3.8) is None
        assert exp.nb_probability_cells(0.0, 3.8) is None

    def test_deterministic(self):
        assert exp.nb_probability_cells(4.0, 3.8) == exp.nb_probability_cells(4.0, 3.8)

    def test_probabilities_valid_range(self):
        cells = exp.nb_probability_cells(4.0, 3.8)
        for k, v in cells.items():
            assert 0.0 <= v <= 1.0, f"{k}={v} out of range"

    def test_includes_run_margin_family(self):
        cells = exp.nb_probability_cells(4.0, 3.8)
        for m in exp.MARGIN_THRESHOLDS:
            assert f"run_margin_win_by_at_least_{m}" in cells


class TestSeasonProgressBands:
    def test_bands_do_not_overlap(self):
        seen = set()
        for name, lo, hi in exp.SEASON_BANDS:
            band_range = set(range(lo, hi + 1))
            assert not (band_range & seen), f"{name} overlaps a previous band"
            seen |= band_range

    def test_bands_keyed_by_prior_games_not_game_number(self):
        # games_1_5 means game numbers 1-5, i.e. priorGames 0-4
        assert exp.SEASON_BANDS[0] == ("games_1_5", 0, 4)


class TestE2NotRunDocumented:
    def test_report_includes_not_run_status_for_e2(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        assert '"notRun"' in main_source
        assert "E2" in main_source


class TestPinnacleComputesE1NotJustE0:
    def test_main_computes_both_e0_and_e1_pinnacle_comparisons(self):
        """MLB-RSCH-0017's own preregistration requires evaluating whether
        the SURVIVING CANDIDATE (not just the control) narrows the
        Pinnacle gap -- the Pinnacle stage must compute an E1-specific
        proxy probability, not only reuse rsch0008's own E0-only enrich_row."""
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        assert "proxyMlHomeProb_E1" in main_source
        assert "proxyTotalOverProb_E1" in main_source
        assert '"PINNACLE/ML/E1"' in main_source
        assert '"PINNACLE/TOTAL/E1"' in main_source
        assert "mlE1" in main_source and "totalE1" in main_source

    def test_e1_pinnacle_reuses_frozen_e1_component_never_refits(self):
        """The E1 Pinnacle enrichment must reuse e1_component/
        run_prevention_component/expected_runs with the ALREADY-frozen
        k_prior and hfa_e0 -- never fit anything new against Pinnacle data."""
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        pinnacle_section = main_source[main_source.index("Pinnacle secondary stage"):]
        assert "e1_component(" in pinnacle_section
        assert "run_prevention_component(" in pinnacle_section
        assert "k_prior)" in pinnacle_section or "k_prior," in pinnacle_section
        assert "fit_k_prior" not in pinnacle_section
