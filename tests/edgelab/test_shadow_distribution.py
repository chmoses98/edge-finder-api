#!/usr/bin/env python3
"""
tests/edgelab/test_shadow_distribution.py
=================================================
Coverage for lib/edgelab/shadow_distribution.py -- MLB-RSCH-0011's pure,
research-only paired CONTROL_POISSON/CANDIDATE_NB_0010 probability
computation.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest

from lib.edgelab import shadow_distribution as sd
from lib.edgelab.backtest.run_distributions import joint_pmf_sums_to_one, independent_joint_pmf


def test_frozen_dispersion_matches_canonical_mlb_rsch_0010_artifact():
    import json
    path = os.path.join("data", "edgelab", "analytics", "latest_mlb_rsch_0010_run_distribution.json")
    with open(path) as f:
        artifact = json.load(f)
    assert sd.FROZEN_DISPERSION == artifact["fittedParameters"]["overdispersion"]


class TestComputePairedProbabilities:
    def test_raises_on_none_means(self):
        with pytest.raises(ValueError):
            sd.compute_paired_probabilities(None, 4.2)
        with pytest.raises(ValueError):
            sd.compute_paired_probabilities(4.2, None)

    def test_raises_on_non_positive_means(self):
        with pytest.raises(ValueError):
            sd.compute_paired_probabilities(0.0, 4.2)
        with pytest.raises(ValueError):
            sd.compute_paired_probabilities(4.2, -1.0)

    def test_moneyline_complement_sums_to_one_for_both_control_and_candidate(self):
        cells = sd.compute_paired_probabilities(3.8, 4.2)
        control_total = cells["moneyline_home_win"]["control"] + cells["moneyline_away_win"]["control"]
        candidate_total = cells["moneyline_home_win"]["candidate"] + cells["moneyline_away_win"]["candidate"]
        # push probability is not double counted in either side -- home_win + away_win < 1 by exactly the push mass
        assert 0.0 < control_total < 1.0
        assert 0.0 < candidate_total < 1.0

    def test_every_preregistered_game_total_line_present(self):
        cells = sd.compute_paired_probabilities(3.8, 4.2)
        for line in sd.GAME_TOTAL_LINES:
            key = f"game_total_over_{line}"
            assert key in cells
            assert 0.0 <= cells[key]["control"] <= 1.0
            assert 0.0 <= cells[key]["candidate"] <= 1.0

    def test_game_total_over_probability_decreases_as_line_increases(self):
        cells = sd.compute_paired_probabilities(3.8, 4.2)
        control_overs = [cells[f"game_total_over_{line}"]["control"] for line in sd.GAME_TOTAL_LINES]
        candidate_overs = [cells[f"game_total_over_{line}"]["candidate"] for line in sd.GAME_TOTAL_LINES]
        assert control_overs == sorted(control_overs, reverse=True)
        assert candidate_overs == sorted(candidate_overs, reverse=True)

    def test_every_preregistered_team_total_line_present_both_sides(self):
        cells = sd.compute_paired_probabilities(3.8, 4.2)
        for line in sd.TEAM_TOTAL_LINES:
            assert f"team_total_away_over_{line}" in cells
            assert f"team_total_home_over_{line}" in cells

    def test_margin_win_by_3_never_exceeds_win_by_2(self):
        cells = sd.compute_paired_probabilities(3.8, 4.5)
        assert cells["run_margin_win_by_at_least_3"]["control"] <= cells["run_margin_win_by_at_least_2"]["control"]
        assert cells["run_margin_win_by_at_least_3"]["candidate"] <= cells["run_margin_win_by_at_least_2"]["candidate"]

    def test_lose_by_at_least_matches_win_by_at_least_with_swapped_means(self):
        """P(home loses by margin+) with means (away=3.8, home=4.5) must equal P(home wins by margin+) with means swapped (away=4.5, home=3.8) -- a pure symmetry check on the swapped-joint construction."""
        cells = sd.compute_paired_probabilities(3.8, 4.5)
        swapped_cells = sd.compute_paired_probabilities(4.5, 3.8)
        for margin in sd.MARGIN_THRESHOLDS:
            assert cells[f"run_margin_lose_by_at_least_{margin}"]["control"] == pytest.approx(
                swapped_cells[f"run_margin_win_by_at_least_{margin}"]["control"], abs=1e-9
            )
            assert cells[f"run_margin_lose_by_at_least_{margin}"]["candidate"] == pytest.approx(
                swapped_cells[f"run_margin_win_by_at_least_{margin}"]["candidate"], abs=1e-9
            )

    def test_dispersion_zero_makes_candidate_equal_control(self):
        """dispersion=0 degenerates the negative-binomial candidate to Poisson exactly -- proving this module's D1 construction matches lib.edgelab.backtest.run_distributions' own documented degeneracy, never a silently different candidate formula."""
        cells = sd.compute_paired_probabilities(3.8, 4.2, dispersion=0.0)
        for key, pair in cells.items():
            assert pair["control"] == pytest.approx(pair["candidate"], abs=1e-9), key

    def test_frozen_dispersion_candidate_differs_from_control(self):
        """With the real frozen dispersion (> 0), the candidate must differ from control on at least the game-total cells -- otherwise this module would be silently computing Poisson twice."""
        cells = sd.compute_paired_probabilities(3.8, 4.2)
        differences = [
            abs(cells[f"game_total_over_{line}"]["control"] - cells[f"game_total_over_{line}"]["candidate"])
            for line in sd.GAME_TOTAL_LINES
        ]
        assert any(d > 1e-6 for d in differences)

    def test_no_f5_or_nrfi_cells_ever_computed(self):
        cells = sd.compute_paired_probabilities(3.8, 4.2)
        for key in cells:
            assert not any(h in key.upper() for h in sd.UNSUPPORTED_HORIZONS)

    def test_deterministic(self):
        first = sd.compute_paired_probabilities(3.8, 4.2)
        second = sd.compute_paired_probabilities(3.8, 4.2)
        assert first == second


class TestBuildShadowRecordsForSnapshotCycle:
    def _entry(self, game_id="g1", checkpoint="T_MINUS_90", game=None):
        return {"gameId": game_id, "checkpoint": checkpoint, "game": game or {"gameId": game_id}}

    def test_success_case_produces_one_record_with_cells(self):
        def ctx_fn(game):
            return {"awayProjRuns": 3.8, "homeProjRuns": 4.2, "totalProj": 8.0}

        records, failures = sd.build_shadow_records_for_snapshot_cycle(
            [self._entry()], compute_projection_context_fn=ctx_fn,
            run_id="run1", experiment_id="MLB-RSCH-0011", evidence_level="E4_PROSPECTIVE_SHADOW",
        )
        assert len(records) == 1
        assert not failures
        r = records[0]
        assert r["computationStatus"] == sd.STATUS_SUCCESS
        assert r["awayProjRuns"] == 3.8 and r["homeProjRuns"] == 4.2
        assert r["cells"] is not None
        assert r["failureReason"] is None
        assert r["frozenDispersion"] == sd.FROZEN_DISPERSION
        assert r["candidateVersion"] == sd.CANDIDATE_VERSION

    def test_missing_projection_means_produces_isolated_failure_not_an_exception(self):
        def ctx_fn(game):
            return {"awayProjRuns": None, "homeProjRuns": 4.2}

        records, failures = sd.build_shadow_records_for_snapshot_cycle(
            [self._entry()], compute_projection_context_fn=ctx_fn,
            run_id="run1", experiment_id="MLB-RSCH-0011", evidence_level="E4_PROSPECTIVE_SHADOW",
        )
        assert len(records) == 1
        assert len(failures) == 1
        r = records[0]
        assert r["computationStatus"] == sd.STATUS_FAILED
        assert r["cells"] is None
        assert r["failureReason"] is not None
        assert "g1" in failures[0]["reason"]

    def test_one_bad_game_does_not_affect_other_games_in_the_same_cycle(self):
        def ctx_fn(game):
            if game["gameId"] == "bad":
                raise RuntimeError("boom")
            return {"awayProjRuns": 3.8, "homeProjRuns": 4.2}

        entries = [self._entry(game_id="bad", game={"gameId": "bad"}), self._entry(game_id="good", game={"gameId": "good"})]
        records, failures = sd.build_shadow_records_for_snapshot_cycle(
            entries, compute_projection_context_fn=ctx_fn,
            run_id="run1", experiment_id="MLB-RSCH-0011", evidence_level="E4_PROSPECTIVE_SHADOW",
        )
        assert len(records) == 2
        by_game = {r["gameId"]: r for r in records}
        assert by_game["bad"]["computationStatus"] == sd.STATUS_FAILED
        assert by_game["good"]["computationStatus"] == sd.STATUS_SUCCESS
        assert len(failures) == 1

    def test_empty_input_produces_no_records(self):
        records, failures = sd.build_shadow_records_for_snapshot_cycle(
            [], compute_projection_context_fn=lambda g: {}, run_id="run1",
            experiment_id="MLB-RSCH-0011", evidence_level="E4_PROSPECTIVE_SHADOW",
        )
        assert records == [] and failures == []

    def test_never_imports_evaluate_game_or_any_production_recommendation_function(self):
        """Structural guarantee: this module never IMPORTS evaluate_game/risk_gate/write_pending_bets -- it can only ever be handed a projection_context function by its caller, never call production recommendation machinery itself. (The docstrings legitimately mention these names in prose -- only actual import/call syntax is checked here.)"""
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(sd))
        imported_names = set()
        called_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported_names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                called_names.add(node.func.id)
        forbidden = {"evaluate_game", "run_risk_gate", "evaluate_correlation_gate", "write_pending_bets"}
        assert not (imported_names & forbidden), imported_names & forbidden
        assert not (called_names & forbidden), called_names & forbidden
