#!/usr/bin/env python3
"""
tests/edgelab/test_kalshi_internal_efficiency_experiment.py
=========================================================
Coverage for scripts/edgelab/run_kalshi_internal_efficiency_experiment.py --
MLB-RSCH-0026's market-only internal-efficiency study.
"""
import ast
import os
import random
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

import run_kalshi_internal_efficiency_experiment as exp  # noqa: E402

SCRIPT_PATH = os.path.join(_ROOT, "scripts", "edgelab", "run_kalshi_internal_efficiency_experiment.py")


def _fn(name):
    tree = ast.parse(open(SCRIPT_PATH).read())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(open(SCRIPT_PATH).read(), node)
    raise AssertionError(f"{name}() not found")


class TestIndependenceFromProductionModel:
    def test_corpus_builder_drops_model_probability(self):
        source = _fn("build_market_only_rows")
        assert "modelP" not in source, "the market-only corpus must not carry production's probability"

    def test_no_scoring_or_fitting_function_reads_model_probability(self):
        for name in ("fit_beta", "_nll", "shrunk_probability", "score", "paired_delta",
                     "band_analysis", "family_analysis", "secondary_economics"):
            assert "modelP" not in _fn(name), f"{name} must not read production's probability"

    def test_report_asserts_independence(self):
        main = _fn("main")
        assert '"usesProductionModelProbability": False' in main


class TestShrinkForm:
    def test_beta_one_is_exactly_the_market(self):
        for fair in (0.10, 0.35, 0.75, 0.92):
            assert abs(exp.shrunk_probability(fair, 1.0, 0.45) - fair) < 1e-9

    def test_beta_below_one_shrinks_both_extremes_toward_base(self):
        base = 0.45
        assert exp.shrunk_probability(0.90, 0.5, base) < 0.90
        assert exp.shrunk_probability(0.10, 0.5, base) > 0.10

    def test_beta_above_one_sharpens(self):
        base = 0.45
        assert exp.shrunk_probability(0.90, 1.5, base) > 0.90

    def test_monotone_in_fair_price(self):
        vals = [exp.shrunk_probability(f, 0.8, 0.45) for f in (0.05, 0.2, 0.5, 0.8, 0.95)]
        assert vals == sorted(vals)

    def test_output_in_unit_interval(self):
        for beta in (0.2, 1.0, 2.0):
            for fair in (0.01, 0.5, 0.99):
                assert 0.0 < exp.shrunk_probability(fair, beta, 0.43) < 1.0


class TestBetaRecovery:
    def _rows(self, true_beta, n=1500, seed=5):
        """Generate a market whose price is too extreme by construction:
        true prob = shrink(fair, true_beta)."""
        rng = random.Random(seed)
        base = 0.45
        rows = []
        for i in range(n):
            fair = rng.uniform(0.08, 0.92)
            true_p = exp.shrunk_probability(fair, true_beta, base)
            rows.append({"marketFair": fair, "outcome": 1 if rng.random() < true_p else 0, "gameId": f"G{i // 5}"})
        return rows, base

    def test_recovers_shrink_when_market_is_too_extreme(self):
        rows, base = self._rows(0.6)
        fit = exp.fit_beta(rows, base)
        assert 0.4 < fit["beta"] < 0.85

    def test_recovers_near_one_for_a_calibrated_market(self):
        rows, base = self._rows(1.0)
        fit = exp.fit_beta(rows, base)
        assert 0.8 < fit["beta"] < 1.25

    def test_deterministic(self):
        rows, base = self._rows(0.6)
        assert exp.fit_beta(rows, base) == exp.fit_beta(rows, base)

    def test_respects_preregistered_bounds(self):
        rows, base = self._rows(0.05, seed=9)
        fit = exp.fit_beta(rows, base)
        assert exp.BETA_BOUNDS[0] <= fit["beta"] <= exp.BETA_BOUNDS[1]

    def test_bounds_are_constants(self):
        assert exp.BETA_BOUNDS == (0.2, 2.0)

    def test_returns_none_below_minimum_sample(self):
        rows, base = self._rows(0.6, n=10)
        assert exp.fit_beta(rows, base) is None


class TestSelectionRule:
    def _ci(self, lo, hi):
        return {"low": lo, "high": hi}

    def test_passes_when_all_criteria_met(self):
        passes, reasons = exp.selection_passes(-0.001, -0.002, 0.04, 0.05, 3, self._ci(0.6, 0.9))
        assert passes and reasons == []

    def test_fails_when_beta_ci_includes_one(self):
        passes, reasons = exp.selection_passes(-0.001, -0.002, 0.04, 0.05, 3, self._ci(0.85, 1.16))
        assert not passes
        assert any("includes 1.0" in r for r in reasons)

    def test_fails_when_brier_not_improved(self):
        passes, _ = exp.selection_passes(0.001, -0.002, 0.04, 0.05, 3, self._ci(0.6, 0.9))
        assert not passes

    def test_fails_when_calibration_worse(self):
        passes, _ = exp.selection_passes(-0.001, -0.002, 0.09, 0.05, 3, self._ci(0.6, 0.9))
        assert not passes

    def test_fails_when_confined_to_one_band(self):
        passes, reasons = exp.selection_passes(-0.001, -0.002, 0.04, 0.05, 1, self._ci(0.6, 0.9))
        assert not passes
        assert any("price bands" in r for r in reasons)

    def test_min_improving_bands_is_a_constant(self):
        assert exp.MIN_IMPROVING_BANDS == 2


class TestGovernance:
    def test_economics_not_computed_when_selection_fails(self):
        main = _fn("main")
        assert 'economics never rescue a failed forecaster' in main

    def test_economics_uses_canonical_fee_engine(self):
        assert "taker_fee(" in _fn("secondary_economics")

    def test_forward_window_never_scored_in_this_run(self):
        main = _fn("main")
        assert "2026-09" not in main
        assert 'forwardRowsAvailable' in main

    def test_frozen_artifact_has_rerun_threshold(self):
        main = _fn("main")
        assert "rerunThresholdIfInconclusive" in main
        assert "minForwardRows" in main

    def test_price_bands_are_fixed_constants(self):
        assert exp.PRICE_BANDS == ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0))

    def test_max_disposition_is_level_1(self):
        main = _fn("main")
        assert '"LEVEL_1_SHADOW_CANDIDATE"' in main
        assert "LEVEL_2" not in main
        assert "PROMOTION" not in main
