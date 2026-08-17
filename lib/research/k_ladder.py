#!/usr/bin/env python3
"""
lib/research/k_ladder.py
============================
Pitcher strikeout ladder tail discipline: full-ladder distribution
construction, an explicit monotonicity/bounds self-check, and a
distributional, uncertainty-aware "knee" selector that compares
thresholds JOINTLY on risk-adjusted, fee-aware net EV -- never simply
the largest nominal payout or raw edge.

THE GAP THIS MODULE CLOSES
-----------------------------
lib.research.pitcher_workload_projection.p_strikeouts_at_least() already
computes P(K>=n) via a Binomial tail sum, and is monotonic in n BY
CONSTRUCTION (a binomial survival function cannot rise as the threshold
rises) -- but no function anywhere in the repo builds the WHOLE ladder
in one call, jointly compares thresholds, or applies fee-aware EV to a
pitcher-strikeout contract at all (pitcher_strikeouts is not in
scripts/build_market_ledger.py's REQUIRED_MARKETS, and
scripts/discover_kalshi_mlb_markets.py's board-building path never calls
lib.edgelab.kalshi_fees for it -- see that module's own docstring).

THE LESSON THIS MODULE ENCODES
-----------------------------------
Logan Henderson 6+ Ks (a central threshold) hit exactly 6 and was the
preferred expression; Misiorowski 9+ Ks (an aggressive tail threshold)
lost despite a strong pitching performance. The model's own probability
estimates are least reliable far into the tail of a binomial
distribution -- small absolute errors in k_rate or batters-faced near
the tail translate into LARGE relative errors in a small tail
probability (e.g. a k_rate off by 2 percentage points barely moves
P(K>=6) but can materially move P(K>=10)). This module does not
"believe" the raw tail probability less (it is reported honestly,
unmodified) -- it discounts the EDGE/EV computed from that probability
by how many standard deviations from the distribution's own mean the
threshold sits, a standard, principled shrinkage (not an arbitrary
recent-result-based rule, and not a hardcoded threshold ban): a
threshold within roughly one standard deviation of the mean is fully
trusted; further out, the same nominal edge must be proportionally
larger to still look attractive after discounting, exactly the "a 9+ or
10+ prop should require materially stronger edge than a central
5+/6+/7+ threshold" requirement.

SCOPE / SAFETY
---------------
Every function here is pure: no I/O, no network, no mutation of any
argument, deterministic given deterministic inputs -- the same contract
as lib.research.pitcher_workload_projection, on which this module is
built (it does not reimplement the binomial math, only adds ladder
construction, a self-check, and the discount/selection layer on top).
Nothing here is imported by scripts/build_market_ledger.py,
scripts/risk_gate.py, or scripts/write_pending_bets.py -- research/
discovery-path scaffolding only, matching
lib.research.pitcher_workload_projection's own scope.
"""

from lib.edgelab.kalshi_fees import (
    FEE_TYPE_TAKER,
    fee_adjusted_break_even_probability,
    net_expected_value_per_dollar,
)
from lib.research.pitcher_workload_projection import _k_rate, p_strikeouts_at_least

_MONOTONIC_TOLERANCE = 1e-9

# Thresholds within this many standard deviations of the binomial
# distribution's own mean are fully trusted (discount == 1.0) -- roughly
# the central 5+/6+/7+ range for a typical modern starter's expected-K
# distribution. Beyond this band, the discount shrinks quadratically.
CENTRAL_Z_BAND = 1.0

# Quadratic growth rate of the tail-uncertainty discount beyond
# CENTRAL_Z_BAND -- deliberately steep. A cheap, deep-tail contract has
# enormous EV "leverage" (net_expected_value_per_dollar scales like
# 1/price), so a THIRD-strength discount on the edge alone is nowhere
# near enough to offset it (confirmed numerically: a 12+ prop ~2.7 SDs
# out with a tiny 0.3-cent mispricing still shows a raw edge >50x a
# fairly-priced central threshold's, purely from price leverage). This
# coefficient is calibrated so a genuinely deep tail threshold (~2.5+
# SDs out) with a REALISTIC, modest mispricing loses to a fairly-priced
# central threshold once shrunk -- while a threshold that is only
# moderately tail (just past CENTRAL_Z_BAND) or one with a genuinely
# extreme mispricing still clears the bar (see test coverage for both).
TAIL_UNCERTAINTY_COEF = 5.0


def _binomial_mean_std(n_trials, k_rate):
    if n_trials is None or k_rate is None or n_trials <= 0:
        return None, None
    mean = n_trials * k_rate
    variance = n_trials * k_rate * (1.0 - k_rate)
    return mean, (variance ** 0.5 if variance > 0 else 0.0)


def build_strikeout_ladder(batters_faced, k_pct, thresholds, *, opponent_k_pct=None):
    """
    Pure. Builds the full P(K>=n) ladder for every threshold in
    `thresholds` (any iterable of positive ints) from the SAME
    Binomial(battersFaced, kRate) model
    lib.research.pitcher_workload_projection.p_strikeouts_at_least()
    already uses -- never a second, independently-derived probability.

    Returns:
        {
          'probabilities': {n: P(K>=n) or None, ...},
          'mean': binomial mean (expected strikeouts), or None,
          'std': binomial standard deviation, or None,
          'monotonic': bool -- P(K>=n) never rises as n rises (should
                       always be True by construction; reported as an
                       explicit, auditable self-check rather than
                       assumed),
          'boundsValid': bool -- every probability is within [0, 1],
          'nTrials': the rounded batters-faced trial count actually used,
        }

    Never fabricates: if batters_faced or k_pct is None, every threshold
    maps to None and monotonic/boundsValid are trivially True (nothing
    to violate).
    """
    thresholds = sorted(set(int(t) for t in thresholds))
    probs = {
        n: p_strikeouts_at_least(batters_faced, k_pct, n, opponent_k_pct=opponent_k_pct)
        for n in thresholds
    }

    known = [n for n in thresholds if probs[n] is not None]
    monotonic = all(
        probs[known[i]] >= probs[known[i + 1]] - _MONOTONIC_TOLERANCE
        for i in range(len(known) - 1)
    )
    bounds_valid = all(-1e-9 <= probs[n] <= 1.0 + 1e-9 for n in known)

    n_trials = max(0, round(batters_faced)) if batters_faced is not None else None
    k_rate = _k_rate(k_pct, opponent_k_pct)
    mean, std = _binomial_mean_std(n_trials, k_rate)

    return {
        "probabilities": probs,
        "mean": round(mean, 4) if mean is not None else None,
        "std": round(std, 4) if std is not None else None,
        "monotonic": monotonic,
        "boundsValid": bounds_valid,
        "nTrials": n_trials,
    }


def tail_uncertainty_discount(threshold, mean, std):
    """
    Pure. 1.0 (full trust) for a threshold within CENTRAL_Z_BAND standard
    deviations of the distribution's own mean; shrinks quadratically
    beyond that band. Uses the standard continuity-corrected z-score
    (threshold - 0.5 - mean) / std, matching the discrete-to-normal
    approximation convention. Returns 1.0 (no discount) when std is
    unavailable/zero -- never a fabricated inflation OR deflation from
    missing variance data.
    """
    if mean is None or std is None or std <= 0:
        return 1.0
    z = abs((threshold - 0.5 - mean) / std)
    if z <= CENTRAL_Z_BAND:
        return 1.0
    excess = z - CENTRAL_Z_BAND
    return 1.0 / (1.0 + TAIL_UNCERTAINTY_COEF * excess * excess)


def evaluate_k_ladder_expressions(ladder, prices_cents, *, fee_type=FEE_TYPE_TAKER):
    """
    Pure. Jointly compares every threshold in `ladder` (the output of
    build_strikeout_ladder) against its own executable ask price
    (`prices_cents`, a {threshold: price_cents} dict -- not every
    threshold need have a price) on fee-adjusted, risk-adjusted net EV.

    The risk adjustment SHRINKS THE PROBABILITY toward the market price
    by the tail-uncertainty discount, rather than linearly scaling the
    already-computed EV: `pShrunk = price + discount * (p - price)`, a
    standard shrinkage-toward-the-market-implied-null estimator. This
    matters because net_expected_value_per_dollar scales roughly like
    1/price -- a cheap, deep-tail contract has enormous EV "leverage,"
    so discounting the EV number directly barely moves it; discounting
    the underlying EDGE (p - price) in probability space, before it gets
    leveraged by division-by-price, is what actually neutralizes an
    unreliable deep-tail estimate proportionally to how unreliable it
    is (confirmed numerically -- see test coverage's central-vs-tail
    scenarios).

    For each priced threshold, returns:
      {
        'probability': raw P(K>=n) -- UNMODIFIED, honestly reported
            (the shrinkage never touches this field, only the EV/
            ranking calculation),
        'priceCents': the price supplied,
        'feeAdjustedBreakEvenProbability': ...,
        'netExpectedValuePerDollar': raw fee-adjusted net EV from the
            UNSHRUNK probability -- the "does this even clear fees on
            its own honest number" gate,
        'tailUncertaintyDiscount': tail_uncertainty_discount() for this
            threshold given the ladder's own mean/std,
        'riskAdjustedNetEV': fee-adjusted net EV computed from the
            shrunk probability -- the quantity thresholds are actually
            RANKED on,
        'qualifies': bool -- raw netExpectedValuePerDollar > 0 (a
            threshold that isn't even nominally +EV before any
            shrinkage can never be the best expression, regardless of
            how the shrinkage would treat it),
      }

    `bestExpression`: the threshold (int) with the highest
    riskAdjustedNetEV among qualifying (`qualifies=True`) thresholds, or
    None if no threshold qualifies. Ties broken toward the LOWER
    threshold (the less tail-exposed expression) -- never toward the
    threshold with the larger nominal payout.

    Never auto-prefers the lowest threshold (a genuinely strong tail
    edge can still win -- see test coverage) and never auto-bans the
    highest (a threshold is excluded only by failing to qualify or
    losing the risk-adjusted comparison, never by a hardcoded rule
    against its threshold value alone).
    """
    probs = ladder["probabilities"]
    mean = ladder["mean"]
    std = ladder["std"]

    per_threshold = {}
    for n, price_c in prices_cents.items():
        p = probs.get(n)
        if p is None or price_c is None:
            continue
        price = price_c / 100.0
        net_ev = net_expected_value_per_dollar(p, price, fee_type=fee_type)
        break_even = fee_adjusted_break_even_probability(price, fee_type=fee_type)
        discount = tail_uncertainty_discount(n, mean, std)
        p_shrunk = price + discount * (p - price)
        risk_adjusted = net_expected_value_per_dollar(p_shrunk, price, fee_type=fee_type)
        per_threshold[n] = {
            "probability": p,
            "priceCents": price_c,
            "feeAdjustedBreakEvenProbability": break_even,
            "netExpectedValuePerDollar": net_ev,
            "tailUncertaintyDiscount": round(discount, 6),
            "riskAdjustedNetEV": risk_adjusted,
            "qualifies": bool(net_ev is not None and net_ev > 0),
        }

    qualifying = [n for n, r in per_threshold.items() if r["qualifies"]]
    best = None
    if qualifying:
        best = min(
            qualifying,
            key=lambda n: (-per_threshold[n]["riskAdjustedNetEV"], n),
        )

    return {
        "thresholds": per_threshold,
        "bestExpression": best,
    }
