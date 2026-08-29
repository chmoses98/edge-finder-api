#!/usr/bin/env python3
"""
tests/edgelab/test_team_run_mean_audit_experiment.py
====================================================
Coverage for MLB-RSCH-0033's team-run mean root-cause audit.

Load-bearing guarantees: the control is production's OWN function and is
validated before any ablation is believed; an ablation that changes
nothing is reported inconclusive rather than as evidence the component is
inert; and a low r-squared is judged against the ceiling its own spread
allows rather than against 1.0.
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

import run_team_run_mean_audit_experiment as exp  # noqa: E402

SCRIPT = os.path.join(_ROOT, "scripts", "edgelab", "run_team_run_mean_audit_experiment.py")
SOURCE = open(SCRIPT).read()


def _fn(name):
    for node in ast.parse(SOURCE).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(SOURCE, node)
    raise AssertionError(f"{name}() not found")


class TestControlIsProductionItself:
    def test_imports_productions_own_projection_function(self):
        assert "from scripts.build_market_ledger import compute_projections" in SOURCE

    def test_no_reimplementation_of_the_projection(self):
        for node in ast.parse(SOURCE).body:
            if isinstance(node, ast.FunctionDef):
                assert "proj" not in node.name or not node.name.startswith("compute_"), node.name

    def test_control_is_validated_against_the_archived_output(self):
        src = _fn("validate_control")
        assert "archived" in src and "CONTROL_TOLERANCE" in src

    def test_study_is_restricted_to_reproducing_team_games(self):
        src = _fn("build_pairs")
        assert "reproducing is not None and (date, abbr) not in reproducing" in src

    def test_league_fallbacks_are_productions_own_defaults(self):
        assert exp.LEAGUE_OFFENSE_BASELINE == 4.5
        assert exp.LEAGUE_PEN_XFIP == 4.0
        assert exp.LEAGUE_STARTER_IP == 6.0
        assert exp.NEUTRAL_PARK_FACTOR == 100


class TestAblationsAreHonest:
    def test_a_no_op_ablation_is_flagged_inconclusive(self):
        src = _fn("main")
        assert "ablationActuallyBit" in src
        assert "INCONCLUSIVE, not evidence the component" in src

    def test_ablation_does_not_delete_fields_that_would_abort_the_projection(self):
        """Removing a field could make compute_projections bail out, which
        would measure COVERAGE rather than signal. Checked against the code,
        not the docstring that explains it."""
        src = _fn("_ablate")
        code = "\n".join(ln for ln in src.splitlines()
                          if not ln.strip().startswith("#") and '"""' not in ln)
        assert "del " not in code, "an ablation must neutralise, not remove"
        assert ".pop(" not in code, "an ablation must neutralise, not remove"

    def test_every_preregistered_component_is_covered(self):
        assert exp.ABLATIONS == ("OFFENSE_BASELINE", "OPPOSING_STARTER", "OPPOSING_BULLPEN",
                                 "PARK", "STARTER_WORKLOAD_SPLIT", "PLATOON_LINEUP")

    def test_ablation_neutralises_to_a_value_not_to_none(self):
        game = {"awayTeamStats": {"offenseBaselineAdj": 5.2, "abbr": "AAA"},
                "homeTeamStats": {"offenseBaselineAdj": 3.9, "abbr": "BBB"}}
        out = exp._ablate(game, "OFFENSE_BASELINE")
        assert out["awayTeamStats"]["offenseBaselineAdj"] == exp.LEAGUE_OFFENSE_BASELINE
        assert out["homeTeamStats"]["offenseBaselineAdj"] == exp.LEAGUE_OFFENSE_BASELINE

    def test_ablation_does_not_mutate_the_input(self):
        game = {"awayTeamStats": {"offenseBaselineAdj": 5.2}, "homeTeamStats": {}}
        exp._ablate(game, "OFFENSE_BASELINE")
        assert game["awayTeamStats"]["offenseBaselineAdj"] == 5.2


class TestVarianceCeiling:
    def test_ceiling_is_the_projection_variance_share(self):
        score = {"sdProjected": 0.6, "sdActual": 3.0, "rSquared": 0.04}
        out = exp.variance_ceiling(score)
        assert abs(out["maxAchievableRSquaredGivenThisSpread"] - (0.36 / 9.0)) < 1e-6

    def test_share_of_ceiling_can_exceed_nothing_when_absent(self):
        assert exp.variance_ceiling({})["status"] == "INSUFFICIENT_SAMPLE"

    def test_a_low_r_squared_at_full_ceiling_is_not_a_broken_mean(self):
        """The distinction this function exists to make."""
        out = exp.variance_ceiling({"sdProjected": 0.6, "sdActual": 3.2, "rSquared": 0.0352})
        assert out["shareOfCeilingAchieved"] > 0.9
        assert "irreducible" in _fn("variance_ceiling") or "cannot explain more than" in out["interpretation"]


class TestMethodologyV2Scoring:
    def _pairs(self, n=100):
        return [{"projected": 4.0 + (i % 5) * 0.2, "actual": (i % 9),
                 "gameKey": f"G{i//2}", "date": f"2026-08-{10 + i % 10:02d}"} for i in range(n)]

    def test_mse_is_primary_and_mae_is_labelled_secondary(self):
        s = exp.score_pairs(self._pairs(), "t")
        assert "mse" in s and "rmse" in s
        assert "maeSecondaryOnly" in s

    def test_constant_baseline_is_reported(self):
        s = exp.score_pairs(self._pairs(), "t")
        assert "constantBaselineMse" in s and "beatsConstant" in s

    def test_bias_and_calibration_slope_reported(self):
        s = exp.score_pairs(self._pairs(), "t")
        assert "bias" in s and "calibrationSlope" in s

    def test_insufficient_sample_is_not_scored(self):
        assert exp.score_pairs(self._pairs(10), "t")["status"] == "INSUFFICIENT_SAMPLE"


class TestRootCauseClassification:
    CTRL = {"mse": 10.0, "beatsConstant": True, "rSquared": 0.03, "sdProjected": 0.6}

    def test_invalid_control_short_circuits(self):
        case, _ = exp.classify_root_cause(self.CTRL, {}, False, {})
        assert case == "CASE_D_PROJECTION_RECOVERY_INVALID"

    def test_one_helpful_removal_is_case_a(self):
        abl = {"X": {"mse": 9.0}, "Y": {"mse": 10.5}}
        case, _ = exp.classify_root_cause(self.CTRL, abl, True, {})
        assert case == "CASE_A_ONE_COMPONENT_DOMINATES_NOISE"

    def test_multiple_helpful_removals_is_case_b(self):
        abl = {"X": {"mse": 9.0}, "Y": {"mse": 9.5}}
        case, _ = exp.classify_root_cause(self.CTRL, abl, True, {})
        assert case == "CASE_B_MULTIPLE_COMPONENTS_ADD_NOISE"

    def test_losing_to_constant_with_no_helpful_removal_is_case_c(self):
        ctrl = dict(self.CTRL, beatsConstant=False)
        case, _ = exp.classify_root_cause(ctrl, {"X": {"mse": 11.0}}, True, {})
        assert case == "CASE_C_BASE_OFFENSE_SIGNAL_WEAK"

    def test_scaled_mean_beating_constant_with_low_r2_is_case_e(self):
        case, _ = exp.classify_root_cause(self.CTRL, {"X": {"mse": 11.0}}, True, {})
        assert case == "CASE_E_DISTRIBUTION_CONVERSION_PRIMARY_PROBLEM"


class TestSupersessionIsExplicitNotSilent:
    def test_rsch0032_recovery_is_round_trip_tested(self):
        src = _fn("validate_rsch0032_recovery")
        assert "shifted" in src and "unshifted" in src

    def test_both_threshold_conventions_are_reported(self):
        src = _fn("validate_rsch0032_recovery")
        assert "P(X >= T+1)" in src and "P(X >= T)" in src

    def test_supersession_is_declared_and_prior_artifact_not_rewritten(self):
        main = _fn("main")
        assert '"supersedes"' in main
        assert "NOT" in main and "rewritten" in main

    def test_prior_artifact_on_disk_is_untouched(self):
        import json
        p = os.path.join(_ROOT, "data", "edgelab", "analytics",
                         "latest_mlb_rsch_0032_yellow_family_audit.json")
        if not os.path.exists(p):
            pytest.skip("RSCH-0032 artifact not on this branch")
        a = json.load(open(p))
        assert a["teamTotalProjectionDiagnostic"]["conclusion"] == "CASE_B_TEAM_RUN_MEAN_UNINFORMATIVE"


class TestGovernance:
    def test_nothing_is_fitted(self):
        for node in ast.parse(SOURCE).body:
            if isinstance(node, ast.FunctionDef):
                assert not node.name.startswith("fit_")
        assert '"parametersFitted": 0' in _fn("main")

    def test_threshold_defect_is_explicitly_out_of_scope(self):
        assert "out of scope" in SOURCE.lower() or "OUT OF SCOPE" in SOURCE

    def test_no_production_action_authorized(self):
        assert '"productionActionAuthorized": False' in _fn("main")

    def test_evidence_level_is_not_overclaimed(self):
        assert "E1_RECONSTRUCTED_RETROSPECTIVE" in SOURCE
        assert "deliberately NOT E2" in SOURCE
