#!/usr/bin/env python3
"""
lib/research/pitcher_workload_joint.py
======================================
W1-D RESEARCH CHALLENGER (PURE_INDEPENDENT): sports-only, as-of starter
workload context and a JOINT batters-faced -> outs / strikeouts model.

Version: ``mlb_pitcher_workload_joint_v1``. RESEARCH ONLY: never imported by the
betting path, the app export, or the champion
(lib/research/pitcher_prop_projection.py, which is left byte-for-byte unchanged).

Inputs (all sports data, all strictly before the projected game's date):
  * the starter's previous <= 12 starts: outs, batters faced, strikeouts, pitch
    counts, dates (rest days), and season start count;
  * his club's previous starts this season (organisational hook / leash);
  * his bullpen's relief pitch counts over the previous three calendar days;
  * the opponent's season-to-date strikeouts and non-outs per plate appearance.
No odds, prices, settlements, market listings or market-derived values are
accepted anywhere in this module (see FEATURE_KEYS; the runtime mutation test
reruns the full pipeline on market-removed / market-randomized data).

Model (one distribution, every family derived from it: outs -> BF -> K):
  1. Duration: E[outs] = ridge-OLS workload model on the as-of features (pitch-count
     leash, recent outs, rest/return, season debut, bullpen load, opponent on-base);
     P(outs = o | E[outs]) = kernel-weighted empirical distribution of ACTUAL
     development-season outs among starts with similar fitted E[outs], which keeps the
     inning-boundary lumps of real hooks (15/18/21 outs).
  2. BF | outs = outs + X, E[X | outs] = development league curve of non-out PAs by
     outs recorded (short outings are short BECAUSE runners reached), scaled by the
     pitcher x opponent non-out rate; X ~ Binomial(N, E[X]/N) (under-dispersed,
     variance ratio fitted).
  3. K | BF ~ BetaBinomial(BF, p(BF), phi_k), p = shrunk K/BF x opponent K factor x a
     fitted multiplier on the BF residual (BF - E[BF]).
Outs, BF and K thresholds are tail sums of PMFs built from the same outs mixture, so
they are monotone and mutually coherent (a short hook lowers all three).
Pure: no I/O, no clock, no network.
"""

from __future__ import annotations

import bisect
import math
from collections import defaultdict
from datetime import date as _date

MODEL_VERSION = "mlb_pitcher_workload_joint_v1"
MAX_HISTORY_STARTS = 12
RECENCY_DECAY = 0.85
MAX_BF = 45
MAX_OUTS = 27

#: The ONLY feature names the model consumes. Every one is a sports statistic.
FEATURE_KEYS = (
    "n_hist", "season_starts", "days_rest", "w_bf", "w_outs", "w_pitches", "last_pitches", "last_bf",
    "max_pitches_last3", "pitches_per_bf", "k_rate_hist_bf", "k_rate_hist_k", "outs_per_bf_hist_bf",
    "outs_per_bf_hist_outs", "bullpen_pitches_3d",
    "bullpen_pitches_1d", "opp_k_rate", "opp_nonout_rate", "opp_pa",
)

DEFAULT_PARAMS = {
    "league_outs": 15.5, "league_pitches": 88.0, "league_k_rate": 0.22, "league_nonout_per_out": 0.41,
    "league_nonout_rate": 0.31, "k_bf_prior": 150.0, "k_nonout_prior": 120.0, "opp_k_pa": 1500.0,
    "opp_alpha_k": 1.0, "opp_alpha_o": 1.0, "k_pitch_prior": 3.0,
    "phi_k": 100.0,
    "mean_coef": None,  # {"names": [...], "beta": [...]} fitted on development seasons
    "outs_table": None,  # kernel table of development outs by fitted E[outs] (see outs_distribution)
    "outs_kernel_h": 0.5,
    "k_resid_slope": 0.0, "pitcher_alpha_r": 1.0, "nonout_var_ratio": 1.0,
    "nonout_curve": None,  # development mean of (BF - outs) for outs = 0..27
}


def _days(a, b):
    return (_date.fromisoformat(b) - _date.fromisoformat(a)).days


# ----------------------------------------------------------------------------- as-of index

class AsOfIndex:
    """Chronological index over team pitching games (see
    lib/research/pitcher_workload_sources.load_pitching_games). Every query takes a
    date and only returns games whose date is STRICTLY earlier."""

    def __init__(self, team_games):
        self.by_pid = defaultdict(list)
        self.by_team = defaultdict(list)
        for g in sorted(team_games, key=lambda x: (x["date"], x.get("start_utc") or "", str(x["pk"]))):
            self.by_team[g["team"]].append(g)
            st = g.get("starter")
            if st and st.get("outs") is not None:
                self.by_pid[str(st["pid"])].append(g)
        self._pid_dates = {p: [g["date"] for g in v] for p, v in self.by_pid.items()}
        self._team_dates = {t: [g["date"] for g in v] for t, v in self.by_team.items()}

    def prior_starts(self, pid, date, cutoff=None):
        lst = self.by_pid.get(str(pid), [])
        out = lst[:bisect.bisect_left(self._pid_dates.get(str(pid), []), date)]
        return out if cutoff is None else [g for g in out if (g.get("observed_at") or "") <= cutoff]

    def prior_team_games(self, team, date, cutoff=None):
        lst = self.by_team.get(team, [])
        out = lst[:bisect.bisect_left(self._team_dates.get(team, []), date)]
        return out if cutoff is None else [g for g in out if (g.get("observed_at") or "") <= cutoff]


def features(index, pid, team, opp, date, cutoff=None):
    """As-of sports feature dict for one projected start. Only games dated before `date`
    AND observable (observed_at, ISO UTC) at or before `cutoff` are used, so a suspended
    game resumed later is excluded until it was actually completed. Also returns the
    latest observed_at of any game used (the source cutoff)."""
    hist = index.prior_starts(pid, date, cutoff)
    recent = list(reversed(hist[-MAX_HISTORY_STARTS:]))  # newest first
    w = [RECENCY_DECAY ** i for i in range(len(recent))]
    ws = sum(w) or 1.0

    def wmean(key):
        vals = [(wi, g["starter"].get(key)) for wi, g in zip(w, recent) if g["starter"].get(key) is not None]
        s = sum(wi for wi, _ in vals)
        return sum(wi * v for wi, v in vals) / s if s else None

    season = date[:4]
    with_bf = [g["starter"] for g in recent if g["starter"].get("bf")]
    opp_prior = [g for g in index.prior_team_games(opp, date, cutoff) if g["season"] == season] if opp else []
    # opponent batting = the PA/K OTHER clubs' pitchers recorded against `opp`: stored on
    # the opposing club's team-game row; reconstruct from opp's own rows' mirror games
    opp_pa = opp_k = opp_outs = 0
    for g in opp_prior:
        m = g.get("_mirror")
        if m is not None:
            opp_pa += m["batting_pa"]
            opp_k += m["batting_k"]
            opp_outs += m["pitching_outs"]
    bull = [g for g in index.prior_team_games(team, date, cutoff) if 1 <= _days(g["date"], date) <= 3]
    used = list(recent) + opp_prior + bull
    feat = {
        "n_hist": len(recent),
        "season_starts": sum(1 for g in hist if g["season"] == season),
        "days_rest": _days(recent[0]["date"], date) if recent else None,
        "w_bf": wmean("bf"), "w_outs": wmean("outs"), "w_pitches": wmean("pitches"),
        "last_pitches": recent[0]["starter"].get("pitches") if recent else None,
        "last_bf": recent[0]["starter"].get("bf") if recent else None,
        "max_pitches_last3": max((g["starter"].get("pitches") or 0 for g in recent[:3]), default=None),
        "pitches_per_bf": (sum((g["starter"].get("pitches") or 0) for g in recent if g["starter"].get("bf") and g["starter"].get("pitches")),
                           sum(g["starter"]["bf"] for g in recent if g["starter"].get("bf") and g["starter"].get("pitches"))),
        "k_rate_hist_bf": sum(s["bf"] for s in with_bf),
        "k_rate_hist_k": sum(s.get("k") or 0 for s in with_bf),
        "outs_per_bf_hist_bf": sum(s["bf"] for s in with_bf),
        "outs_per_bf_hist_outs": sum(s["outs"] for s in with_bf),
        "bullpen_pitches_3d": sum(g.get("relief_pitches") or 0 for g in bull),
        "bullpen_pitches_1d": sum(g.get("relief_pitches") or 0 for g in bull if _days(g["date"], date) == 1),
        "opp_k_rate": (opp_k / opp_pa) if opp_pa else None,
        "opp_nonout_rate": (1.0 - opp_outs / opp_pa) if opp_pa else None,
        "opp_pa": opp_pa,
    }
    observed = max((g.get("observed_at") or "" for g in used), default=None) or None
    return feat, observed


def link_mirrors(team_games):
    """Attach to each team-game the opponent-side batting it faced: `_mirror` =
    {batting_pa, batting_k, pitching_outs} of the OTHER club's pitchers vs this club,
    i.e. this club's offence in that game. Mutates and returns team_games."""
    by_pk = defaultdict(dict)
    for g in team_games:
        by_pk[g["pk"]][g["side"]] = g
    for sides in by_pk.values():
        for side, g in sides.items():
            o = sides.get("home" if side == "away" else "away")
            if o is None:
                g["_mirror"] = None
                continue
            outs = (o["starter"]["outs"] if o.get("starter") else 0) + (o.get("relief_outs") or 0)
            g["_mirror"] = {"batting_pa": o["batting_pa"], "batting_k": o["batting_k"], "pitching_outs": outs}
    return team_games


# ----------------------------------------------------------------------------- design vector

# Frozen after the 2022-23 -> 2024 development ablation (docs/research/
# PITCHER_WORKLOAD_PREREG.md): team_hook_dev, early_season and few_hist were dropped
# (removing them changed 2024 outs log-loss by < 0.001).
DESIGN_NAMES = ("const", "outs_shr", "pitch_shr", "last_pitches_dev", "max3_dev", "ppb_dev", "rest_short",
                "rest_ext", "rest_return", "season_debut", "bullpen_3d", "opp_nonout_dev")


def _shrunk(feat, key, league, k):
    n = feat["n_hist"]
    if not n or feat[key] is None:
        return league
    wsum = sum(RECENCY_DECAY ** i for i in range(n))
    return (feat[key] * wsum + league * k) / (wsum + k)


def design(feat, params):
    """Numeric design vector for the duration (E[outs]) model. All centring
    constants are development-sample statistics stored in params."""
    p = params
    lo, lp = p["league_outs"], p["league_pitches"]
    kp = p["k_pitch_prior"]
    outs_shr = _shrunk(feat, "w_outs", lo, kp)
    pit_shr = _shrunk(feat, "w_pitches", lp, kp)
    lastp = feat["last_pitches"] if feat["last_pitches"] is not None else pit_shr
    max3 = feat["max_pitches_last3"] if feat["max_pitches_last3"] else pit_shr
    pp, pb = feat["pitches_per_bf"]
    ppb = (pp + 3.85 * 60.0) / (pb + 60.0)
    rest = feat["days_rest"]
    same_season_prior = feat["season_starts"] > 0
    opp_pa = feat["opp_pa"] or 0
    opp_no = ((feat["opp_nonout_rate"] or p["league_nonout_rate"]) * opp_pa + p["league_nonout_rate"] * p["opp_k_pa"]) / (opp_pa + p["opp_k_pa"])
    return [
        1.0,
        outs_shr - lo,
        (pit_shr - lp) / 10.0,
        (lastp - pit_shr) / 10.0,
        (max3 - pit_shr) / 10.0,
        (ppb - 3.85) * 10.0,
        1.0 if (rest is not None and rest <= 4 and same_season_prior) else 0.0,
        1.0 if (rest is not None and 8 <= rest <= 14 and same_season_prior) else 0.0,
        1.0 if (rest is not None and rest >= 15 and same_season_prior) else 0.0,
        0.0 if same_season_prior else 1.0,
        feat["bullpen_pitches_3d"] / 100.0,
        (opp_no - p["league_nonout_rate"]) * 100.0,
    ]


def expected_outs(feat, params):
    coef = params.get("mean_coef")
    x = design(feat, params)
    if not coef:
        return max(1.0, min(26.0, params["league_outs"] + x[1]))
    mu = sum(b * xi for b, xi in zip(coef["beta"], x))
    return max(1.0, min(26.0, mu))


# ----------------------------------------------------------------------------- distributions

def _bucket(edges, mu):
    return bisect.bisect_right(edges, mu)


OUTS_GRID_LO, OUTS_GRID_STEP, OUTS_GRID_N = 4.0, 0.25, 85   # E[outs] grid 4.0 .. 25.0


def outs_distribution(mu, params):
    """P(outs = o), o = 0..27, conditional on E[outs] = mu.

    Kernel-weighted empirical distribution of ACTUAL development-season outs among
    starts whose fitted E[outs] was near mu (Gaussian kernel, bandwidth
    params["outs_kernel_h"], on a 0.25-out grid of E[outs]). Using actual integer
    outs (not rounded residuals) preserves the inning-boundary lumps of real hooks
    (15, 18, 21 outs). The table is params["outs_table"] = {"counts": [[...28] x grid]}."""
    table = params.get("outs_table")
    probs = [0.0] * (MAX_OUTS + 1)
    if table:
        h = params["outs_kernel_h"]
        counts = table["counts"]
        tot = 0.0
        for gi, row in enumerate(counts):
            m = OUTS_GRID_LO + gi * OUTS_GRID_STEP
            z = (m - mu) / h
            if abs(z) > 4.0:
                continue
            w = math.exp(-0.5 * z * z)
            for o, c in enumerate(row):
                if c:
                    probs[o] += w * c
            tot += w * sum(row)
        if tot > 0:
            probs = [x / tot for x in probs]
            return probs
    sd = 4.3  # explicit unfitted fallback (tests only): discretized normal
    for o in range(MAX_OUTS + 1):
        lo, hi = (o - 0.5 - mu) / sd, (o + 0.5 - mu) / sd
        probs[o] = 0.5 * (math.erf(hi / math.sqrt(2)) - math.erf(lo / math.sqrt(2)))
    s = sum(probs)
    return [x / s for x in probs]


def _betabinom(n, p, phi):
    a, b = max(1e-6, p * phi), max(1e-6, (1 - p) * phi)
    lb = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    lgn = math.lgamma(n + 1)
    out = []
    for k in range(n + 1):
        lc = lgn - math.lgamma(k + 1) - math.lgamma(n - k + 1)
        out.append(math.exp(lc + math.lgamma(k + a) + math.lgamma(n - k + b) - math.lgamma(n + a + b) - lb))
    return out


def _negbin(mean, size, upto):
    """P(X = x), x = 0..upto, NegBin with given mean and size (Poisson as size -> inf)."""
    if mean <= 0:
        return [1.0] + [0.0] * upto
    pr = size / (size + mean)
    out = []
    lg = math.lgamma(size)
    for x in range(upto + 1):
        out.append(math.exp(math.lgamma(x + size) - lg - math.lgamma(x + 1) + size * math.log(pr) + x * math.log1p(-pr)))
    return out


def _binom(n, p, upto):
    out = []
    for x in range(upto + 1):
        if x > n:
            out.append(0.0)
            continue
        out.append(math.exp(math.lgamma(n + 1) - math.lgamma(x + 1) - math.lgamma(n - x + 1)
                            + (x * math.log(p) if x else 0.0) + ((n - x) * math.log1p(-p) if n - x else 0.0)))
    return out


def nonout_mean(o, r0, params):
    """E[BF - outs | outs = o]: the development league curve of non-out PAs by outs
    recorded (short outings are short BECAUSE runners reached), scaled by the
    pitcher x opponent non-out rate relative to league."""
    curve = params.get("nonout_curve")
    base = curve[min(o, len(curve) - 1)] if curve else params["league_nonout_per_out"] * o
    return max(0.0, base * (r0 / params["league_nonout_per_out"]) ** params["pitcher_alpha_r"])


def nonout_pmf(o, r0, params, upto):
    """P(BF - outs = x | outs = o). Under-dispersed relative to Poisson with
    variance ratio params["nonout_var_ratio"] (< 1: Binomial(N, lam/N))."""
    lam = nonout_mean(o, r0, params)
    rho = params["nonout_var_ratio"]
    if lam <= 1e-9:
        return [1.0] + [0.0] * upto
    if rho >= 0.999:
        return _negbin(lam, 1e6, upto)
    n = max(1, int(round(lam / (1.0 - rho))))
    pr = min(0.999, lam / n)
    out = _binom(n, pr, upto)
    s = sum(out)
    return [x / s for x in out] if s > 0 else [1.0] + [0.0] * upto


def rates(feat, params):
    """(k_rate per BF, non-outs per out) base rates for pitcher x opponent before the
    duration-residual multiplier."""
    p = params
    kr = (feat["k_rate_hist_k"] + p["k_bf_prior"] * p["league_k_rate"]) / (feat["k_rate_hist_bf"] + p["k_bf_prior"])
    hb, ho = feat["outs_per_bf_hist_bf"], feat["outs_per_bf_hist_outs"]
    r = (max(0, hb - ho) + p["k_nonout_prior"] * p["league_nonout_per_out"]) / (ho + p["k_nonout_prior"])
    opa = feat["opp_pa"] or 0
    if feat["opp_k_rate"] is not None and opa:
        ok = (feat["opp_k_rate"] * opa + p["league_k_rate"] * p["opp_k_pa"]) / (opa + p["opp_k_pa"])
        kr *= (ok / p["league_k_rate"]) ** p["opp_alpha_k"]
    if feat["opp_nonout_rate"] is not None and opa:
        on = (feat["opp_nonout_rate"] * opa + p["league_nonout_rate"] * p["opp_k_pa"]) / (opa + p["opp_k_pa"])
        # non-outs per out scale with the odds of reaching base
        lg = p["league_nonout_rate"]
        r *= ((on / (1 - on)) / (lg / (1 - lg))) ** p["opp_alpha_o"]
    return max(0.02, min(0.6, kr)), max(0.1, min(1.5, r))


def _resid_mult(slope, resid):
    # multiplicative rate adjustment for outing length relative to expectation,
    # bounded so extreme hooks cannot produce degenerate rates
    return math.exp(max(-0.6, min(0.6, slope * max(-12.0, min(12.0, resid)))))


def project(feat, params=None):
    """Joint projection: outs -> BF | outs -> K | BF. Returns PMFs and summaries."""
    params = {**DEFAULT_PARAMS, **(params or {})}
    mu = expected_outs(feat, params)
    p_outs = outs_distribution(mu, params)
    k0, r0 = rates(feat, params)
    bf = [0.0] * (MAX_BF + 1)
    for o, po in enumerate(p_outs):
        if po <= 0:
            continue
        extra = nonout_pmf(o, r0, params, MAX_BF - o)
        for x, px in enumerate(extra):
            bf[o + x] += po * px
    s = sum(bf)
    bf = [x / s for x in bf]
    # K | BF with the duration residual measured on the BF scale (E[BF] from the mixture)
    ebf = sum(b * p for b, p in enumerate(bf))
    ks = [0.0] * (MAX_BF + 1)
    for b, pb in enumerate(bf):
        if pb <= 1e-12 or b == 0:
            if b == 0:
                ks[0] += pb
            continue
        qk = max(0.01, min(0.7, k0 * _resid_mult(params["k_resid_slope"], b - ebf)))
        for k, pk in enumerate(_betabinom(b, qk, params["phi_k"])):
            ks[k] += pb * pk
    s = sum(ks)
    ks = [x / s for x in ks]
    return {
        "model_version": MODEL_VERSION,
        "expected_outs_latent": mu,
        "k_rate": k0, "nonout_per_out": r0,
        "bf_pmf": bf, "outs_pmf": p_outs, "k_pmf": ks,
        "expected_batters_faced": ebf,
        "expected_outs": sum(o * p for o, p in enumerate(p_outs)),
        "expected_strikeouts": sum(k * p for k, p in enumerate(ks)),
    }


def tail(pmf):
    """P(X >= n) for n = 0..len(pmf); nonincreasing by construction."""
    out = [0.0] * (len(pmf) + 1)
    acc = 0.0
    for n in range(len(pmf) - 1, -1, -1):
        acc += pmf[n]
        out[n] = min(1.0, acc)
    return out


def quantile(pmf, q):
    c = 0.0
    for i, p in enumerate(pmf):
        c += p
        if c >= q - 1e-12:
            return i
    return len(pmf) - 1


# ----------------------------------------------------------------------------- fitting (stdlib)

def _solve(a, b):
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        if abs(m[c][c]) < 1e-12:
            m[c][c] = 1e-12
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    return [m[i][n] / m[i][i] for i in range(n)]


def ols(xs, ys, ridge=1.0):
    k = len(xs[0])
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    for x, y in zip(xs, ys):
        for i in range(k):
            xi = x[i]
            if xi == 0.0:
                continue
            xty[i] += xi * y
            row = xtx[i]
            for j in range(k):
                row[j] += xi * x[j]
    for i in range(1, k):
        xtx[i][i] += ridge
    return _solve(xtx, xty)


def _lbb(k, n, p, phi):
    a, b = max(1e-6, p * phi), max(1e-6, (1 - p) * phi)
    return (math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
            + math.lgamma(k + a) + math.lgamma(n - k + b) - math.lgamma(n + a + b)
            - (math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)))


def fit(rows, *, league=None, ridge=1.0, kernel_h=0.5):
    """Fit every constant on development rows only. rows: [{"feat": {...}, "bf": int,
    "outs": int, "k": int}], each with n_hist >= 1 and observed BF. `league` carries
    league_* constants computed from the same development seasons."""
    params = dict(DEFAULT_PARAMS)
    params.update(league or {})
    xs = [design(r["feat"], params) for r in rows]
    ys = [float(r["outs"]) for r in rows]
    beta = ols(xs, ys, ridge=ridge)
    params["mean_coef"] = {"names": list(DESIGN_NAMES), "beta": [round(b, 6) for b in beta]}
    mus = [expected_outs(r["feat"], params) for r in rows]
    counts = [[0] * (MAX_OUTS + 1) for _ in range(OUTS_GRID_N)]
    for r, mu in zip(rows, mus):
        gi = int(round((min(max(mu, OUTS_GRID_LO), OUTS_GRID_LO + (OUTS_GRID_N - 1) * OUTS_GRID_STEP) - OUTS_GRID_LO) / OUTS_GRID_STEP))
        counts[gi][min(MAX_OUTS, r["outs"])] += 1
    params["outs_table"] = {"grid_lo": OUTS_GRID_LO, "grid_step": OUTS_GRID_STEP, "counts": counts}
    params["outs_kernel_h"] = kernel_h

    ebfs = []

    def k_ll(pp):
        tot = 0.0
        for r, ebf in zip(rows, ebfs):
            k0, _ = rates(r["feat"], pp)
            q = max(0.01, min(0.7, k0 * _resid_mult(pp["k_resid_slope"], r["bf"] - ebf)))
            tot += _lbb(r["k"], r["bf"], q, pp["phi_k"])
        return tot

    sums = [0.0] * (MAX_OUTS + 1)
    cnts = [0] * (MAX_OUTS + 1)
    for r in rows:
        o = min(MAX_OUTS, r["outs"])
        sums[o] += max(0, r["bf"] - r["outs"])
        cnts[o] += 1
    lg = params["league_nonout_per_out"]
    # sparse outs values borrow from neighbours (pooled +-1) and fall back to the league rate
    curve = []
    for o in range(MAX_OUTS + 1):
        lo_, hi_ = max(0, o - 1), min(MAX_OUTS, o + 1)
        n = sum(cnts[lo_:hi_ + 1])
        curve.append(round((sum(sums[lo_:hi_ + 1]) + 20 * lg * o) / (n + 20), 4) if (n + 20) else lg * o)
    params["nonout_curve"] = curve

    def r_ll(pp):
        tot = 0.0
        for r in rows:
            _, r0 = rates(r["feat"], pp)
            x = max(0, r["bf"] - r["outs"])
            pm = nonout_pmf(min(MAX_OUTS, r["outs"]), r0, pp, x)
            tot += math.log(max(1e-12, pm[x]))
        return tot

    grids_k = (("k_bf_prior", (60.0, 100.0, 150.0, 250.0, 400.0)), ("opp_alpha_k", (0.0, 0.5, 0.75, 1.0, 1.25)),
               ("k_resid_slope", (-0.02, -0.01, 0.0, 0.01, 0.02, 0.03, 0.04)), ("phi_k", (30.0, 60.0, 100.0, 200.0, 1000.0)))
    grids_r = (("k_nonout_prior", (60.0, 120.0, 250.0, 500.0)), ("opp_alpha_o", (0.0, 0.5, 1.0, 1.5)),
               ("pitcher_alpha_r", (0.5, 0.75, 1.0, 1.25)), ("nonout_var_ratio", (0.5, 0.6, 0.7, 0.8, 0.9, 1.0)))
    for grids, fn in ((grids_r, r_ll), (grids_k, k_ll)):
        if fn is k_ll:  # E[BF] of the fitted outs -> BF mixture (r block already fitted)
            for r, mu in zip(rows, mus):
                _, r0 = rates(r["feat"], params)
                po = outs_distribution(mu, params)
                ebfs.append(sum(p_ * (o + nonout_mean(o, r0, params)) for o, p_ in enumerate(po)))
        for _sweep in range(2):
            for name, grid in grids:
                best = None
                for v in grid:
                    pp = {**params, name: v}
                    ll = fn(pp)
                    if best is None or ll > best[0]:
                        best = (ll, v)
                params[name] = best[1]
    params["fit"] = {"n_rows": len(rows), "ridge": ridge}
    return params


def league_constants(team_games, seasons):
    """league_* constants from development-season team games (sports data only)."""
    st = [g["starter"] for g in team_games if g["season"] in seasons and g.get("starter") and g["starter"].get("bf")]
    pit = [s for s in st if s.get("pitches")]
    pa = sum(g["batting_pa"] for g in team_games if g["season"] in seasons)
    outs = sum((g["starter"]["outs"] if g.get("starter") else 0) + (g.get("relief_outs") or 0)
               for g in team_games if g["season"] in seasons)
    so = sum(s["outs"] for s in st)
    return {
        "league_outs": round(so / len(st), 4),
        "league_pitches": round(sum(s["pitches"] for s in pit) / len(pit), 4),
        "league_k_rate": round(sum(s["k"] for s in st) / sum(s["bf"] for s in st), 5),
        "league_nonout_per_out": round(sum(s["bf"] - s["outs"] for s in st) / so, 5),
        "league_nonout_rate": round(1.0 - outs / pa, 5),
    }
