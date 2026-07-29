#!/usr/bin/env python3
"""
lib/research/three_way_projection.py
=========================================
Model Performance Phase 1 (Market Audit) -- RESEARCH-ONLY canonical
three-way (Away/Tie/Home) result probability functions for the
full-game, F3, F5, and F7 horizons.

WHY THIS MODULE EXISTS
-----------------------
scripts/build_market_ledger.py's existing ML_Away/ML_Home and
F5_ML_Away/F5_ML_Home evaluation already computes a joint
away/home/tie probability via an independent-Poisson double sum
(`p_team_wins()`, confirmed identical math reused here as
`three_way_result_probs()`'s core) -- but it then RENORMALIZES the
away/home win probabilities after discarding the tie:

    p_away_net = p_away_win / (1 - p_push)
    p_home_net = p_home_win / (1 - p_push)

This is defensible ONLY for a market that has no tradable tie outcome
(confirmed via a real, current Kalshi snapshot,
data/kalshi_registry_snapshots/kalshi_search_2026-07-29_0803.json:
every KXMLBGAME event has exactly 2 market tickers, no "-TIE" suffix --
full-game moneyline is a genuine two-way market on Kalshi, because a
tied regulation game always continues into extra innings until a
winner is decided). It is NOT defensible for KXMLBF5, which the SAME
snapshot confirms has exactly 3 market tickers per event, including an
explicit "-TIE" ticker (e.g. KXMLBF5-26JUL292210SEALAD-TIE) -- Kalshi
literally sells a TIE contract for F5, and the current production code
discards that probability mass via renormalization instead of pricing
it. This module's entire purpose is to compute and RETAIN the tie
probability as a first-class output, never discarding or renormalizing
it away, so a future phase can decide whether to price the real TIE
contract -- this phase does not activate anything.

SCOPE / SAFETY
---------------
- Every function in this module is pure: no file I/O, no network, no
  clock reads, no environment reads, no printing, no logging, no
  mutation of any argument, deterministic given deterministic inputs.
- Nothing in this module reads prices, applies eligibility rules,
  applies calibration, or applies bet sizing -- it computes model
  probabilities only.
- Nothing in this module is imported by any production script
  (scripts/build_market_ledger.py, scripts/risk_gate.py,
  scripts/write_pending_bets.py, scripts/protect_slate.py,
  scripts/validate_slate_final.py) as of this phase. It is
  research-only scaffolding.
"""
import math

DEFAULT_MAX_RUNS = 40
# Regulation full-game truncation matches production's existing
# `p_team_wins(max_r=20)` for continuity/comparability, but this module
# defaults higher (40) since sub-inning horizons (F3/F5) have much
# lower means and the SAME max_r used for a 9-inning mean would leave
# more relative tail mass; reporting truncationMass (below) makes the
# actual residual explicit either way rather than assuming a fixed
# truncation point is always "enough."

HORIZON_INNINGS = {
    "F3": 3,
    "F5": 5,
    "F7": 7,
    "full_game": 9,
}


def poisson_pmf(k, lam):
    """
    P(X = k) for a Poisson(lam) random variable. Returns 0.0 for lam
    <= 0 (a team with zero or negative projected runs cannot score a
    positive count and is treated as a point mass at 0 by the caller,
    not by this function itself).
    """
    if lam is None or lam <= 0:
        return 1.0 if k == 0 and lam == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def three_way_result_probs(away_proj, home_proj, max_runs=DEFAULT_MAX_RUNS):
    """
    Pure. Computes P(Away), P(Tie), P(Home) directly from the joint
    independent-Poisson run distribution for a single horizon (full
    game, F3, F5, or F7 -- the caller supplies horizon-scaled
    away_proj/home_proj; this function has no concept of "which
    horizon" and does not need one, since the horizon only affects
    the input means, not the combinatorics).

    Returns:
        {
            "awayWinProb": float,
            "tieProb": float,
            "homeWinProb": float,
            "truncationMass": float,   # 1 - (awayWinProb+tieProb+homeWinProb)
                                        # before the final proportional
                                        # correction below; reported so
                                        # callers can judge whether
                                        # max_runs was adequate
            "maxRuns": int,
            "awayProj": float,
            "homeProj": float,
        }

    Requirements satisfied (Part 5 / Critical Three-Way Market
    Requirement):
      - P(Away) + P(Tie) + P(Home) == 1 within numerical tolerance:
        the three raw sums are computed from the SAME finite grid, so
        residual truncation mass (whatever probability lands outside
        [0, max_runs] x [0, max_runs]) is folded back in proportionally
        rather than silently discarded and rather than being
        arbitrarily assigned to one outcome. This is NOT the same
        error as production's "renormalize away/home after removing
        tie" -- here the tie is retained; only the (typically
        vanishingly small) truncated tail is redistributed, in exact
        proportion to each outcome's own untruncated share, so the tie
        probability's RELATIVE weight versus away/home is unchanged by
        this correction.
      - Tie is computed directly from the score distribution
        (sum of P(away=k)*P(home=k) for k in range), never inferred,
        never treated as a push.
      - Away/Home are NEVER renormalized after removing the tie -- the
        tie stays in the returned probabilities as tieProb, and
        awayWinProb + tieProb + homeWinProb sum to 1 together.
    """
    away_proj = float(away_proj) if away_proj is not None else 0.0
    home_proj = float(home_proj) if home_proj is not None else 0.0

    away_pmf = [poisson_pmf(k, away_proj) for k in range(max_runs + 1)]
    home_pmf = [poisson_pmf(k, home_proj) for k in range(max_runs + 1)]

    p_away = 0.0
    p_tie = 0.0
    p_home = 0.0
    for a in range(max_runs + 1):
        pa = away_pmf[a]
        if pa == 0.0:
            continue
        for h in range(max_runs + 1):
            p = pa * home_pmf[h]
            if p == 0.0:
                continue
            if a > h:
                p_away += p
            elif a == h:
                p_tie += p
            else:
                p_home += p

    raw_total = p_away + p_tie + p_home
    truncation_mass = max(0.0, 1.0 - raw_total)

    if raw_total > 0:
        p_away_corrected = p_away / raw_total
        p_tie_corrected = p_tie / raw_total
        p_home_corrected = p_home / raw_total
    else:
        p_away_corrected = p_tie_corrected = p_home_corrected = 0.0

    return {
        "awayWinProb": p_away_corrected,
        "tieProb": p_tie_corrected,
        "homeWinProb": p_home_corrected,
        "truncationMass": truncation_mass,
        "maxRuns": max_runs,
        "awayProj": away_proj,
        "homeProj": home_proj,
    }


def three_way_result_probs_for_horizon(away_full_proj, home_full_proj, horizon,
                                        max_runs=DEFAULT_MAX_RUNS,
                                        scale_fn=None):
    """
    Pure. Convenience wrapper that scales a full-game projection down
    to the requested horizon ("F3", "F5", "F7", or "full_game") before
    calling three_way_result_probs(). `scale_fn`, if provided, must be
    a pure function (away_full_proj, home_full_proj, innings) ->
    (away_horizon_proj, home_horizon_proj); if omitted, a naive
    innings-fraction scale (horizon_innings / 9) is used as a
    RESEARCH-ONLY placeholder -- NOT a claim that naive linear scaling
    is the right model. Production's actual F5 scaling
    (scripts/build_market_ledger.py lines ~393-397) is materially more
    sophisticated (starter workload, xFIP, times-through-order,
    park), which is exactly why this function accepts an injectable
    scale_fn rather than hardcoding the naive fraction as the only
    option -- callers wanting production-realistic F5/F3/F7 means
    should supply their own scale_fn built from those same inputs.
    """
    if horizon not in HORIZON_INNINGS:
        raise ValueError(f"unknown horizon {horizon!r}; expected one of {sorted(HORIZON_INNINGS)}")

    innings = HORIZON_INNINGS[horizon]
    if scale_fn is not None:
        away_h, home_h = scale_fn(away_full_proj, home_full_proj, innings)
    else:
        fraction = innings / 9.0
        away_h = float(away_full_proj) * fraction if away_full_proj is not None else 0.0
        home_h = float(home_full_proj) * fraction if home_full_proj is not None else 0.0

    result = three_way_result_probs(away_h, home_h, max_runs=max_runs)
    result["horizon"] = horizon
    result["horizonInnings"] = innings
    return result


def assert_probabilities_valid(result, tolerance=1e-9):
    """
    Pure validation helper (research/test use, not itself a gate on
    anything production). Raises AssertionError if
    awayWinProb+tieProb+homeWinProb does not sum to 1 within
    tolerance, or if any probability is negative or > 1.
    """
    total = result["awayWinProb"] + result["tieProb"] + result["homeWinProb"]
    if abs(total - 1.0) > tolerance:
        raise AssertionError(f"three-way probabilities sum to {total!r}, not 1 (tolerance={tolerance})")
    for key in ("awayWinProb", "tieProb", "homeWinProb"):
        v = result[key]
        if v < -tolerance or v > 1 + tolerance:
            raise AssertionError(f"{key}={v!r} out of [0, 1] range")
    return True
