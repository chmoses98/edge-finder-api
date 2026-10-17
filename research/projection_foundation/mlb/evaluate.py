"""Scoring, distribution construction and comparison helpers shared by the MLB families."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from pf_core import dists, metrics, models

# Kalshi rungs (P(X >= k)) per family
RUNGS = {
    "pitcher_strikeouts": [3, 4, 5, 6, 7, 8, 9],
    "outs": [12, 15, 16, 17, 18, 19],
    "batters_faced": [18, 20, 22, 24, 26],
    "pitcher_walks": [1, 2, 3, 4],
    "pitcher_hits_allowed": [3, 4, 5, 6, 7],
    "pitcher_runs_allowed": [1, 2, 3, 4, 5],
    "plate_appearances": [4, 5],
    "hits": [1, 2, 3],
    "total_bases": [1, 2, 3, 4],
    "home_runs": [1],
    "runs": [1, 2],
    "rbis": [1, 2, 3],
    "hits_runs_rbis": [1, 2, 3, 4, 5],
    "team_runs": [2, 3, 4, 5, 6, 7],
    "game_total": [7, 8, 9, 10, 11],
}


def score(y, dist: dists.DistBatch, clusters, family: str | None = None) -> dict:
    y = np.asarray(y, float)
    pm = metrics.point_metrics(y, dist.mean)
    dm = metrics.dist_metrics(y, dist, thresholds=RUNGS.get(family))
    out = {"n_obs": int(len(y)), "n_clusters": int(pd.Series(np.asarray(clusters)).nunique()),
           "mae": pm["mae"], "rmse": pm["rmse"], "bias": pm["bias"], "mean_actual": pm["mean_actual"],
           "mean_pred": pm["mean_pred"], "corr": pm["corr"], "crps": dm["crps"], "log_score": dm["log_score"],
           "cover_50": dm["cover_50"], "cover_80": dm["cover_80"], "cover_90": dm["cover_90"],
           "range_cover_80": dm["range_cover_80"], "pit_var_ratio": dm["pit_var_ratio"],
           "pit_deciles": dm["pit_deciles"], "thresholds": dm.get("thresholds", {})}
    if out["thresholds"]:
        out["mean_threshold_brier"] = float(np.mean([v["brier"] for v in out["thresholds"].values()]))
    return out


def paired(loss_a, loss_b, clusters, n_boot: int = 1000) -> dict:
    r = metrics.cluster_bootstrap_diff(loss_a, loss_b, clusters, n_boot=n_boot)
    return {"mean_diff": r["mean_diff"], "ci95": r["ci95"], "n_obs": r["n_obs"], "n_clusters": r["n_clusters"]}


def rung_rps(pge: np.ndarray, y, rungs) -> np.ndarray:
    """Per-row sum over rungs of (P(X>=k) - 1{y>=k})^2 — CRPS restricted to a common rung set."""
    y = np.asarray(y, float)
    O = (y[:, None] >= np.asarray(rungs)[None, :]).astype(float)
    return ((pge - O) ** 2).sum(axis=1)


def dist_prob_ge_matrix(dist: dists.DistBatch, rungs) -> np.ndarray:
    return np.column_stack([dist.prob_ge(k) for k in rungs])


# --------------------------------------------------------------------------- distributions
def binomial_mixture(n_dist: dists.DistBatch, p, phi: float | None = None, K: int | None = None) -> dists.DistBatch:
    """X | N ~ Binomial(N, p) (or beta-binomial with overdispersion phi = 1/(a+b)), N ~ n_dist.

    p is per row; the result has mean E[N]*p. Used for strikeouts given batters faced and for
    hitter counting stats given plate appearances."""
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    N = n_dist.support
    PN = n_dist.P
    nmax = int(N.max())
    K = K or nmax + 1
    x = np.arange(K)
    out = np.zeros((len(p), K))
    for j, n in enumerate(N):
        w = PN[:, j]
        if n < 0 or not (w > 1e-10).any():
            continue
        if phi is None or phi <= 0:
            pm = stats.binom.pmf(x[None, :], n, p[:, None])
        else:
            ab = 1.0 / phi - 1.0 if phi < 1 else 1e-3
            a = p * ab
            b = (1 - p) * ab
            pm = stats.betabinom.pmf(x[None, :], n, a[:, None], b[:, None])
        out += w[:, None] * pm
    return dists.DistBatch(0, out)


def multinomial_bases_mixture(n_dist: dists.DistBatch, probs: np.ndarray, K: int = 20) -> dists.DistBatch:
    """Total bases: each PA yields 0/1/2/3/4 bases with per-row probs (n x 5); N ~ n_dist.
    Exact convolution of the per-PA distribution, mixed over N."""
    probs = np.asarray(probs, float)
    probs = probs / probs.sum(axis=1, keepdims=True)
    n = len(probs)
    one = np.zeros((n, K))
    one[:, :5] = probs
    cur = np.zeros((n, K))
    cur[:, 0] = 1.0
    out = np.zeros((n, K))
    N = n_dist.support
    PN = n_dist.P
    maxn = int(N.max())
    # FFT-free repeated convolution (K small)
    for k in range(0, maxn + 1):
        if k > 0:
            nxt = np.zeros_like(cur)
            for b in range(5):
                nxt[:, b:] += cur[:, :K - b] * one[:, b:b + 1]
            cur = nxt
        j = np.where(N == k)[0]
        if len(j):
            out += PN[:, j[0]][:, None] * cur
    return dists.DistBatch(0, out)


def choose_dist(y_dev, mu_dev, candidates: dict, clusters_dev=None) -> tuple[str, dict]:
    """candidates: name -> callable(mu, idx_mask)->DistBatch evaluated on dev rows. Returns
    (best name, {name: dev crps})."""
    res = {}
    for name, fn in candidates.items():
        d = fn()
        res[name] = float(np.mean(d.crps(y_dev)))
    best = min(res, key=res.get)
    return best, res


def fit_family_dist(kind: str, y, mu, K: int, lo: int = 0):
    if kind in ("poisson",):
        return {"kind": "poisson", "K": K}
    if kind == "nb":
        return {"kind": "nb", "alpha": dists.fit_nb_alpha(y, mu), "K": K}
    if kind == "nb_binned":
        p = models.fit_dist_params("nb_binned", y, mu)
        p["K"] = K
        return p
    if kind == "normal_binned":
        return models.fit_dist_params("normal_binned", y, mu, K=K, lo=lo, floor_at=lo)
    if kind == "empirical_resid":
        return models.fit_dist_params("empirical_resid", y, mu, K=K, lo=lo)
    if kind == "empirical_ratio":
        return models.fit_dist_params("empirical_ratio", y, mu, K=K, lo=lo)
    raise ValueError(kind)


def make_family_dist(params: dict, mu) -> dists.DistBatch:
    mu = np.asarray(mu, float)
    if params["kind"] == "poisson":
        return dists.poisson(mu, params["K"])
    if params["kind"] == "nb":
        return dists.negbin(mu, params["alpha"], params["K"])
    return models.make_dist(params, mu)
