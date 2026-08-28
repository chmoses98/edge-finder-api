"""
lib/edgelab/backtest/run_distributions.py
====================================================================
MLB-RSCH-0010: PIT-safe, dev-only-fit scoring-distribution candidates
that convert a fixed pair of expected runs (lambda_home, lambda_away --
frozen, from MLB-RSCH-0009's own {offense, bullpen} mean model,
UNCHANGED) into event probabilities. Deliberately separates MEAN
QUALITY (already frozen by MLB-RSCH-0009, never touched here) from
DISTRIBUTION QUALITY (this module's only concern).

Candidates (each exposes a "joint(h, a) -> probability" function over
non-negative integer home/away run outcomes, so every derived
probability -- game result, game total, team total, margin -- is
computed by ONE shared, generic set of functions regardless of which
candidate produced the joint):

  D0 (control): independent Poisson marginals -- lib.edgelab's OWN
      p_team_wins/p_over_total (scripts/build_market_ledger.py) already
      compute exactly this; this module's independent_joint_pmf +
      derived-probability functions reproduce their outputs exactly
      (proven by a dedicated equivalence test) so every OTHER
      candidate's outputs are directly, apples-to-apples comparable
      through the SAME derivation code, not two different code paths
      that might silently disagree on edge cases.
  D1: independent negative-binomial marginals -- same means
      (lambda_home/lambda_away, UNCHANGED), inflated variance via ONE
      preregistered, dev-fit-once dispersion parameter (method-of-
      moments, closed-form, never iterative).
  D2: bivariate Poisson (shared-environment construction) -- SAME
      marginal means as D0 (a structural property of this
      construction, not an empirical claim -- see
      bivariate_poisson_joint_pmf's own docstring), only the JOINT
      (game total / margin / correlation-sensitive quantities) differs.
      Correlation parameter fit by closed-form method-of-moments
      (empirical Cov(actual_home, actual_away) around the frozen
      means), dev-only, once, frozen.

REUSES, DOES NOT REIMPLEMENT: poisson_pmf (scripts/build_market_ledger.py,
imported unchanged, the same function D0's own production counterpart
uses).

NO PARAMETER HUNTING: every dispersion/correlation parameter here is
fit via ONE fixed, preregistered, closed-form method-of-moments
computation over DEVELOPMENT rows only -- never iteratively optimized,
never chosen by comparing candidates against Pinnacle or against
validation/holdout performance.
"""
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_SCRIPTS_DIR = os.path.join(_ROOT, "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from build_market_ledger import poisson_pmf  # noqa: E402

MAX_RUNS = 30  # shared support ceiling for every candidate's discrete summation -- matches p_over_total's own convention


# ── Marginal PMFs ────────────────────────────────────────────────────────

def negative_binomial_pmf(k, mean, dispersion):
    """
    Pure. NB parameterized by its own MEAN (kept IDENTICAL to the
    Poisson mean -- this is the whole point of this candidate: only the
    variance changes) and a dispersion parameter `dispersion` (variance
    = mean + dispersion * mean**2; dispersion=0 degenerates to Poisson
    exactly, dispersion>0 is overdispersed relative to Poisson).
    Standard reparameterization to (r, p): r = 1/dispersion,
    p = r / (r + mean). Computed in log-space via math.lgamma for
    numerical stability at large k/non-integer r.
    """
    if mean is None or mean <= 0 or k < 0:
        return 0.0
    if dispersion is None or dispersion <= 0:
        return poisson_pmf(k, mean)
    r = 1.0 / dispersion
    p = r / (r + mean)
    log_pmf = (
        math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1)
        + r * math.log(p) + k * math.log(1.0 - p)
    )
    return math.exp(log_pmf)


# ── Joint PMFs (one per candidate) ──────────────────────────────────────

def independent_joint_pmf(pmf_home, pmf_away):
    """Pure. Wraps two independent marginal pmf functions (each
    int -> probability) into a joint(h, a) -> probability function --
    used identically for D0 (both Poisson) and D1 (both negative
    binomial). D0's own joint IS mathematically p_team_wins/p_over_total's
    own independent-Poisson assumption, just exposed uniformly."""
    def joint(h, a):
        return pmf_home(h) * pmf_away(a)
    return joint


def bivariate_poisson_joint_pmf(lambda_home, lambda_away, lambda_c):
    """
    Pure. Classic shared-environment construction: X = W + U,
    Y = W + V, with W ~ Poisson(lambda_c) (the shared "game
    environment"), U ~ Poisson(lambda_home - lambda_c), V ~
    Poisson(lambda_away - lambda_c), U/V/W mutually independent. A
    STRUCTURAL property of this construction (not fit, not estimated):
    the MARGINAL of X is exactly Poisson(lambda_home) and the marginal
    of Y is exactly Poisson(lambda_away) -- IDENTICAL to D0's own
    marginals, regardless of lambda_c. Only Cov(X, Y) = lambda_c is new.
    `lambda_c` is capped at min(lambda_home, lambda_away) (and at >= 0)
    so U/V's own Poisson rates are never negative -- a defensive clamp,
    not a fitting choice.

    poisson_pmf (reused unchanged) returns 0.0 for ANY lam<=0 at every
    k, including k=0 -- correct for lam<0 (undefined) but WRONG for the
    legitimate degenerate case lam==0, where a Poisson(0) is a point
    mass at 0 (P(X=0)=1). lambda_c==0 is not an edge case here -- it is
    the expected value whenever fit_correlation_dev_only floors a
    non-positive empirical covariance to exactly 0.0 -- so this function
    wraps poisson_pmf with a local, zero-safe substitute for its own
    three convolution terms rather than silently mis-scoring the whole
    joint distribution whenever lambda_c (or, at the lambda_home==lambda_c
    boundary, lambda_h_indep/lambda_a_indep) is exactly 0.
    """
    lambda_c = max(0.0, min(lambda_c or 0.0, lambda_home, lambda_away))
    lambda_h_indep = lambda_home - lambda_c
    lambda_a_indep = lambda_away - lambda_c

    def _pmf(k, lam):
        if lam == 0:
            return 1.0 if k == 0 else 0.0
        return poisson_pmf(k, lam)

    def joint(h, a):
        total = 0.0
        for k in range(0, min(h, a) + 1):
            total += _pmf(k, lambda_c) * _pmf(h - k, lambda_h_indep) * _pmf(a - k, lambda_a_indep)
        return total
    return joint


# ── Derived probabilities (generic over ANY joint pmf) ──────────────────

def home_win_and_push_prob(joint_fn, max_runs=MAX_RUNS):
    """Pure. (P(home wins), P(push/tie)) from any joint(h, a) function."""
    p_win = p_push = 0.0
    for h in range(max_runs + 1):
        for a in range(max_runs + 1):
            p = joint_fn(h, a)
            if h > a:
                p_win += p
            elif h == a:
                p_push += p
    return p_win, p_push


def total_over_prob(joint_fn, line, max_runs=MAX_RUNS):
    """Pure. P(home + away > line) from any joint(h, a) function."""
    return sum(
        joint_fn(h, a) for h in range(max_runs + 1) for a in range(max_runs + 1) if (h + a) > line
    )


def margin_at_least_prob(joint_fn, margin, max_runs=MAX_RUNS):
    """Pure. P(home - away >= margin) -- margin may be negative (an
    away-side margin, e.g. margin=-2 is 'away wins by 2+')."""
    return sum(
        joint_fn(h, a) for h in range(max_runs + 1) for a in range(max_runs + 1) if (h - a) >= margin
    )


def team_total_over_prob(pmf_fn, line, max_runs=MAX_RUNS):
    """Pure. P(team runs > line) from ONE team's own marginal pmf
    function -- valid for D0/D1 (each team's own independent marginal)
    and equally valid for D2 (whose marginals are, structurally,
    identical to D0's -- see bivariate_poisson_joint_pmf's docstring),
    so D2's team-total probabilities are computed by calling this with
    D0's OWN Poisson marginal, never re-derived from D2's joint."""
    return sum(pmf_fn(k) for k in range(int(line) + 1, max_runs + 1))


def joint_pmf_sums_to_one(joint_fn, max_runs=MAX_RUNS, tolerance=1e-6):
    """Pure. Numerical-stability / probability-mass-conservation check --
    sums the joint pmf over its full truncated support. Never exactly
    1.0 due to MAX_RUNS truncation (a vanishingly small tail beyond 30
    combined runs is dropped, by design, matching p_over_total's own
    existing max_r convention) -- callers compare against `tolerance`."""
    total = sum(joint_fn(h, a) for h in range(max_runs + 1) for a in range(max_runs + 1))
    return abs(total - 1.0) <= tolerance


# ── Dev-only, closed-form, method-of-moments parameter fitting ─────────

def fit_overdispersion_dev_only(pairs):
    """
    Pure. `pairs`: iterable of (actual_runs, lambda) for DEVELOPMENT
    rows only, POOLED across home and away sides (ONE league-wide
    dispersion parameter -- the mission's preferred, most parsimonious
    option). Method-of-moments: for a candidate with mean=lambda and
    variance=lambda + dispersion*lambda**2, ((actual-lambda)**2 -
    lambda) / lambda**2 is, in expectation, an unbiased single-
    observation estimator of `dispersion`; averaging over thousands of
    games converges to the population value even though each individual
    game contributes only one noisy draw. Floored at 0.0 -- if the
    empirical estimate is negative (data looks UNDER-dispersed relative
    to Poisson), this returns exactly 0.0 (the NB candidate degenerates
    to Poisson, a legitimate, honestly-reported finding, never forced
    positive to manufacture a "different" candidate).
    """
    vals = []
    for actual, lam in pairs:
        if actual is None or lam is None or lam <= 0:
            continue
        vals.append(((actual - lam) ** 2 - lam) / (lam ** 2))
    if not vals:
        return 0.0
    return max(0.0, round(sum(vals) / len(vals), 6))


def fit_correlation_dev_only(triples):
    """
    Pure. `triples`: iterable of (actual_home, lambda_home, actual_away,
    lambda_away) -- wait, 4 values; see signature below. Kept as one
    function taking 4-tuples to make the pairing explicit and avoid a
    zip-order bug. Method-of-moments: for the bivariate Poisson
    construction, Cov(X, Y) = lambda_c exactly (a structural identity of
    that model), so the empirical mean of (actual_home - lambda_home) *
    (actual_away - lambda_away) over DEVELOPMENT rows is an unbiased
    estimator of the single, global, ONE-parameter lambda_c. Floored at
    0.0 for the same reason as fit_overdispersion_dev_only -- a negative
    empirical covariance means D2 degenerates toward D0 (lambda_c=0,
    independent), never forced positive.
    """
    vals = []
    for actual_home, lambda_home, actual_away, lambda_away in triples:
        if actual_home is None or lambda_home is None or actual_away is None or lambda_away is None:
            continue
        vals.append((actual_home - lambda_home) * (actual_away - lambda_away))
    if not vals:
        return 0.0
    return max(0.0, round(sum(vals) / len(vals), 6))


# ── Empirical diagnostics (compare realized outcomes to what Poisson implies) ──

def empirical_mean_variance(values):
    """Pure. (mean, variance) of a list of realized run counts, ignoring
    None entries. Population variance (divide by n, not n-1) -- matches
    every other aggregate statistic in this program's research reports.
    (None, None) for an empty/all-None input."""
    vals = [v for v in values if v is not None]
    if not vals:
        return None, None
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    return round(mean, 4), round(var, 4)


def empirical_correlation(pairs):
    """Pure. Pearson correlation coefficient of `pairs` ([(x, y), ...]),
    ignoring any pair with a None entry. None if fewer than 2 valid pairs
    or either variable has zero variance (never a fabricated 0.0)."""
    valid = [(x, y) for x, y in pairs if x is not None and y is not None]
    n = len(valid)
    if n < 2:
        return None
    mean_x = sum(x for x, _ in valid) / n
    mean_y = sum(y for _, y in valid) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in valid) / n
    var_x = sum((x - mean_x) ** 2 for x, _ in valid) / n
    var_y = sum((y - mean_y) ** 2 for _, y in valid) / n
    if var_x <= 0 or var_y <= 0:
        return None
    return round(cov / math.sqrt(var_x * var_y), 4)


def empirical_tail_frequency(values, threshold, mode="at_least"):
    """Pure. Fraction of `values` (ignoring None) satisfying the tail
    condition -- mode="at_least": value >= threshold; mode="exactly":
    value == threshold. `threshold`/`mode` must be preregistered BEFORE
    inspecting any real result (this function itself takes no default,
    forcing every call site to state its own fixed definition)."""
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    if mode == "at_least":
        hits = sum(1 for v in vals if v >= threshold)
    elif mode == "exactly":
        hits = sum(1 for v in vals if v == threshold)
    else:
        raise ValueError(f"Unknown mode {mode!r}, expected 'at_least' or 'exactly'")
    return round(hits / len(vals), 4)


def poisson_implied_tail_frequency(lambdas, threshold, mode="at_least", max_runs=MAX_RUNS):
    """Pure. What Poisson (independent, mean=lambda per row) PREDICTS
    the same tail frequency should be, averaged over the same rows'
    own lambdas -- the direct comparison point for empirical_tail_frequency,
    so 'how Poisson is wrong' can be read off as a simple difference."""
    lambdas = [lam for lam in lambdas if lam is not None and lam > 0]
    if not lambdas:
        return None
    if mode == "at_least":
        probs = [sum(poisson_pmf(k, lam) for k in range(threshold, max_runs + 1)) for lam in lambdas]
    elif mode == "exactly":
        probs = [poisson_pmf(threshold, lam) for lam in lambdas]
    else:
        raise ValueError(f"Unknown mode {mode!r}, expected 'at_least' or 'exactly'")
    return round(sum(probs) / len(probs), 4)
