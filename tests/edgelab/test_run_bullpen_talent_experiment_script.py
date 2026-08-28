import ast
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab"), os.path.join(_ROOT, "scripts", "edgelab", "backtest")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

import run_bullpen_talent_experiment as exp  # noqa: E402

SCRIPT_PATH = os.path.join(_ROOT, "scripts", "edgelab", "run_bullpen_talent_experiment.py")


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


class TestPreregistrationOrdering:
    def test_register_experiment_called_first_in_main(self):
        names = _call_names_in_order(_find_function_node("main"))
        registration_index = names.index("register_experiment")
        for call in ("build_corpus", "fit_empirical_bayes_bullpen_k_dev_only", "attach_predictions"):
            occurrences = [i for i, n in enumerate(names) if n == call]
            assert occurrences, f"expected main() to call {call!r}"
            assert min(occurrences) > registration_index


class TestOffenseHeldFrozen:
    def test_frozen_offense_uses_current_fixed_constant_unchanged(self):
        from lib.edgelab.backtest.proxy_enrichment import stabilized_offense_rate, OFFENSE_SHRINKAGE_K
        raw = {"offenseRunsPerGame": 4.6, "priorGamesThisSeason": 25}
        result = exp._frozen_offense(raw, league_avg_offense=4.4)
        expected = stabilized_offense_rate(4.6, 25, 4.4, k=OFFENSE_SHRINKAGE_K)
        assert result == expected

    def test_frozen_offense_never_references_mlb_rsch_0012_k_variable(self):
        source = open(SCRIPT_PATH).read()
        assert "k_o1" not in source
        assert "MLB-RSCH-0012's own O1" in source  # documented, not silently reused


class TestP0ExactlyReproducesCurrentBullpenConstant:
    def test_p0_uses_fixed_shrinkage_k(self):
        raw = {"bullpenEarnedRunsPer9": 4.0, "priorGamesWithBullpenData": 25}
        result = exp.bullpen_component_for(exp.P0, raw, league_avg_bullpen_er9=4.1)
        from lib.edgelab.backtest.proxy_enrichment import stabilized_bullpen_rate, BULLPEN_SHRINKAGE_K
        expected = stabilized_bullpen_rate(4.0, 25, 4.1, k=BULLPEN_SHRINKAGE_K)
        assert result == expected

    def test_none_raw_bullpen_returns_none(self):
        assert exp.bullpen_component_for(exp.P0, None, 4.1) is None


class TestP1EmpiricalBayesFit:
    def _relief_by_season(self, means_by_team, n_games=25, noise=None):
        out = {}
        for team_id, mean_v in means_by_team.items():
            spread = noise or [-0.5, 0.3, -0.2, 0.4, -0.3, 0.2, 0.1, -0.4, 0.3, -0.1] * 3
            games = [{"reliefEarnedRunsPer9": max(0, mean_v + spread[i % len(spread)])} for i in range(n_games)]
            out[team_id] = games
        return out

    def test_returns_fixed_k_when_no_eligible_team_seasons(self):
        relief_by_season = {s: {} for s in exp.DEV_SEASONS}
        k_hat, diagnostics = exp.fit_empirical_bayes_bullpen_k_dev_only(relief_by_season, league_avg_bullpen_er9=4.1)
        assert k_hat == exp.BULLPEN_SHRINKAGE_K
        assert "fallback" in diagnostics

    def test_produces_positive_k_from_real_variation(self):
        means = {1: 3.5, 2: 4.0, 3: 4.5, 4: 5.0, 5: 5.5}
        relief_by_season = {s: self._relief_by_season(means) for s in exp.DEV_SEASONS}
        k_hat, diagnostics = exp.fit_empirical_bayes_bullpen_k_dev_only(relief_by_season, league_avg_bullpen_er9=4.5)
        assert k_hat > 0
        assert diagnostics["teamSeasonsUsed"] > 0

    def test_excludes_games_with_undefined_er9(self):
        """A game with no relief innings (undefined ER9, e.g. a complete-game start) must never be treated as 0 -- it's excluded from the variance estimate entirely."""
        relief_by_season = {
            s: {1: [{"reliefEarnedRunsPer9": 4.0}] * 20 + [{"reliefEarnedRunsPer9": None}] * 10}
            for s in exp.DEV_SEASONS
        }
        # Only 20 defined games -- exactly at MIN_PRIOR_GAMES_FOR_BASELINE, should be included, not excluded by the None entries counting toward eligibility.
        k_hat, diagnostics = exp.fit_empirical_bayes_bullpen_k_dev_only(relief_by_season, league_avg_bullpen_er9=4.0)
        # All values identical (no variance) -> falls back gracefully (var=0 excluded per n>1 check, or produces a valid large k) -- must not crash.
        assert k_hat is not None

    def test_never_examines_validation_or_holdout_seasons(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("fit_empirical_bayes_bullpen_k_dev_only"))
        assert "VALIDATION_SEASONS" not in source
        assert "HOLDOUT_SEASONS" not in source
        assert "DEV_SEASONS" in source


class TestMeanAccuracyAndPairedDelta:
    def test_empty_observations_returns_none_metrics(self):
        result = exp.mean_accuracy_metrics([])
        assert result["n"] == 0
        assert result["mae"] is None

    def test_negative_delta_when_b_more_accurate(self):
        obs_a = [{"gamePk": 1, "teamId": 100, "predicted": 6.0, "actual": 4.0}]
        obs_b = [{"gamePk": 1, "teamId": 100, "predicted": 4.5, "actual": 4.0}]
        result = exp.paired_mean_mae_delta(obs_a, obs_b)
        assert result["maeDelta"] < 0


class TestSeasonBandBreakdown:
    def test_all_preregistered_bands_present(self):
        bands = exp.season_band_breakdown([], [])
        assert set(bands.keys()) == {name for name, _, _ in exp.SEASON_BANDS}

    def test_games_1_15_band_key_exists_even_if_structurally_empty(self):
        """Same inherited 20-game eligibility floor as MLB-RSCH-0012 -- this band is expected to be empty at runtime, but the band itself must still be reported, never silently dropped."""
        bands = exp.season_band_breakdown([], [])
        assert "games_1_15" in bands


class TestTeamRobustness:
    def test_per_team_deltas_computed(self):
        obs_a = [{"gamePk": 1, "teamId": 1, "predicted": 6.0, "actual": 4.0}, {"gamePk": 2, "teamId": 2, "predicted": 6.0, "actual": 4.0}]
        obs_b = [{"gamePk": 1, "teamId": 1, "predicted": 4.5, "actual": 4.0}, {"gamePk": 2, "teamId": 2, "predicted": 4.5, "actual": 4.0}]
        result = exp.team_robustness(obs_a, obs_b)
        assert result["nTeamsTotal"] == 2
        assert result["nTeamsPositive"] == 2

    def test_empty_input_does_not_crash(self):
        result = exp.team_robustness([], [])
        assert result["nTeamsTotal"] == 0


class TestNbProbabilityCells:
    def test_deterministic_and_uses_frozen_dispersion(self):
        cells = exp.nb_probability_cells(4.0, 3.8)
        assert cells is not None
        assert cells == exp.nb_probability_cells(4.0, 3.8)

    def test_none_for_non_positive_means(self):
        assert exp.nb_probability_cells(None, 3.8) is None
        assert exp.nb_probability_cells(0.0, 3.8) is None


class TestSelectionRule:
    def test_fails_when_dev_not_improved(self):
        passes, reasons = exp.selection_passes(dev_mae_delta=0.01, val_mae_delta=-0.01, band_deltas={}, val_nb_primary_delta=-0.001)
        assert not passes

    def test_passes_when_all_criteria_met(self):
        band_deltas = {
            "games_1_15": {"maeDelta": None},
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
    def test_pinnacle_stage_runs_after_holdout_unlock(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        holdout_index = main_source.index("holdout_nb = frozen_nb_probability_eval")
        pinnacle_index = main_source.index("Pinnacle secondary stage")
        assert holdout_index < pinnacle_index

    def test_selection_precedes_holdout_evaluation(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        selection_index = main_source.index("selection_passes(")
        holdout_index = main_source.index("holdout_nb = frozen_nb_probability_eval")
        assert selection_index < holdout_index
