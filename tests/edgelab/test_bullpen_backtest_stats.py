import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab.backtest import bullpen_backtest_stats as stats


def test_spearman_perfect_positive_correlation():
    pairs = [(i, i) for i in range(10)]
    assert stats.spearman_correlation(pairs) == 1.0


def test_spearman_perfect_negative_correlation():
    pairs = [(i, -i) for i in range(10)]
    assert stats.spearman_correlation(pairs) == -1.0


def test_spearman_none_for_small_n():
    assert stats.spearman_correlation([(1, 2), (2, 3)]) is None


def test_spearman_none_for_zero_variance():
    assert stats.spearman_correlation([(1, 5), (1, 5), (1, 5)]) is None


def test_spearman_ignores_none_values():
    pairs = [(1, 1), (2, None), (3, 3), (4, 4)]
    assert round(stats.spearman_correlation(pairs), 9) == 1.0


def _rows(n, cluster_prefix="G"):
    return [{"gamePk": f"{cluster_prefix}{i}", "x": i, "outcome": i * 2.0} for i in range(n)]


def test_correlation_with_ci_returns_expected_shape():
    rows = _rows(30)
    result = stats.correlation_with_ci(rows, "x", "outcome", cluster_key="gamePk", seed=1)
    assert result["n"] == 30
    assert round(result["spearman"], 9) == 1.0
    assert result["ci"]["low"] is not None and result["ci"]["high"] is not None
    assert result["ci"]["level"] == 0.95


def test_mean_difference_with_ci_basic():
    rows = [{"gamePk": f"G{i}", "flag": i % 2 == 0, "outcome": 10.0 if i % 2 == 0 else 2.0} for i in range(40)]
    result = stats.mean_difference_with_ci(rows, lambda r: r["flag"], "outcome", cluster_key="gamePk", seed=1)
    assert result["meanGroupA"] == 10.0
    assert result["meanGroupB"] == 2.0
    assert result["meanDifference"] == 8.0
    assert result["ci"]["low"] > 0  # confidently positive, given a clean deterministic split


def test_mean_difference_with_ci_excludes_missing_outcomes():
    rows = [
        {"gamePk": "G1", "flag": True, "outcome": 5.0},
        {"gamePk": "G2", "flag": True, "outcome": None},
        {"gamePk": "G3", "flag": False, "outcome": 1.0},
    ]
    result = stats.mean_difference_with_ci(rows, lambda r: r["flag"], "outcome", cluster_key="gamePk", seed=1)
    assert result["nGroupA"] == 1
    assert result["nGroupB"] == 1


def test_bucket_means_equal_count_buckets():
    rows = [{"x": i, "outcome": float(i)} for i in range(100)]
    buckets = stats.bucket_means(rows, "x", "outcome", n_buckets=10)
    assert len(buckets) == 10
    assert sum(b["n"] for b in buckets) == 100
    # monotonic in this deterministic fixture
    means = [b["outcomeMean"] for b in buckets]
    assert means == sorted(means)


def test_bucket_means_excludes_rows_missing_either_field():
    rows = [{"x": 1, "outcome": 1.0}, {"x": None, "outcome": 2.0}, {"x": 3, "outcome": None}]
    buckets = stats.bucket_means(rows, "x", "outcome", n_buckets=2)
    assert sum(b["n"] for b in buckets) == 1


def test_bucket_means_empty_input():
    assert stats.bucket_means([], "x", "outcome") == []
