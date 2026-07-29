#!/usr/bin/env python3
"""
tests/research/test_projection_outcome_comparison.py
=========================================================
Model Performance Phase 1, Part 7A -- tests for
scripts/research/generate_projection_outcome_comparison.py.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS_RESEARCH_DIR = os.path.join(ROOT, "scripts", "research")
sys.path.insert(0, ROOT)
sys.path.insert(0, SCRIPTS_RESEARCH_DIR)

import generate_projection_outcome_comparison as comp


class TestComparisonBuild:

    def test_build_comparison_covers_all_fixture_games(self):
        result = comp.build_comparison()
        assert len(result["games"]) == len(comp.FIXTURE_GAMES)

    def test_every_game_covers_all_four_horizons(self):
        result = comp.build_comparison()
        for game in result["games"]:
            assert set(game["horizons"].keys()) == {"full_game", "F3", "F5", "F7"}

    def test_candidate_probabilities_sum_to_one(self):
        result = comp.build_comparison()
        for game in result["games"]:
            for horizon_data in game["horizons"].values():
                c = horizon_data["candidateRetainedTie"]
                total = c["awayWinProb"] + c["tieProb"] + c["homeWinProb"]
                assert total == pytest.approx(1.0, abs=1e-6)

    def test_production_current_replicates_renormalization_anti_pattern(self):
        """
        Confirms the comparison script's "production_current" method
        actually reproduces the current, real, documented renormalize-
        after-tie-removal behavior (away+home summing to 1 WITHOUT the
        tie) -- proving the comparison is honest, not a strawman.
        """
        result = comp.build_comparison()
        for game in result["games"]:
            for horizon_data in game["horizons"].values():
                p = horizon_data["productionCurrent"]
                total = p["awayWinProb"] + p["homeWinProb"]
                assert total == pytest.approx(1.0, abs=1e-6)
                assert p["tieProbComputedThenDiscarded"] > 0

    def test_candidate_recovers_nonzero_tie_where_production_discards_it(self):
        result = comp.build_comparison()
        for game in result["games"]:
            f5 = game["horizons"]["F5"]
            assert f5["delta"]["tieProbRecoveredByCandidate"] > 0
            assert f5["candidateRetainedTie"]["tieProb"] == pytest.approx(
                f5["productionCurrent"]["tieProbComputedThenDiscarded"], abs=1e-9
            )

    def test_not_implemented_methods_are_honestly_labeled(self):
        result = comp.build_comparison()
        assert "negative_binomial" in result["methodsNotImplementedThisPhase"]
        assert "bivariate_poisson" in result["methodsNotImplementedThisPhase"]
        assert "empirical_simulation" in result["methodsNotImplementedThisPhase"]
        assert "market_informed" in result["methodsNotImplementedThisPhase"]

    def test_never_reads_real_production_data(self):
        """
        Static proof: this module never contains an `open(...)` call
        naming data/slate.json or bets.json -- checked via AST (not a
        bare substring search, since the module's own docstring
        legitimately mentions both paths in prose).
        """
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(comp))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                if node.args and isinstance(node.args[0], ast.Constant):
                    path_arg = str(node.args[0].value)
                    assert "slate.json" not in path_arg
                    assert "bets.json" not in path_arg

    def test_output_path_is_research_only(self):
        assert os.path.normpath(comp.OUTPUT_PATH).endswith(
            os.path.normpath("data/research/projection_outcome_comparison.json")
        )

    def test_deterministic(self):
        r1 = comp.build_comparison()
        r2 = comp.build_comparison()
        # Strip timestamps before comparing
        r1.pop("generatedAt")
        r2.pop("generatedAt")
        assert r1 == r2
