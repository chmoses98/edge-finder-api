"""
MLB-RSCH-0005 statistics: a small, dependency-free (no numpy) closed-form
multiple linear regression, plus prediction-error metrics for the
CONTROL-vs-RECENCY-CANDIDATE comparison. Deliberately the simplest
defensible fit -- ordinary least squares via the normal equations, solved
once by Gauss-Jordan elimination, no regularization, no hyperparameters,
no cross-validation, fit ONCE on development rows and frozen unchanged for
validation/holdout. Mirrors research_stats.calibration_slope_intercept's
"documented, deliberately simple" spirit, generalized to multiple features
because this experiment's H4 needs more than one predictor.
"""
import math


def percentile(values, pct):
    """Linear-interpolation percentile (pct in [0, 100]) of a list of
    numbers. None on an empty list. Pure, deterministic, no numpy."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return ordered[lo]
    frac = rank - lo
    return ordered[lo] + frac * (ordered[hi] - ordered[lo])


def extreme_group_summary(rows, recent_rate_key, baseline_key="seasonToDateRunsPerGame", outcome_key="runsScored"):
    """
    Descriptive persistence-vs-regression summary for a preselected
    subset of rows (the caller filters to an "extreme" group using
    FROZEN, development-only cutoffs before calling this). Reports the
    group's own actual next-game scoring against its own recent hot/cold
    rate and its own season baseline -- persistenceFraction near 0 means
    full regression to baseline by the next game; near 1 means the
    recent hot/cold rate persisted essentially unchanged.
    """
    usable = [r for r in rows if r.get(outcome_key) is not None and r.get(baseline_key) is not None and r.get(recent_rate_key) is not None]
    n = len(usable)
    if n == 0:
        return None
    mean_next = sum(r[outcome_key] for r in usable) / n
    mean_baseline = sum(r[baseline_key] for r in usable) / n
    mean_recent = sum(r[recent_rate_key] for r in usable) / n
    gap = mean_recent - mean_baseline
    persistence_fraction = (mean_next - mean_baseline) / gap if gap else None
    return {
        "n": n,
        "meanNextGameRuns": round(mean_next, 4),
        "meanSeasonBaseline": round(mean_baseline, 4),
        "meanRecentFormRate": round(mean_recent, 4),
        "persistenceFraction": round(persistence_fraction, 4) if persistence_fraction is not None else None,
    }


def _solve_linear_system(matrix, vector):
    """Gauss-Jordan elimination with partial pivoting. matrix is an NxN
    list-of-lists, vector is length N. Returns the length-N solution, or
    None if the system is singular (e.g. a feature with zero variance)."""
    n = len(vector)
    aug = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot_row = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot_row][col]) < 1e-12:
            return None
        aug[col], aug[pivot_row] = aug[pivot_row], aug[col]
        pivot = aug[col][col]
        aug[col] = [v / pivot for v in aug[col]]
        for r in range(n):
            if r == col:
                continue
            factor = aug[r][col]
            if factor:
                aug[r] = [aug[r][k] - factor * aug[col][k] for k in range(n + 1)]
    return [aug[r][n] for r in range(n)]


def ols_fit(rows, feature_keys, target_key):
    """
    Fits y = intercept + sum(coef_i * feature_i) via ordinary least
    squares (normal equations) on `rows` (each a dict). Rows missing any
    feature or the target are skipped. Returns {"intercept": ..., feature:
    coef, ...} or None if fewer than 5x the feature count usable rows, or
    the system is singular.
    """
    usable = []
    for r in rows:
        if r.get(target_key) is None:
            continue
        if any(r.get(k) is None for k in feature_keys):
            continue
        usable.append(r)
    p = len(feature_keys) + 1  # + intercept
    if len(usable) < 5 * p:
        return None

    design = [[1.0] + [float(r[k]) for k in feature_keys] for r in usable]
    targets = [float(r[target_key]) for r in usable]

    xtx = [[sum(design[i][a] * design[i][b] for i in range(len(usable))) for b in range(p)] for a in range(p)]
    xty = [sum(design[i][a] * targets[i] for i in range(len(usable))) for a in range(p)]

    solution = _solve_linear_system(xtx, xty)
    if solution is None:
        return None
    coefs = {"intercept": round(solution[0], 6)}
    for idx, key in enumerate(feature_keys):
        coefs[key] = round(solution[idx + 1], 6)
    return coefs


def ols_predict(coefs, row, feature_keys):
    """Applies a frozen coefs dict (from ols_fit) to one row. None if any
    required feature is missing on this row."""
    if coefs is None or any(row.get(k) is None for k in feature_keys):
        return None
    value = coefs["intercept"]
    for key in feature_keys:
        value += coefs[key] * row[key]
    return value


def evaluate_predictions(rows, coefs, feature_keys, target_key):
    """
    Applies a FROZEN coefs dict (fit on development, never refit here) to
    `rows` and reports MAE, RMSE, and mean Poisson deviance (a defensible
    count-outcome error metric for a non-negative integer target like
    runs scored) between predicted and actual. Predictions are clamped to
    a small positive epsilon before the Poisson deviance term (log(0) is
    undefined) -- this never changes the fitted coefficients themselves.
    """
    pairs = []
    for r in rows:
        predicted = ols_predict(coefs, r, feature_keys)
        actual = r.get(target_key)
        if predicted is None or actual is None:
            continue
        pairs.append((float(actual), float(predicted)))
    n = len(pairs)
    if n == 0:
        return None

    abs_errors = [abs(a - p) for a, p in pairs]
    sq_errors = [(a - p) ** 2 for a, p in pairs]
    mae = sum(abs_errors) / n
    rmse = math.sqrt(sum(sq_errors) / n)

    deviance_terms = []
    for actual, predicted in pairs:
        mu = max(predicted, 1e-6)
        if actual > 0:
            deviance_terms.append(2 * (actual * math.log(actual / mu) - (actual - mu)))
        else:
            deviance_terms.append(2 * mu)
    mean_poisson_deviance = sum(deviance_terms) / n

    return {
        "n": n,
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "meanPoissonDeviance": round(mean_poisson_deviance, 4),
    }
