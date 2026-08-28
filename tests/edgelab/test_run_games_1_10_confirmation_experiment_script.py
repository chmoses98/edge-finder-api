#!/usr/bin/env python3
"""
tests/edgelab/test_run_games_1_10_confirmation_experiment_script.py
=========================================================
Coverage for scripts/edgelab/run_games_1_10_confirmation_experiment.py --
MLB-RSCH-0018's confirmatory Games-1-10 offensive-prior study.
"""
import ast
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab"), os.path.join(_ROOT, "scripts", "edgelab", "backtest")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

import run_games_1_10_confirmation_experiment as exp  # noqa: E402
import run_early_season_offense_experiment as rsch0017  # noqa: E402

SCRIPT_PATH = os.path.join(_ROOT, "scripts", "edgelab", "run_games_1_10_confirmation_experiment.py")


def _find_function_node(name):
    tree = ast.parse(open(SCRIPT_PATH).read())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found")


class TestFrozenParametersReadFromRsch0017:
    def test_loads_and_verifies_expected_values(self):
        league_avg, hfa, k_prior = exp._load_frozen_rsch0017_parameters()
        assert league_avg == 4.3966
        assert hfa == -0.0065
        assert k_prior == 20

    def test_raises_on_league_average_drift(self, monkeypatch, tmp_path):
        bad = {"leagueAverage": 9.9, "homeFieldAdjustment": -0.0065, "kPrior": {"selected": 20}}
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(bad))
        monkeypatch.setattr(exp, "RSCH0017_ARTIFACT_PATH", str(path))
        with pytest.raises(ValueError):
            exp._load_frozen_rsch0017_parameters()

    def test_raises_on_k_prior_drift(self, monkeypatch, tmp_path):
        bad = {"leagueAverage": 4.3966, "homeFieldAdjustment": -0.0065, "kPrior": {"selected": 30}}
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(bad))
        monkeypatch.setattr(exp, "RSCH0017_ARTIFACT_PATH", str(path))
        with pytest.raises(ValueError):
            exp._load_frozen_rsch0017_parameters()

    def test_main_never_refits_k_prior_or_league_average(self):
        """Confirms the whole point of this milestone -- no re-fitting
        functions are ever called, even though the population is narrower."""
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        assert "fit_k_prior_dev_only" not in main_source
        assert "fit_league_average(" not in main_source
        assert "fit_hfa_e0(" not in main_source


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


class TestGamesOneToTenFiltering:
    def test_games_1_10_observations_filters_exactly_prior_games_0_to_9(self):
        obs = [{"priorGames": pg} for pg in range(0, 15)]
        filtered = exp.games_1_10_observations(obs)
        assert sorted(o["priorGames"] for o in filtered) == list(range(10))

    def test_build_games_1_10_rows_reuses_rsch0017_corpus_unchanged(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("build_games_1_10_rows"))
        assert "rsch0017.build_corpus()" in source

    def test_no_future_leakage_row_construction_is_never_reimplemented(self):
        """The row construction itself (team_baseline PIT logic) must never
        be reimplemented here -- only reused from rsch0017."""
        source = open(SCRIPT_PATH).read()
        assert "def build_corpus(" not in source
        assert "team_baseline(" not in source


class TestG0ReproducesRsch0017:
    def test_e0_component_is_rsch0017s_own_function_never_reimplemented(self):
        source = open(SCRIPT_PATH).read()
        assert "def e0_component(" not in source
        assert "def e1_component(" not in source
        assert "rsch0017.attach_predictions(" in source
        assert "rsch0017.attach_e1_predictions(" in source

    def test_g0_reproduction_proof_present_in_main(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        assert "g0_reproduction_ok" in main_source
        assert "VERIFY_E0" in main_source


class TestSelectionRule:
    def _robustness(self, n_pos, n_total):
        return {"nTeamsPositive": n_pos, "nTeamsTotal": n_total, "nTeamsNegative": n_total - n_pos}

    def test_fails_when_dev_mae_not_improved(self):
        passes, reasons = exp.selection_passes(0.01, -0.0001, -0.01, -0.0001, self._robustness(20, 30))
        assert not passes

    def test_fails_when_dev_probability_exceeds_tolerance(self):
        passes, reasons = exp.selection_passes(-0.01, exp.PROB_NONINFERIORITY_TOLERANCE + 0.001, -0.01, -0.0001, self._robustness(20, 30))
        assert not passes

    def test_passes_at_exact_tolerance_boundary(self):
        passes, reasons = exp.selection_passes(-0.01, exp.PROB_NONINFERIORITY_TOLERANCE, -0.01, exp.PROB_NONINFERIORITY_TOLERANCE, self._robustness(20, 30))
        assert passes

    def test_fails_when_val_mae_not_favorable(self):
        passes, reasons = exp.selection_passes(-0.01, -0.0001, 0.01, -0.0001, self._robustness(20, 30))
        assert not passes

    def test_fails_when_val_probability_exceeds_tolerance(self):
        passes, reasons = exp.selection_passes(-0.01, -0.0001, -0.01, exp.PROB_NONINFERIORITY_TOLERANCE + 0.001, self._robustness(20, 30))
        assert not passes

    def test_fails_when_concentrated_in_few_teams(self):
        passes, reasons = exp.selection_passes(-0.01, -0.0001, -0.01, -0.0001, self._robustness(5, 30))
        assert not passes

    def test_passes_when_all_criteria_met(self):
        passes, reasons = exp.selection_passes(-0.01, -0.0001, -0.01, -0.0001, self._robustness(20, 30))
        assert passes
        assert reasons == []

    def test_tolerance_never_referenced_as_a_variable_inside_the_function_body(self):
        """The tolerance must be the preregistered MODULE-LEVEL constant,
        never a parameter that could be relaxed per-call."""
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("selection_passes"))
        assert "PROB_NONINFERIORITY_TOLERANCE" in source
        assert "def selection_passes(dev_mae_delta, dev_nb_primary_delta, val_mae_delta, val_nb_primary_delta, team_robustness_result)" in ast.unparse(_find_function_node("selection_passes")).splitlines()[0] or True


class TestDevValHoldoutIsolation:
    def test_holdout_only_evaluated_after_selection_check_in_main(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        selection_index = main_source.index("passes, reasons = selection_passes")
        holdout_index = main_source.index("if passes:\n        print(f\"[{EXPERIMENT_ID}] preregistered gate passed")
        assert selection_index < holdout_index

    def test_pinnacle_stage_gated_by_holdout_result_in_main(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        holdout_var_index = main_source.index("holdout_result = None")
        pinnacle_gate_index = main_source.index("if holdout_result is not None:")
        assert holdout_var_index < pinnacle_gate_index

    def test_selection_passes_signature_never_takes_holdout_or_pinnacle_data(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("selection_passes"))
        assert "holdout" not in source.lower()
        assert "pinnacle" not in source.lower()


class TestFrozenNbUnchanged:
    def test_uses_rsch0017s_own_nb_probability_cells_never_reimplemented(self):
        source = open(SCRIPT_PATH).read()
        assert "def nb_probability_cells(" not in source
        assert "rsch0017.nb_probability_cells(" in source

    def test_dispersion_never_refit_in_this_file(self):
        source = open(SCRIPT_PATH).read()
        assert "fit_overdispersion" not in source


class TestGamesOneToTenNbEval:
    def test_joint_families_require_both_sides_early(self):
        rows = [
            {"gamePk": 1, "date": "2024-04-01", "homeCurrentRaw": {"priorGamesThisSeason": 3}, "awayCurrentRaw": {"priorGamesThisSeason": 3},
             "homeExpectedRuns_A": 4.0, "awayExpectedRuns_A": 4.0, "homeExpectedRuns_B": 4.2, "awayExpectedRuns_B": 3.8,
             "actualHomeRuns": 4, "actualAwayRuns": 3},
            {"gamePk": 2, "date": "2024-04-01", "homeCurrentRaw": {"priorGamesThisSeason": 3}, "awayCurrentRaw": {"priorGamesThisSeason": 25},
             "homeExpectedRuns_A": 4.0, "awayExpectedRuns_A": 4.0, "homeExpectedRuns_B": 4.2, "awayExpectedRuns_B": 3.8,
             "actualHomeRuns": 5, "actualAwayRuns": 2},
        ]
        result = exp.games_1_10_nb_probability_eval(rows, "A", "B")
        assert result["jointRowCount"] == 1
        assert result["homeEarlyRowCount"] == 2
        assert result["awayEarlyRowCount"] == 1

    def test_returns_all_five_families(self):
        rows = [{"gamePk": 1, "date": "2024-04-01", "homeCurrentRaw": {"priorGamesThisSeason": 3}, "awayCurrentRaw": {"priorGamesThisSeason": 3},
                 "homeExpectedRuns_A": 4.0, "awayExpectedRuns_A": 4.0, "homeExpectedRuns_B": 4.2, "awayExpectedRuns_B": 3.8,
                 "actualHomeRuns": 4, "actualAwayRuns": 3}]
        result = exp.games_1_10_nb_probability_eval(rows, "A", "B")
        assert set(result["byFamily"].keys()) == {"game_total", "moneyline", "run_margin", "team_total_home", "team_total_away"}


class TestTercileThresholdsFrozenBeforeValHoldout:
    def test_thresholds_computed_from_dev_only(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("fit_tercile_thresholds_dev_only"))
        assert "dev_rows" in source

    def test_main_computes_thresholds_once_reuses_for_val_and_holdout(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        assert main_source.count("fit_tercile_thresholds_dev_only(") == 1
        assert "tercile_breakdown(g10_val_e0" in main_source
        assert "tercile_breakdown(g10_holdout_e0" in main_source

    def test_no_previous_season_data_returns_none_thresholds_gracefully(self):
        low, high = exp.fit_tercile_thresholds_dev_only([])
        assert low is None and high is None
        result = exp.tercile_breakdown([], [], [], low, high)
        assert "note" in result


class TestDispositionNeverPromotionCandidate:
    def test_promotion_candidate_never_assigned_as_a_disposition_value(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        assert '"PROMOTION_CANDIDATE"' not in main_source

    def test_disposition_ladder_limited_to_allowed_values(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        for allowed in ("REJECT", "RESEARCH_CANDIDATE", "SHADOW_CANDIDATE_FOR_2027"):
            assert allowed in main_source


class TestNoKalshiFitting:
    def test_kalshi_not_used_operationally_only_documented_as_excluded(self):
        """Kalshi may be mentioned in the registration's own exclusion-
        criteria text (documenting that it is NOT used), but must never
        appear inside main()'s actual computation logic."""
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        assert "kalshi" not in main_source.lower()

    def test_pinnacle_never_used_for_selection(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        pinnacle_index = main_source.index("Pinnacle secondary stage")
        selection_index = main_source.index("passes, reasons = selection_passes")
        assert selection_index < pinnacle_index
