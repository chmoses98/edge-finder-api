#!/usr/bin/env python3
"""
lib/research/pitcher_prop_projection.py
=======================================
RESEARCH projection for starting-pitcher Kalshi props: pitcher_outs (KXMLBOUTS) and
pitcher_strikeouts (KXMLBKS). Both are "N+" contracts (YES iff stat >= N).

WHY THIS EXISTS (MLB postseason P0, 2026-10-07)
-----------------------------------------------
lib/research/pitcher_workload_projection.py (the engine production discovery prices
these families with) uses ONE constant per-out survival probability p = T/(T+1).
That makes outs geometric: a 6-inning target has sd ~= mean (~18 outs), where real
starter outs have sd ~4.3 (2022-2025 box scores). Its K tail then takes
Binomial(round(E[BF]), k) at a single batters-faced point. MLB-RSCH-0022/0032 measured
the result: K Brier 0.203 vs market 0.107, outs calibration slope -0.38. Its inputs
(avgIPperStart, kPct, opponent context) are sound; its distribution is not.

THE MODEL -- one workload distribution, everything derived from it
------------------------------------------------------------------
For a starter with prior starts h (strictly before the game date):

  mu_outs   = shrink(recency-weighted mean of h.outs -> LEAGUE_OUTS, k_outs starts)
              + postseason_shift (postseason games only; fitted, shrunk, see params)
  P(outs=o) = empirical residual distribution of (outs - mu_outs) in mu-buckets,
              fitted on development seasons only (params["outs_residuals"])
  BF | outs = outs + Poisson(r * outs),  r = shrunk non-out PA per out of h
  K  | BF   = BetaBinomial(BF, p_k, phi),
              p_k = shrink(h.K / h.BF -> LEAGUE_K_RATE, k_bf BF) x opponent factor
              (opponent team K/PA over its prior games, shrunk, ^ alpha)

P(outs >= n) and P(K >= n) are both sums over the SAME outs distribution, so the two
families can never carry incompatible workload assumptions (a short-leash postseason
shift lowers both). Every constant that is not a sample statistic lives in the params
file written by scripts/research/mlb_pitcher_prop_calibration.py, fitted on
development seasons and evaluated chronologically on held-out seasons and on real
Kalshi prices; nothing is fitted on the evaluation data.

Pure: no I/O, no clock, no network. RESEARCH ONLY -- never imported by the betting
path (build_market_ledger / risk_gate / write_pending_bets / protect_slate).
"""

from __future__ import annotations

import math

MAX_OUTS = 27
MAX_EXTRA_PA = 25
RECENCY_DECAY = 0.85
MAX_HISTORY_STARTS = 12

#: Defaults used only when no params file is supplied (tests); production always
#: loads data/edgelab/analytics/mlb_pitcher_prop_model_params.json.
DEFAULT_PARAMS = {
    "league_outs": 15.5,
    "k_outs": 4.0,
    "league_k_rate": 0.222,
    "k_bf": 220.0,
    "league_nonout_per_out": 0.42,
    "k_nonout_outs": 120.0,
    "phi": 60.0,
    "opp_alpha": 0.7,
    "opp_k_pa": 1500.0,
    "postseason_shift_outs": 0.0,
    "postseason_shift_n": 0,
    "outs_residuals": None,   # {"edges": [...], "buckets": [[residual ints...], ...]}
}


def _w(i):
    return RECENCY_DECAY ** i


def history_summary(starts):
    """starts: list of dicts {outs, bf, k, nonout?, date} OLDEST FIRST, all strictly
    before the projected game. Returns recency-weighted sample statistics."""
    recent = [s for s in starts if s.get("outs") is not None][-MAX_HISTORY_STARTS:]
    recent = list(reversed(recent))  # newest first for weights
    wsum = sum(_w(i) for i in range(len(recent)))
    w_outs = sum(_w(i) * s["outs"] for i, s in enumerate(recent))
    # K rate only from starts that carry BF (postseason starts known from settlements have
    # outs/K but no BF: they inform workload, never the per-PA strikeout rate)
    bf = sum(s["bf"] for s in recent if s.get("bf") and s.get("k") is not None)
    k = sum(s["k"] for s in recent if s.get("bf") and s.get("k") is not None)
    outs = sum(s["outs"] for s in recent)
    outs_with_bf = sum(s["outs"] for s in recent if s.get("bf") and s.get("k") is not None)
    return {"n_starts": len(recent), "wsum": wsum, "w_outs": w_outs, "bf": bf, "k": k, "outs": outs_with_bf,
            "nonout": max(0, bf - outs_with_bf), "last_outs": recent[0]["outs"] if recent else None,
            "last_date": recent[0].get("date") if recent else None}


def expected_outs(summary, params, postseason=False):
    p = params
    num = summary["w_outs"] + p["k_outs"] * p["league_outs"]
    den = summary["wsum"] + p["k_outs"]
    mu = num / den
    shift = p.get("postseason_shift_outs") or 0.0
    if postseason and shift:
        mu += shift
    return max(1.0, min(26.0, mu))


def _bucket(edges, mu):
    for i, e in enumerate(edges):
        if mu < e:
            return i
    return len(edges)


def outs_distribution(mu, params):
    """{outs: prob} for outs in 0..27. Empirical residuals by mu-bucket when fitted;
    otherwise a discretized normal (sd 4.3) as the explicit unfitted fallback."""
    table = params.get("outs_residuals")
    probs = [0.0] * (MAX_OUTS + 1)
    if table:
        resid = table["buckets"][_bucket(table["edges"], mu)]
        w = 1.0 / len(resid)
        for e in resid:
            o = int(round(mu + e))
            probs[max(0, min(MAX_OUTS, o))] += w
    else:
        sd = 4.3
        for o in range(MAX_OUTS + 1):
            lo, hi = (o - 0.5 - mu) / sd, (o + 0.5 - mu) / sd
            probs[o] = 0.5 * (math.erf(hi / math.sqrt(2)) - math.erf(lo / math.sqrt(2)))
        s = sum(probs)
        probs = [x / s for x in probs]
    return probs


def _poisson(k, lam):
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1))


def _betabinom_pmf_all(n, p, phi):
    """P(K=k) for k=0..n under BetaBinomial(n, a=p*phi, b=(1-p)*phi)."""
    a, b = max(1e-6, p * phi), max(1e-6, (1 - p) * phi)
    lb = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    out = []
    for k in range(n + 1):
        lc = math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
        lbk = math.lgamma(k + a) + math.lgamma(n - k + b) - math.lgamma(n + a + b)
        out.append(math.exp(lc + lbk - lb))
    return out


def k_rate(summary, params, opp_k_rate=None, opp_pa=0):
    p = params
    base = (summary["k"] + p["k_bf"] * p["league_k_rate"]) / (summary["bf"] + p["k_bf"])
    factor = 1.0
    if opp_k_rate is not None and opp_pa:
        opp = (opp_k_rate * opp_pa + p["league_k_rate"] * p["opp_k_pa"]) / (opp_pa + p["opp_k_pa"])
        factor = (opp / p["league_k_rate"]) ** p["opp_alpha"]
    return max(0.02, min(0.6, base * factor)), factor


def nonout_rate(summary, params):
    p = params
    return (summary["nonout"] + p["k_nonout_outs"] * p["league_nonout_per_out"]) / (summary["outs"] + p["k_nonout_outs"])


def project(starts, params=None, *, postseason=False, opp_k_rate=None, opp_pa=0):
    """Full projection for one starter. Returns dict with the shared outs distribution,
    P(outs >= n), P(K >= n) closures' tables, expected values and drivers."""
    params = {**DEFAULT_PARAMS, **(params or {})}
    s = history_summary(starts)
    mu = expected_outs(s, params, postseason=postseason)
    p_outs = outs_distribution(mu, params)
    pk, opp_factor = k_rate(s, params, opp_k_rate, opp_pa)
    r = nonout_rate(s, params)
    phi = params["phi"]
    # P(BF = b) from the shared outs distribution, then K | BF once per distinct BF
    bf_dist = [0.0] * (MAX_OUTS + MAX_EXTRA_PA + 1)
    for o, po in enumerate(p_outs):
        if po <= 0:
            continue
        lam = r * o
        for x in range(MAX_EXTRA_PA + 1):
            px = _poisson(x, lam)
            if px < 1e-9:
                if x > lam:
                    break
                continue
            bf_dist[o + x] += po * px
    k_dist = [0.0] * (MAX_OUTS + MAX_EXTRA_PA + 1)
    bf_mean = 0.0
    for bf, w in enumerate(bf_dist):
        if w <= 0:
            continue
        bf_mean += w * bf
        for kk, pkk in enumerate(_betabinom_pmf_all(bf, pk, phi)):
            k_dist[kk] += w * pkk
    tot = sum(k_dist)
    k_dist = [x / tot for x in k_dist] if tot > 0 else k_dist
    outs_ge = [sum(p_outs[n:]) for n in range(MAX_OUTS + 2)]
    k_ge = [sum(k_dist[n:]) for n in range(len(k_dist) + 1)]
    exp_outs = sum(o * p for o, p in enumerate(p_outs))
    exp_k = sum(k * p for k, p in enumerate(k_dist))
    return {
        "history_starts": s["n_starts"],
        "mu_outs": mu,
        "postseason_shift_applied": (params.get("postseason_shift_outs") or 0.0) if postseason else 0.0,
        "expected_outs": exp_outs,
        "expected_batters_faced": bf_mean,
        "expected_strikeouts": exp_k,
        "k_rate": pk,
        "opponent_k_factor": opp_factor,
        "nonout_per_out": r,
        "outs_pmf": p_outs,
        "k_pmf": k_dist,
        "p_outs_ge": outs_ge,
        "p_k_ge": k_ge,
    }


def p_at_least(table, n):
    if n <= 0:
        return 1.0
    return table[n] if n < len(table) else 0.0


def quantile(pmf, q):
    c = 0.0
    for i, p in enumerate(pmf):
        c += p
        if c >= q:
            return i
    return len(pmf) - 1
