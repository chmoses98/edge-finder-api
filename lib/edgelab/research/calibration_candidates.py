"""
lib/edgelab/research/calibration_candidates.py
===============================================
Candidate probability transforms for the calibration research programme.
RESEARCH ONLY.

Two kinds of candidate live here:

1. STRUCTURAL (no parameters fit on this data): re-pricing every run-based
   contract from the SAME frozen projections production used, replacing the
   independent-Poisson run distribution with the independent negative-binomial
   distribution whose single overdispersion parameter (0.281513) was fit by
   MLB-RSCH-0010 on 2022-2024 development seasons and confirmed on the 2025
   validation and locked 2026 holdout seasons.  Nothing about it has seen an
   August-2026 Kalshi outcome.  `poisson_probability()` reproduces production's
   own adapters exactly (used as a round-trip check on the projection join).

2. PARAMETRIC MAPS (fit inside the walk-forward harness, never here):
   logit-affine (Platt), beta calibration, isotonic, market blends -- see
   lib/edgelab/research/calibration_analysis.py.
"""
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab.backtest.run_distributions import negative_binomial_pmf  # noqa: E402
from scripts.build_market_ledger import poisson_pmf  # noqa: E402

FROZEN_NB_DISPERSION = 0.281513   # MLB-RSCH-0010, dev-fit 2022-2024, never refit
MAX_R = 30


def _pmf_vec(mean, dispersion, max_r=MAX_R):
    if mean is None or mean <= 0:
        return None
    if dispersion is None or dispersion <= 0:
        v = [poisson_pmf(k, mean) for k in range(max_r + 1)]
    else:
        v = [negative_binomial_pmf(k, mean, dispersion) for k in range(max_r + 1)]
    return v


def _three_way(pa, ph):
    """(P(away>home), P(tie), P(home>away)) from two marginal pmfs, truncation mass folded in proportionally."""
    away = tie = home = 0.0
    for a, pa_ in enumerate(pa):
        for h, ph_ in enumerate(ph):
            p = pa_ * ph_
            if a > h:
                away += p
            elif a == h:
                tie += p
            else:
                home += p
    s = away + tie + home
    if s <= 0:
        return None, None, None
    return away / s, tie / s, home / s


def _tail_over(pmf, line):
    """P(X > line) for integer/half-integer line."""
    return sum(p for k, p in enumerate(pmf) if k > line)


def _margin_over(p_team, p_opp, margin):
    return sum(pt * po for a, pt in enumerate(p_team) for h, po in enumerate(p_opp) if a - h > margin)


_PERIOD_INNINGS = {"F3": 3.0, "F5": 5.0, "F7": 7.0, "full_game": 9.0}


def _period_projs(row, game, period, mean_shift=0.0):
    """Team run means for the contract's horizon; mean_shift (runs per team per 9 innings) is scaled by innings."""
    if period == "F5":
        a, h = game.get("ctxF5AwayProj"), game.get("ctxF5HomeProj")
    elif period in ("F3", "F7"):
        a, h = game.get("ctx%sAwayProj" % period), game.get("ctx%sHomeProj" % period)
    else:
        a, h = game.get("ctxAwayProjRuns"), game.get("ctxHomeProjRuns")
    if a is None or h is None:
        return None, None
    sh = mean_shift * _PERIOD_INNINGS.get(period, 9.0) / 9.0
    return max(0.05, a + sh), max(0.05, h + sh)


def contract_probability(row, game, dispersion, mean_shift=0.0):
    """
    Fair YES probability for an Engine-B contract row from the game's replayed
    projection context, under an independent count model with the given
    dispersion (0 => Poisson == production; FROZEN_NB_DISPERSION => candidate).
    Returns None for families this module does not price (pitcher props).
    """
    fam, period = row.get("family"), row.get("period") or "full_game"
    side, line = row.get("contractSide"), row.get("line")
    away, home = _period_projs(row, game, period, mean_shift)
    if away is None or home is None:
        return None
    pa, ph = _pmf_vec(away, dispersion), _pmf_vec(home, dispersion)
    if pa is None or ph is None:
        return None
    if fam == "game_result":
        a, t, h = _three_way(pa, ph)
        if a is None:
            return None
        denom = 1 - t
        return (a / denom) if side == "Away" else (h / denom) if side == "Home" else None
    if fam == "inning_result":
        a, t, h = _three_way(pa, ph)
        return {"Away": a, "Tie": t, "Home": h}.get(side)
    if fam in ("game_total", "inning_total"):
        if line is None:
            return None
        # production: p_over_total(total_proj, line - 1) on the SUMMED projection
        # (one Poisson with mean away+home). Under NB the sum of two independent
        # NBs is not NB; use the convolution of the two team marginals instead --
        # for dispersion=0 the convolution of Poissons is exactly the summed Poisson.
        tot = [0.0] * (2 * MAX_R + 1)
        for a, pa_ in enumerate(pa):
            for h, ph_ in enumerate(ph):
                tot[a + h] += pa_ * ph_
        p_over = sum(p for k, p in enumerate(tot) if k > (line - 1))
        return p_over if side in ("Over", None) else (1 - p_over)
    if fam == "team_total":
        team = row.get("subjectId")
        pmf = pa if team == row.get("awayTeam") else ph if team == row.get("homeTeam") else None
        if pmf is None or line is None:
            return None
        p_over = _tail_over(pmf, line)
        return p_over if side in ("Over", None) else (1 - p_over)
    if fam == "winning_margin":
        team = row.get("subjectId")
        if line is None:
            return None
        if team == row.get("awayTeam"):
            return _margin_over(pa, ph, line)
        if team == row.get("homeTeam"):
            return _margin_over(ph, pa, line)
        return None
    if fam == "first_inning_run":
        # production: naive proj/9 first-inning lambda per team, P(YRFI) = 1 - P(0)P(0)
        la, lh = away / 9.0, home / 9.0
        p0a = _pmf_vec(la, dispersion, 5)[0]
        p0h = _pmf_vec(lh, dispersion, 5)[0]
        p_yrfi = 1 - p0a * p0h
        return p_yrfi if side in ("Yes", None) else (1 - p_yrfi)
    return None


def poisson_probability(row, game):
    return contract_probability(row, game, 0.0)


def nb_probability(row, game, dispersion=FROZEN_NB_DISPERSION, mean_shift=0.0):
    return contract_probability(row, game, dispersion, mean_shift)


MEAN_SHIFT_GRID = (0.0, 0.15, 0.30, 0.45)   # runs per team per 9 innings, selected on training dates only
