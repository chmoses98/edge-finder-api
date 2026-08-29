#!/usr/bin/env python3
"""
tests/edgelab/test_run_probability_recalibration_experiment_script.py
=========================================================
Coverage for scripts/edgelab/run_probability_recalibration_experiment.py --
MLB-RSCH-0023's production-probability recalibration experiment.
"""
import ast
import math
import os
import random
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

import run_probability_recalibration_experiment as exp  # noqa: E402

SCRIPT_PATH = os.path.join(_ROOT, "scripts", "edgelab", "run_probability_recalibration_experiment.py")


def _find_function_node(name):
    tree = ast.parse(open(SCRIPT_PATH).read())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found")


def _rows_overconfident(n=500, seed=7):
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        true_p = rng.uniform(0.1, 0.9)
        stated = exp._clamp(0.5 + (true_p - 0.5) * 1.8)
        rows.append({"modelP": stated, "outcome": 1 if rng.random() < true_p else 0,
                     "gameId": f"G{i}", "family": "game_result", "marketP": true_p, "settleDate": "2026-08-10"})
    return rows


class TestRegistrationIdempotent:
    def test_register_experiment_is_idempotent_across_reruns(self, tmp_path, monkeypatch):
        import lib.edgelab.experiment_registry as reg
        import lib.edgelab.control_identity as ctrl_id
        monkeypatch.setattr(reg, "EXPERIMENTS_ROOT", str(tmp_path / "experiments"))
        monkeypatch.setattr(ctrl_id, "CONTROL_MODELS_ROOT", str(tmp_path / "control_models"))
        c1, d1 = exp.register_experiment()
        c2, d2 = exp.register_experiment()
        assert d1 == d2 and c1 == c2


class TestLoadersReusedFromRsch0022:
    def test_corpus_built_via_rsch0022_functions_only(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        for fn in ("load_settled_outcomes", "load_evaluated_rows", "build_audit_rows"):
            assert f"rsch0022.{fn}" in main_source
        source = open(SCRIPT_PATH).read()
        assert "def load_settled_outcomes" not in source
        assert "def build_audit_rows" not in source


class TestFitCorrectness:
    def test_recovers_shrink_on_overconfident_synthetic(self):
        params = exp.fit_logit_affine_dev_only(_rows_overconfident())
        assert params is not None
        assert 0 < params["b"] < 1  # a genuine shrink, not divergence

    def test_recovers_identity_on_calibrated_synthetic(self):
        rng = random.Random(11)
        rows = []
        for i in range(2000):
            p = rng.uniform(0.05, 0.95)
            rows.append({"modelP": p, "outcome": 1 if rng.random() < p else 0, "gameId": f"H{i}",
                         "family": "game_result", "marketP": p, "settleDate": "2026-08-10"})
        params = exp.fit_logit_affine_dev_only(rows)
        assert abs(params["a"]) < 0.1 and abs(params["b"] - 1.0) < 0.15

    def test_damping_regression_never_diverges(self):
        """Regression test for the raw-Newton divergence found during
        development (parameters exploding to ~1e7 on the overconfident
        synthetic case before backtracking damping was added)."""
        params = exp.fit_logit_affine_dev_only(_rows_overconfident())
        assert abs(params["a"]) < 10 and abs(params["b"]) < 10

    def test_deterministic(self):
        rows = _rows_overconfident()
        assert exp.fit_logit_affine_dev_only(rows) == exp.fit_logit_affine_dev_only(rows)

    def test_returns_none_below_minimum_sample(self):
        assert exp.fit_logit_affine_dev_only(_rows_overconfident(n=10)) is None


class TestMapProperties:
    def test_monotone_for_positive_b(self):
        ps = [0.05, 0.2, 0.4, 0.6, 0.8, 0.95]
        mapped = [exp.apply_map(p, a=-0.05, b=0.5) for p in ps]
        assert mapped == sorted(mapped)

    def test_identity_map_is_identity_within_clamp(self):
        for p in (0.1, 0.3, 0.5, 0.7, 0.9):
            assert abs(exp.apply_map(p, 0.0, 1.0) - p) < 1e-9

    def test_output_always_in_unit_interval(self):
        for p in (0.01, 0.5, 0.99):
            for a, b in ((-3, 0.1), (3, 2.0), (0, 0.5)):
                assert 0.0 < exp.apply_map(p, a, b) < 1.0


class TestTiersFixedBeforeFitting:
    def test_tier_definitions_are_module_constants(self):
        assert set(exp.TIERS.keys()) == {"TIER_GAME", "TIER_LOCAL", "TIER_PROPS"}
        assert "pitcher_strikeouts" in exp.TIERS["TIER_PROPS"]
        assert "game_result" in exp.TIERS["TIER_GAME"]

    def test_unknown_family_gets_preregistered_default_tier(self):
        assert exp.tier_for_family("some_future_family") == "TIER_LOCAL"


class TestSelectionRule:
    def test_passes_when_all_gates_met(self):
        passes, reasons = exp.selection_passes(-0.01, -0.01, -0.005, 0.10, 0.05)
        assert passes and reasons == []

    def test_fails_on_val_non_replication(self):
        passes, reasons = exp.selection_passes(-0.01, -0.01, 0.002, 0.10, 0.05)
        assert not passes
        assert any("VAL" in r for r in reasons)

    def test_fails_when_ece_worsens(self):
        passes, reasons = exp.selection_passes(-0.01, -0.01, -0.005, 0.05, 0.10)
        assert not passes

    def test_r2_concentration_gate(self):
        passes, reasons = exp.selection_passes(-0.01, -0.01, -0.005, 0.10, 0.05,
                                               tier_deltas={"TIER_GAME": -0.01, "TIER_LOCAL": 0.002, "TIER_PROPS": None})
        assert not passes
        assert any("tiers" in r for r in reasons)

    def test_val_gate_is_strict_no_tolerance(self):
        passes, _ = exp.selection_passes(-0.01, -0.01, 0.0, 0.10, 0.05)
        assert not passes  # exactly zero does not count as replication


class TestForwardWindowUntouched:
    def test_main_never_references_dates_beyond_val_max(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        assert "2026-09" not in main_source

    def test_val_filter_upper_bound_is_val_date_max(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        assert 'r["settleDate"] <= VAL_DATE_MAX' in main_source.replace("'", '"')


class TestNoMarketOrRoiFitting:
    def test_fit_function_never_reads_market_price(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("fit_logit_affine_dev_only"))
        assert "marketP" not in source

    def test_no_roi_terms_in_operational_code(self):
        import re
        tree = ast.parse(open(SCRIPT_PATH).read())
        funcs = [n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name != "register_experiment"]
        source = "\n".join(ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node(f)) for f in funcs)
        # word-boundary match so the governance assertion key "noRoiFit": True
        # (which EXCLUDES ROI fitting) is not itself flagged
        for term in ("roi", "profit", "stake"):
            hits = [m for m in re.findall(rf"\b{term}\b", source.lower())]
            assert hits == [], f"operational code references {term!r}"


class TestClassificationCap:
    def test_max_classification_is_level_1_even_on_pass(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        assert '"LEVEL_1_SHADOW_CANDIDATE"' in main_source
        assert "LEVEL_2" not in main_source
        assert "PROMOTION" not in main_source
