"""
lib/edgelab/backtest/proxy_enrichment.py
====================================================================
MLB-RSCH-0009: PIT-safe enrichment components for MLB-RSCH-0008's
simple baseball proxy (M0 = team_baseline + expected_runs +
home_field_adjustment, unchanged). Preregistered ablation sequence,
never reordered based on results:

  M0: MLB-RSCH-0008's simple proxy, unchanged.
  M1: M0 + stabilized (shrinkage) offense baseline.
  M2: M1 + starting-pitcher quality (CONDITIONAL on
      scripts/edgelab/backtest/probe_starter_identity_pit_safety.py's
      real verdict -- built only if historical starter IDENTITY is
      PIT-safe at scale; otherwise this component is UNAVAILABLE, not
      merely rejected on performance, and M2 == M1).
  M3: M2 + bullpen quality.
  M4: M3 + park factor / season run-environment.

Every fixed constant below (*_SHRINKAGE_K, *_BLEND_WEIGHT,
PARK_MIN_DEV_GAMES) is a PREREGISTERED, FIXED hyperparameter chosen
before any real result was computed -- never grid-searched, never
tuned against Pinnacle or holdout performance ("no coefficient
hunting"). The only numbers this module fits from data are CLOSED-FORM
means over DEVELOPMENT rows only (league-average runs/game,
league-average bullpen ER/9, per-venue park indices) -- the exact same
discipline lib.edgelab.backtest.proxy_model.fit_home_field_adjustment
already established: fit once, frozen, reused unchanged for
validation/holdout.

REUSES, DOES NOT REIMPLEMENT:
  - lib.edgelab.backtest.bullpen_backtest_reconstruction's
    is_strictly_before/prior_games/relief_outcome_for_game (MLB-RSCH-0003).
  - lib.edgelab.backtest.team_offense_recency_reconstruction's
    prior_games_this_season/season_to_date_rate/MIN_PRIOR_GAMES_FOR_BASELINE
    (MLB-RSCH-0005) -- applied here to NEW fields (bullpen ER/9 per
    game), the same reuse pattern MLB-RSCH-0008's team_baseline already
    established for runsScored/runsAllowed.
  - lib.edgelab.backtest.proxy_model's team_baseline/expected_runs
    remain UNCHANGED -- this module only produces alternative baseline
    dicts (same {offenseRunsPerGame, runPreventionRunsAllowedPerGame,
    priorGamesThisSeason} shape) and a post-hoc runs multiplier, both
    designed to feed into expected_runs()/game_ml_proxy_probability()/
    game_total_proxy_probability() unmodified.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab.backtest.bullpen_backtest_reconstruction import is_strictly_before  # noqa: F401  (re-exported for callers)
from lib.edgelab.backtest.team_offense_recency_reconstruction import (
    prior_games_this_season,
    season_to_date_rate,
    MIN_PRIOR_GAMES_FOR_BASELINE,
)

# ── M1: stabilized (shrinkage) offense baseline ─────────────────────────

# Preregistered, fixed -- NOT fit. Roughly 1.5x MIN_PRIOR_GAMES_FOR_BASELINE
# (20), so a team at the eligibility floor is still meaningfully shrunk
# toward league average, while a team with a near-full season of games
# is barely shrunk at all (standard empirical-Bayes stabilization shape).
OFFENSE_SHRINKAGE_K = 30


def fit_league_average_runs_per_game(development_team_games_by_team):
    """
    Closed-form, DEVELOPMENT-only, fit once and frozen (same discipline
    as fit_home_field_adjustment). `development_team_games_by_team`:
    an iterable of team-game lists (one list per team, MLB-RSCH-0003's
    extract_team_games_from_schedule output), development seasons only.
    Returns the mean runsScored across every team-game, or None if empty.
    """
    values = []
    for team_games in development_team_games_by_team:
        for g in team_games:
            if g.get("runsScored") is not None:
                values.append(g["runsScored"])
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def stabilized_offense_rate(raw_rate, prior_game_count, league_avg, k=OFFENSE_SHRINKAGE_K):
    """
    Pure. Empirical-Bayes shrinkage toward a FIXED, dev-frozen league
    average: shrunk = (raw*n + league_avg*k) / (n + k). Returns
    raw_rate unchanged if either raw_rate or league_avg is unavailable
    (never fabricates a value from missing inputs).
    """
    if raw_rate is None or league_avg is None or prior_game_count is None:
        return raw_rate
    return round((raw_rate * prior_game_count + league_avg * k) / (prior_game_count + k), 4)


# ── M3: bullpen quality ─────────────────────────────────────────────────

BULLPEN_SHRINKAGE_K = 30
# Fixed preregistered blend weight between the run-prevention rate M0
# already computes (season-to-date runs allowed/game, a TEAM-level,
# offense-and-defense-blended number) and the bullpen-specific quality
# signal this component adds. 0.5 = equal weight, chosen before any
# result was inspected -- never tuned.
BULLPEN_BLEND_WEIGHT = 0.5


def team_relief_er9_games(team_games, relief_outcomes_by_game_pk):
    """
    Pure. `team_games`: one team's extract_team_games_from_schedule()
    output (has "date"/"gameNumber"/"gamePk", already sorted).
    `relief_outcomes_by_game_pk`: {gamePk: relief_outcome_for_game()
    output} for this team's SIDE of each game. Returns a new list, same
    date/gameNumber ordering, each entry carrying a "reliefEarnedRunsPer9"
    field (None for a complete-game start -- zero relief innings means
    ER/9 is undefined, not zero, so it is excluded from any average
    rather than silently treated as a shutout bullpen performance).
    Shaped so season_to_date_rate() (MLB-RSCH-0005, reused unchanged)
    can be called directly on the result with field="reliefEarnedRunsPer9".
    """
    out = []
    for g in team_games:
        outcome = relief_outcomes_by_game_pk.get(g.get("gamePk"))
        er9 = None
        if outcome is not None:
            outs, er = outcome.get("bullpenOuts"), outcome.get("reliefEarnedRunsAllowed")
            if outs and er is not None:
                er9 = round(er / outs * 27, 4)
        out.append({**g, "reliefEarnedRunsPer9": er9})
    return out


def bullpen_quality_baseline(relief_er9_games, as_of_game, min_prior_games=MIN_PRIOR_GAMES_FOR_BASELINE):
    """
    Pure. Reuses MLB-RSCH-0005's prior_games_this_season/
    season_to_date_rate UNCHANGED, applied to this component's own
    "reliefEarnedRunsPer9" field. Eligibility requires
    min_prior_games games with a DEFINED (non-None) relief ER/9 --
    NOT merely min_prior_games games played (a team whose starters
    mostly throw complete games would otherwise look falsely
    "eligible" off very few real bullpen appearances).
    """
    prior = prior_games_this_season(relief_er9_games, as_of_game)
    defined = [g for g in prior if g.get("reliefEarnedRunsPer9") is not None]
    if len(defined) < min_prior_games:
        return None
    rate = season_to_date_rate(defined, "reliefEarnedRunsPer9")
    if rate is None:
        return None
    return {"bullpenEarnedRunsPer9": rate, "priorGamesWithBullpenData": len(defined)}


def fit_league_average_bullpen_er9(development_relief_er9_games_by_team):
    """Closed-form, DEVELOPMENT-only, fit once and frozen. Mean of every
    defined per-game reliefEarnedRunsPer9 value across development."""
    values = []
    for team_games in development_relief_er9_games_by_team:
        for g in team_games:
            if g.get("reliefEarnedRunsPer9") is not None:
                values.append(g["reliefEarnedRunsPer9"])
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def stabilized_bullpen_rate(raw_rate, prior_game_count, league_avg, k=BULLPEN_SHRINKAGE_K):
    """Pure. Same shrinkage shape as stabilized_offense_rate, applied to
    bullpen ER/9 -- a SEPARATE fixed constant (BULLPEN_SHRINKAGE_K), not
    reused from OFFENSE_SHRINKAGE_K, since the two are different
    quantities on different scales even though the current preregistered
    values happen to match."""
    if raw_rate is None or league_avg is None or prior_game_count is None:
        return raw_rate
    return round((raw_rate * prior_game_count + league_avg * k) / (prior_game_count + k), 4)


def blend_run_prevention_with_bullpen_quality(base_run_prevention_runs_per_game, stabilized_bullpen_er9, weight=BULLPEN_BLEND_WEIGHT):
    """
    Pure. Converts the bullpen's ER/9 (a per-9-innings rate) onto the
    SAME runs-per-game scale as base_run_prevention_runs_per_game via a
    fixed 9-inning game assumption, then blends the two with a fixed
    preregistered weight. Returns base_run_prevention_runs_per_game
    unchanged if the bullpen component is unavailable (never fabricates
    a blended value from a missing input).
    """
    if base_run_prevention_runs_per_game is None:
        return None
    if stabilized_bullpen_er9 is None:
        return base_run_prevention_runs_per_game
    return round(
        (1 - weight) * base_run_prevention_runs_per_game + weight * stabilized_bullpen_er9, 4
    )


# ── M4: park factor / season run environment ────────────────────────────

# A venue's development-era park index is trusted only with at least
# this many development home-games at that venue -- mirrors
# lib.research.park_factor_derivation's own MIN_PARK_PA_FOR_FACTOR
# precedent ("a single small sample must not produce a confident-
# looking index"), reapplied at game-count rather than PA-count scale.
PARK_MIN_DEV_GAMES = 100


def extract_team_games_with_venue(schedule, team_id):
    """
    Pure. Like bullpen_backtest_reconstruction.extract_team_games_from_schedule,
    but additionally carries the game's venue id/name -- already present,
    unused, in the raw cached schedule JSON (zero new fetch). Deliberately
    a separate function (not a modification of the MLB-RSCH-0003 original)
    for the same reason extract_pitcher_lines' own docstring gives for its
    own additive extensions: existing callers of the original function are
    unaffected.
    """
    from lib.edgelab import bullpen_usage
    from lib.edgelab.player_stats import parse_nonnegative_int

    if not schedule:
        return []
    games = []
    for day in schedule.get("dates") or []:
        for g in day.get("games") or []:
            status = (g.get("status") or {}).get("detailedState")
            if status not in bullpen_usage.COMPLETED_STATUSES:
                continue
            teams = g.get("teams") or {}
            away_id = ((teams.get("away") or {}).get("team") or {}).get("id")
            home_id = ((teams.get("home") or {}).get("team") or {}).get("id")
            if away_id == team_id:
                side = "away"
            elif home_id == team_id:
                side = "home"
            else:
                continue
            own_block = teams.get(side) or {}
            opp_side = "home" if side == "away" else "away"
            opp_block = teams.get(opp_side) or {}
            venue = g.get("venue") or {}
            games.append({
                "gamePk": g.get("gamePk"),
                "date": day.get("date"),
                "side": side,
                "doubleHeader": g.get("doubleHeader"),
                "gameNumber": g.get("gameNumber"),
                "runsScored": parse_nonnegative_int(own_block.get("score")),
                "runsAllowed": parse_nonnegative_int(opp_block.get("score")),
                "opponentTeamId": (opp_block.get("team") or {}).get("id"),
                "venueId": venue.get("id"),
                "venueName": venue.get("name"),
            })
    games.sort(key=lambda g: (g["date"] or "", g.get("gameNumber") or 1, g["gamePk"] or 0))
    return games


def fit_park_factors(development_home_games, min_dev_games=PARK_MIN_DEV_GAMES):
    """
    Closed-form, DEVELOPMENT-only, fit once and frozen. `development_home_games`:
    every development-era game where the team WAS the home side (so
    runsScored+runsAllowed together is that game's total runs at that
    venue), each carrying "venueId"/"runsScored"/"runsAllowed". Returns
    {venueId: {"gamesUsed", "meanTotalRunsAtVenue", "parkRunIndex"}} --
    100 = development-era league average, same convention as
    api/slate.js's existing static parkFactor and
    lib.research.park_factor_derivation's runFactor. A venue with fewer
    than min_dev_games qualifying development home-games is OMITTED
    entirely (never a confident-looking index off a tiny sample).
    """
    by_venue = {}
    all_totals = []
    for g in development_home_games:
        rs, ra = g.get("runsScored"), g.get("runsAllowed")
        venue_id = g.get("venueId")
        if rs is None or ra is None or venue_id is None:
            continue
        total = rs + ra
        all_totals.append(total)
        by_venue.setdefault(venue_id, []).append(total)

    if not all_totals:
        return {}
    league_avg = sum(all_totals) / len(all_totals)
    if league_avg == 0:
        return {}

    result = {}
    for venue_id, totals in by_venue.items():
        if len(totals) < min_dev_games:
            continue
        mean_total = sum(totals) / len(totals)
        result[venue_id] = {
            "gamesUsed": len(totals),
            "meanTotalRunsAtVenue": round(mean_total, 4),
            "parkRunIndex": round(100.0 * mean_total / league_avg, 1),
        }
    return result


def season_run_environment(all_teams_games_by_season_as_of_date, as_of_date):
    """
    Pure. `all_teams_games_by_season_as_of_date`: every team's game list
    for ONE season (the same season as as_of_date; caller is responsible
    for season scoping, same convention team_baseline's own callers
    already follow). Returns the league-wide mean total runs/game across
    every STRICTLY PRIOR completed game (any team, that same season, date
    < as_of_date) -- None if no qualifying games yet. Deliberately date-
    filtered directly (not via is_strictly_before/gameNumber, since this
    aggregates ACROSS many different games/teams on possibly the same
    date as as_of_date, where per-game gameNumber ordering is meaningless
    league-wide) -- excludes same-date games entirely, the conservative
    choice consistent with is_strictly_before's own same-date handling.
    """
    totals = []
    for team_games in all_teams_games_by_season_as_of_date:
        for g in team_games:
            if g.get("side") != "home":
                continue  # count each game's total runs exactly once, from its home side
            if (g.get("date") or "") >= as_of_date:
                continue
            rs, ra = g.get("runsScored"), g.get("runsAllowed")
            if rs is None or ra is None:
                continue
            totals.append(rs + ra)
    if not totals:
        return None
    return round(sum(totals) / len(totals), 4)


def fit_reference_season_run_environment(development_home_games):
    """Closed-form, DEVELOPMENT-only, fit once and frozen: the mean
    total runs/game across ALL development-era games (every season
    pooled) -- the fixed reference every season's own OWN season-to-date
    run environment is indexed against."""
    totals = [
        (g["runsScored"] + g["runsAllowed"])
        for g in development_home_games
        if g.get("runsScored") is not None and g.get("runsAllowed") is not None
    ]
    if not totals:
        return None
    return round(sum(totals) / len(totals), 4)


def park_and_environment_multiplier(venue_id, park_factors, season_env_runs_per_game, reference_env_runs_per_game):
    """
    Pure. Combines the frozen per-venue park index (from fit_park_factors,
    100 = development-league-average) with the season's OWN run
    environment relative to the frozen development-era reference (also
    100 = development-league-average) into ONE multiplicative scalar
    applied identically to both teams' expected runs (the park and the
    season's scoring level affect both sides of the same game equally).
    Returns 1.0 (no adjustment) if either input is unavailable -- a
    missing/undersampled venue or an as-yet-unmeasured season environment
    must never silently distort expected runs.
    """
    park_index = (park_factors.get(venue_id) or {}).get("parkRunIndex") if park_factors else None
    if park_index is None or season_env_runs_per_game is None or not reference_env_runs_per_game:
        return 1.0
    season_index = 100.0 * season_env_runs_per_game / reference_env_runs_per_game
    return round((park_index / 100.0) * (season_index / 100.0), 6)


def apply_runs_multiplier(expected_home_runs, expected_away_runs, multiplier):
    """Pure. Applies ONE combined multiplier to both sides' expected
    runs -- never asymmetric, since park/season environment affects both
    teams playing the same game equally."""
    if expected_home_runs is None or expected_away_runs is None:
        return None, None
    return round(expected_home_runs * multiplier, 4), round(expected_away_runs * multiplier, 4)
