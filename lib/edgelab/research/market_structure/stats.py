"""
Inference primitives for the MRV program (standard library only).

- cluster_bootstrap(): game-clustered bootstrap CI + null-centred p-value
  for any statistic computed from a list of rows.  Resamples whole clusters
  (physical games) with replacement, never rows.
- benjamini_hochberg(): BH-FDR over a {key: p} map; the shared copy this
  program uses instead of an eleventh script-local one.
- wilson(): naive per-row interval, fallback/diagnostic only.
- date_half_stability(): sign agreement of a statistic between the first
  and second half of the distinct dates.
"""
import math
import random
from collections import defaultdict

DEFAULT_RESAMPLES = 2000
DEFAULT_SEED = 20260922
DEFAULT_CI = 0.90
FDR_Q = 0.10


def _percentile(sorted_vals, q):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * q
    f = math.floor(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def cluster_bootstrap(rows, value_fn, cluster_key="physicalGameKey", *, null_value=0.0,
                      n_resamples=DEFAULT_RESAMPLES, ci=DEFAULT_CI, seed=DEFAULT_SEED):
    """
    value_fn(rows_subset) -> float | None.  Returns dict:
      point, ciLow, ciHigh, pTwoSided (null-centred: the bootstrap
      distribution is shifted to `null_value` and the share of resamples at
      least as extreme as the observed point is reported), clusters, n,
      resamplesUsed, method.
    Deterministic for a given seed.  Rows lacking a cluster key are ignored
    (reported in `droppedNoCluster`).
    """
    by_c = defaultdict(list)
    dropped = 0
    for r in rows:
        k = r.get(cluster_key)
        if k is None:
            dropped += 1
            continue
        by_c[k].append(r)
    clusters = sorted(by_c, key=str)
    out = {"point": None, "ciLow": None, "ciHigh": None, "pTwoSided": None,
           "clusters": len(clusters), "n": sum(len(v) for v in by_c.values()),
           "resamplesUsed": 0, "droppedNoCluster": dropped, "method": "GAME_CLUSTERED_BOOTSTRAP"}
    if not clusters:
        return out
    point = value_fn([r for k in clusters for r in by_c[k]])
    out["point"] = point
    if point is None or len(clusters) < 2:
        return out
    rng = random.Random(seed)
    draws = []
    m = len(clusters)
    for _ in range(n_resamples):
        pick = [clusters[rng.randrange(m)] for _ in range(m)]
        v = value_fn([r for k in pick for r in by_c[k]])
        if v is not None:
            draws.append(v)
    if not draws:
        return out
    draws.sort()
    alpha = (1.0 - ci) / 2.0
    out["ciLow"] = _percentile(draws, alpha)
    out["ciHigh"] = _percentile(draws, 1.0 - alpha)
    out["resamplesUsed"] = len(draws)
    # null-centred p: shift bootstrap distribution so its mean equals null_value
    mean = sum(draws) / len(draws)
    shift = null_value - mean
    obs_dev = abs(point - null_value)
    extreme = sum(1 for d in draws if abs(d + shift - null_value) >= obs_dev - 1e-12)
    out["pTwoSided"] = (extreme + 1) / (len(draws) + 1)
    out["pPercentile"] = percentile_p(draws, null_value)
    return out


def percentile_p(draws, null_value=0.0):
    """Two-sided percentile-bootstrap p: 2 * min(share of draws <= null, share >= null), floored at 1/(B+1).
    Consistent with the percentile CI (p < 0.10 iff the CI90 excludes the null)."""
    if not draws:
        return None
    b = len(draws)
    lo = sum(1 for d in draws if d <= null_value)
    hi = sum(1 for d in draws if d >= null_value)
    return max(min(1.0, 2.0 * min(lo, hi) / b), 1.0 / (b + 1))


def cluster_bootstrap_ratio(rows, num_key, den_key, cluster_key="physicalGameKey", *, null_value=0.0,
                            n_resamples=DEFAULT_RESAMPLES, ci=DEFAULT_CI, seed=DEFAULT_SEED):
    """
    Fast path for ratio statistics (ROI = sum(pl)/sum(cash), weighted mean =
    sum(w*v)/sum(w)): per-cluster sums are precomputed once, so each resample
    costs O(clusters) instead of O(rows).  Same interface/semantics as
    cluster_bootstrap (rows with a None numerator are skipped).
    """
    sums = {}
    dropped = 0
    n = 0
    for r in rows:
        k = r.get(cluster_key)
        v = r.get(num_key)
        if k is None or v is None:
            dropped += 1
            continue
        w = r.get(den_key)
        if w is None:
            dropped += 1
            continue
        a = sums.setdefault(k, [0.0, 0.0])
        a[0] += v
        a[1] += w
        n += 1
    clusters = sorted(sums, key=str)
    out = {"point": None, "ciLow": None, "ciHigh": None, "pTwoSided": None, "pPercentile": None,
           "clusters": len(clusters), "n": n, "resamplesUsed": 0, "droppedNoCluster": dropped, "method": "GAME_CLUSTERED_BOOTSTRAP_RATIO"}
    if not clusters:
        return out
    tn = sum(sums[k][0] for k in clusters); td = sum(sums[k][1] for k in clusters)
    if td == 0:
        return out
    point = tn / td
    out["point"] = point
    if len(clusters) < 2:
        return out
    rng = random.Random(seed)
    m = len(clusters)
    vals = [sums[k] for k in clusters]
    draws = []
    for _ in range(n_resamples):
        sn = sd = 0.0
        for _ in range(m):
            a = vals[rng.randrange(m)]
            sn += a[0]; sd += a[1]
        if sd != 0:
            draws.append(sn / sd)
    if not draws:
        return out
    draws.sort()
    alpha = (1.0 - ci) / 2.0
    out["ciLow"] = _percentile(draws, alpha)
    out["ciHigh"] = _percentile(draws, 1.0 - alpha)
    out["resamplesUsed"] = len(draws)
    mean_d = sum(draws) / len(draws)
    shift = null_value - mean_d
    obs_dev = abs(point - null_value)
    extreme = sum(1 for d in draws if abs(d + shift - null_value) >= obs_dev - 1e-12)
    out["pTwoSided"] = (extreme + 1) / (len(draws) + 1)
    out["pPercentile"] = percentile_p(draws, null_value)
    return out


def benjamini_hochberg(pvalues_by_key, q=FDR_Q):
    """{key: p or None} -> {key: bool survivor}; None p never survives but counts toward m."""
    items = [(k, p) for k, p in pvalues_by_key.items()]
    m = len(items)
    valid = sorted([(k, p) for k, p in items if p is not None], key=lambda kv: kv[1])
    max_i = 0
    for i, (_, p) in enumerate(valid, start=1):
        if p <= q * i / m:
            max_i = i
    surv = {k: (i <= max_i) for i, (k, _) in enumerate(valid, start=1)}
    for k, p in items:
        if p is None:
            surv[k] = False
    return surv


def wilson(successes, n, z=1.645):
    if n <= 0:
        return None, None
    p = successes / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def date_half_stability(rows, value_fn, date_key="gameDate"):
    """Split distinct dates in half chronologically; report the statistic in each half and sign agreement."""
    dates = sorted({r.get(date_key) for r in rows if r.get(date_key)})
    if len(dates) < 2:
        return {"firstHalf": None, "secondHalf": None, "sameSign": None, "dates": len(dates)}
    cut = dates[len(dates) // 2]
    a = value_fn([r for r in rows if r.get(date_key) and r[date_key] < cut])
    b = value_fn([r for r in rows if r.get(date_key) and r[date_key] >= cut])
    same = None if a is None or b is None else ((a > 0) == (b > 0))
    return {"firstHalf": a, "secondHalf": b, "sameSign": same, "dates": len(dates), "cutDate": cut}


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def ols_slope(pairs):
    """Least-squares slope of y on x for [(x, y)]; None if <3 points or zero variance."""
    pts = [(x, y) for x, y in pairs if x is not None and y is not None]
    if len(pts) < 3:
        return None
    mx = sum(x for x, _ in pts) / len(pts)
    my = sum(y for _, y in pts) / len(pts)
    sxx = sum((x - mx) ** 2 for x, _ in pts)
    if sxx <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in pts) / sxx


def partial_slope(triples):
    """
    Coefficient on x in y ~ a + b*x + c*z (two-regressor OLS via normal
    equations).  triples = [(x, z, y)].  None if singular or < 4 points.
    """
    pts = [(x, z, y) for x, z, y in triples if None not in (x, z, y)]
    n = len(pts)
    if n < 4:
        return None
    sx = sum(p[0] for p in pts); sz = sum(p[1] for p in pts); sy = sum(p[2] for p in pts)
    sxx = sum(p[0] ** 2 for p in pts); szz = sum(p[1] ** 2 for p in pts); sxz = sum(p[0] * p[1] for p in pts)
    sxy = sum(p[0] * p[2] for p in pts); szy = sum(p[1] * p[2] for p in pts)
    # centred
    cxx = sxx - sx * sx / n; czz = szz - sz * sz / n; cxz = sxz - sx * sz / n
    cxy = sxy - sx * sy / n; czy = szy - sz * sy / n
    det = cxx * czz - cxz * cxz
    if abs(det) < 1e-12:
        return None
    return (cxy * czz - cxz * czy) / det
