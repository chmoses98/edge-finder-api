#!/usr/bin/env python3
"""
lib/research/hitter_shrinkage.py
===================================
Hitter Projection Engine -- Phase 4 reusable hierarchical shrinkage.

Every granular matchup split this repo can derive (hitter vs a specific
pitch family/velocity bucket, hitter vs a specific location zone, ...)
is small-sample by nature. This module is the ONE shrinkage primitive
every Phase 4 model (PA-outcome rates, swing/take rates, contact
quality) uses instead of trusting a raw split directly -- consistent
with PR #77's own MIN_PA_HITTER_SPLIT convention
(lib.research.platoon_context), generalized here into a reusable,
continuous (not just floor/no-floor) form so nearby sample sizes don't
produce discontinuous jumps at an arbitrary threshold.

METHOD: empirical-Bayes / pseudocount shrinkage
--------------------------------------------------
shrink_rate(observed_successes, observed_trials, prior_rate, prior_strength)
treats `prior_strength` as a number of "pseudo-observations" of the
broader prior, and blends:

    shrunk_rate = (observed_successes + prior_rate * prior_strength)
                  / (observed_trials + prior_strength)

This is a Beta-Binomial posterior mean under a Beta(prior_rate *
prior_strength, (1-prior_rate) * prior_strength) prior -- a standard,
well-understood empirical-Bayes shrinkage, not an invented formula. As
observed_trials -> 0, shrunk_rate -> prior_rate (full shrinkage); as
observed_trials -> infinity, shrunk_rate -> the raw observed rate (no
shrinkage) -- continuous in observed_trials, never a hard cutoff.

hierarchical_shrink() chains multiple levels (finest to broadest),
shrinking each level toward the ALREADY-SHRUNK broader level below it,
so uncertainty compounds correctly through the whole fallback chain
(exact/similar pitch profile -> pitch type x velocity -> pitch type ->
handedness split -> overall hitter -> league prior) instead of blending
only the two endpoints.
"""

from typing import Optional, Sequence


def shrink_rate(observed_successes: float, observed_trials: float,
                 prior_rate: float, prior_strength: float) -> float:
    """
    Beta-Binomial posterior mean. `prior_strength` is in units of
    "trials" -- a prior_strength of 50 means the prior carries as much
    weight as 50 real observations. Raises ValueError on a negative
    trial/strength count (a caller bug, not a data-quality issue) rather
    than silently producing a nonsensical rate.
    """
    if observed_trials < 0 or prior_strength < 0:
        raise ValueError("observed_trials and prior_strength must be >= 0")
    if not (0.0 <= prior_rate <= 1.0):
        raise ValueError("prior_rate must be in [0, 1]")
    denom = observed_trials + prior_strength
    if denom == 0:
        return prior_rate
    return (observed_successes + prior_rate * prior_strength) / denom


def effective_sample_size(observed_trials: float, prior_strength: float) -> float:
    """
    How much real evidence this shrunk estimate actually reflects --
    always <= observed_trials + prior_strength, and always >=
    observed_trials (the prior only ever ADDS confidence, on top of
    whatever real data exists). Used for uncertainty reporting, not for
    the point estimate itself.
    """
    return observed_trials


class ShrinkageLevel:
    """One rung of a hierarchical_shrink() fallback chain."""
    __slots__ = ("name", "successes", "trials", "strength")

    def __init__(self, name: str, successes: Optional[float], trials: Optional[float], strength: float):
        self.name = name
        self.successes = successes
        self.trials = trials
        self.strength = strength


def hierarchical_shrink(levels: Sequence[ShrinkageLevel], floor_prior_rate: float) -> dict:
    """
    levels: ordered FINEST-FIRST (e.g. [pitchShapeLevel, pitchTypeLevel,
    platoonLevel, seasonLevel]) -- each level's `successes`/`trials` may
    be None (that level had zero data, e.g. no raw archive at all) or a
    real observed count. `floor_prior_rate` is the broadest/league prior
    (e.g. league-average K% for this outcome), used as the ultimate
    base case when every level is empty.

    Chains shrinkage from the BROADEST level up to the FINEST: each
    level's raw rate (if it has any trials) is shrunk toward the
    ALREADY-COMPUTED shrunk rate of the level below it (broader), using
    that level's own `strength`. A level with zero trials is skipped
    entirely (contributes nothing, correctly -- shrinking "0 trials"
    toward the parent is a no-op by construction since shrink_rate with
    observed_trials=0 returns prior_rate exactly).

    Returns {"rate": final shrunk rate, "effectiveSampleSize": total
    real trials actually observed across every level, "levelsUsed":
    [name, ...] of levels that had at least 1 real trial, "chain":
    [{"level", "rawRate", "trials", "shrunkRate"}, ...] full audit trail
    for explainability}.
    """
    current_rate = floor_prior_rate
    chain = []
    total_trials = 0.0
    levels_used = []

    # Iterate broadest-to-finest (reverse of the finest-first input) so
    # each successive shrink target is the previous (broader) level's
    # already-shrunk rate.
    for level in reversed(list(levels)):
        if level.trials is None or level.trials <= 0:
            chain.append({"level": level.name, "rawRate": None, "trials": 0, "shrunkRate": current_rate})
            continue
        raw_rate = level.successes / level.trials
        shrunk = shrink_rate(level.successes, level.trials, current_rate, level.strength)
        chain.append({"level": level.name, "rawRate": round(raw_rate, 4), "trials": level.trials, "shrunkRate": round(shrunk, 4)})
        current_rate = shrunk
        total_trials += level.trials
        levels_used.append(level.name)

    chain.reverse()  # report finest-first, matching the input order
    return {
        "rate": round(current_rate, 4),
        "effectiveSampleSize": total_trials,
        "levelsUsed": levels_used,
        "chain": chain,
    }
