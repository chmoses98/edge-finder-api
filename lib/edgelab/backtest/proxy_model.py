"""
lib/edgelab/backtest/proxy_model.py
====================================================================
MLB-RSCH-0008 proxy model: the simplest defensible PIT-safe historical
research model. EXPLICITLY LABELED as a historical research proxy --
never a claim of reproducing production's actual historical
probability (Milestone 2's PIT audit found season-aggregate team
offense/starter quality/bullpen talent UNAVAILABLE_HISTORICALLY for
every market family; this proxy is built entirely from the
reconstructable components MLB-RSCH-0003/0004/0005 already proved
PIT-safe, per the historical sharp-market audit's own §6 classification
of every family as C. PROXY_MODEL_POSSIBLE, not A. EXACT_PIT_
RECONSTRUCTABLE).

REUSES, DOES NOT REIMPLEMENT:
  - lib.edgelab.backtest.team_offense_recency_reconstruction's
    prior_games_this_season / season_to_date_rate (MLB-RSCH-0005's own
    PIT-safe team scoring/run-prevention baseline primitive) for BOTH
    the offense and run-prevention component of both the ML and total
    proxy.
  - scripts.build_market_ledger's poisson_pmf / p_team_wins /
    p_over_total, imported UNCHANGED -- the exact same reuse MLB-
    RSCH-0002 already established for this exact module (see that
    experiment's own module docstring: "Imports and calls scripts/
    build_market_ledger.py's compute_projections()/p_team_wins()/
    p_over_total() ... UNCHANGED -- never modifies, monkeypatches, or
    reimplements"). p_team_wins(home_proj, away_proj) turns two
    expected-runs values into a Poisson-based win probability;
    p_over_total(total_proj, line) turns a total-runs expectation into
    an Over probability at an EXACT line -- both pure, stateless math,
    zero production-state coupling, safe to call from research.

ONE genuinely dev-fit parameter (mission: "Use development only for
coefficients... No coefficient hunting"): HOME_FIELD_RUNS_ADJUSTMENT,
a single additive runs bump to the home team's expected runs, fit by
closed-form mean-residual calibration on DEVELOPMENT rows only (see
fit_home_field_adjustment), then frozen and reused UNCHANGED for
validation/holdout -- never refit per split.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_SCRIPTS_DIR = os.path.join(_ROOT, "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from lib.edgelab.backtest.team_offense_recency_reconstruction import (
    prior_games_this_season,
    season_to_date_rate,
    MIN_PRIOR_GAMES_FOR_BASELINE,
)
from build_market_ledger import poisson_pmf, p_team_wins, p_over_total  # noqa: E402


def team_baseline(team_games, as_of_game, min_prior_games=MIN_PRIOR_GAMES_FOR_BASELINE):
    """Pure. Reuses MLB-RSCH-0005's own eligibility rule (>= 20 strictly-
    prior completed games this season) and season_to_date_rate for both
    runsScored (offense) and runsAllowed (run prevention). Returns None
    if the team isn't yet eligible this season -- never approximated."""
    prior = prior_games_this_season(team_games, as_of_game)
    if len(prior) < min_prior_games:
        return None
    return {
        "offenseRunsPerGame": season_to_date_rate(prior, "runsScored"),
        "runPreventionRunsAllowedPerGame": season_to_date_rate(prior, "runsAllowed"),
        "priorGamesThisSeason": len(prior),
    }


def expected_runs(home_baseline, away_baseline, home_field_adjustment=0.0):
    """
    Pure. Standard sabermetric combination -- a team's expected runs in
    THIS game is the average of (its own season scoring rate) and (the
    opponent's season run-allowed rate); mission: "team season-to-date
    scoring strength... opponent context." home_field_adjustment (a
    single, frozen, dev-fit or fixed constant -- see module docstring)
    is added to the home team's expected runs only. Returns
    (expected_home_runs, expected_away_runs) or (None, None) if either
    baseline is unavailable.
    """
    if home_baseline is None or away_baseline is None:
        return None, None
    expected_home = (home_baseline["offenseRunsPerGame"] + away_baseline["runPreventionRunsAllowedPerGame"]) / 2.0 + home_field_adjustment
    expected_away = (away_baseline["offenseRunsPerGame"] + home_baseline["runPreventionRunsAllowedPerGame"]) / 2.0
    return round(expected_home, 4), round(expected_away, 4)


def game_ml_proxy_probability(expected_home_runs, expected_away_runs):
    """Reuses p_team_wins UNCHANGED. Returns (homeWinProb, pushProb) or
    (None, None) if either expectation is missing."""
    if expected_home_runs is None or expected_away_runs is None:
        return None, None
    home_win_prob, push_prob = p_team_wins(expected_home_runs, expected_away_runs)
    return round(home_win_prob, 6), round(push_prob, 6)


def game_total_proxy_probability(expected_home_runs, expected_away_runs, line):
    """Reuses p_over_total UNCHANGED, applied at the market's own EXACT
    line (mission: "Only exact-line comparisons are eligible"). Returns
    P(Over line) or None if inputs are missing. p_over_total expects an
    integer line (Kalshi-style, floors internally) -- MLB totals lines
    are always X.5, so int(line) is exact, never a rounding
    approximation across a real threshold."""
    if expected_home_runs is None or expected_away_runs is None or line is None:
        return None
    total_expected = expected_home_runs + expected_away_runs
    return round(p_over_total(total_expected, line), 6)


def fit_home_field_adjustment(development_rows):
    """
    Closed-form, single-parameter, non-iterative calibration (mission:
    "Use development only for coefficients... No coefficient hunting"):
    the mean residual between actual home-minus-away runs and the
    NAIVE (no home-field term) expected home-minus-away runs, averaged
    over development rows only. Frozen by the caller and reused
    UNCHANGED for validation/holdout -- this function is never called
    again after development. Returns 0.0 (no adjustment) if there are
    no usable rows, never a fabricated nonzero value.
    """
    residuals = []
    for row in development_rows:
        home_b, away_b = row.get("homeBaseline"), row.get("awayBaseline")
        actual_home_runs, actual_away_runs = row.get("actualHomeRuns"), row.get("actualAwayRuns")
        if not home_b or not away_b or actual_home_runs is None or actual_away_runs is None:
            continue
        naive_home, naive_away = expected_runs(home_b, away_b, home_field_adjustment=0.0)
        if naive_home is None:
            continue
        actual_diff = actual_home_runs - actual_away_runs
        naive_diff = naive_home - naive_away
        residuals.append(actual_diff - naive_diff)
    if not residuals:
        return 0.0
    # The residual is a DIFFERENCE (home minus away); home_field_adjustment
    # is applied to the home side only, so it should absorb the full
    # mean differential residual, not half of it.
    return round(sum(residuals) / len(residuals), 4)
