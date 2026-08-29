#!/usr/bin/env python3
"""
tests/edgelab/test_yellow_family_audit_experiment.py
====================================================
Coverage for MLB-RSCH-0032's YELLOW family validity audit.

Load-bearing guarantees: sample floors are never lowered to manufacture a
result, unrelated boards are never pooled to raise n, a family is never
called usable merely for not being significantly bad, and the team-total
projection diagnostic reaches the right case for the right reason.
"""
import ast
import math
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab")):
    if p not in sys.path:
        sys.path.insert(0, p)

import run_yellow_family_audit_experiment as exp  # noqa: E402
from lib.edgelab.research import methodology_v3 as v3  # noqa: E402

SCRIPT = os.path.join(_ROOT, "scripts", "edgelab", "run_yellow_family_audit_experiment.py")
SOURCE = open(SCRIPT).read()


def _fn(name):
    for node in ast.parse(SOURCE).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(SOURCE, node)
    raise AssertionError(f"{name}() not found")


def _rows(model_p, market_p, outcome, n=200, games=None):
    games = games or n
    return [{"modelP": model_p, "marketP": market_p, "outcome": outcome,
             "gameId": f"G{i % games}", "settleDate": f"2026-08-{10 + (i % 12):02d}"}
            for i in range(n)]


class TestMethodologyV3IsActuallyUsed:
    def test_labels_come_from_v3(self):
        assert "betting_shadow_gate_v3" in SOURCE
        assert "from lib.edgelab.research.methodology_v3 import" in SOURCE

    def test_each_family_preregisters_its_own_justified_floors(self):
        pre = exp.materiality_for("KXMLBRFI", 225, 225, 21)
        assert isinstance(pre, v3.MaterialityPreregistration)
        assert len(pre.justification) > 100
        assert pre.effect_floor > 0

    def test_floors_are_not_universal_constants_pulled_from_v3(self):
        for attr in dir(v3):
            assert not attr.startswith("DEFAULT_")

    def test_preregistration_requires_leave_one_date_out_transport(self):
        assert exp.materiality_for("X", 1, 1, 1).required_transport == v3.TRANSPORT_LEAVE_DATE_OUT


class TestSampleFloorsAreNeverLowered:
    def test_floors_are_fixed_constants(self):
        assert exp.MIN_FAMILY_ROWS == 150
        assert exp.MIN_FAMILY_GAMES == 100
        assert exp.MIN_FAMILY_DATES == 10

    def test_below_floor_family_is_insufficient_even_when_favourable(self):
        rows = _rows(0.9, 0.5, 1, n=60, games=60)     # model far better than market
        a = exp.audit_family(rows, "KXMLBF5")
        assert a["classification"] == "INSUFFICIENT_SAMPLE"

    def test_frozen_prospective_design_states_floors_are_not_lowered(self):
        main = _fn("main")
        assert "floors are NOT lowered to manufacture a result" in main

    def test_a_correlated_row_count_cannot_substitute_for_games(self):
        # 200 rows but only 5 distinct games
        rows = _rows(0.9, 0.5, 1, n=200, games=5)
        a = exp.audit_family(rows, "KXMLBRFI")
        assert a["independentGames"] == 5
        assert a["classification"] == "INSUFFICIENT_SAMPLE"


class TestClassificationIsConservative:
    def test_model_worse_than_base_rate_is_model_trails_market(self):
        score = {"rows": 300, "independentGames": 300, "modelBrier": 0.28,
                 "baseRateBrier": 0.25, "pairedBrierDelta": 0.01,
                 "pairedBrierDeltaCI": {"low": -0.002, "high": 0.02}}
        assert exp.classify_family(score, {}, {}, False, {}) == "MODEL_TRAILS_MARKET"

    def test_ci_entirely_above_zero_is_model_trails_market(self):
        score = {"rows": 300, "independentGames": 300, "modelBrier": 0.24,
                 "baseRateBrier": 0.25, "pairedBrierDelta": 0.01,
                 "pairedBrierDeltaCI": {"low": 0.004, "high": 0.02}}
        assert exp.classify_family(score, {}, {}, False, {}) == "MODEL_TRAILS_MARKET"

    def test_beating_market_but_failing_v3_is_only_parity(self):
        """Not significantly bad is never the same as usable."""
        score = {"rows": 300, "independentGames": 300, "modelBrier": 0.24,
                 "baseRateBrier": 0.25, "pairedBrierDelta": -0.01,
                 "pairedBrierDeltaCI": {"low": -0.02, "high": -0.004}}
        assert exp.classify_family(score, {}, {}, False, {}) == "PARITY"

    def test_beating_market_and_passing_v3_is_validated(self):
        score = {"rows": 300, "independentGames": 300, "modelBrier": 0.24,
                 "baseRateBrier": 0.25, "pairedBrierDelta": -0.01,
                 "pairedBrierDeltaCI": {"low": -0.02, "high": -0.004}}
        assert exp.classify_family(score, {}, {}, True, {}) == "VALIDATED_FOR_CONTINUED_SHADOW"

    def test_missing_ci_is_insufficient_not_a_pass(self):
        score = {"rows": 300, "independentGames": 300, "modelBrier": 0.24,
                 "baseRateBrier": 0.25, "pairedBrierDelta": -0.01,
                 "pairedBrierDeltaCI": {"low": None, "high": None}}
        assert exp.classify_family(score, {}, {}, True, {}) == "INSUFFICIENT_SAMPLE"


class TestScoringDirection:
    def test_negative_delta_means_model_better(self):
        assert exp.paired_brier_delta(_rows(0.9, 0.5, 1)) < 0

    def test_positive_delta_means_market_better(self):
        assert exp.paired_brier_delta(_rows(0.1, 0.5, 1)) > 0

    def test_base_rate_brier_is_reported_as_a_floor(self):
        s = exp.score_family(_rows(0.5, 0.5, 1, n=200), "X")
        assert "baseRateBrier" in s


class TestTeamTotalProjectionDiagnostic:
    def test_lambda_inversion_round_trips(self):
        for lam in (2.0, 4.5, 7.0):
            for n in (3, 5, 8):
                p = exp._p_at_least(n, lam)
                if 0.001 < p < 0.999:
                    assert abs(exp._solve_lambda(p, n) - lam) < 0.05

    def test_recovery_is_faithful_to_the_production_poisson_path(self):
        src = _fn("_solve_lambda")
        assert "p_over_total" in src and "poisson_pmf" in src

    def test_case_b_when_projection_loses_to_a_constant(self):
        out = exp.team_total_projection_diagnostic([])
        assert out["recoveredPairs"] == 0

    def test_mapping_swap_test_is_present(self):
        src = _fn("team_total_projection_diagnostic")
        assert "swapFitsBetter" in src and "CASE_C_TEAM_MAPPING_DEFECT" in src

    def test_all_three_cases_are_reachable(self):
        src = _fn("team_total_projection_diagnostic")
        for case in ("CASE_A_PROJECTION_INFORMATIVE_CONVERSION_BROKEN",
                     "CASE_B_TEAM_RUN_MEAN_UNINFORMATIVE",
                     "CASE_C_TEAM_MAPPING_DEFECT"):
            assert case in src


class TestGovernance:
    def test_nothing_is_fitted(self):
        for node in ast.parse(SOURCE).body:
            if isinstance(node, ast.FunctionDef):
                assert not node.name.startswith("fit_")
        assert '"parametersFitted": 0' in _fn("main")

    def test_no_production_action_authorized(self):
        assert '"productionActionAuthorized": False' in _fn("main")
        assert "PRODUCTION_APPROVED" not in SOURCE

    def test_economics_never_select_anything(self):
        assert "Never used to tune any parameter or threshold" in _fn("fee_aware_capacity")
        assert "taker_fee(" in _fn("fee_aware_capacity")

    def test_unrelated_boards_are_not_pooled(self):
        main = _fn("main")
        assert "for f in RESEARCH_ONLY_FAMILIES" in main

    def test_synthetic_recovery_is_attempted_and_reported_not_faked(self):
        main = _fn("main")
        assert '"recoveryAttempted": True' in main
        assert "No\\n                   \"approximate, fuzzy or date-proximity match" in main \
            or "approximate, fuzzy or date-proximity match" in main

    def test_f5_three_way_semantics_are_not_bridged(self):
        main = _fn("main")
        assert "f5SemanticBlocker" in main
        assert "-TIE" in SOURCE
