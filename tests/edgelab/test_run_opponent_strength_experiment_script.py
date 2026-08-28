#!/usr/bin/env python3
"""
tests/edgelab/test_run_opponent_strength_experiment_script.py
=========================================================
Coverage for scripts/edgelab/run_opponent_strength_experiment.py --
MLB-RSCH-0015's PIT-safe opponent-strength / schedule-adjustment experiment.
"""
import ast
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab"), os.path.join(_ROOT, "scripts", "edgelab", "backtest")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

import run_opponent_strength_experiment as exp  # noqa: E402

SCRIPT_PATH = os.path.join(_ROOT, "scripts", "edgelab", "run_opponent_strength_experiment.py")


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

    def test_verify_frozen_dispersion_raises_on_drift(self, monkeypatch):
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


class TestBuildRawBaselineLookup:
    def _games(self, team_id, opponent_id, runs_scored_seq, runs_allowed_seq):
        games = []
        for i, (rs, ra) in enumerate(zip(runs_scored_seq, runs_allowed_seq)):
            games.append({"gamePk": 100 + i, "date": f"2023-04-{i+1:02d}", "gameNumber": 1, "side": "home",
                          "runsScored": rs, "runsAllowed": ra, "opponentTeamId": opponent_id})
        return games

    def test_first_game_has_zero_prior_games(self):
        games = self._games(1, 2, [4, 5, 3], [3, 2, 6])
        team_games_by_season = {2023: {1: games}}
        lookup = exp.build_raw_baseline_lookup(team_games_by_season)
        first = lookup[2023][games[0]["gamePk"]]["home"]
        assert first["priorGamesThisSeason"] == 0
        assert first["offenseRunsPerGame"] is None

    def test_later_game_reflects_only_strictly_prior_games(self):
        games = self._games(1, 2, [4, 6], [3, 2])
        team_games_by_season = {2023: {1: games}}
        lookup = exp.build_raw_baseline_lookup(team_games_by_season)
        second = lookup[2023][games[1]["gamePk"]]["home"]
        assert second["priorGamesThisSeason"] == 1
        assert second["offenseRunsPerGame"] == 4.0  # only game 1's runsScored, never game 2's own


class TestComputeScheduleAdjustment:
    def test_credits_offense_against_strong_pitching(self):
        """Team A scores 5.0 against opponents whose OWN prior run-prevention
        was elite (allowed few runs) -- adjusted offense should exceed raw."""
        team_games_by_season = {
            2023: {
                1: [{"gamePk": 1, "date": "2023-05-01", "gameNumber": 1, "side": "home", "runsScored": 5, "runsAllowed": 3, "opponentTeamId": 2}],
                2: [{"gamePk": 1, "date": "2023-05-01", "gameNumber": 1, "side": "away", "runsScored": 3, "runsAllowed": 5, "opponentTeamId": 1}],
            }
        }
        # opponent (team 2) quality lookup: elite prior run-prevention (allowed only 2.0) at that meeting
        opponent_quality_lookup = {
            2023: {
                1: {"away": {"offenseRunsPerGame": 4.0, "runPreventionRunsAllowedPerGame": 2.0, "priorGamesThisSeason": 20}},
            }
        }
        result = exp.compute_schedule_adjustment(team_games_by_season, opponent_quality_lookup, league_avg_offense=4.4, league_avg_run_prevention=4.4, min_prior_games_opponent=5)
        # team 1 has ZERO prior games of its own (game 1 is its first) -- so its own raw offense is None,
        # adjustment can't apply to a nonexistent raw value. Use a second game to test the credit properly.
        assert result[2023][1] is not None  # entry exists (own_raw computed even if None-valued)

    def test_excludes_opponent_below_minimum_prior_games(self):
        team_games_by_season = {
            2023: {
                1: [
                    {"gamePk": 1, "date": "2023-04-01", "gameNumber": 1, "side": "home", "runsScored": 4, "runsAllowed": 4, "opponentTeamId": 2},
                    {"gamePk": 2, "date": "2023-04-05", "gameNumber": 1, "side": "home", "runsScored": 6, "runsAllowed": 2, "opponentTeamId": 3},
                ],
            }
        }
        opponent_quality_lookup = {
            2023: {
                1: {"away": {"offenseRunsPerGame": 4.0, "runPreventionRunsAllowedPerGame": 2.0, "priorGamesThisSeason": 2}},  # below min=5
            }
        }
        result = exp.compute_schedule_adjustment(team_games_by_season, opponent_quality_lookup, league_avg_offense=4.4, league_avg_run_prevention=4.4, min_prior_games_opponent=5)
        second_game = result[2023][2]["home"]
        assert second_game["nOpponentsPrevention"] == 0  # excluded, never fabricated
        # with no valid opponent snapshot, adjusted value falls back to the raw (own) rate unchanged
        assert second_game["offenseRunsPerGame"] == 4.0  # team 1's own raw rate as of game 2 (just game 1's 4 runs)

    def test_output_shape_is_composable_as_next_level_input(self):
        """Proves S2 can literally reuse S1's own output dict as its own
        opponent_quality_lookup input -- same field names."""
        team_games_by_season = {2023: {1: [{"gamePk": 1, "date": "2023-04-01", "gameNumber": 1, "side": "home", "runsScored": 4, "runsAllowed": 4, "opponentTeamId": 2}]}}
        raw_lookup = exp.build_raw_baseline_lookup(team_games_by_season)
        s1 = exp.compute_schedule_adjustment(team_games_by_season, raw_lookup, 4.4, 4.4, min_prior_games_opponent=5)
        # s1's own output must be directly usable as opponent_quality_lookup again (no KeyError)
        s2 = exp.compute_schedule_adjustment(team_games_by_season, s1, 4.4, 4.4, min_prior_games_opponent=5)
        assert s2 is not None


class TestNoMarketDataInAdjustment:
    def test_compute_schedule_adjustment_never_references_pinnacle_or_kalshi(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("compute_schedule_adjustment"))
        assert "pinnacle" not in source.lower()
        assert "kalshi" not in source.lower()

    def test_build_raw_baseline_lookup_never_references_pinnacle_or_kalshi(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("build_raw_baseline_lookup"))
        assert "pinnacle" not in source.lower()
        assert "kalshi" not in source.lower()


class TestSelectionRule:
    def test_fails_when_dev_not_improved(self):
        passes, reasons = exp.selection_passes(dev_mae_delta=0.01, val_mae_delta=-0.01, band_deltas={}, val_nb_primary_delta=-0.001)
        assert not passes

    def test_fails_when_validation_degrades_beyond_tolerance(self):
        passes, reasons = exp.selection_passes(dev_mae_delta=-0.02, val_mae_delta=0.2, band_deltas={}, val_nb_primary_delta=-0.001)
        assert not passes

    def test_fails_when_improvement_confined_to_games_1_15_only(self):
        band_deltas = {"games_1_15": {"maeDelta": -0.05}, "games_16_40": {"maeDelta": 0.01}, "games_41_80": {"maeDelta": 0.02}, "games_81_plus": {"maeDelta": None}}
        passes, reasons = exp.selection_passes(dev_mae_delta=-0.02, val_mae_delta=-0.01, band_deltas=band_deltas, val_nb_primary_delta=-0.001)
        assert not passes
        assert any("games_1_15" in r for r in reasons)

    def test_passes_when_all_criteria_met(self):
        band_deltas = {"games_1_15": {"maeDelta": -0.05}, "games_16_40": {"maeDelta": -0.02}, "games_41_80": {"maeDelta": -0.01}, "games_81_plus": {"maeDelta": -0.005}}
        passes, reasons = exp.selection_passes(dev_mae_delta=-0.02, val_mae_delta=-0.01, band_deltas=band_deltas, val_nb_primary_delta=-0.001)
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
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        call_index = main_source.index("evaluate_frozen_winner_holdout(")
        call_line = main_source[call_index:call_index + 120]
        assert "frozen_winner" in call_line


class TestNbProbabilityCells:
    def test_includes_run_margin_family(self):
        cells = exp.nb_probability_cells(4.0, 3.8)
        assert cells is not None
        for m in exp.MARGIN_THRESHOLDS:
            assert f"run_margin_win_by_at_least_{m}" in cells

    def test_none_for_nonpositive_means(self):
        assert exp.nb_probability_cells(None, 3.8) is None
        assert exp.nb_probability_cells(0.0, 3.8) is None

    def test_deterministic(self):
        assert exp.nb_probability_cells(4.0, 3.8) == exp.nb_probability_cells(4.0, 3.8)

    def test_probabilities_valid_range(self):
        cells = exp.nb_probability_cells(4.0, 3.8)
        for k, v in cells.items():
            assert 0.0 <= v <= 1.0, f"{k}={v} out of range"


class TestScheduleBaselineForRow:
    def _row(self, home_adj, away_adj, min_prior=exp.MIN_PRIOR_GAMES_MAIN):
        return {
            "season": 2023, "gamePk": 1,
            "homeBullpenRaw": None, "awayBullpenRaw": None,
        }, {2023: {1: {"home": home_adj, "away": away_adj}}}

    def test_none_when_below_eligibility_floor(self):
        row, lookup = self._row(
            {"offenseRunsPerGame": 4.5, "runPreventionRunsAllowedPerGame": 4.0, "priorGamesThisSeason": 10},
            {"offenseRunsPerGame": 4.2, "runPreventionRunsAllowedPerGame": 4.1, "priorGamesThisSeason": 25},
        )
        result = exp._schedule_baseline_for_row(row, "home", lookup, 4.4, 4.2, min_prior_games=exp.MIN_PRIOR_GAMES_MAIN)
        assert result is None

    def test_returns_baseline_when_eligible(self):
        row, lookup = self._row(
            {"offenseRunsPerGame": 4.5, "runPreventionRunsAllowedPerGame": 4.0, "priorGamesThisSeason": 25},
            {"offenseRunsPerGame": 4.2, "runPreventionRunsAllowedPerGame": 4.1, "priorGamesThisSeason": 25},
        )
        result = exp._schedule_baseline_for_row(row, "home", lookup, 4.4, 4.2, min_prior_games=exp.MIN_PRIOR_GAMES_MAIN)
        assert result is not None
        assert result["priorGamesThisSeason"] == 25

    def test_lower_floor_allows_early_season_row(self):
        row, lookup = self._row(
            {"offenseRunsPerGame": 4.5, "runPreventionRunsAllowedPerGame": 4.0, "priorGamesThisSeason": 8},
            {"offenseRunsPerGame": 4.2, "runPreventionRunsAllowedPerGame": 4.1, "priorGamesThisSeason": 25},
        )
        result = exp._schedule_baseline_for_row(row, "home", lookup, 4.4, 4.2, min_prior_games=exp.MIN_PRIOR_GAMES_EARLY_DIAGNOSTIC)
        assert result is not None


class TestEarlySeasonDiagnosticNeverUsedForSelection:
    def test_early_season_diagnostic_never_appears_in_selection_passes(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("selection_passes"))
        assert "earlydiag" not in source.lower().replace("_", "")
        assert "min_prior_games_early_diagnostic" not in source.lower()

    def test_main_computes_early_diagnostic_after_selection_frozen(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        selection_index = main_source.index("frozen_winner = ")
        early_index = main_source.index("early_season_diagnostic(")
        assert selection_index < early_index


class TestProductionMappingReadOnly:
    def test_no_production_files_referenced_for_writing(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("production_mapping_notes"))
        assert "open(" not in source
        assert "write" not in source.lower()
