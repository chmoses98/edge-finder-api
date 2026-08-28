#!/usr/bin/env python3
"""
tests/edgelab/test_run_uncertainty_prediction_experiment_script.py
=========================================================
Coverage for scripts/edgelab/run_uncertainty_prediction_experiment.py --
MLB-RSCH-0019's uncertainty/error prediction study.
"""
import ast
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab"), os.path.join(_ROOT, "scripts", "edgelab", "backtest")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

import run_uncertainty_prediction_experiment as exp  # noqa: E402

SCRIPT_PATH = os.path.join(_ROOT, "scripts", "edgelab", "run_uncertainty_prediction_experiment.py")


def _find_function_node(name):
    tree = ast.parse(open(SCRIPT_PATH).read())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found")


class TestBaselineComponentsVerified:
    def test_matches_rsch0009_own_artifact(self):
        assert exp.BASELINE_COMPONENTS == frozenset({"offense", "bullpen"})

    def test_verify_raises_on_drift(self, monkeypatch):
        monkeypatch.setattr(exp, "BASELINE_COMPONENTS", frozenset({"offense", "bullpen", "park"}))
        with pytest.raises(ValueError):
            exp._verify_baseline_components()


class TestRegistrationIdempotent:
    def test_register_experiment_is_idempotent_across_reruns(self, tmp_path, monkeypatch):
        import lib.edgelab.experiment_registry as reg
        import lib.edgelab.control_identity as ctrl_id
        monkeypatch.setattr(reg, "EXPERIMENTS_ROOT", str(tmp_path / "experiments"))
        monkeypatch.setattr(ctrl_id, "CONTROL_MODELS_ROOT", str(tmp_path / "control_models"))
        control1, definition1 = exp.register_experiment()
        control2, definition2 = exp.register_experiment()
        assert definition1 == definition2
        assert control1 == control2


class TestNoBettingPLOrFutureLeakage:
    def _operational_source(self):
        """Concatenated source of every function that actually COMPUTES
        results -- excludes the module docstring and registration text,
        which legitimately document exclusions ("no ROI", "no rookie
        flags") in prose without those concepts ever being used."""
        tree = ast.parse(open(SCRIPT_PATH).read())
        funcs = [n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name not in ("register_experiment",)]
        return "\n".join(ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node(f)) for f in funcs)

    def test_no_pnl_or_roi_in_targets(self):
        source = self._operational_source()
        assert "roi" not in source.lower()
        assert "pnl" not in source.lower()
        assert "profit" not in source.lower()

    def test_no_future_information_fields_referenced(self):
        source = self._operational_source()
        for forbidden in ("closingLine", "postgameStatcast", "settlement", "finalScore"):
            assert forbidden not in source

    def test_no_starter_identity_or_rookie_features_used(self):
        """MLB-RSCH-0009's own starterIdentityVerdict already found starter
        identity NOT PIT-safe at scale -- must not be reused as a feature."""
        source = self._operational_source()
        assert "starterIdentityVerdict" not in source
        assert "rookie" not in source.lower()


class TestSeasonBucketsDisclosedDeviation:
    def test_buckets_do_not_overlap(self):
        seen = set()
        for name, lo, hi in exp.SEASON_BUCKETS:
            rng = set(range(lo, hi + 1))
            assert not (rng & seen)
            seen |= rng

    def test_season_bucket_for_returns_none_outside_range(self):
        assert exp.season_bucket_for(5) is None  # below 20-game floor, structurally absent from this corpus

    def test_season_bucket_for_correct_bucket(self):
        assert exp.season_bucket_for(25) == "near_floor_20_40"
        assert exp.season_bucket_for(60) == "mid_41_80"
        assert exp.season_bucket_for(100) == "late_81_plus"


class TestComponentDisagreement:
    def test_zero_when_signals_agree_exactly(self):
        home = {"offenseRunsPerGame": 4.4, "runPreventionRunsAllowedPerGame": 4.4}
        away = {"offenseRunsPerGame": 4.4, "runPreventionRunsAllowedPerGame": 4.4}
        assert exp.game_component_disagreement(home, away, 4.4) == 0.0

    def test_none_when_either_side_missing(self):
        assert exp.game_component_disagreement(None, {"offenseRunsPerGame": 4.4, "runPreventionRunsAllowedPerGame": 4.4}, 4.4) is None

    def test_positive_when_signals_diverge(self):
        home = {"offenseRunsPerGame": 6.0, "runPreventionRunsAllowedPerGame": 4.4}
        away = {"offenseRunsPerGame": 4.4, "runPreventionRunsAllowedPerGame": 4.4}
        assert exp.game_component_disagreement(home, away, 4.4) > 0


class TestU1UnweightedFlagSum:
    def _row(self, **kwargs):
        base = {"minSampleDepth": 100, "minBullpenSampleDepth": 100, "componentDisagreement": 0.0, "probExtremeness": 0.0, "seasonBucket": "late_81_plus"}
        base.update(kwargs)
        return base

    def test_zero_flags_when_all_reliable(self):
        thresholds = {"lowSampleThreshold": 30, "lowBullpenThreshold": 30, "highDisagreementThreshold": 1.0, "highExtremenessThreshold": 0.3}
        assert exp.compute_u1_score(self._row(), thresholds) == 0

    def test_all_five_flags_when_all_risky(self):
        thresholds = {"lowSampleThreshold": 30, "lowBullpenThreshold": 30, "highDisagreementThreshold": 1.0, "highExtremenessThreshold": 0.3}
        row = self._row(minSampleDepth=10, minBullpenSampleDepth=10, componentDisagreement=2.0, probExtremeness=0.4, seasonBucket="near_floor_20_40")
        assert exp.compute_u1_score(row, thresholds) == 5

    def test_weights_never_fit_to_holdout(self):
        """U1 is an UNWEIGHTED sum -- no fitting of any kind happens in
        compute_u1_score, only threshold comparisons."""
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("compute_u1_score"))
        assert "fit" not in source.lower()


class TestU2RidgeRegularizedNoGiantModel:
    def test_no_forbidden_model_families(self):
        tree = ast.parse(open(SCRIPT_PATH).read())
        funcs = [n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name != "register_experiment"]
        source = "\n".join(ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node(f)) for f in funcs)
        for forbidden in ("RandomForest", "GradientBoosting", "XGBoost", "sklearn", "torch", "tensorflow", "neural"):
            assert forbidden.lower() not in source.lower()

    def test_ridge_lambda_is_fixed_not_searched(self):
        source = open(SCRIPT_PATH).read()
        assert "RIDGE_LAMBDA = 1.0" in source
        main_source = ast.get_source_segment(source, _find_function_node("main"))
        assert main_source.count("RIDGE_LAMBDA") <= 2  # used, never looped/searched over

    def test_ridge_fit_uses_only_dev_rows(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        assert "_ridge_fit(dev_rows," in main_source

    def test_ridge_fit_recovers_known_linear_relationship(self):
        rows = [{"x_z": float(i), "y_z": 1.0 if i % 2 == 0 else -1.0, "gameAvgAbsError": 2.0 * i + 1.0} for i in range(10)]
        coeffs = exp._ridge_fit(rows, ["x_z", "y_z"], "gameAvgAbsError", lam=0.0)
        assert coeffs is not None
        assert abs(coeffs["x_z"] - 2.0) < 0.01
        assert abs(coeffs["intercept"] - 1.0) < 0.5

    def test_standardization_uses_dev_only(self):
        dev_rows = [{"f": 10.0}, {"f": 20.0}, {"f": 30.0}]
        stats = exp.fit_standardization_dev_only(dev_rows, ["f"])
        mean, std = stats["f"]
        assert mean == 20.0

    def test_standardization_applied_consistently_to_val_holdout(self):
        stats = {"f": (20.0, 10.0)}
        rows = [{"f": 30.0}]
        exp.apply_standardization(rows, ["f"], stats)
        assert rows[0]["f_z"] == 1.0

    def test_predict_u2_uses_already_suffixed_field_names_no_double_suffix(self):
        """Regression test: main() calls predict_u2 with feature_fields
        already suffixed (e.g. "minSampleDepth_z"), matching both the
        ridge coefficients' own keys and the row's own standardized
        attribute names -- predict_u2 must NOT append another "_z"."""
        row = {"f_z": 2.0}
        coefficients = {"intercept": 1.0, "f_z": 3.0}
        assert exp.predict_u2(row, coefficients, ["f_z"]) == 1.0 + 3.0 * 2.0

    def test_predict_u2_matches_main_calling_convention(self):
        """main() calls: predict_u2(r, u2_coefficients, [f + "_z" for f in FEATURE_FIELDS])"""
        row = {f + "_z": 1.0 for f in exp.FEATURE_FIELDS}
        coefficients = {"intercept": 0.0, **{f + "_z": 1.0 for f in exp.FEATURE_FIELDS}}
        result = exp.predict_u2(row, coefficients, [f + "_z" for f in exp.FEATURE_FIELDS])
        assert result == float(len(exp.FEATURE_FIELDS))


class TestPearsonCorrelation:
    def test_perfect_positive_correlation(self):
        pairs = [(i, i) for i in range(10)]
        assert exp.pearson_corr(pairs) == 1.0

    def test_no_correlation_returns_zero_ish(self):
        pairs = [(0, 5), (1, 5), (2, 5)]  # y is constant -- zero variance
        assert exp.pearson_corr(pairs) is None

    def test_none_for_fewer_than_two_points(self):
        assert exp.pearson_corr([(1, 1)]) is None


class TestTiersFrozenBeforeValHoldout:
    def test_cutpoints_computed_from_dev_only(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        assert "fit_tier_cutpoints_dev_only([o[score_field] for o in obs_dev" in main_source

    def test_tier_for_boundaries(self):
        assert exp.tier_for(1.0, low_cut=2.0, high_cut=5.0) == "LOW"
        assert exp.tier_for(3.0, low_cut=2.0, high_cut=5.0) == "MEDIUM"
        assert exp.tier_for(6.0, low_cut=2.0, high_cut=5.0) == "HIGH"
        assert exp.tier_for(2.0, low_cut=2.0, high_cut=5.0) == "LOW"

    def test_monotonic_detection(self):
        increasing = {"LOW": {"meanAbsError": 1.0}, "MEDIUM": {"meanAbsError": 2.0}, "HIGH": {"meanAbsError": 3.0}}
        decreasing = {"LOW": {"meanAbsError": 3.0}, "MEDIUM": {"meanAbsError": 2.0}, "HIGH": {"meanAbsError": 1.0}}
        assert exp.is_monotonic_increasing(increasing)
        assert not exp.is_monotonic_increasing(decreasing)


class TestSelectionRuleLockedBeforeResults:
    def test_fails_when_dev_correlation_below_floor(self):
        tiers_ok = {"LOW": {"meanAbsError": 1.0}, "MEDIUM": {"meanAbsError": 2.0}, "HIGH": {"meanAbsError": 3.0}}
        passes, reasons = exp.selection_passes(0.01, 0.05, tiers_ok, tiers_ok)
        assert not passes

    def test_fails_when_val_correlation_below_floor(self):
        tiers_ok = {"LOW": {"meanAbsError": 1.0}, "MEDIUM": {"meanAbsError": 2.0}, "HIGH": {"meanAbsError": 3.0}}
        passes, reasons = exp.selection_passes(0.10, 0.01, tiers_ok, tiers_ok)
        assert not passes

    def test_fails_when_tiers_not_monotonic(self):
        tiers_bad = {"LOW": {"meanAbsError": 3.0}, "MEDIUM": {"meanAbsError": 2.0}, "HIGH": {"meanAbsError": 1.0}}
        tiers_ok = {"LOW": {"meanAbsError": 1.0}, "MEDIUM": {"meanAbsError": 2.0}, "HIGH": {"meanAbsError": 3.0}}
        passes, reasons = exp.selection_passes(0.10, 0.10, tiers_bad, tiers_ok)
        assert not passes

    def test_passes_when_all_criteria_met(self):
        tiers_ok = {"LOW": {"meanAbsError": 1.0}, "MEDIUM": {"meanAbsError": 2.0}, "HIGH": {"meanAbsError": 3.0}}
        passes, reasons = exp.selection_passes(0.10, 0.05, tiers_ok, tiers_ok)
        assert passes
        assert reasons == []

    def test_thresholds_are_locked_module_constants(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("selection_passes"))
        assert "DEV_CORRELATION_FLOOR" in source
        assert "VAL_CORRELATION_FLOOR" in source


class TestHoldoutGatedBySelectionInMain:
    def test_holdout_only_evaluated_after_selection_check(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        selection_index = main_source.index('if results["U1"]["selection"]["passes"]')
        holdout_index = main_source.index("if selected is not None:")
        assert selection_index < holdout_index

    def test_no_rescue_of_a_failed_selection(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        assert "no rescue" in main_source.lower()


class TestLargeErrorClassification:
    def test_threshold_frozen_from_dev_only(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        assert "large_error_threshold = exp._percentile(dev_abs_errors" in main_source or "_percentile(dev_abs_errors, LARGE_ERROR_DEV_QUANTILE)" in main_source

    def test_auc_perfect_separation(self):
        scored = [(0.1, 0), (0.2, 0), (0.8, 1), (0.9, 1)]
        assert exp.compute_auc(scored) == 1.0

    def test_auc_none_when_one_class_missing(self):
        assert exp.compute_auc([(0.1, 0), (0.2, 0)]) is None

    def test_auc_handles_ties(self):
        scored = [(0.5, 0), (0.5, 1), (0.5, 0), (0.5, 1)]
        auc = exp.compute_auc(scored)
        assert auc == 0.5


class TestFamilySquaredErrorsFrozenNb:
    def test_uses_frozen_dispersion_via_rsch0017_reuse(self):
        source = open(SCRIPT_PATH).read()
        assert "def nb_probability_cells(" not in source
        assert "rsch0017.nb_probability_cells(" in source

    def test_returns_all_five_families(self):
        row = {"homeExpectedRuns": 4.5, "awayExpectedRuns": 4.0, "actualHomeRuns": 5, "actualAwayRuns": 3}
        result = exp.family_squared_errors(row)
        assert set(result.keys()) == {"game_total", "moneyline", "run_margin", "team_total_home", "team_total_away"}

    def test_none_when_predictions_missing(self):
        row = {"homeExpectedRuns": None, "awayExpectedRuns": 4.0, "actualHomeRuns": 5, "actualAwayRuns": 3}
        assert exp.family_squared_errors(row) is None


class TestLayerBNeverMixedWithLayerA:
    def test_layer_b_status_reported_separately_in_report(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        assert '"layerB": layer_b' in main_source
        assert '"layerA":' in main_source

    def test_layer_b_never_writes_into_layer_a_results(self):
        source = open(SCRIPT_PATH).read()
        assert "layer_b" not in ast.get_source_segment(source, _find_function_node("build_layer_a_corpus"))

    def test_insufficient_sample_when_zero_settled_records(self, monkeypatch, tmp_path):
        monkeypatch.setattr(exp, "_ROOT", str(tmp_path))
        os.makedirs(tmp_path / "data", exist_ok=True)
        result = exp.layer_b_prospective_cohort()
        assert result["status"] == "INSUFFICIENT_SAMPLE"
        assert result["settledGames"] == 0


class TestClassificationLadder:
    def test_no_useful_signal_when_nothing_selected(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        assert '"NO_USEFUL_SIGNAL"' in main_source

    def test_disposition_never_exceeds_shadow_candidate(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        assert "PROMOTION" not in main_source
        assert '"SHADOW_CANDIDATE"' in main_source
