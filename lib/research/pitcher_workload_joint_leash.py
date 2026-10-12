#!/usr/bin/env python3
"""
lib/research/pitcher_workload_joint_leash.py
============================================
RESEARCH VARIANT of the frozen W1-D challenger ``mlb_pitcher_workload_joint_v1``
(lib/research/pitcher_workload_joint.py, which this module imports and never
modifies). Version: ``mlb_pitcher_workload_joint_leash_v1``. RESEARCH ONLY: not
imported by the betting path, the app export, the champion or the frozen
challenger. Preregistration: docs/research/PITCHER_LONG_LEASH_PREREG.md.

Why it exists (docs/research/PITCHER_LONG_LEASH_DIAGNOSIS.md): the frozen
challenger under-projects batters faced (and so strikeouts) for long-leash
starters and over-projects them for short-leash starters. Two structural causes
were found on the development seasons (2022-2024), where they replicate:

  1. BF | outs ignores the pitch-count leash. E[BF - outs | outs] is ONE league
     curve. But the hook is pitch-count driven and non-outs consume pitches, so at
     a given outs count a long-leash starter has carried more baserunners than a
     short-leash one (who must have been efficient to get that deep). The league
     curve averages over leash, so it is too low for long leashes and too high for
     short ones.
  2. The kernel outs mixture weights every development start equally, so where
     the density of fitted E[outs] is steep (the tails) the neighbours on the dense
     side dominate and E[outs] is pulled toward the centre.

The module implements the candidate corrections tried on development data (fit
2022-23, validate 2024; data/research/pitcher_long_leash/development_2024.json):

  * ``nonout_mode = "leash_gamma"`` (B1): E[BF - outs | outs, L] = c[o] *
    (r0 / league)^alpha * exp(gamma * L), where L is the frozen design's own
    pitch-count leash term (shrunk recency-weighted prior pitches - league) / 10,
    clamped to [-LEASH_CLAMP, LEASH_CLAMP], and c is re-estimated jointly with gamma
    (ratio estimator, same +-1 pooling and pseudo-count as v1).
  * ``nonout_mode = "leash_gamma_by_outs"`` (B2): the same with gamma(o) =
    leash_gamma + leash_gamma_slope * (o - 18) / 6, because the development
    residuals show the leash effect shrinking as outs rise.
  * ``kernel_mode = "bin_normalized"`` (A): every E[outs] grid bin's empirical outs
    distribution gets the kernel weight alone (not weight x bin count); bins with
    fewer than ``kernel_min_bin`` starts are skipped.

THE PREREGISTERED VARIANT is B2 alone (nonout_mode="leash_gamma_by_outs",
kernel_mode="frozen"): it had the best 2024 BF log loss, and A did not improve
2024 outs log loss, so A was not adopted (it stays here, off, as a recorded
development negative). With kernel_mode="frozen" the outs distribution is the
frozen challenger's exactly; only BF | outs (and K | BF through E[BF]) changes.

The duration mean model (``mean_coef``), the development outs table, the league
constants and every feature are the frozen challenger's. Inputs remain the
sports-only FEATURE_KEYS of the frozen module. Pure: no I/O, no clock, no network.
"""

from __future__ import annotations

import math

from lib.research import pitcher_workload_joint as J

MODEL_VERSION = "mlb_pitcher_workload_joint_leash_v1"
LEASH_CLAMP = 3.0
FEATURE_KEYS = J.FEATURE_KEYS

VARIANT_DEFAULTS = {
    "nonout_mode": "frozen",        # "frozen" | "leash_gamma" | "leash_gamma_by_outs"
    "kernel_mode": "frozen",        # "frozen" | "bin_normalized"
    "leash_gamma": 0.0,             # leash effect at outs = LEASH_PIVOT_OUTS
    "leash_gamma_slope": 0.0,       # change of the leash effect per LEASH_SLOPE_OUTS outs ("leash_gamma_by_outs")
    "kernel_min_bin": 5,
    "nonout_curve_leash": None,     # c_gamma[o], o = 0..27 (fitted with leash_gamma)
}

GAMMA_GRID = (0.0, 0.025, 0.05, 0.075, 0.1, 0.125, 0.15, 0.2, 0.25, 0.3)
GAMMA_SLOPE_GRID = (-0.15, -0.1, -0.075, -0.05, -0.025, 0.0, 0.025, 0.05)
LEASH_PIVOT_OUTS = 18
LEASH_SLOPE_OUTS = 6.0


def gamma_at(o, params):
    """Leash coefficient at outs o: constant ("leash_gamma") or linear in outs ("leash_gamma_by_outs")."""
    g = params["leash_gamma"]
    if params["nonout_mode"] == "leash_gamma_by_outs":
        g += params["leash_gamma_slope"] * (o - LEASH_PIVOT_OUTS) / LEASH_SLOPE_OUTS
    return g


def _p(params):
    return {**J.DEFAULT_PARAMS, **VARIANT_DEFAULTS, **(params or {})}


def leash_dev(feat, params):
    """The frozen design's pitch-count leash term (design()[2]), clamped."""
    p = params
    pit = J._shrunk(feat, "w_pitches", p["league_pitches"], p["k_pitch_prior"])
    return max(-LEASH_CLAMP, min(LEASH_CLAMP, (pit - p["league_pitches"]) / 10.0))


def outs_distribution(mu, params):
    if params["kernel_mode"] != "bin_normalized" or not params.get("outs_table"):
        return J.outs_distribution(mu, params)
    h = params["outs_kernel_h"]
    probs = [0.0] * (J.MAX_OUTS + 1)
    tot = 0.0
    for gi, row in enumerate(params["outs_table"]["counts"]):
        n = sum(row)
        if n < params["kernel_min_bin"]:
            continue
        m = J.OUTS_GRID_LO + gi * J.OUTS_GRID_STEP
        z = (m - mu) / h
        if abs(z) > 4.0:
            continue
        w = math.exp(-0.5 * z * z)
        for o, c in enumerate(row):
            if c:
                probs[o] += w * c / n
        tot += w
    if tot <= 0:
        return J.outs_distribution(mu, params)
    return [x / tot for x in probs]


def nonout_mean(o, r0, lz, params):
    if params["nonout_mode"] not in LEASH_MODES or not params.get("nonout_curve_leash"):
        return J.nonout_mean(o, r0, params)
    curve = params["nonout_curve_leash"]
    base = curve[min(o, len(curve) - 1)]
    return max(0.0, base * (r0 / params["league_nonout_per_out"]) ** params["pitcher_alpha_r"]
               * math.exp(gamma_at(o, params) * lz))


def nonout_pmf(o, r0, lz, params, upto):
    lam = nonout_mean(o, r0, lz, params)
    rho = params["nonout_var_ratio"]
    if lam <= 1e-9:
        return [1.0] + [0.0] * upto
    if rho >= 0.999:
        return J._negbin(lam, 1e6, upto)
    n = max(1, int(round(lam / (1.0 - rho))))
    pr = min(0.999, lam / n)
    out = J._binom(n, pr, upto)
    s = sum(out)
    return [x / s for x in out] if s > 0 else [1.0] + [0.0] * upto


def project(feat, params=None):
    """outs -> BF | outs, leash -> K | BF, as the frozen v1 except the two stages above."""
    params = _p(params)
    mu = J.expected_outs(feat, params)
    p_outs = outs_distribution(mu, params)
    k0, r0 = J.rates(feat, params)
    lz = leash_dev(feat, params)
    bf = [0.0] * (J.MAX_BF + 1)
    for o, po in enumerate(p_outs):
        if po <= 0:
            continue
        for x, px in enumerate(nonout_pmf(o, r0, lz, params, J.MAX_BF - o)):
            bf[o + x] += po * px
    s = sum(bf)
    bf = [x / s for x in bf]
    ebf = sum(b * p for b, p in enumerate(bf))
    ks = [0.0] * (J.MAX_BF + 1)
    for b, pb in enumerate(bf):
        if b == 0:
            ks[0] += pb
            continue
        if pb <= 1e-12:
            continue
        qk = max(0.01, min(0.7, k0 * J._resid_mult(params["k_resid_slope"], b - ebf)))
        for k, pk in enumerate(J._betabinom(b, qk, params["phi_k"])):
            ks[k] += pb * pk
    s = sum(ks)
    ks = [x / s for x in ks]
    return {
        "model_version": MODEL_VERSION,
        "expected_outs_latent": mu, "leash_dev": lz,
        "k_rate": k0, "nonout_per_out": r0,
        "bf_pmf": bf, "outs_pmf": p_outs, "k_pmf": ks,
        "expected_batters_faced": ebf,
        "expected_outs": sum(o * p for o, p in enumerate(p_outs)),
        "expected_strikeouts": sum(k * p for k, p in enumerate(ks)),
    }


# ----------------------------------------------------------------------------- fit (development rows only)

LEASH_MODES = ("leash_gamma", "leash_gamma_by_outs")


def _leash_curve(rows, lz, params, lg):
    """Ratio estimator of c_gamma[o] = E[X] / E[exp(gamma L)] per outs value, pooled +-1 with the
    frozen v1's 20-start league pseudo-count."""
    sx = [0.0] * (J.MAX_OUTS + 1)
    sm = [0.0] * (J.MAX_OUTS + 1)
    cn = [0] * (J.MAX_OUTS + 1)
    for r, z in zip(rows, lz):
        o = min(J.MAX_OUTS, r["outs"])
        sx[o] += max(0, r["bf"] - r["outs"])
        sm[o] += math.exp(gamma_at(o, params) * z)
        cn[o] += 1
    curve = []
    for o in range(J.MAX_OUTS + 1):
        lo_, hi_ = max(0, o - 1), min(J.MAX_OUTS, o + 1)
        curve.append(round((sum(sx[lo_:hi_ + 1]) + 20 * lg * o) / (sum(sm[lo_:hi_ + 1]) + 20), 4))
    return curve


def fit(rows, base_params, *, nonout_mode="leash_gamma", kernel_mode="bin_normalized", kernel_min_bin=5):
    """Fit the variant's stages on DEVELOPMENT rows only, starting from a fitted frozen-v1 params dict
    (base_params: mean_coef, outs_table, league constants stay as they are).

    rows: [{"feat", "outs", "bf", "k"}] (the frozen v1 training rows). Refitted, with the frozen v1's
    own grids and two coordinate sweeps: the BF|outs block (leash_gamma + its curve, k_nonout_prior,
    opp_alpha_o, pitcher_alpha_r, nonout_var_ratio) by non-out log likelihood given actual outs, then
    the K|BF block (k_bf_prior, opp_alpha_k, k_resid_slope, phi_k) by K log likelihood given actual BF
    with E[BF] from the variant's own outs -> BF mixture."""
    params = _p(base_params)
    params.update({"nonout_mode": nonout_mode, "kernel_mode": kernel_mode, "kernel_min_bin": kernel_min_bin})
    lg = params["league_nonout_per_out"]
    lz = [leash_dev(r["feat"], params) for r in rows]

    def set_gamma(pp, name, g):
        pp[name] = g
        pp["nonout_curve_leash"] = _leash_curve(rows, lz, pp, lg) if nonout_mode in LEASH_MODES else None

    def r_ll(pp):
        tot = 0.0
        for r, z in zip(rows, lz):
            _, r0 = J.rates(r["feat"], pp)
            x = max(0, r["bf"] - r["outs"])
            pm = nonout_pmf(min(J.MAX_OUTS, r["outs"]), r0, z, pp, x)
            tot += math.log(max(1e-12, pm[x]))
        return tot

    ebfs = []

    def k_ll(pp):
        tot = 0.0
        for r, ebf in zip(rows, ebfs):
            k0, _ = J.rates(r["feat"], pp)
            q = max(0.01, min(0.7, k0 * J._resid_mult(pp["k_resid_slope"], r["bf"] - ebf)))
            tot += J._lbb(r["k"], r["bf"], q, pp["phi_k"])
        return tot

    grids_r = [("k_nonout_prior", (60.0, 120.0, 250.0, 500.0)), ("opp_alpha_o", (0.0, 0.5, 1.0, 1.5)),
               ("pitcher_alpha_r", (0.5, 0.75, 1.0, 1.25)), ("nonout_var_ratio", (0.5, 0.6, 0.7, 0.8, 0.9, 1.0))]
    if nonout_mode in LEASH_MODES:
        grids_r = [("leash_gamma", GAMMA_GRID)] + grids_r
    if nonout_mode == "leash_gamma_by_outs":
        grids_r = [grids_r[0], ("leash_gamma_slope", GAMMA_SLOPE_GRID)] + grids_r[1:]
    set_gamma(params, "leash_gamma", 0.0)
    set_gamma(params, "leash_gamma_slope", 0.0)
    grids_k = (("k_bf_prior", (60.0, 100.0, 150.0, 250.0, 400.0)), ("opp_alpha_k", (0.0, 0.5, 0.75, 1.0, 1.25)),
               ("k_resid_slope", (-0.02, -0.01, 0.0, 0.01, 0.02, 0.03, 0.04)), ("phi_k", (30.0, 60.0, 100.0, 200.0, 1000.0)))
    for _sweep in range(2):
        for name, grid in grids_r:
            best = None
            for v in grid:
                pp = dict(params)
                if name in ("leash_gamma", "leash_gamma_slope"):
                    set_gamma(pp, name, v)
                else:
                    pp[name] = v
                ll = r_ll(pp)
                if best is None or ll > best[0]:
                    best = (ll, v)
            if name in ("leash_gamma", "leash_gamma_slope"):
                set_gamma(params, name, best[1])
            else:
                params[name] = best[1]
    for r, z in zip(rows, lz):
        mu = J.expected_outs(r["feat"], params)
        _, r0 = J.rates(r["feat"], params)
        po = outs_distribution(mu, params)
        ebfs.append(sum(p_ * (o + nonout_mean(o, r0, z, params)) for o, p_ in enumerate(po)))
    for _sweep in range(2):
        for name, grid in grids_k:
            best = None
            for v in grid:
                ll = k_ll({**params, name: v})
                if best is None or ll > best[0]:
                    best = (ll, v)
            params[name] = best[1]
    params["model_version"] = MODEL_VERSION
    params["variant_fit"] = {"n_rows": len(rows), "nonout_mode": nonout_mode, "kernel_mode": kernel_mode,
                             "kernel_min_bin": kernel_min_bin, "base_model_version": J.MODEL_VERSION}
    return params
