import ast
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab"), os.path.join(_ROOT, "scripts", "edgelab", "backtest")):
    if p not in sys.path:
        sys.path.insert(0, p)

import run_proxy_ablation_experiment as exp  # noqa: E402

SCRIPT_PATH = os.path.join(_ROOT, "scripts", "edgelab", "run_proxy_ablation_experiment.py")
PROBE_RESULT_PATH = os.path.join(_ROOT, "data", "research_cache", "sharp_market_probe", "starter_identity_probe_result.json")


def _find_function_node(name):
    tree = ast.parse(open(SCRIPT_PATH).read())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found")


def _call_names_in_order(func_node):
    """Depth-first, SOURCE-ORDER walk (ast.walk is breadth-first and does
    NOT preserve execution order across statements) -- same helper
    MLB-RSCH-0008's own test file established."""
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
        result_calls = ["build_season_rows", "attach_stabilized_components", "fit_home_field_adjustment_for_components"]
        for call in result_calls:
            occurrences = [i for i, n in enumerate(names) if n == call]
            assert occurrences, f"expected main() to call {call!r}"
            assert min(occurrences) > registration_index


class TestStarterIdentityExclusion:
    def test_starter_is_not_in_the_candidate_sequence(self):
        assert "starter" not in exp.CANDIDATE_SEQUENCE

    def test_only_offense_bullpen_park_are_testable_candidates(self):
        assert set(exp.CANDIDATE_SEQUENCE) == {"offense", "bullpen", "park"}

    def test_real_probe_result_committed_and_verdict_is_not_pit_safe(self):
        # This is the REAL Phase A probe result this milestone's own
        # exclusion decision is based on -- not a fabricated verdict.
        assert os.path.exists(PROBE_RESULT_PATH), "starter identity probe must have actually been run"
        verdict = exp.load_starter_identity_verdict()
        assert verdict["verdict"] == "STARTER_IDENTITY_NOT_PIT_SAFE_AT_SCALE"
        assert verdict["pitSafeAtScale"] is False
        assert verdict["comparableRows"] >= 50

    def test_main_source_references_the_real_verdict_not_a_hardcoded_bypass(self):
        source = open(SCRIPT_PATH).read()
        assert "load_starter_identity_verdict" in source


class TestHoldoutIsolation:
    def test_forward_selection_never_references_holdout_rows(self):
        """The KEEP/REJECT model-selection loop in main() must only ever
        use dev_rows/val_rows -- holdout_rows is a name that must not
        appear inside the ablation loop's own source segment."""
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        # Isolate the forward-selection loop specifically (between the
        # "for candidate in CANDIDATE_SEQUENCE" line and the "final_components ="
        # assignment that follows it).
        start = main_source.index("for candidate in CANDIDATE_SEQUENCE")
        end = main_source.index("final_components =")
        loop_source = main_source[start:end]
        assert "holdout_rows" not in loop_source

    def test_holdout_split_evaluated_only_once_after_freeze(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        # "final_components"/"final_key" must be assigned (the freeze)
        # BEFORE holdout_rows is ever passed to evaluate_split.
        freeze_index = main_source.index("final_key = current_key")
        first_holdout_eval_index = main_source.index("evaluate_split(holdout_rows")
        assert freeze_index < first_holdout_eval_index

    def test_incremental_delta_asserts_same_row_list_never_holdout_vs_dev_mismatch(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("incremental_delta"))
        assert "assert rows_a is rows_b" in source


class TestDevOnlyFitting:
    def test_league_average_offense_fit_only_on_dev_home_team_games(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        fit_line_index = main_source.index("fit_league_average_runs_per_game(dev_home_team_games)")
        # dev_home_team_games must itself be built from DEV_SEASONS only.
        build_line_index = main_source.index("dev_home_team_games = [")
        assert build_line_index < fit_line_index
        build_line = main_source[build_line_index:fit_line_index]
        assert "DEV_SEASONS" in build_line
        assert "VALIDATION_SEASONS" not in build_line
        assert "HOLDOUT_SEASONS" not in build_line

    def test_park_factors_fit_only_on_dev_home_games_with_venue(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        build_line_index = main_source.index("dev_home_games_with_venue = [")
        fit_line_index = main_source.index("fit_park_factors(dev_home_games_with_venue)")
        assert build_line_index < fit_line_index
        build_line = main_source[build_line_index:fit_line_index]
        assert "DEV_SEASONS" in build_line

    def test_home_field_adjustment_fit_function_only_receives_dev_rows_in_main(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        calls = [
            line for line in main_source.splitlines()
            if "fit_home_field_adjustment_for_components(" in line
        ]
        assert calls, "expected at least one fit_home_field_adjustment_for_components call"
        for line in calls:
            assert "dev_rows" in line


class TestNoNewOddsAcquisition:
    def test_script_never_imports_network_fetch_modules(self):
        source = open(SCRIPT_PATH).read()
        assert "fetch_historical_pinnacle_cache" not in source
        assert "urllib" not in source
        assert "ODDS_API_KEY" not in source

    def test_reuses_rsch0008_build_matched_rows_unchanged(self):
        source = open(SCRIPT_PATH).read()
        assert "rsch0008.build_matched_rows" in source
        assert "rsch0008.enrich_row" in source


class TestFinalProxySelectionNeverInspectsPinnacle:
    def test_baseline_for_components_and_incremental_delta_never_reference_pinnacle(self):
        for fn_name in ("baseline_for_components", "incremental_delta", "fit_home_field_adjustment_for_components"):
            source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node(fn_name))
            assert "pinnacle" not in source.lower()

    def test_pinnacle_stage_runs_strictly_after_final_components_frozen(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        freeze_index = main_source.index("final_key = current_key")
        pinnacle_index = main_source.index("rsch0008.build_matched_rows")
        assert freeze_index < pinnacle_index


class TestComponentComposition:
    def test_baseline_for_components_offense_only_affects_offense_field(self):
        raw = {"offenseRunsPerGame": 4.0, "runPreventionRunsAllowedPerGame": 4.5, "priorGamesThisSeason": 30}
        out = exp.baseline_for_components(raw, offense_stabilized=4.2, bullpen_stabilized=None, components=frozenset({"offense"}))
        assert out["offenseRunsPerGame"] == 4.2
        assert out["runPreventionRunsAllowedPerGame"] == 4.5  # untouched

    def test_baseline_for_components_bullpen_only_affects_run_prevention_field(self):
        raw = {"offenseRunsPerGame": 4.0, "runPreventionRunsAllowedPerGame": 4.5, "priorGamesThisSeason": 30}
        out = exp.baseline_for_components(raw, offense_stabilized=4.2, bullpen_stabilized=4.0, components=frozenset({"bullpen"}))
        assert out["offenseRunsPerGame"] == 4.0  # untouched
        assert out["runPreventionRunsAllowedPerGame"] == 4.25  # blended (equal-weight midpoint of 4.5/4.0)

    def test_baseline_for_components_none_raw_returns_none(self):
        assert exp.baseline_for_components(None, 4.0, 4.0, frozenset({"offense"})) is None

    def test_park_component_applies_multiplier_only_when_present(self):
        row = {
            "homeBaselineRaw": {"offenseRunsPerGame": 4.0, "runPreventionRunsAllowedPerGame": 4.0, "priorGamesThisSeason": 30},
            "awayBaselineRaw": {"offenseRunsPerGame": 4.0, "runPreventionRunsAllowedPerGame": 4.0, "priorGamesThisSeason": 30},
            "homeOffenseStabilized": 4.0, "awayOffenseStabilized": 4.0,
            "homeBullpenStabilized": None, "awayBullpenStabilized": None,
            "parkEnvMultiplier": 1.2,
        }
        ml_no_park, total_no_park, expected_no_park = exp.predict_for_components(row, frozenset(), home_field_adjustment=0.0)
        ml_park, total_park, expected_park = exp.predict_for_components(row, frozenset({"park"}), home_field_adjustment=0.0)
        assert expected_park > expected_no_park  # multiplier > 1.0 must scale expected total runs up


class TestSeasonEnvironmentLookupEquivalence:
    def test_fast_lookup_matches_reference_season_run_environment(self):
        from lib.edgelab.backtest.proxy_enrichment import season_run_environment

        team_a = [
            {"side": "home", "date": "2023-04-01", "runsScored": 4, "runsAllowed": 4, "gamePk": 1},
            {"side": "home", "date": "2023-04-10", "runsScored": 6, "runsAllowed": 4, "gamePk": 2},
        ]
        team_b = [
            {"side": "home", "date": "2023-04-05", "runsScored": 2, "runsAllowed": 2, "gamePk": 3},
        ]
        home_games = team_a + team_b
        lookup = exp.build_season_environment_lookup(home_games)

        for as_of in ("2023-04-01", "2023-04-05", "2023-04-10", "2023-04-15"):
            fast = lookup(as_of)
            reference = season_run_environment([team_a, team_b], as_of_date=as_of)
            assert fast == reference

    def test_no_prior_games_returns_none(self):
        lookup = exp.build_season_environment_lookup([])
        assert lookup("2023-04-01") is None
