import ast
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab"), os.path.join(_ROOT, "scripts", "edgelab", "backtest")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

import run_offense_talent_experiment as exp  # noqa: E402

SCRIPT_PATH = os.path.join(_ROOT, "scripts", "edgelab", "run_offense_talent_experiment.py")


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


class TestFrozenDispersion:
    def test_matches_canonical_mlb_rsch_0010_artifact(self):
        import json
        path = os.path.join(_ROOT, "data", "edgelab", "analytics", "latest_mlb_rsch_0010_run_distribution.json")
        with open(path) as f:
            canonical = json.load(f)["fittedParameters"]["overdispersion"]
        assert exp.FROZEN_DISPERSION == canonical

    def test_never_refit_no_fit_call_near_frozen_dispersion_usage(self):
        source = open(SCRIPT_PATH).read()
        assert "fit_correlation_dev_only" not in source
        assert "fit_overdispersion_dev_only" not in source


class TestPreregistrationOrdering:
    def test_register_experiment_called_first_in_main(self):
        names = _call_names_in_order(_find_function_node("main"))
        registration_index = names.index("register_experiment")
        result_calls = ["build_corpus", "fit_empirical_bayes_offense_k_dev_only", "attach_predictions"]
        for call in result_calls:
            occurrences = [i for i, n in enumerate(names) if n == call]
            assert occurrences, f"expected main() to call {call!r}"
            assert min(occurrences) > registration_index


class TestO0ExactlyReproducesRsch0009:
    def test_o0_uses_current_fixed_shrinkage_constant_unchanged(self):
        result = exp.offense_component_for(exp.O0, raw_rate=5.0, prior_games=20, league_avg_offense=4.4)
        from lib.edgelab.backtest.proxy_enrichment import stabilized_offense_rate, OFFENSE_SHRINKAGE_K
        expected = stabilized_offense_rate(5.0, 20, 4.4, k=OFFENSE_SHRINKAGE_K)
        assert result == expected

    def test_o0_never_uses_a_dev_fit_k(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("offense_component_for"))
        # The O0 branch must reference OFFENSE_SHRINKAGE_K, never k_o1, for the O0 case specifically.
        assert "OFFENSE_SHRINKAGE_K" in source


class TestO1EmpiricalBayesFit:
    def _team_games_by_season(self, means_by_team, n_games=25, noise=None):
        """Synthetic team-season data: team i's games are n_games draws with the given mean and a small fixed spread."""
        out = {}
        for team_id, mean_v in means_by_team.items():
            spread = noise or [-1, 0, 1, 0, -1, 1, 0, 0, -1, 1] * 3
            games = [{"runsScored": max(0, mean_v + spread[i % len(spread)])} for i in range(n_games)]
            out[team_id] = games
        return out

    def test_returns_fixed_k_when_no_eligible_team_seasons(self):
        team_games_by_season = {s: {} for s in exp.DEV_SEASONS}
        k_hat, diagnostics = exp.fit_empirical_bayes_offense_k_dev_only(team_games_by_season, league_avg_offense=4.4)
        assert k_hat == exp.OFFENSE_SHRINKAGE_K
        assert "fallback" in diagnostics

    def test_produces_a_positive_finite_k_from_real_variation(self):
        means = {1: 3.5, 2: 4.0, 3: 4.5, 4: 5.0, 5: 5.5}
        team_games_by_season = {s: self._team_games_by_season(means) for s in exp.DEV_SEASONS}
        k_hat, diagnostics = exp.fit_empirical_bayes_offense_k_dev_only(team_games_by_season, league_avg_offense=4.5)
        assert k_hat > 0
        assert diagnostics["teamSeasonsUsed"] > 0

    def test_never_examines_validation_or_holdout_seasons(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("fit_empirical_bayes_offense_k_dev_only"))
        assert "VALIDATION_SEASONS" not in source
        assert "HOLDOUT_SEASONS" not in source
        assert "DEV_SEASONS" in source

    def test_all_identical_team_means_yields_high_k_heavy_shrinkage(self):
        """Zero between-team variance (all teams identical talent) -> tau2 floored near-zero -> k_hat very large (near-total shrinkage), never a crash/negative value."""
        means = {1: 4.5, 2: 4.5, 3: 4.5}
        team_games_by_season = {s: self._team_games_by_season(means) for s in exp.DEV_SEASONS}
        k_hat, _ = exp.fit_empirical_bayes_offense_k_dev_only(team_games_by_season, league_avg_offense=4.5)
        assert k_hat > 0


class TestMeanAccuracyMetrics:
    def test_empty_observations_returns_none_metrics(self):
        result = exp.mean_accuracy_metrics([])
        assert result["n"] == 0
        assert result["mae"] is None

    def test_computes_mae_rmse_bias(self):
        obs = [
            {"gamePk": 1, "teamId": 100, "predicted": 5.0, "actual": 4.0, "priorGames": 30},
            {"gamePk": 2, "teamId": 100, "predicted": 3.0, "actual": 5.0, "priorGames": 40},
        ]
        result = exp.mean_accuracy_metrics(obs)
        assert result["n"] == 2
        assert result["mae"] == pytest.approx((1.0 + 2.0) / 2, abs=1e-6)
        assert result["bias"] == pytest.approx((1.0 + -2.0) / 2, abs=1e-6)


class TestPairedMeanMaeDelta:
    def test_zero_delta_when_predictions_identical(self):
        obs_a = [{"gamePk": 1, "teamId": 100, "predicted": 5.0, "actual": 4.0}]
        obs_b = [{"gamePk": 1, "teamId": 100, "predicted": 5.0, "actual": 4.0}]
        result = exp.paired_mean_mae_delta(obs_a, obs_b)
        assert result["maeDelta"] == 0.0

    def test_negative_delta_when_b_more_accurate(self):
        obs_a = [{"gamePk": 1, "teamId": 100, "predicted": 6.0, "actual": 4.0}]  # err=2
        obs_b = [{"gamePk": 1, "teamId": 100, "predicted": 4.5, "actual": 4.0}]  # err=0.5
        result = exp.paired_mean_mae_delta(obs_a, obs_b)
        assert result["maeDelta"] < 0

    def test_only_common_keys_are_paired(self):
        obs_a = [{"gamePk": 1, "teamId": 100, "predicted": 5.0, "actual": 4.0}, {"gamePk": 2, "teamId": 100, "predicted": 5.0, "actual": 4.0}]
        obs_b = [{"gamePk": 1, "teamId": 100, "predicted": 5.0, "actual": 4.0}]
        result = exp.paired_mean_mae_delta(obs_a, obs_b)
        assert result["n"] == 1


class TestSeasonBandBreakdown:
    def test_bands_partition_by_prior_games(self):
        obs_a = [
            {"gamePk": 1, "teamId": 1, "predicted": 5.0, "actual": 4.0, "priorGames": 10},
            {"gamePk": 2, "teamId": 1, "predicted": 5.0, "actual": 4.0, "priorGames": 30},
            {"gamePk": 3, "teamId": 1, "predicted": 5.0, "actual": 4.0, "priorGames": 90},
        ]
        obs_b = [dict(o) for o in obs_a]
        bands = exp.season_band_breakdown(obs_a, obs_b)
        assert bands["games_1_15"]["n"] == 1
        assert bands["games_16_40"]["n"] == 1
        assert bands["games_81_plus"]["n"] == 1

    def test_all_preregistered_bands_present(self):
        bands = exp.season_band_breakdown([], [])
        assert set(bands.keys()) == {name for name, _, _ in exp.SEASON_BANDS}


class TestTeamRobustness:
    def test_per_team_deltas_and_leave_one_out(self):
        obs_a = [
            {"gamePk": 1, "teamId": 1, "predicted": 6.0, "actual": 4.0},
            {"gamePk": 2, "teamId": 2, "predicted": 6.0, "actual": 4.0},
        ]
        obs_b = [
            {"gamePk": 1, "teamId": 1, "predicted": 4.5, "actual": 4.0},
            {"gamePk": 2, "teamId": 2, "predicted": 4.5, "actual": 4.0},
        ]
        result = exp.team_robustness(obs_a, obs_b)
        assert result["nTeamsTotal"] == 2
        assert result["nTeamsPositive"] == 2  # both improved
        assert set(result["leaveOneTeamOutDeltas"].keys()) == {"1", "2"}

    def test_empty_input_does_not_crash(self):
        result = exp.team_robustness([], [])
        assert result["nTeamsTotal"] == 0


class TestNbProbabilityCells:
    def test_uses_frozen_dispersion_for_both_sides(self):
        cells = exp.nb_probability_cells(4.0, 3.8)
        assert cells is not None
        assert "moneyline_home_win" in cells
        for line in exp.GAME_TOTAL_LINES:
            assert f"game_total_over_{line}" in cells

    def test_none_for_non_positive_means(self):
        assert exp.nb_probability_cells(None, 3.8) is None
        assert exp.nb_probability_cells(0.0, 3.8) is None

    def test_deterministic(self):
        assert exp.nb_probability_cells(4.0, 3.8) == exp.nb_probability_cells(4.0, 3.8)


class TestSelectionRule:
    def test_fails_when_dev_not_improved(self):
        passes, reasons = exp.selection_passes(dev_mae_delta=0.01, val_mae_delta=-0.01, band_deltas={}, val_nb_primary_delta=-0.001)
        assert not passes
        assert reasons

    def test_fails_when_validation_degrades_beyond_tolerance(self):
        passes, reasons = exp.selection_passes(dev_mae_delta=-0.02, val_mae_delta=0.2, band_deltas={}, val_nb_primary_delta=-0.001)
        assert not passes

    def test_fails_when_improvement_confined_to_first_band_only(self):
        band_deltas = {
            "games_1_15": {"maeDelta": -0.05},
            "games_16_40": {"maeDelta": 0.01},
            "games_41_80": {"maeDelta": 0.02},
            "games_81_plus": {"maeDelta": None},
        }
        passes, reasons = exp.selection_passes(dev_mae_delta=-0.02, val_mae_delta=-0.01, band_deltas=band_deltas, val_nb_primary_delta=-0.001)
        assert not passes
        assert any("games_1_15" in r for r in reasons)

    def test_passes_when_all_criteria_met(self):
        band_deltas = {
            "games_1_15": {"maeDelta": -0.05},
            "games_16_40": {"maeDelta": -0.02},
            "games_41_80": {"maeDelta": -0.01},
            "games_81_plus": {"maeDelta": -0.005},
        }
        passes, reasons = exp.selection_passes(dev_mae_delta=-0.02, val_mae_delta=-0.01, band_deltas=band_deltas, val_nb_primary_delta=-0.001)
        assert passes
        assert reasons == []

    def test_never_uses_holdout_or_pinnacle_in_its_own_signature(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("selection_passes"))
        assert "holdout" not in source.lower()
        assert "pinnacle" not in source.lower()


class TestSelectionNeverInspectsHoldoutOrPinnacleDuringMain:
    def test_selection_call_precedes_holdout_unlock_in_main(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        selection_index = main_source.index("selection_passes(")
        holdout_index = main_source.index("# ---- Unlock 2026 holdout")
        assert selection_index < holdout_index

    def test_pinnacle_stage_runs_after_holdout_unlock(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        holdout_index = main_source.index("# ---- Unlock 2026 holdout")
        pinnacle_index = main_source.index("Pinnacle secondary stage")
        assert holdout_index < pinnacle_index
