import ast
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab"), os.path.join(_ROOT, "scripts", "edgelab", "backtest")):
    if p not in sys.path:
        sys.path.insert(0, p)

import run_distribution_calibration_experiment as exp  # noqa: E402

SCRIPT_PATH = os.path.join(_ROOT, "scripts", "edgelab", "run_distribution_calibration_experiment.py")


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


class TestPreregistrationOrdering:
    def test_register_experiment_called_first_in_main(self):
        names = _call_names_in_order(_find_function_node("main"))
        registration_index = names.index("register_experiment")
        result_calls = ["build_rows_with_frozen_lambdas", "attach_candidate_predictions", "evaluate_split"]
        for call in result_calls:
            occurrences = [i for i, n in enumerate(names) if n == call]
            assert occurrences, f"expected main() to call {call!r}"
            assert min(occurrences) > registration_index


class TestFrozenMeanModelReuse:
    def test_frozen_components_match_rsch0009s_final_composition(self):
        assert exp.FROZEN_MEAN_COMPONENTS == frozenset({"offense", "bullpen"})

    def test_build_rows_uses_rsch0009_functions_unchanged(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("build_rows_with_frozen_lambdas"))
        for fn in (
            "rsch0009.load_all_team_games_with_venue", "rsch0009.load_relief_er9_games",
            "rsch0009.build_season_rows", "rsch0009.attach_stabilized_components",
            "rsch0009.fit_home_field_adjustment_for_components", "rsch0009.baseline_for_components",
        ):
            assert fn in source, f"expected {fn!r} in build_rows_with_frozen_lambdas"
        # expected_runs is proxy_model's own unchanged function, imported directly (not via rsch0009.)
        assert "expected_runs(" in source

    def test_mean_model_construction_never_reimplements_expected_runs_math(self):
        """No arithmetic combination of offenseRunsPerGame/runPreventionRunsAllowedPerGame
        appears directly in this function -- it must always go through the
        reused expected_runs() call, never a local reimplementation."""
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("build_rows_with_frozen_lambdas"))
        assert "offenseRunsPerGame" not in source
        assert "runPreventionRunsAllowedPerGame" not in source


class TestD3Excluded:
    def test_no_d3_module_attribute_exists(self):
        # The module docstring explains WHY D3 is excluded (a real,
        # documented design decision) -- but no D3 candidate constant,
        # joint-pmf branch, or evaluation logic exists in code.
        assert not hasattr(exp, "D3")

    def test_joint_for_candidate_has_no_d3_branch(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("joint_for_candidate"))
        assert "D3" not in source

    def test_only_three_candidates_defined(self):
        assert {exp.D0, exp.D1, exp.D2} == {"D0_poisson", "D1_negative_binomial", "D2_bivariate_poisson"}


class TestSelectionRuleNeverInspectsHoldoutOrPinnacle:
    def test_selection_block_in_main_never_references_holdout_rows(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        start = main_source.index("d1_dev_delta, d1_dev_improved_cells")
        end = main_source.index("# ---- Unlock 2026 holdout")
        selection_block = main_source[start:end]
        assert "holdout_rows" not in selection_block
        assert "pinnacle" not in selection_block.lower()

    def test_holdout_evaluated_only_after_final_candidate_frozen(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        freeze_index = main_source.index("final_candidate = D0")
        holdout_eval_index = main_source.index("d0_holdout = evaluate_split(holdout_rows")
        assert freeze_index < holdout_eval_index

    def test_pinnacle_stage_runs_only_after_holdout_and_final_candidate_are_both_settled(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        holdout_index = main_source.index("d0_holdout = evaluate_split(holdout_rows")
        pinnacle_index = main_source.index("rsch0008.build_matched_rows")
        assert holdout_index < pinnacle_index

    def test_aggregate_primary_delta_never_references_pinnacle(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("aggregate_primary_delta"))
        assert "pinnacle" not in source.lower()


class TestNoNewOddsAcquisition:
    def test_script_never_imports_network_fetch_modules(self):
        source = open(SCRIPT_PATH).read()
        assert "fetch_historical_pinnacle_cache" not in source
        assert "urllib" not in source
        assert "ODDS_API_KEY" not in source

    def test_reuses_rsch0008_build_matched_rows_and_enrich_row_unchanged(self):
        source = open(SCRIPT_PATH).read()
        assert "rsch0008.build_matched_rows" in source
        assert "rsch0008.enrich_row" in source


class TestJointForCandidate:
    def test_d0_uses_poisson(self):
        joint = exp.joint_for_candidate(exp.D0, 4.2, 3.8)
        from lib.edgelab.backtest.run_distributions import joint_pmf_sums_to_one
        assert joint_pmf_sums_to_one(joint, max_runs=exp.MAX_RUNS, tolerance=1e-4)

    def test_d1_requires_dispersion_and_uses_negative_binomial(self):
        joint_poisson_like = exp.joint_for_candidate(exp.D1, 4.2, 3.8, dispersion=0.0)
        joint_d0 = exp.joint_for_candidate(exp.D0, 4.2, 3.8)
        # dispersion=0 -> D1 degenerates to Poisson -> should match D0 closely
        assert abs(joint_poisson_like(3, 3) - joint_d0(3, 3)) < 1e-9

    def test_d2_uses_bivariate_poisson(self):
        joint = exp.joint_for_candidate(exp.D2, 4.2, 3.8, lambda_c=1.0)
        from lib.edgelab.backtest.run_distributions import joint_pmf_sums_to_one
        assert joint_pmf_sums_to_one(joint, max_runs=exp.MAX_RUNS, tolerance=1e-4)

    def test_unknown_candidate_raises(self):
        import pytest
        with pytest.raises(ValueError):
            exp.joint_for_candidate("bogus", 4.2, 3.8)


class TestEvaluateRowConsistency:
    def _matrix_for(self, lam_h=4.2, lam_a=3.8):
        joint = exp.joint_for_candidate(exp.D0, lam_h, lam_a)
        return exp._joint_matrix(joint)

    def test_moneyline_complement_sums_to_one(self):
        result = exp.evaluate_row(self._matrix_for())
        assert abs((result["pHomeWin"] + result["pPush"] + result["pAwayWin"]) - 1.0) < 1e-6

    def test_total_over_line_decreases_as_line_increases(self):
        result = exp.evaluate_row(self._matrix_for())
        overs = [result[f"pTotalOver_{line}"] for line in exp.GAME_TOTAL_LINES]
        assert overs == sorted(overs, reverse=True)

    def test_team_total_over_line_decreases_as_line_increases(self):
        result = exp.evaluate_row(self._matrix_for())
        home_overs = [result[f"pHomeTeamTotalOver_{line}"] for line in exp.TEAM_TOTAL_LINES]
        assert home_overs == sorted(home_overs, reverse=True)

    def test_win_by_3_plus_never_exceeds_win_by_2_plus(self):
        result = exp.evaluate_row(self._matrix_for())
        assert result["pWinByAtLeast_3"] <= result["pWinByAtLeast_2"]

    def test_margin_probabilities_deterministic(self):
        matrix = self._matrix_for()
        first = exp.evaluate_row(matrix)
        second = exp.evaluate_row(matrix)
        assert first == second


class TestAggregatePrimaryDelta:
    def _split(self, brier_result_offset):
        base = 0.25
        return {
            "gameResult": {"brier": base + brier_result_offset},
            "gameTotal": {str(line): {"brier": base + brier_result_offset} for line in exp.GAME_TOTAL_LINES},
        }

    def test_negative_offset_improves_all_cells(self):
        d0 = self._split(0.0)
        candidate = self._split(-0.01)
        delta, improved = exp.aggregate_primary_delta(candidate, d0)
        assert delta < 0
        assert improved == len(exp.PRIMARY_CELLS)

    def test_zero_offset_no_improvement(self):
        d0 = self._split(0.0)
        candidate = self._split(0.0)
        delta, improved = exp.aggregate_primary_delta(candidate, d0)
        assert delta == 0.0
        assert improved == 0


class TestPrimaryCellsFixed:
    def test_primary_cells_is_game_result_plus_four_totals(self):
        assert exp.PRIMARY_CELLS == ["gameResult"] + [f"gameTotal_{line}" for line in exp.GAME_TOTAL_LINES]
        assert len(exp.PRIMARY_CELLS) == 5
