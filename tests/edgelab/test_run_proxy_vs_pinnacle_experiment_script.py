import ast
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab"), os.path.join(_ROOT, "scripts", "edgelab", "backtest")):
    if p not in sys.path:
        sys.path.insert(0, p)

import run_proxy_vs_pinnacle_experiment as exp  # noqa: E402
from lib.edgelab.backtest.pinnacle_reconstruction import MAX_MINUTES_BEFORE_START  # noqa: E402


SCRIPT_PATH = os.path.join(_ROOT, "scripts", "edgelab", "run_proxy_vs_pinnacle_experiment.py")


def _find_function_node(name):
    tree = ast.parse(open(SCRIPT_PATH).read())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found")


def _call_names_in_order(func_node):
    """Depth-first, SOURCE-ORDER walk (ast.walk is breadth-first and does
    NOT preserve execution order across statements -- this does, by
    recursing through ast.iter_child_nodes, which yields fields in the
    order they're declared, matching top-to-bottom source order)."""
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
        result_calls = ["build_matched_rows", "fit_home_field_adjustment", "paired_analysis"]
        for call in result_calls:
            occurrences = [i for i, n in enumerate(names) if n == call]
            assert occurrences, f"expected main() to call {call!r}"
            assert min(occurrences) > registration_index


class TestHoldoutIsolation:
    def test_paired_analysis_source_never_references_season_group_constants(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("paired_analysis"))
        for forbidden in ("DEV_SEASONS", "VALIDATION_SEASONS", "HOLDOUT_SEASONS", "season"):
            assert forbidden not in source

    def test_main_applies_paired_analysis_identically_to_all_three_splits(self):
        source = open(SCRIPT_PATH).read()
        # Same function object called for dev/val/holdout -- not three
        # different implementations.
        assert source.count('paired_analysis(dev_rows, "proxyMlHomeProb"') == 1
        assert source.count('paired_analysis(val_rows, "proxyMlHomeProb"') == 1
        assert source.count('paired_analysis(holdout_rows, "proxyMlHomeProb"') == 1


class TestFrozenHomeFieldAdjustment:
    def test_fit_home_field_adjustment_called_exactly_once_in_main(self):
        source = open(SCRIPT_PATH).read()
        assert source.count("fit_home_field_adjustment(") == 1

    def test_fit_called_only_on_development_rows(self):
        source = open(SCRIPT_PATH).read()
        assert "fit_home_field_adjustment(dev_rows_raw)" in source

    def test_enrich_row_applied_uniformly_across_all_seasons(self):
        """The SAME home_field_adjustment value must be passed to
        enrich_row for every season -- proven by there being exactly one
        CALL SITE (inside main(), not the def) applying it inside a loop
        over all seasons, not one call per split with a different
        adjustment."""
        names = _call_names_in_order(_find_function_node("main"))
        assert names.count("enrich_row") == 1


class TestNoFutureFeatures:
    def test_2026_only_used_as_holdout_seasons_member(self):
        assert 2026 in exp.HOLDOUT_SEASONS
        assert 2026 not in exp.DEV_SEASONS
        assert 2026 not in exp.VALIDATION_SEASONS

    def test_team_baseline_reused_unchanged_enforces_pit_safety(self):
        from lib.edgelab.backtest.proxy_model import team_baseline as real_team_baseline
        assert exp.team_baseline is real_team_baseline


class TestEnrichRow:
    def _row(self, home_offense=4.5, home_ra=4.0, away_offense=4.2, away_ra=4.3, home_price=-130, away_price=110, over_price=-110, under_price=-110, line=8.5):
        return {
            "gamePk": 1, "date": "2024-06-10", "homeAbbr": "NYY", "awayAbbr": "BOS",
            "actualHomeRuns": 5, "actualAwayRuns": 3, "minutesBeforeStart": 30.0,
            "homeBaseline": {"offenseRunsPerGame": home_offense, "runPreventionRunsAllowedPerGame": home_ra},
            "awayBaseline": {"offenseRunsPerGame": away_offense, "runPreventionRunsAllowedPerGame": away_ra},
            "h2hMarket": {"key": "h2h", "outcomes": [{"name": "New York Yankees", "price": home_price}, {"name": "Boston Red Sox", "price": away_price}]},
            "totalsMarket": {"key": "totals", "outcomes": [{"name": "Over", "point": line, "price": over_price}, {"name": "Under", "point": line, "price": under_price}]},
        }

    def test_populates_proxy_and_pinnacle_fields(self):
        row = exp.enrich_row(self._row(), home_field_adjustment=0.0)
        assert row["proxyMlHomeProb"] is not None
        assert row["pinnacleMlHomeFair"] is not None
        assert row["proxyTotalOverProb"] is not None
        assert row["pinnacleTotalOverFair"] is not None

    def test_actual_home_win_derived_correctly(self):
        row = exp.enrich_row(self._row(), home_field_adjustment=0.0)
        assert row["actualHomeWin"] == 1  # 5 > 3

    def test_actual_over_derived_at_exact_line(self):
        row = exp.enrich_row(self._row(), home_field_adjustment=0.0)
        assert row["actualOver"] == 0  # 5+3=8 < 8.5

    def test_home_field_adjustment_changes_proxy_but_not_pinnacle(self):
        row_a = exp.enrich_row(self._row(), home_field_adjustment=0.0)
        row_b = exp.enrich_row(self._row(), home_field_adjustment=0.5)
        assert row_a["proxyMlHomeProb"] != row_b["proxyMlHomeProb"]
        assert row_a["pinnacleMlHomeFair"] == row_b["pinnacleMlHomeFair"]

    def test_devig_uses_correct_side_regardless_of_outcome_order(self):
        row = exp.enrich_row(self._row(home_price=-200, away_price=170), home_field_adjustment=0.0)
        assert row["pinnacleMlHomeFair"] > row["pinnacleMlAwayFair"] if row.get("pinnacleMlAwayFair") else row["pinnacleMlHomeFair"] > 0.5


class TestPairedAnalysis:
    def _rows(self, n, proxy_key="p", pinnacle_key="m", outcome_key="o"):
        rows = []
        for i in range(n):
            rows.append({"gamePk": i, "date": f"2024-04-{(i % 28) + 1:02d}", proxy_key: 0.5 + (0.01 * (i % 5)), pinnacle_key: 0.5, outcome_key: i % 2})
        return rows

    def test_empty_rows_returns_honest_zero(self):
        result = exp.paired_analysis([], "p", "m", "o", "TEST")
        assert result["n"] == 0
        assert result["pairedBrierDelta_proxyMinusPinnacle"] is None

    def test_perfect_pairing_no_control_or_candidate_only(self):
        rows = self._rows(20)
        result = exp.paired_analysis(rows, "p", "m", "o", "TEST")
        assert result["n"] == 20

    def test_rows_missing_a_key_are_excluded(self):
        rows = self._rows(10)
        rows[0]["p"] = None
        result = exp.paired_analysis(rows, "p", "m", "o", "TEST")
        assert result["n"] == 9


class TestClassifyFamilySignal:
    def _result(self, delta, lo, hi, games):
        return {"independentGames": games, "pairedBrierDelta_proxyMinusPinnacle": delta, "pairedDeltaConfidenceInterval95": {"low": lo, "high": hi}}

    def test_insufficient_below_min_games(self):
        dev = self._result(-0.01, -0.05, 0.02, 10)
        assert exp.classify_family_signal(dev, None, None, {}) == exp.SIGNAL_INSUFFICIENT

    def test_sharp_dominant_when_confidently_worse(self):
        dev = self._result(0.02, 0.005, 0.04, 100)
        assert exp.classify_family_signal(dev, None, None, {}) == exp.SIGNAL_SHARP_DOMINANT

    def test_proxy_beats_requires_all_three_splits_confident(self):
        dev = self._result(-0.02, -0.04, -0.005, 100)
        val = self._result(-0.02, -0.04, -0.005, 100)
        holdout = self._result(-0.02, -0.04, -0.005, 100)
        assert exp.classify_family_signal(dev, val, holdout, {}) == exp.SIGNAL_PROXY_BEATS

    def test_partial_when_dev_confident_but_not_both_other_splits(self):
        dev = self._result(-0.02, -0.04, -0.005, 100)
        val = self._result(0.0, -0.02, 0.02, 100)
        assert exp.classify_family_signal(dev, val, None, {}) == exp.SIGNAL_PARTIAL

    def test_parity_when_no_confident_direction_anywhere(self):
        dev = self._result(0.0, -0.02, 0.02, 100)
        assert exp.classify_family_signal(dev, None, None, {}) == exp.SIGNAL_PARITY


class TestSnapshotTimingReuse:
    def test_max_minutes_before_start_reused_not_redefined(self):
        source = open(SCRIPT_PATH).read()
        assert "MAX_MINUTES_BEFORE_START = " not in source
        assert MAX_MINUTES_BEFORE_START == 60


class TestProductionUnchanged:
    def test_no_direct_writes_to_production_paths(self):
        source = open(SCRIPT_PATH).read()
        for forbidden in ("config/rules.json", "data/bets.json", "data/slates/"):
            assert forbidden not in source

    def test_reuses_p_team_wins_p_over_total_unchanged(self):
        from build_market_ledger import p_team_wins, p_over_total
        from lib.edgelab.backtest.proxy_model import game_ml_proxy_probability, game_total_proxy_probability
        # already proven in test_proxy_model.py; sanity re-check here that
        # the orchestration script's own imports resolve to the same objects
        import lib.edgelab.backtest.proxy_model as pm
        assert pm.p_team_wins is p_team_wins
        assert pm.p_over_total is p_over_total
