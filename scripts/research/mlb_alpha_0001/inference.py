#!/usr/bin/env python3
"""MLB-ALPHA-0001: clustered inference for net executable ROI.

WHY THIS MODULE EXISTS (maintainer finding #3). The first pass computed
`2 * min(P(ROI* <= 0), P(ROI* >= 0))` over an ORDINARY (unshifted)
cluster bootstrap and labelled it a p-value. That quantity never imposes
the null: it describes how far the sampling distribution AROUND THE
OBSERVED ESTIMATE sits from zero, so it is a CI-inversion heuristic, not
a hypothesis test, and it is anti-conservative when the estimator is
skewed -- which a 90-99c win/loss ratio estimator badly is. It is
withdrawn. Nothing here was chosen to preserve prior significance: both
tests below are standard, were fixed before rerunning, and the primary
test is the one reported and FDR-adjusted regardless of outcome.

THE ESTIMATOR. Per game cluster g we have net executable P/L `net_g` and
cash actually deployed `cash_g`. The statistic is the ratio

    ROI = sum_g net_g / sum_g cash_g

which is exactly the no-intercept least-squares slope of net on cash.
H0: expected net executable P/L (equivalently ROI) = 0.

PRIMARY TEST -- null-centered (recentered) game-cluster bootstrap:
  1. ROI_hat = sum(net) / sum(cash)
  2. impose the null by recentering each CLUSTER:
         net0_g = net_g - ROI_hat * cash_g      (so sum(net0) == 0)
     This preserves cluster sizes, within-cluster correlation, the
     cash/exposure structure and the heteroskedasticity, while making
     the resampling population satisfy H0 exactly.
  3. resample GAMES (never rows) with replacement, B times, from the
     recentered population; ROI*_b = sum(net0*) / sum(cash*)
  4. p = (1 + #{ |ROI*_b| >= |ROI_hat| }) / (B + 1)
     The +1 correction keeps p strictly positive and unbiased at the
     boundary (Davison & Hinkley); a p of 1/(B+1) means "smaller than
     this design can resolve", never "zero".

SECONDARY / ROBUSTNESS -- restricted wild cluster bootstrap (Cameron,
Gelbach & Miller): under H0 the restricted fit is identically zero, so
the restricted residual of cluster g is net_g itself; draw Rademacher
weights v_g in {-1,+1} per GAME and form
    ROI*_b = sum_g v_g net_g / sum_g cash_g.
This is the recommended small-/moderate-cluster-count test and does not
rely on resampling clusters at all, so it fails differently from the
primary test. Both are reported for every cell. Disagreement between
them is itself reported, never silently resolved.

The percentile cluster-bootstrap CI is RETAINED but is only ever labelled
a confidence interval -- never a test.
"""

import numpy as np

DEFAULT_B = 10000


def _arrays(net_by_game, cash_by_game):
    games = list(net_by_game)
    net = np.array([net_by_game[g] for g in games], dtype=float)
    cash = np.array([cash_by_game[g] for g in games], dtype=float)
    return net, cash


def clustered_roi_inference(net_by_game, cash_by_game, rng, B=DEFAULT_B):
    """Returns point estimate, percentile CI (descriptive) and TWO valid
    null-centered p-values. Never returns a sign-crossing 'p-value'."""
    net, cash = _arrays(net_by_game, cash_by_game)
    n = len(net)
    total_cash = cash.sum()
    if n == 0 or total_cash <= 0:
        return None
    roi_hat = net.sum() / total_cash

    # --- descriptive percentile CI (ordinary cluster bootstrap) ---
    idx = rng.integers(0, n, size=(B, n))
    cash_s = cash[idx].sum(axis=1)
    net_s = net[idx].sum(axis=1)
    rois = np.where(cash_s > 0, net_s / np.maximum(cash_s, 1e-12), np.nan)
    ci_lo, ci_hi = np.nanpercentile(rois, [5, 95])

    # --- PRIMARY: null-centered cluster bootstrap ---
    net0 = net - roi_hat * cash          # sum(net0) == 0 by construction
    idx0 = rng.integers(0, n, size=(B, n))
    cash0_s = cash[idx0].sum(axis=1)
    net0_s = net0[idx0].sum(axis=1)
    roi_null = np.where(cash0_s > 0, net0_s / np.maximum(cash0_s, 1e-12), np.nan)
    exceed = np.nansum(np.abs(roi_null) >= abs(roi_hat))
    p_null = (1.0 + float(exceed)) / (B + 1.0)

    # --- SECONDARY: restricted wild cluster bootstrap (Rademacher) ---
    v = rng.choice(np.array([-1.0, 1.0]), size=(B, n))
    roi_wild = (v * net).sum(axis=1) / total_cash
    exceed_w = float(np.sum(np.abs(roi_wild) >= abs(roi_hat)))
    p_wild = (1.0 + exceed_w) / (B + 1.0)

    return {
        "netROIClustered": round(float(roi_hat), 6),
        "ci90": [round(float(ci_lo), 6), round(float(ci_hi), 6)],
        "pNullClusterBootstrap": round(p_null, 6),
        "pWildClusterBootstrap": round(p_wild, 6),
        "pPrimary": round(p_null, 6),
        "testsAgree": bool((p_null < 0.05) == (p_wild < 0.05)),
        "clusters": int(n),
        "bootstrapIterations": int(B),
        "method": "null_centered_game_cluster_bootstrap (primary) + restricted_wild_cluster_bootstrap (secondary)",
    }


def bh_fdr(results, p_key="pPrimary", q=0.10, flag="fdrSurvivor"):
    """Benjamini-Hochberg over every cell that carried a valid test."""
    tested = [r for r in results if r.get(p_key) is not None]
    tested.sort(key=lambda r: r[p_key])
    m = len(tested)
    cutoff = -1
    for i, r in enumerate(tested):
        if r[p_key] <= q * (i + 1) / m:
            cutoff = i
    for i, r in enumerate(tested):
        r[flag] = i <= cutoff
    return m, sum(1 for r in tested if r[flag])
