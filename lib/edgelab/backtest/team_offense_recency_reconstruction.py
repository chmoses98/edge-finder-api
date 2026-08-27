"""
MLB-RSCH-0005 PIT reconstruction: team offense recency/form.

No new fetch is needed for this experiment's primary analysis -- every
schedule payload already committed under
data/research_cache/bullpen_backtest/<season>/schedules/ (by MLB-RSCH-0003)
carries both teams' final scores per game
(extract_team_games_from_schedule's additive runsScored/runsAllowed/
opponentTeamId fields). This module reuses that schedule cache read-only,
reuses is_strictly_before() unchanged, and reuses
extract_team_games_from_schedule() unchanged -- no new reconstruction
primitive duplicates what MLB-RSCH-0003 already built and tested.

Fixed, preregistered, non-tuned parameters:
"""
from lib.edgelab.backtest.bullpen_backtest_reconstruction import is_strictly_before

# A row becomes primary-analysis eligible only once its team has this many
# STRICTLY PRIOR completed games this season -- guarantees the largest
# (20-game) recent-form window is always fillable from within-season games
# once a row is eligible at all (no cross-season window mixing needed).
MIN_PRIOR_GAMES_FOR_BASELINE = 20

# Separate, preregistered, fixed hypotheses (H1/H2/H3) -- never optimized
# post hoc to find the best-performing window.
RECENT_FORM_WINDOWS = (5, 10, 20)

OUTCOME_THRESHOLDS = (3, 4, 5)


def prior_games_this_season(team_games, as_of_game):
    """Every game in team_games strictly before as_of_game, per
    is_strictly_before -- includes the target game and any future game
    ONLY to prove they are excluded."""
    return [g for g in team_games if is_strictly_before(g, as_of_game)]


def _mean(values):
    if not values:
        return None
    return sum(values) / len(values)


def season_to_date_rate(prior_games, field):
    """Mean of `field` (runsScored or runsAllowed) across ALL strictly
    prior games this season. None if no prior games (never fabricated)."""
    values = [g[field] for g in prior_games if g.get(field) is not None]
    if not values:
        return None
    return _mean(values)


def recent_form_rate(prior_games, window, field="runsScored"):
    """
    Mean of `field` over the most recent `window` STRICTLY PRIOR games
    this season, using exact prior games only (the last `window` entries
    of prior_games, which the caller already guarantees is
    chronologically sorted and leakage-filtered). None if fewer than
    `window` prior games exist -- never padded/approximated with a
    smaller window.
    """
    if len(prior_games) < window:
        return None
    recent = prior_games[-window:]
    values = [g[field] for g in recent if g.get(field) is not None]
    if len(values) < window:
        return None
    return _mean(values)


def reconstruct_offense_features(team_games, opponent_games, as_of_game, min_prior_games=MIN_PRIOR_GAMES_FOR_BASELINE):
    """
    Pure. team_games/opponent_games must already be sorted chronologically
    (same convention as extract_team_games_from_schedule's own sort).
    Returns None when the team does not yet have `min_prior_games`
    strictly-prior completed games this season (eligibility rule -- not
    approximated with a fabricated baseline, mirrors MLB-RSCH-0003/0004's
    own "first game(s) of season excluded" pattern).

    Every recent-form window (5/10/20) is computed independently and may
    itself be None if that specific window isn't fillable yet -- eligible
    rows always have min_prior_games >= 20, so all three windows are
    guaranteed fillable once the row is eligible at all.
    """
    prior = prior_games_this_season(team_games, as_of_game)
    if len(prior) < min_prior_games:
        return None

    season_baseline = season_to_date_rate(prior, "runsScored")

    opp_prior = prior_games_this_season(opponent_games, as_of_game) if opponent_games else []
    opponent_baseline_runs_allowed = season_to_date_rate(opp_prior, "runsAllowed")

    features = {
        "asOfDate": as_of_game.get("date"),
        "doubleHeader": as_of_game.get("doubleHeader"),
        "gameNumber": as_of_game.get("gameNumber"),
        "priorGamesThisSeason": len(prior),
        "seasonToDateRunsPerGame": season_baseline,
        "opponentSeasonToDateRunsAllowedPerGame": opponent_baseline_runs_allowed,
    }
    for window in RECENT_FORM_WINDOWS:
        rate = recent_form_rate(prior, window)
        deviation = None
        if rate is not None and season_baseline is not None:
            deviation = rate - season_baseline
        features[f"recentFormRate_{window}"] = rate
        features[f"recentFormDeviation_{window}"] = deviation
    return features


def offense_outcome_for_game(target_game):
    """
    Pure. Extracts the NEXT-game outcome directly from the target game's
    own already-cached runsScored -- never a predictor, only ever the
    outcome side of a row. None if runsScored is missing.
    """
    runs = target_game.get("runsScored")
    if runs is None:
        return None
    outcome = {"runsScored": runs, "shutout": runs == 0}
    for threshold in OUTCOME_THRESHOLDS:
        outcome[f"scored{threshold}Plus"] = runs >= threshold
    return outcome
