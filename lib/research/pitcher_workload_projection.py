#!/usr/bin/env python3
"""
lib/research/pitcher_workload_projection.py
================================================
RESEARCH-ONLY joint workload/performance projection for a starting
pitcher's strikeout ("pitcher_strikeouts", Kalshi series KXMLBKS) and
outs-recorded ("pitcher_outs", KXMLBOUTS) props.

Both families are literal Kalshi "N+" contracts: YES iff the final stat
is >= N -- no push, no half-line (lib.edgelab.player_prop_settlement's
module docstring). Every pitcher-prop bet ever logged in this repo
(data/edgelab/bets/bets.jsonl) has modelFairProbability=null --
lib.kalshi_probability_adapters._NEVER_MODELED_FAMILIES has always
listed both families as having "no ... probability distribution ... in
this codebase." This module is that distribution.

WHY A SHARED SURVIVAL CURVE, NOT TWO INDEPENDENT POINT ESTIMATES
---------------------------------------------------------------------
The Aug 2026 postmortems (data/edgelab/postmortems/2026-08-0{3,5,6}/
postmortem.md) are explicit about the failure mode this fixes:
"expensive starter-outs unders require several workload signals",
"pitcher-outs markets require explicit manager/workload modeling" (the
Wacha 19+ outs miss -- 6 1/3 innings, exactly the third-time-through
boundary below), "Perez 16+ outs over-weighted a recent long outing",
and "joint pitcher workload/K projection" as an explicit follow-up.
Treating K and outs as independent averages can't reproduce any of
that: a start-shortening risk (a tough opponent, TTO fatigue, an
opener, high walk rate) has to reduce BOTH the outs ceiling and the K
ceiling together, because a shorter start is fewer batters faced no
matter which stat is being priced.

So there is exactly ONE generative model here: a discrete per-out
survival ("hook") process. per_out_survival[i] is P(the pitcher
records out i+1 | he already recorded outs 1..i) -- built from
avgIPperStart (the one required input), walk-rate efficiency, opener
status, and, when supplied, third-time-through-the-order risk, recent
workload restriction, and opponent offensive strength. Everything else
is derived from that ONE curve:

  * P(Outs >= N) = product of the first N survival probabilities --
    Requirement 3's literal target ("19+ outs = probability of
    recording at least 19 outs, not merely 'projects around six
    innings'"): the "6 1/3" framing falls out for free, since
    19 outs / 3 == 6.333... innings.
  * Expected outs = sum_{n=1}^{max} P(Outs >= n) (the standard
    nonnegative-integer expectation identity -- avoids ever
    re-deriving a second, possibly-inconsistent mean).
  * Expected batters faced = expected outs inflated by the SAME
    walk rate that already shaped the survival curve (a walk is a
    plate appearance that produces no out).
  * P(K >= n) = Binomial(round(battersFaced), kRate) tail, where
    kRate is the pitcher's own kPct, scaled (only when supplied) by
    an opponent contact/K-tendency multiplier.

Any input this module cannot verify is real for a given call (opponent
batting K-rate, recent pitch-count/workload restriction, opponent
offensive strength) is an OPTIONAL, explicitly-named keyword that
defaults to "not applied" and is reported back in `diagnostics` as an
explicit "...DataAvailable" flag -- never guessed from a default that
pretends to be real data (Requirement 6). As of this module's
introduction, real committed data/slate.json has NONE of
awayTeamStats.teamKPct, pitcherSavant.vsLHH/vsRHH, or
bullpen.recentUsage populated (confirmed by direct inspection, not
assumed) -- every corresponding parameter here is expected to be None
in production today; this module's job is to be ready the moment that
data exists, and to be honest about it in the meantime, not to fabricate
a value in its absence. See the "INTENTIONALLY DEFERRED" note at the
bottom of this docstring for what full production wiring still needs.

WHY A SEPARATE MODULE, NOT AN IMPORT OF build_market_ledger.py's LOGIC
------------------------------------------------------------------------
Same rationale as lib/kalshi_period_projections.py (see that module's
own docstring): scripts/build_market_ledger.py is the real-money
execution gate and this module must never be entangled with it. This
module does not import from it and does not modify it.

SCOPE / SAFETY
---------------
Every function here is pure: no file I/O, no network, no clock reads,
no printing, no mutation of any argument, deterministic given
deterministic inputs -- the same contract as
lib.research.three_way_projection. Nothing here is imported by
scripts/build_market_ledger.py, scripts/risk_gate.py,
scripts/write_pending_bets.py, or scripts/protect_slate.py -- this is
research/discovery-path scaffolding only (see
lib.kalshi_probability_adapters.adapt_pitcher_strikeouts/
adapt_pitcher_outs for the one place it is actually wired in).

INTENTIONALLY DEFERRED (not part of this module)
---------------------------------------------------
lib.kalshi_mlb_market_classifier.classify_contract()'s _PITCHER_FAMILIES
branch still leaves subjectId/subjectName/side/line unresolved for a
real pitcher_strikeouts/pitcher_outs contract ("deliberately left
unimplemented ... always routed to modelSupportStatus=UNSUPPORTED
downstream regardless" -- see that module's own comment). Populating
those fields (via lib.research.player_prop_parser, which already
parses ticker/threshold/player token correctly) and threading a
specific pitcher's Savant fields into
scripts/discover_kalshi_mlb_markets.py's projection_context is real,
separate plumbing work -- out of scope for this joint-modeling change
and not attempted here.
"""
import math

OUTS_PER_INNING = 3

# Defensive upper bound on the survival curve's support -- 12 innings
# (36 outs) is far beyond any realistic modern MLB start; kept generous
# specifically so expected_outs()'s truncated sum has negligible
# residual mass even for a very durable projected workload, mirroring
# lib.research.three_way_projection's own "generous max, truncation
# mass never silently discarded" philosophy (its DEFAULT_MAX_RUNS
# docstring).
DEFAULT_MAX_OUTS = 36

# Illustrative league-average constants, documented as such (not fit
# from this repo's own historical data -- no such fitting pass exists
# yet, matching lib.research.market_taxonomy's and
# lib.kalshi_period_projections's precedent of clearly-labeled
# illustrative constants rather than silently-precise ones).
LEAGUE_AVG_BB_PCT = 8.5      # percent
LEAGUE_AVG_K_PCT = 22.0      # percent (used only to scale an opponent K-tendency multiplier)
LEAGUE_AVG_OPPONENT_WRC_PLUS = 100.0

# Walk-rate -> target-outs efficiency coefficient: how much a pitcher's
# own bbPct above/below league average shrinks/grows the innings a
# manager lets him work before pitch-count pressure ends the start.
BB_EFFICIENCY_COEF = 2.2
MIN_TARGET_OUTS = 3.0    # never project below 1 inning of expected workload
MAX_TARGET_OUTS = 30.0   # never project above 10 innings

# Third-time-through-the-order risk begins once the 19th out is on
# deck (~6 IP -- roughly twice through a 9-batter order) -- the exact
# boundary Requirement 2's "19+ outs" example sits on, and the real
# Aug 6 2026 postmortem's "Wacha 19+ outs" miss.
TTO_THRESHOLD_OUTS = 18
TTO_PENALTY_COEF = 0.35   # ttoSplit (xFIP delta, 3rd TTO vs 1st) -> extra per-out hook risk beyond the threshold, when tto_risk is supplied True

OPENER_CAP_OUTS = 8              # openers are essentially never left in beyond ~2.2 IP
OPENER_BASE_SURVIVAL = 0.90      # still a short outing even within the cap
OPENER_POST_CAP_SURVIVAL = 0.10

RECENT_WORKLOAD_PENALTY = 0.10   # flat per-out survival penalty applied across the whole start when a caller supplies real evidence of a workload restriction (e.g. a documented pitch-count cap coming off a recent long outing) -- never applied from a guess

OPPONENT_STRENGTH_COEF = 0.0015  # per-point-of-wRC+-above-average shrinkage to per-out survival (blowup/early-hook proxy)

# How strongly a supplied opponent batting strikeout rate (relative to
# league average) scales the pitcher's own kPct -- 1.0 = proportional.
OPPONENT_KRATE_COEF = 1.0

_SURVIVAL_FLOOR = 0.01
_SURVIVAL_CEILING = 0.99


def survival_curve(avg_ip_per_start, *, bb_pct=None, opener=False,
                    tto_split=None, tto_risk=None,
                    recent_workload_restricted=None,
                    opponent_wrc_plus=None, max_outs=DEFAULT_MAX_OUTS):
    """
    Pure. Returns (per_out_survival, diagnostics).

    per_out_survival: list[float] of length `max_outs`, 0-indexed --
    per_out_survival[i] is P(records out i+1 | recorded outs 1..i).
    None (with diagnostics['insufficientWorkloadData']=True) when
    avg_ip_per_start is None -- the one required input, and NEVER
    defaulted the way scripts/build_market_ledger.py's
    compute_projections() defaults a missing avgIPperStart to 6.0 for
    the (already-modeled, already-calibrated) run-scoring model. A
    fabricated average here would directly manufacture a fair
    probability for a brand-new market family with no calibration
    history yet -- exactly the "over-weighted"/"requires several
    workload signals" failure the postmortems describe -- so this
    module refuses instead (Requirement 6).

    diagnostics always reports which optional inputs were actually
    supplied (`ttoDataAvailable`, `recentWorkloadDataAvailable`,
    `opponentStrengthDataAvailable`) plus the derived `targetOuts` and
    `baseSurvival`, so a caller can audit exactly what the curve did
    and did not account for.
    """
    diagnostics = {
        "ttoDataAvailable": tto_split is not None and tto_risk is not None,
        "recentWorkloadDataAvailable": recent_workload_restricted is not None,
        "opponentStrengthDataAvailable": opponent_wrc_plus is not None,
    }
    if avg_ip_per_start is None:
        diagnostics["insufficientWorkloadData"] = True
        return None, diagnostics
    diagnostics["insufficientWorkloadData"] = False

    target_outs = avg_ip_per_start * OUTS_PER_INNING
    if bb_pct is not None:
        efficiency_factor = 1.0 - BB_EFFICIENCY_COEF * (bb_pct - LEAGUE_AVG_BB_PCT) / 100.0
        target_outs *= efficiency_factor
    target_outs = max(MIN_TARGET_OUTS, min(MAX_TARGET_OUTS, target_outs))

    if opener:
        p_base = OPENER_BASE_SURVIVAL
    else:
        # Solves E[Outs] == target_outs for a single-phase (constant-p)
        # discrete survival process: mean = p / (1 - p) => p = target / (target + 1).
        p_base = target_outs / (target_outs + 1.0)

    if opponent_wrc_plus is not None:
        p_base -= OPPONENT_STRENGTH_COEF * (opponent_wrc_plus - LEAGUE_AVG_OPPONENT_WRC_PLUS)
        p_base = max(_SURVIVAL_FLOOR, min(_SURVIVAL_CEILING, p_base))

    tto_penalty = 0.0
    if tto_risk and tto_split is not None:
        tto_penalty = min(0.5, TTO_PENALTY_COEF * tto_split)

    workload_penalty = RECENT_WORKLOAD_PENALTY if recent_workload_restricted else 0.0

    curve = []
    for i in range(max_outs):
        out_number = i + 1
        if opener and out_number > OPENER_CAP_OUTS:
            p_i = OPENER_POST_CAP_SURVIVAL
        else:
            p_i = p_base - workload_penalty
            if out_number > TTO_THRESHOLD_OUTS:
                p_i -= tto_penalty
            p_i = max(_SURVIVAL_FLOOR, min(_SURVIVAL_CEILING, p_i))
        curve.append(p_i)

    diagnostics["targetOuts"] = round(target_outs, 3)
    diagnostics["baseSurvival"] = round(p_base, 4)
    return curve, diagnostics


def p_outs_at_least(per_out_survival, n):
    """Pure. P(Outs >= n) -- product of the first n survival probabilities. P(Outs >= 0) == 1.0 always (never negative outs)."""
    if per_out_survival is None:
        return None
    if n <= 0:
        return 1.0
    n = min(n, len(per_out_survival))
    p = 1.0
    for s in per_out_survival[:n]:
        p *= s
    return p


def expected_outs(per_out_survival):
    """
    Pure. E[Outs] = sum_{n=1}^{N} P(Outs >= n) -- the standard
    nonnegative-integer-random-variable expectation identity, so this
    can never silently disagree with p_outs_at_least().

    Every curve this module actually builds (survival_curve()) is
    piecewise-constant and holds its LAST value from some point
    onward (the post-TTO-threshold or post-opener-cap regime) rather
    than dropping to 0 the instant the list runs out -- so simply
    truncating the sum at len(per_out_survival) would understate
    E[Outs] by a real, non-negligible amount for any durable-enough
    workload (confirmed numerically: a 6.0-IP-target curve with no
    other risk factors truncates to ~15.4 expected outs at a 36-out
    list length instead of its true ~18.0, a 14% low bias, because its
    per-out survival probability stays close to 1 for a long time).
    This adds the exact geometric tail beyond the list's own length,
    treating its last value as continuing forever -- true for every
    curve this module produces, and a reasonable, explicitly-documented
    assumption for any other constant-tailed curve a caller supplies.
    """
    if per_out_survival is None:
        return None
    n = len(per_out_survival)
    total = sum(p_outs_at_least(per_out_survival, k) for k in range(1, n + 1))
    p_tail = per_out_survival[-1]
    if p_tail < 1.0:
        reach_tail_prob = p_outs_at_least(per_out_survival, n)
        total += reach_tail_prob * (p_tail / (1.0 - p_tail))
    return total


def expected_batters_faced(exp_outs, bb_pct):
    """
    Pure. PA = outs / (1 - walkRate) -- a walk is a plate appearance
    that produces no out. Hits-allowed are deliberately NOT modeled as
    a separate PA-inflation term: no hits-allowed-rate field exists
    anywhere in this repo's pitcher data (contact QUALITY is already
    captured, separately, by the xFIP-based run-scoring model -- this
    module never re-derives that); this is a documented, intentionally
    partial approximation, not a claim of sabermetric completeness.
    """
    if exp_outs is None:
        return None
    bb_rate = max(0.0, min(0.5, (bb_pct or 0.0) / 100.0))
    return exp_outs / (1.0 - bb_rate)


def binomial_pmf(k, n, p):
    """Pure. Standard binomial pmf -- k successes in n iid Bernoulli(p) trials, via math.comb (no scipy/numpy in this repo's dependencies)."""
    if n < 0 or k < 0 or k > n:
        return 0.0
    if p <= 0.0:
        return 1.0 if k == 0 else 0.0
    if p >= 1.0:
        return 1.0 if k == n else 0.0
    return math.comb(n, k) * (p ** k) * ((1.0 - p) ** (n - k))


def _k_rate(k_pct, opponent_k_pct=None):
    if k_pct is None:
        return None
    k_rate = k_pct / 100.0
    if opponent_k_pct is not None:
        k_rate *= (opponent_k_pct / LEAGUE_AVG_K_PCT) * OPPONENT_KRATE_COEF
    return max(0.0, min(1.0, k_rate))


def p_strikeouts_at_least(batters_faced, k_pct, n, *, opponent_k_pct=None):
    """
    Pure. P(K >= n) via a Binomial(round(battersFaced), kRate) tail sum.
    kRate is the pitcher's own kPct; opponent_k_pct (a team-level
    batting strikeout rate against, e.g. a future
    awayTeamStats.get('teamKPct') -- confirmed null on every real
    committed data/slate.json game-side as of this module's
    introduction) scales it proportionally to league average when
    supplied, and is left unadjusted (neutral) when omitted -- never
    guessed.
    """
    if batters_faced is None or k_pct is None:
        return None
    k_rate = _k_rate(k_pct, opponent_k_pct)
    n_trials = max(0, round(batters_faced))
    if n <= 0:
        return 1.0
    return sum(binomial_pmf(k, n_trials, k_rate) for k in range(n, n_trials + 1))


def project_pitcher_workload(*, avg_ip_per_start, k_pct, bb_pct=None, opener=False,
                              tto_split=None, tto_risk=None,
                              recent_workload_restricted=None,
                              opponent_wrc_plus=None, opponent_k_pct=None,
                              max_outs=DEFAULT_MAX_OUTS):
    """
    Pure top-level entry point. Builds the shared survival curve ONCE
    and derives every other figure from it, so a caller pricing both a
    pitcher_outs and a pitcher_strikeouts contract for the SAME
    pitcher/game never computes two independently-drifting workload
    pictures (Requirement 2) -- see lib.kalshi_probability_adapters.
    adapt_pitcher_strikeouts/adapt_pitcher_outs, the one place this is
    wired into the market-pricing path.

    Returns:
        {
            "insufficientWorkloadData": bool,
            "perOutSurvival": list[float] | None,
            "expectedOuts": float | None,
            "expectedBattersFaced": float | None,
            "expectedStrikeouts": float | None,
            "pOutsAtLeast": callable(int) -> float | None,
            "pStrikeoutsAtLeast": callable(int) -> float | None,
            "diagnostics": {...},   # see survival_curve()
        }
    `pOutsAtLeast`/`pStrikeoutsAtLeast` are plain closures over this
    call's own inputs (not memoized state) -- safe to call repeatedly
    for every rung of a market's alternate-line ladder.
    """
    curve, diagnostics = survival_curve(
        avg_ip_per_start, bb_pct=bb_pct, opener=opener,
        tto_split=tto_split, tto_risk=tto_risk,
        recent_workload_restricted=recent_workload_restricted,
        opponent_wrc_plus=opponent_wrc_plus, max_outs=max_outs,
    )
    if curve is None:
        return {
            "insufficientWorkloadData": True,
            "perOutSurvival": None,
            "expectedOuts": None,
            "expectedBattersFaced": None,
            "expectedStrikeouts": None,
            "pOutsAtLeast": lambda n: None,
            "pStrikeoutsAtLeast": lambda n: None,
            "diagnostics": diagnostics,
        }

    exp_outs = expected_outs(curve)
    exp_bf = expected_batters_faced(exp_outs, bb_pct)
    k_rate = _k_rate(k_pct, opponent_k_pct)
    exp_k = (exp_bf * k_rate) if (exp_bf is not None and k_rate is not None) else None

    return {
        "insufficientWorkloadData": False,
        "perOutSurvival": curve,
        "expectedOuts": round(exp_outs, 3),
        "expectedBattersFaced": round(exp_bf, 3),
        "expectedStrikeouts": round(exp_k, 3) if exp_k is not None else None,
        "pOutsAtLeast": lambda n: p_outs_at_least(curve, n),
        "pStrikeoutsAtLeast": lambda n: p_strikeouts_at_least(exp_bf, k_pct, n, opponent_k_pct=opponent_k_pct),
        "diagnostics": diagnostics,
    }
