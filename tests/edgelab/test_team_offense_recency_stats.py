import random

import pytest

from lib.edgelab.backtest.team_offense_recency_stats import (
    ols_fit,
    ols_predict,
    evaluate_predictions,
    percentile,
    extreme_group_summary,
)


def _linear_rows(n, intercept, coefs, noise=0.0, seed=42):
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        features = {k: rng.uniform(-3, 3) for k in coefs}
        target = intercept + sum(coefs[k] * features[k] for k in coefs) + rng.uniform(-noise, noise)
        row = dict(features)
        row["y"] = target
        rows.append(row)
    return rows


class TestOlsFit:
    def test_recovers_known_linear_relationship_without_noise(self):
        rows = _linear_rows(200, intercept=3.0, coefs={"x1": 2.0, "x2": -1.0}, noise=0.0)
        coefs = ols_fit(rows, ["x1", "x2"], "y")
        assert coefs is not None
        assert coefs["intercept"] == pytest.approx(3.0, abs=1e-3)
        assert coefs["x1"] == pytest.approx(2.0, abs=1e-3)
        assert coefs["x2"] == pytest.approx(-1.0, abs=1e-3)

    def test_none_with_too_few_rows_for_feature_count(self):
        rows = _linear_rows(3, intercept=1.0, coefs={"x1": 1.0})
        assert ols_fit(rows, ["x1"], "y") is None

    def test_rows_with_missing_feature_or_target_skipped(self):
        rows = _linear_rows(200, intercept=1.0, coefs={"x1": 1.0}, noise=0.0)
        rows[0]["x1"] = None
        rows[1]["y"] = None
        coefs = ols_fit(rows, ["x1"], "y")
        assert coefs is not None

    def test_none_on_zero_variance_feature(self):
        rows = [{"x1": 5.0, "y": float(i)} for i in range(50)]
        assert ols_fit(rows, ["x1"], "y") is None

    def test_deterministic(self):
        rows = _linear_rows(100, intercept=1.0, coefs={"x1": 0.5, "x2": 0.3}, noise=1.0, seed=7)
        first = ols_fit(rows, ["x1", "x2"], "y")
        second = ols_fit(rows, ["x1", "x2"], "y")
        assert first == second


class TestOlsPredict:
    def test_matches_manual_dot_product(self):
        coefs = {"intercept": 1.0, "x1": 2.0, "x2": -0.5}
        row = {"x1": 3.0, "x2": 4.0}
        assert ols_predict(coefs, row, ["x1", "x2"]) == pytest.approx(1.0 + 2.0 * 3.0 - 0.5 * 4.0)

    def test_none_when_feature_missing(self):
        coefs = {"intercept": 1.0, "x1": 2.0}
        assert ols_predict(coefs, {"x1": None}, ["x1"]) is None

    def test_none_when_coefs_none(self):
        assert ols_predict(None, {"x1": 1.0}, ["x1"]) is None


class TestEvaluatePredictions:
    def test_perfect_predictions_have_zero_error(self):
        rows = _linear_rows(100, intercept=2.0, coefs={"x1": 1.0}, noise=0.0)
        coefs = ols_fit(rows, ["x1"], "y")
        metrics = evaluate_predictions(rows, coefs, ["x1"], "y")
        assert metrics["mae"] < 1e-3
        assert metrics["rmse"] < 1e-3

    def test_frozen_coefs_applied_unchanged_to_new_rows(self):
        dev_rows = _linear_rows(200, intercept=1.0, coefs={"x1": 2.0}, noise=0.0, seed=1)
        frozen = ols_fit(dev_rows, ["x1"], "y")
        holdout_rows = _linear_rows(50, intercept=1.0, coefs={"x1": 2.0}, noise=0.0, seed=2)
        metrics = evaluate_predictions(holdout_rows, frozen, ["x1"], "y")
        assert metrics["mae"] < 1e-3

    def test_none_when_no_usable_rows(self):
        assert evaluate_predictions([], {"intercept": 0.0, "x1": 1.0}, ["x1"], "y") is None

    def test_poisson_deviance_present_and_nonnegative(self):
        rows = [{"x1": 1.0, "y": 4.0}, {"x1": 2.0, "y": 6.0}, {"x1": 0.5, "y": 2.0}] * 10
        coefs = ols_fit(rows, ["x1"], "y")
        metrics = evaluate_predictions(rows, coefs, ["x1"], "y")
        assert metrics["meanPoissonDeviance"] >= 0


class TestPercentile:
    def test_median_of_odd_list(self):
        assert percentile([1, 2, 3, 4, 5], 50) == 3

    def test_min_and_max(self):
        values = [5, 1, 3, 2, 4]
        assert percentile(values, 0) == 1
        assert percentile(values, 100) == 5

    def test_empty_list_returns_none(self):
        assert percentile([], 50) is None

    def test_interpolates_between_values(self):
        assert percentile([0, 10], 50) == 5


class TestExtremeGroupSummary:
    def test_full_persistence_when_next_game_matches_recent_rate(self):
        rows = [{"runsScored": 8.0, "seasonToDateRunsPerGame": 4.0, "recentFormRate_10": 8.0} for _ in range(20)]
        summary = extreme_group_summary(rows, "recentFormRate_10")
        assert summary["persistenceFraction"] == pytest.approx(1.0)

    def test_full_regression_when_next_game_matches_baseline(self):
        rows = [{"runsScored": 4.0, "seasonToDateRunsPerGame": 4.0, "recentFormRate_10": 8.0} for _ in range(20)]
        summary = extreme_group_summary(rows, "recentFormRate_10")
        assert summary["persistenceFraction"] == pytest.approx(0.0)

    def test_none_persistence_fraction_when_no_gap(self):
        rows = [{"runsScored": 4.0, "seasonToDateRunsPerGame": 4.0, "recentFormRate_10": 4.0} for _ in range(20)]
        summary = extreme_group_summary(rows, "recentFormRate_10")
        assert summary["persistenceFraction"] is None

    def test_none_on_empty_rows(self):
        assert extreme_group_summary([], "recentFormRate_10") is None
