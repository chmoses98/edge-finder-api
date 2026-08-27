"""
lib/edgelab/backtest/bullpen_backtest_stats.py
===================================================
Research Lab MLB-RSCH-0003: statistics helpers for the multi-season
bullpen workload backtest. Self-contained (no scipy dependency in this
repo, matching scripts/edgelab/run_edge_monotonicity_experiment.py's
own precedent of a small self-implemented Spearman correlation rather
than a new library dependency), reusing
lib.edgelab.research_stats.game_clustered_bootstrap_ci (the one
game-clustered-uncertainty primitive this repo already has) for every
confidence interval below -- never a naive per-row interval.
"""

from lib.edgelab.research_stats import game_clustered_bootstrap_ci

PRIMARY_CI = 0.95  # spec section 9: "Use 95% CI for primary confirmatory conclusions"


def _rank(values):
    """Average (tied) ranks, 1-indexed."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def spearman_correlation(pairs):
    """pairs: [(x, y), ...]. Returns None for n<3 or zero-variance input."""
    pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
    n = len(pairs)
    if n < 3:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    rx, ry = _rank(xs), _rank(ys)
    mean_rx, mean_ry = sum(rx) / n, sum(ry) / n
    cov = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
    var_x = sum((r - mean_rx) ** 2 for r in rx)
    var_y = sum((r - mean_ry) ** 2 for r in ry)
    if var_x == 0 or var_y == 0:
        return None
    return cov / (var_x ** 0.5 * var_y ** 0.5)


def correlation_with_ci(rows, predictor_key, outcome_key, cluster_key="gamePk", ci=PRIMARY_CI, seed=None):
    """Game-clustered bootstrap CI on Spearman correlation between
    predictor_key and outcome_key across `rows`."""
    pairs = [(r.get(predictor_key), r.get(outcome_key)) for r in rows]
    point = spearman_correlation(pairs)

    def value_fn(subset):
        return spearman_correlation([(r.get(predictor_key), r.get(outcome_key)) for r in subset])

    kwargs = {"seed": seed} if seed is not None else {}
    low, high, method = game_clustered_bootstrap_ci(rows, value_fn, cluster_key=cluster_key, ci=ci, **kwargs)
    return {"n": len(rows), "spearman": point, "ci": {"low": low, "high": high, "level": ci, "method": method}}


def mean_difference_with_ci(rows, group_predicate, outcome_key, cluster_key="gamePk", ci=PRIMARY_CI, seed=None):
    """
    Paired-style comparison: mean(outcome | group_predicate True) minus
    mean(outcome | group_predicate False), with a game-clustered
    bootstrap CI on that difference. Rows missing outcome_key are
    excluded from both the point estimate and every resample.
    """
    usable = [r for r in rows if r.get(outcome_key) is not None]
    group_a = [r for r in usable if group_predicate(r)]
    group_b = [r for r in usable if not group_predicate(r)]

    def mean_of(rows_subset):
        vals = [r[outcome_key] for r in rows_subset]
        return sum(vals) / len(vals) if vals else None

    point_a, point_b = mean_of(group_a), mean_of(group_b)
    point_diff = (point_a - point_b) if (point_a is not None and point_b is not None) else None

    def value_fn(subset):
        a = [r for r in subset if group_predicate(r)]
        b = [r for r in subset if not group_predicate(r)]
        ma, mb = mean_of(a), mean_of(b)
        return (ma - mb) if (ma is not None and mb is not None) else None

    kwargs = {"seed": seed} if seed is not None else {}
    low, high, method = game_clustered_bootstrap_ci(usable, value_fn, cluster_key=cluster_key, ci=ci, **kwargs)
    return {
        "nGroupA": len(group_a), "nGroupB": len(group_b),
        "meanGroupA": point_a, "meanGroupB": point_b, "meanDifference": point_diff,
        "ci": {"low": low, "high": high, "level": ci, "method": method},
    }


def bucket_means(rows, value_key, outcome_key, n_buckets=10):
    """
    Descriptive decile (or n_buckets-tile) bucketing of `outcome_key` by
    `value_key` -- used for both the current-formula multiplier bucket
    table (spec section 8) and the raw-workload decile table (H1/H4
    nonlinearity check, spec section 9). Buckets are equal-COUNT
    (rank-based), not equal-width, so a skewed predictor still produces
    a readable table. Rows missing either field are excluded.
    """
    usable = [r for r in rows if r.get(value_key) is not None and r.get(outcome_key) is not None]
    if not usable:
        return []
    ordered = sorted(usable, key=lambda r: r[value_key])
    n = len(ordered)
    buckets = []
    for b in range(n_buckets):
        start = (n * b) // n_buckets
        end = (n * (b + 1)) // n_buckets
        chunk = ordered[start:end]
        if not chunk:
            continue
        values = [r[value_key] for r in chunk]
        outcomes = [r[outcome_key] for r in chunk]
        buckets.append({
            "bucket": b + 1,
            "n": len(chunk),
            "valueMin": min(values), "valueMax": max(values),
            "outcomeMean": sum(outcomes) / len(outcomes),
        })
    return buckets
