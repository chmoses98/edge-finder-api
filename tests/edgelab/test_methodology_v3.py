#!/usr/bin/env python3
"""
tests/edgelab/test_methodology_v3.py
====================================
Coverage for METHODOLOGY V3 -- materiality and actionability.

The load-bearing guarantees: V3 is future-only and changes nothing that
already shipped; a favourable sign alone cannot pass; a CI containing the
null blocks when preregistered; zero executable capacity blocks a betting
label; independence floors are enforced; and ROI can never be a fitting
objective.
"""
import ast
import json
import os
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab.research import methodology_v3 as v3  # noqa: E402
from lib.edgelab.research import methodology_v2 as v2  # noqa: E402

GOOD_JUSTIFICATION = (
    "Floors chosen from the game-level archive's own independent-game depth and from the "
    "smallest Brier gain that would survive canonical Kalshi fees at typical prices."
)


def _prereg(**kw):
    base = dict(null_value=0.0, effect_floor=0.10, harm_tolerance=0.005,
                require_ci_excludes_null=True, min_score_improvement=0.002,
                min_independent_games=30, min_independent_dates=7,
                required_transport=v3.TRANSPORT_CHRONOLOGICAL_VALIDATION,
                min_executable_opportunities=1, justification=GOOD_JUSTIFICATION)
    base.update(kw)
    return v3.MaterialityPreregistration(**base)


def _obs(**kw):
    base = dict(effect_estimate=0.5, effect_ci_low=0.2, effect_ci_high=0.8,
                score_improvement=0.01, score_ci_low=0.004, score_ci_high=0.016,
                independent_games=40, independent_dates=9, replicating_blocks=0,
                transport_evidence=v3.TRANSPORT_CHRONOLOGICAL_VALIDATION,
                executable_opportunities=25)
    base.update(kw)
    return v3.ObservedEvidence(**base)


class TestFutureOnlyAndNonInvasive:
    def test_v2_is_untouched(self):
        assert v2.METHODOLOGY_VERSION == "v2"
        assert hasattr(v2, "mean_candidate_gates_v2")

    def test_v3_modifies_no_framework_or_production_code(self):
        """Checked against IMPORTS and writes, not prose -- the module docstring
        legitimately names bankroll, fees and prior dispositions when explaining
        why V3 exists."""
        src = open(os.path.join(_ROOT, "lib", "edgelab", "research", "methodology_v3.py")).read()
        tree = ast.parse(src)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        for banned in ("lib.edgelab.experiment_registry", "lib.edgelab.kalshi_fees",
                       "lib.edgelab.recommendations", "scripts.risk_gate"):
            assert not any(banned in m for m in imported), f"V3 imports {banned}"
        # and it writes nothing at all
        assert "open(" not in src and "json.dump" not in src

    def test_v3_is_opt_in_never_wired_into_lib_or_production(self):
        """An EXPERIMENT may explicitly opt in -- that is the intended usage.
        What must never happen is V3 being wired into the framework or a
        production path, where it would apply to experiments that never
        preregistered its floors."""
        out = subprocess.run(["grep", "-rl", "--include=*.py", "methodology_v3",
                              os.path.join(_ROOT, "lib"), os.path.join(_ROOT, "scripts")],
                             capture_output=True, text=True).stdout.split()
        for path in out:
            rel = os.path.relpath(path, _ROOT)
            if rel == os.path.join("lib", "edgelab", "research", "methodology_v3.py"):
                continue                      # the module itself
            assert rel.startswith(os.path.join("scripts", "edgelab", "run_")), (
                f"{rel} references V3 but is not an opt-in experiment runner")

    def test_v3_is_not_referenced_by_the_experiment_registry_or_production(self):
        for rel in (os.path.join("lib", "edgelab", "experiment_registry.py"),
                    os.path.join("lib", "edgelab", "recommendations.py"),
                    os.path.join("lib", "edgelab", "evidence_levels.py")):
            path = os.path.join(_ROOT, rel)
            if os.path.exists(path):
                assert "methodology_v3" not in open(path).read(), f"{rel} wires V3 in implicitly"

    def test_prior_experiment_dispositions_are_not_assigned(self):
        """V3 may DESCRIBE the RSCH-0030 failure that motivated it, but must
        never assign or emit a disposition of its own."""
        src = open(os.path.join(_ROOT, "lib", "edgelab", "research", "methodology_v3.py")).read()
        code = "\n".join(ln for ln in src.splitlines()
                          if not ln.strip().startswith("#"))
        for banned in ("disposition =", "disposition=", "LEVEL_2", "PRODUCTION_APPROVED"):
            assert banned not in code, f"V3 assigns {banned}"

    def test_committed_rsch0030_artifact_still_says_what_it_said(self):
        """V3 must not have altered any shipped verdict."""
        p = os.path.join(_ROOT, "data", "edgelab", "analytics",
                         "latest_mlb_rsch_0030_shrinkage_confirmation.json")
        if not os.path.exists(p):
            pytest.skip("RSCH-0030 artifact not present on this branch")
        a = json.load(open(p))
        assert a["disposition"] == "LEVEL_1_SHADOW_CANDIDATE"
        assert a["successCriteria"]["allRequired"] is True

    def test_describe_states_it_applies_only_to_future_experiments(self):
        assert "no prior experiment" in v3.describe_v3(_prereg())["appliesTo"]


class TestNoUniversalFloorsAreShipped:
    def test_module_defines_no_default_effect_or_score_floor(self):
        for attr in dir(v3):
            if attr.startswith("DEFAULT_"):
                pytest.fail(f"V3 ships a universal default {attr}; floors must be per-experiment")

    def test_preregistration_refuses_a_zero_effect_floor(self):
        with pytest.raises(v3.MaterialityPreregistrationError, match="effect_floor must be positive"):
            _prereg(effect_floor=0.0)

    def test_preregistration_requires_a_proper_score_rule(self):
        with pytest.raises(v3.MaterialityPreregistrationError, match="universal proper-score floor"):
            _prereg(min_score_improvement=None, noninferiority_margin=None)

    def test_preregistration_requires_substantive_justification(self):
        with pytest.raises(v3.MaterialityPreregistrationError, match="justification"):
            _prereg(justification="because")

    def test_preregistration_requires_an_independence_floor(self):
        with pytest.raises(v3.MaterialityPreregistrationError, match="independent information"):
            _prereg(min_independent_games=0, min_independent_dates=0)

    def test_dev_only_is_not_valid_transport(self):
        with pytest.raises(v3.MaterialityPreregistrationError, match="not transport"):
            _prereg(required_transport="DEV_ONLY")

    def test_preregistration_is_immutable(self):
        p = _prereg()
        with pytest.raises(Exception):
            p.effect_floor = 0.0001


class TestFavourableSignAloneCannotPass:
    def test_tiny_favourable_effect_fails_the_effect_floor(self):
        labels = v3.evaluate_materiality_v3(_prereg(), _obs(effect_estimate=0.001,
                                                            effect_ci_low=-0.30, effect_ci_high=0.30))
        assert labels[v3.LABEL_STATISTICAL_SIGNAL]["passes"] is False

    def test_ci_containing_null_blocks_when_preregistered(self):
        labels = v3.evaluate_materiality_v3(_prereg(), _obs(effect_ci_low=-0.4, effect_ci_high=0.6))
        s = labels[v3.LABEL_STATISTICAL_SIGNAL]
        assert s["passes"] is False
        assert any("contains the null" in r for r in s["reasons"])

    def test_ci_containing_null_allowed_when_not_required(self):
        labels = v3.evaluate_materiality_v3(_prereg(require_ci_excludes_null=False),
                                            _obs(effect_ci_low=-0.4, effect_ci_high=0.6))
        assert labels[v3.LABEL_STATISTICAL_SIGNAL]["passes"] is True

    def test_missing_ci_fails_when_required(self):
        labels = v3.evaluate_materiality_v3(_prereg(), _obs(effect_ci_low=None, effect_ci_high=None))
        assert labels[v3.LABEL_STATISTICAL_SIGNAL]["passes"] is False

    def test_negligible_score_gain_fails_materiality(self):
        labels = v3.evaluate_materiality_v3(_prereg(), _obs(score_improvement=0.0002))
        assert labels[v3.LABEL_PREDICTIVE_MATERIALITY]["passes"] is False


class TestExecutableCapacity:
    def test_zero_capacity_blocks_the_betting_label(self):
        labels = v3.evaluate_materiality_v3(_prereg(), _obs(executable_opportunities=0))
        c = labels[v3.LABEL_EXECUTABLE_CAPACITY]
        assert c["passes"] is False
        assert any("cannot be traded" in r for r in c["reasons"])

    def test_capacity_can_be_waived_only_by_preregistration(self):
        labels = v3.evaluate_materiality_v3(_prereg(require_executable_capacity=False),
                                            _obs(executable_opportunities=0))
        assert labels[v3.LABEL_EXECUTABLE_CAPACITY]["passes"] is True

    def test_zero_capacity_fails_the_whole_betting_gate(self):
        passes, reasons, _ = v3.betting_shadow_gate_v3(_prereg(), _obs(executable_opportunities=0))
        assert passes is False and reasons


class TestIndependenceAndTransport:
    def test_insufficient_games_blocks_readiness(self):
        labels = v3.evaluate_materiality_v3(_prereg(), _obs(independent_games=5))
        assert labels[v3.LABEL_IMPLEMENTATION_READINESS]["passes"] is False

    def test_insufficient_dates_blocks_readiness(self):
        labels = v3.evaluate_materiality_v3(_prereg(), _obs(independent_dates=2))
        assert labels[v3.LABEL_IMPLEMENTATION_READINESS]["passes"] is False

    def test_wrong_transport_kind_blocks_readiness(self):
        labels = v3.evaluate_materiality_v3(
            _prereg(), _obs(transport_evidence=v3.TRANSPORT_LEAVE_DATE_OUT))
        assert labels[v3.LABEL_IMPLEMENTATION_READINESS]["passes"] is False

    def test_subject_floor_is_enforced(self):
        labels = v3.evaluate_materiality_v3(_prereg(min_independent_subjects=50, subject_unit="player"),
                                            _obs(independent_subjects=10))
        r = labels[v3.LABEL_IMPLEMENTATION_READINESS]
        assert r["passes"] is False and any("players below floor" in x for x in r["reasons"])


class TestLabelsAreNeverCollapsed:
    def test_all_four_labels_always_returned(self):
        labels = v3.evaluate_materiality_v3(_prereg(), _obs())
        assert set(labels) == set(v3.V3_LABELS)

    def test_gate_returns_the_separated_labels_too(self):
        passes, reasons, labels = v3.betting_shadow_gate_v3(_prereg(), _obs())
        assert passes is True and reasons == []
        assert set(labels) == set(v3.V3_LABELS)

    def test_a_candidate_can_pass_some_labels_and_fail_others(self):
        labels = v3.evaluate_materiality_v3(_prereg(), _obs(executable_opportunities=0))
        assert labels[v3.LABEL_STATISTICAL_SIGNAL]["passes"] is True
        assert labels[v3.LABEL_EXECUTABLE_CAPACITY]["passes"] is False


class TestRoiIsNeverAFittingObjective:
    def test_rejects_a_fitting_objective_that_mentions_roi(self):
        with pytest.raises(v3.MaterialityPreregistrationError, match="roi"):
            v3.assert_roi_not_a_fitting_objective("def fit(rows): return maximize_roi(rows)")

    def test_rejects_pnl_and_profit_objectives(self):
        for src in ("best_net_pnl(rows)", "maximise profit here", "tune to bankroll growth"):
            with pytest.raises(v3.MaterialityPreregistrationError):
                v3.assert_roi_not_a_fitting_objective(src)

    def test_accepts_a_proper_scoring_objective(self):
        v3.assert_roi_not_a_fitting_objective("def fit(rows): return minimize_bernoulli_nll(rows)")

    def test_module_offers_no_way_to_rank_by_roi(self):
        src = open(os.path.join(_ROOT, "lib", "edgelab", "research", "methodology_v3.py")).read()
        tree = ast.parse(src)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                assert "roi" not in node.name.lower() or node.name.startswith("assert_")


class TestReplayOfTheFailureThatMotivatedV3:
    """MLB-RSCH-0030's own numbers, replayed. V3 must catch what the old rule
    let through -- without altering RSCH-0030's shipped verdict."""

    RSCH0030 = dict(effect_estimate=0.0590, effect_ci_low=-0.3621, effect_ci_high=0.5247,
                    score_improvement=0.000214, score_ci_low=-0.0003, score_ci_high=0.0007,
                    independent_games=36, independent_dates=7, replicating_blocks=4,
                    transport_evidence=v3.TRANSPORT_CHRONOLOGICAL_VALIDATION,
                    executable_opportunities=0)

    def _prereg_for_hitters(self):
        return v3.MaterialityPreregistration(
            null_value=0.0, effect_floor=0.15, harm_tolerance=0.002,
            require_ci_excludes_null=True, min_score_improvement=0.002,
            min_independent_games=30, min_independent_dates=7,
            required_transport=v3.TRANSPORT_CHRONOLOGICAL_VALIDATION,
            min_executable_opportunities=1,
            justification=("A shrinkage factor must beat the market by more than a fee-width to be "
                           "worth trading, and must be separable from zero on the archive's own "
                           "independent player-game depth."))

    def test_v3_blocks_the_betting_shadow(self):
        passes, reasons, _ = v3.betting_shadow_gate_v3(
            self._prereg_for_hitters(), v3.ObservedEvidence(**self.RSCH0030))
        assert passes is False and reasons

    def test_it_fails_statistical_signal_because_the_ci_spans_zero(self):
        labels = v3.evaluate_materiality_v3(self._prereg_for_hitters(),
                                            v3.ObservedEvidence(**self.RSCH0030))
        assert labels[v3.LABEL_STATISTICAL_SIGNAL]["passes"] is False

    def test_it_fails_predictive_materiality_on_effect_size(self):
        labels = v3.evaluate_materiality_v3(self._prereg_for_hitters(),
                                            v3.ObservedEvidence(**self.RSCH0030))
        assert labels[v3.LABEL_PREDICTIVE_MATERIALITY]["passes"] is False

    def test_it_fails_executable_capacity_on_zero_tradeable_contracts(self):
        labels = v3.evaluate_materiality_v3(self._prereg_for_hitters(),
                                            v3.ObservedEvidence(**self.RSCH0030))
        assert labels[v3.LABEL_EXECUTABLE_CAPACITY]["passes"] is False

    def test_readiness_alone_would_have_passed_which_is_why_labels_are_separate(self):
        labels = v3.evaluate_materiality_v3(self._prereg_for_hitters(),
                                            v3.ObservedEvidence(**self.RSCH0030))
        assert labels[v3.LABEL_IMPLEMENTATION_READINESS]["passes"] is True
