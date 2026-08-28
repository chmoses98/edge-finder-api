#!/usr/bin/env python3
"""
tests/edgelab/test_run_market_residual_experiment_script.py
=========================================================
Coverage for scripts/edgelab/run_market_residual_experiment.py --
MLB-RSCH-0024's market-anchored residual model.
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

import run_market_residual_experiment as exp  # noqa: E402

SCRIPT_PATH = os.path.join(_ROOT, "scripts", "edgelab", "run_market_residual_experiment.py")


def _find_function_node(name):
    tree = ast.parse(open(SCRIPT_PATH).read())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found")


class TestRegistrationIdempotent:
    def test_idempotent(self, tmp_path, monkeypatch):
        import lib.edgelab.experiment_registry as reg
        import lib.edgelab.control_identity as ctrl_id
        monkeypatch.setattr(reg, "EXPERIMENTS_ROOT", str(tmp_path / "experiments"))
        monkeypatch.setattr(ctrl_id, "CONTROL_MODELS_ROOT", str(tmp_path / "control_models"))
        c1, d1 = exp.register_experiment()
        c2, d2 = exp.register_experiment()
        assert d1 == d2 and c1 == c2

    def test_evidence_level_e4(self, tmp_path, monkeypatch):
        import lib.edgelab.experiment_registry as reg
        import lib.edgelab.control_identity as ctrl_id
        monkeypatch.setattr(reg, "EXPERIMENTS_ROOT", str(tmp_path / "experiments"))
        monkeypatch.setattr(ctrl_id, "CONTROL_MODELS_ROOT", str(tmp_path / "control_models"))
        _, d = exp.register_experiment()
        assert d["evidenceLevel"] == "E4_PROSPECTIVE_SHADOW"


class TestResidualFormEndpoints:
    def test_alpha_zero_is_exactly_market(self):
        for model_p, market_p in ((0.9, 0.5), (0.1, 0.7), (0.5, 0.5)):
            assert abs(exp.residual_probability(model_p, market_p, 0.0) - market_p) < 1e-9

    def test_alpha_one_is_exactly_model(self):
        for model_p, market_p in ((0.9, 0.5), (0.1, 0.7), (0.3, 0.8)):
            assert abs(exp.residual_probability(model_p, market_p, 1.0) - model_p) < 1e-9

    def test_alpha_between_is_between(self):
        p = exp.residual_probability(0.9, 0.5, 0.5)
        assert 0.5 < p < 0.9

    def test_negative_alpha_moves_opposite_the_model(self):
        p = exp.residual_probability(0.9, 0.5, -0.5)
        assert p < 0.5  # model is bullish; anti-signal pushes below the market

    def test_output_always_in_unit_interval(self):
        for a in (-2.0, 0.0, 1.0, 3.0):
            for mp, kp in ((0.01, 0.99), (0.99, 0.01), (0.5, 0.5)):
                assert 0.0 < exp.residual_probability(mp, kp, a) < 1.0


class TestAlphaRecovery:
    def _rows(self, informativeness, n=1500, seed=3):
        rng = random.Random(seed)
        rows = []
        for i in range(n):
            mk = rng.uniform(0.15, 0.85)
            true = exp._sigmoid(exp._logit(mk) + rng.gauss(0, 0.8))
            md = exp._sigmoid(exp._logit(mk) + (exp._logit(true) - exp._logit(mk)) * informativeness)
            rows.append({"modelP": md, "marketFair": mk,
                         "outcome": 1 if rng.random() < true else 0, "gameId": f"G{i // 5}"})
        return rows

    def test_recovers_alpha_near_one_for_informative_model(self):
        fit = exp.fit_alpha(self._rows(1.0))
        assert 0.7 < fit["alpha"] < 1.3

    def test_recovers_alpha_near_zero_for_pure_noise_model(self):
        rng = random.Random(11)
        rows = []
        for i in range(1500):
            mk = rng.uniform(0.15, 0.85)
            md = exp._sigmoid(exp._logit(mk) + rng.gauss(0, 1.0))  # noise, unrelated to outcome
            rows.append({"modelP": md, "marketFair": mk, "outcome": 1 if rng.random() < mk else 0, "gameId": f"G{i // 5}"})
        fit = exp.fit_alpha(rows)
        assert abs(fit["alpha"]) < 0.25

    def test_recovers_negative_alpha_for_anti_signal_model(self):
        fit = exp.fit_alpha(self._rows(-1.0, seed=5))
        assert fit["alpha"] < 0

    def test_deterministic(self):
        rows = self._rows(1.0)
        assert exp.fit_alpha(rows) == exp.fit_alpha(rows)

    def test_respects_preregistered_bounds(self):
        rows = self._rows(10.0, seed=9)
        fit = exp.fit_alpha(rows)
        assert exp.ALPHA_BOUNDS[0] <= fit["alpha"] <= exp.ALPHA_BOUNDS[1]

    def test_returns_none_below_minimum_sample(self):
        assert exp.fit_alpha(self._rows(1.0, n=10)) is None

    def test_bounds_are_preregistered_constants(self):
        assert exp.ALPHA_BOUNDS == (-2.0, 3.0)


class TestFairPriceIsVigFreeMidNotAsk:
    def test_fair_mid_is_midpoint_of_bid_ask(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("load_pregame_fair_prices"))
        assert '"fairMid": round(((yes_bid + yes_ask) / 2.0) / 100.0, 6)' in source

    def test_executable_ask_retained_separately(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("load_pregame_fair_prices"))
        assert '"executableAsk"' in source and '"yesBid"' in source and '"yesAsk"' in source

    def test_only_valid_pregame_observations_used(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("load_pregame_fair_prices"))
        assert "isValidPregameObservation" in source
        assert "gameStartedAtCapture" in source

    def test_scoring_functions_never_use_executable_ask(self):
        for fn in ("score_forecaster", "paired_delta", "_market_fn", "residual_probability"):
            source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node(fn))
            assert "executableAsk" not in source

    def test_executable_ask_used_only_in_secondary_economics(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("secondary_economics"))
        assert "executableAsk" in source
        assert "taker_fee(" in source


class TestPrimaryComparisonIsVsMarket:
    def test_selection_gate_uses_market_delta(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("selection_passes"))
        assert "val_delta_vs_market" in source

    def test_main_labels_m2_vs_m0_as_primary(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        assert "val_m2_vs_m0 = paired_delta(val, _residual_fn(global_alpha), _market_fn)" in main_source
        assert "PRIMARY" in main_source

    def test_selection_fails_when_only_beats_production(self):
        passes, reasons = exp.selection_passes(
            val_delta_vs_market=0.01, alpha_ci={"low": 0.2, "high": 0.5},
            family_concentration_ok=True, val_ece_residual=0.05, val_ece_market=0.05)
        assert not passes
        assert any("vs MARKET" in r for r in reasons)


class TestSelectionRule:
    def test_passes_when_all_gates_met(self):
        passes, reasons = exp.selection_passes(-0.01, {"low": 0.1, "high": 0.6}, True, 0.05, 0.05)
        assert passes and reasons == []

    def test_fails_when_alpha_ci_includes_zero(self):
        passes, reasons = exp.selection_passes(-0.01, {"low": -0.2, "high": 0.4}, True, 0.05, 0.05)
        assert not passes
        assert any("includes 0" in r for r in reasons)

    def test_fails_on_single_family_concentration(self):
        passes, reasons = exp.selection_passes(-0.01, {"low": 0.1, "high": 0.6}, False, 0.05, 0.05)
        assert not passes

    def test_fails_when_calibration_materially_worse(self):
        passes, reasons = exp.selection_passes(-0.01, {"low": 0.1, "high": 0.6}, True, 0.20, 0.05)
        assert not passes


class TestFixedBucketsAndTiers:
    def test_disagreement_buckets_are_preregistered_constants(self):
        assert exp.DISAGREEMENT_BUCKETS == ((0.0, 0.025), (0.025, 0.05), (0.05, 0.075), (0.075, 0.10), (0.10, 1.01))

    def test_tiers_are_preregistered_constants(self):
        assert set(exp.TIERS) == {"TIER_GAME_OUTCOME", "TIER_TOTALS", "TIER_INNING", "TIER_MARGIN", "TIER_PROPS"}
        assert "pitcher_strikeouts" in exp.TIERS["TIER_PROPS"]

    def test_unmapped_family_is_excluded_not_defaulted(self):
        assert exp.tier_for_family("hitter_hits") is None

    def test_minimum_samples_are_constants(self):
        assert exp.MIN_ROWS_TIER == 150 and exp.MIN_GAMES_TIER == 25
        assert exp.MIN_ROWS_FAMILY == 100 and exp.MIN_GAMES_FAMILY == 20


class TestForwardWindowUntouched:
    def test_main_never_references_september_dates(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        assert "2026-09" not in main_source

    def test_val_upper_bound_enforced(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        assert 'r["settleDate"] <= VAL_DATE_MAX' in main_source.replace("'", '"')

    def test_frozen_forward_spec_emitted_with_required_fields(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        for field in ('"candidateId"', '"alpha"', '"trainingEndDate"', '"eligibleFamilies"', '"version"', '"forwardEvaluationRule"'):
            assert field in main_source


class TestNoRoiFitting:
    def test_economics_never_selects(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("secondary_economics"))
        assert "descriptive only" in source
        assert "max(" not in source.replace("max(r[", "").replace("max(price", "")

    def test_fit_alpha_never_sees_prices_or_pl(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("fit_alpha"))
        for term in ("executableAsk", "taker_fee", "roi", "netPl"):
            assert term not in source

    def test_nll_objective_uses_outcomes_only(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("_nll"))
        assert 'r["outcome"]' in source
        assert "executableAsk" not in source


class TestLoadersReused:
    def test_rsch0022_loaders_reused_not_reimplemented(self):
        source = open(SCRIPT_PATH).read()
        assert "rsch0022.load_settled_outcomes()" in source
        assert "rsch0022.build_audit_rows(" in source
        assert "def load_settled_outcomes" not in source
        assert "def build_audit_rows" not in source


class TestBenjaminiHochberg:
    def test_step_up(self):
        result = exp.benjamini_hochberg({"a": 0.01, "b": 0.04, "c": 0.20}, alpha=0.10)
        assert result["a"] and result["b"] and not result["c"]

    def test_none_ignored(self):
        assert exp.benjamini_hochberg({"a": 0.001, "b": None}) == {"a": True}
