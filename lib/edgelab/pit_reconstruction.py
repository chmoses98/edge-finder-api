"""
lib/edgelab/pit_reconstruction.py
=====================================
Research Lab Milestone 2: point-in-time (PIT) safe historical
reconstruction of a team's recent completed-game log, and one feature
value built from it (recent bullpen usage).

WHY THIS EXISTS
-------------------
lib.edgelab.bullpen_usage already wraps the MLB Stats API's
/schedule?...&startDate=...&endDate=... and /game/{gamePk}/boxscore
endpoints for RECENT bullpen usage, but every existing caller
(scripts/fetch_bullpen_usage.py) invokes it with "today"-relative dates
for LIVE slate use only -- there is no as-of guard, and nothing in this
repository proves that pointing this same mechanism at a HISTORICAL date
would honestly exclude games on or after that date (Milestone 2's own
"VERIFY DATE BOUNDING... test for look-ahead explicitly" requirement).
This module adds exactly that guard, reusing bullpen_usage's existing
network adapters and pure parsers rather than duplicating them.

LEAKAGE GUARD (double-enforced, matching lib.research.statcast_pitch_store's
own documented pattern)
-------------------------------------------------------------------------
1. The schedule query itself is bounded to end_date = as_of_date minus one
   day, so the MLB Stats API is never even ASKED about as_of_date or later.
2. Independently of what the API/fetcher actually returns, every game is
   re-filtered here to date < as_of_date -- defense in depth, in case a
   fetcher (the real one, or an injected test double) ever returns
   something outside the requested window.

SCOPE
--------
This does not compute a new offense/pitcher/workload feature definition
-- it only proves and packages the as-of reconstruction primitive, plus
one demonstrative feature value (bullpen recent-usage) built entirely
from lib.edgelab.bullpen_usage's own existing pure functions. See
docs/EDGELAB_MILESTONE2_PIT_FEATURE_AUDIT.md for the full audit this
module is evidence for.
"""

from datetime import datetime, timedelta

from lib.edgelab.bullpen_usage import (
    extract_completed_games_for_team,
    extract_relief_appearances,
    fetch_team_boxscore,
    fetch_team_recent_schedule,
    summarize_team_bullpen_usage,
)

DEFAULT_LOOKBACK_DAYS = 21


def _shift_date(date_str, days):
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return (d + timedelta(days=days)).strftime("%Y-%m-%d")


def as_of_completed_team_games(
    team_id,
    as_of_date,
    lookback_days=DEFAULT_LOOKBACK_DAYS,
    schedule_fetcher=fetch_team_recent_schedule,
):
    """
    Returns a list of {"gamePk", "date", "side", "asOfDate"} for every
    COMPLETED game for `team_id` in [as_of_date - lookback_days,
    as_of_date), oldest first -- i.e. every game strictly BEFORE
    as_of_date, never on or after it. `as_of_date`/dates are "YYYY-MM-DD"
    strings (ISO, comparable as plain strings).

    `schedule_fetcher` defaults to
    lib.edgelab.bullpen_usage.fetch_team_recent_schedule (reused, not
    duplicated) and is injectable so this can be tested and used for
    historical replay without a real network call.

    Returns [] for a missing team_id/as_of_date, or when the fetcher/
    schedule yields nothing -- never raises, matching
    extract_completed_games_for_team's own no-guessing contract.
    """
    if not team_id or not as_of_date:
        return []
    end_date = _shift_date(as_of_date, -1)
    start_date = _shift_date(as_of_date, -lookback_days)
    schedule = schedule_fetcher(team_id, start_date, end_date)
    games = extract_completed_games_for_team(schedule, team_id)
    return [
        dict(g, asOfDate=as_of_date)
        for g in games
        if (g.get("date") or "") < as_of_date
    ]


def reconstruct_team_bullpen_usage_as_of(
    team_id,
    as_of_date,
    lookback_days=DEFAULT_LOOKBACK_DAYS,
    schedule_fetcher=fetch_team_recent_schedule,
    boxscore_fetcher=fetch_team_boxscore,
):
    """
    An as-of historical feature value, built entirely from
    lib.edgelab.bullpen_usage's existing pure functions
    (extract_relief_appearances, summarize_team_bullpen_usage) applied
    only to games as_of_completed_team_games() proves are strictly
    before `as_of_date`. `boxscore_fetcher` is never called for a game
    this module's own leakage guard has already excluded -- a leaking
    game's boxscore is never even requested, let alone parsed.

    Returns summarize_team_bullpen_usage's own result shape, plus an
    `asOfRequested` field carrying the boundary this reconstruction used
    (summarize_team_bullpen_usage's own `asOfDate` field keeps its
    existing meaning: the most recent game date actually considered,
    which may be earlier than `as_of_date` itself).
    """
    games = as_of_completed_team_games(team_id, as_of_date, lookback_days, schedule_fetcher)
    games_with_appearances = [
        {
            "date": g["date"],
            "appearances": extract_relief_appearances(boxscore_fetcher(g["gamePk"]), g["side"]),
        }
        for g in games
    ]
    summary = summarize_team_bullpen_usage(games_with_appearances)
    summary["asOfRequested"] = as_of_date
    return summary
