"""
lib/edgelab/backtest/bullpen_backtest_reconstruction.py
============================================================
Research Lab MLB-RSCH-0003: pure, deterministic PIT-safe feature and
outcome reconstruction for the multi-season bullpen workload backtest.

No network I/O here -- this module operates entirely on already-cached
raw MLB Stats API responses (see scripts/edgelab/backtest/
fetch_mlb_multiseason_bullpen_cache.py for the network adapter).

REUSE, NOT DUPLICATION
--------------------------
The PRIMARY feature specification (the "current production formula
check", spec section 8) is built by reusing, unchanged:
  - lib.edgelab.bullpen_usage.extract_relief_appearances (one game's
    relief-pitcher appearances)
  - lib.edgelab.bullpen_usage.summarize_team_bullpen_usage (the exact
    recentUsage-shaped aggregate compute_bullpen_workload_adjustment
    expects)
  - lib.edgelab.bullpen_availability.compute_bullpen_workload_adjustment
    (the production multiplier itself)
No part of this module reimplements any of the above -- it only builds
their INPUTS from cached historical data and calls them exactly as
production does.

The EXPLORATORY calendar-day-based features (spec section 3's explicit
list: pitches/relievers "previous calendar day", "previous 2 days",
etc.) are NEW reconstruction logic, since compute_bullpen_workload_
adjustment's own inputs are windowed by GAME adjacency, not calendar
adjacency. These are clearly labeled exploratory in every report this
milestone produces -- the reused production formula above remains the
primary specification per the mission's own instruction.

LEAKAGE DISCIPLINE
----------------------
Every function here that accepts "a team's games" accepts ALL of that
team's games (past, the target game itself, and future games) and
filters internally via is_strictly_before() -- never trusts a caller to
have pre-filtered. This is deliberate defense in depth: a bug in an
orchestration script that forgets to filter can never leak a future or
same-game result into a feature. is_strictly_before() also handles
same-date doubleheader ordering via gameNumber (game 1 legitimately
precedes game 2 on the same calendar date) -- a same-date game with an
unknown/missing gameNumber on either side is NEVER treated as prior
(conservative default, matching the mission's "where timestamps
permit" instruction).
"""

from datetime import datetime, timedelta

from lib.edgelab import bullpen_usage
from lib.edgelab.bullpen_usage import extract_relief_appearances, summarize_team_bullpen_usage
from lib.edgelab.bullpen_availability import compute_bullpen_workload_adjustment
from lib.edgelab.player_stats import parse_nonnegative_int

DEFAULT_LOOKBACK_DAYS = 21


def _shift_date(date_str, days):
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return (d + timedelta(days=days)).strftime("%Y-%m-%d")


def is_strictly_before(candidate_game, as_of_game):
    """
    True iff candidate_game causally precedes as_of_game. Date first;
    on a tie (same calendar date -- a doubleheader), only a strictly
    smaller, KNOWN gameNumber counts as prior. A same-date game with a
    missing gameNumber on either side is never treated as prior --
    "where timestamps permit" (mission spec) means exactly this: order
    by gameNumber only when it is actually known, never guess.
    """
    cand_date, as_of_date = candidate_game.get("date"), as_of_game.get("date")
    if cand_date is None or as_of_date is None:
        return False
    if cand_date < as_of_date:
        return True
    if cand_date > as_of_date:
        return False
    cand_gn, as_of_gn = candidate_game.get("gameNumber"), as_of_game.get("gameNumber")
    if cand_gn is None or as_of_gn is None:
        return False
    return cand_gn < as_of_gn


def prior_games(all_team_games, as_of_game):
    """Every game in all_team_games strictly before as_of_game, per
    is_strictly_before -- includes the target game and any future game
    ONLY to prove they are excluded (callers should pass the team's
    full game list; this function never leaks the excluded ones)."""
    return [g for g in all_team_games if is_strictly_before(g, as_of_game)]


def extract_team_games_from_schedule(schedule, team_id):
    """
    Pure. Like lib.edgelab.bullpen_usage.extract_completed_games_for_team,
    but also carries doubleHeader/gameNumber -- this backtest's
    doubleheader-context feature and its same-date ordering
    (is_strictly_before) both need them, and the production-live
    function that summarizes TODAY's usage has no need for them
    (deliberately not modified there -- see this module's own
    docstring's reuse-not-duplication section for why the two functions
    stay separate rather than one gaining an unused field for the
    other's sake).
    """
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
            games.append({
                "gamePk": g.get("gamePk"),
                "date": day.get("date"),
                "side": side,
                "doubleHeader": g.get("doubleHeader"),
                "gameNumber": g.get("gameNumber"),
            })
    games.sort(key=lambda g: (g["date"] or "", g.get("gameNumber") or 1, g["gamePk"] or 0))
    return games


# ── Extraction (boxscore -> pitcher lines, including outcome-only fields) ──

def extract_pitcher_lines(boxscore, side):
    """
    Pure. Every pitcher for one team side in one game's boxscore,
    starter included (orderIndex 0), parallel to lib.edgelab.
    bullpen_usage.extract_relief_appearances but carrying TWO fields
    that function deliberately omits (it is scoped to the production
    RECENT-USAGE predictive feature, never an outcome): runs and
    earnedRuns. Never used as a predictive input -- see
    relief_outcome_for_game's own docstring.
    """
    if not boxscore:
        return []
    team_block = (boxscore.get("teams") or {}).get(side) or {}
    pitcher_ids = team_block.get("pitchers") or []
    players = team_block.get("players") or {}
    lines = []
    for idx, pid in enumerate(pitcher_ids):
        entry = players.get(f"ID{pid}") or {}
        person = entry.get("person") or {}
        pitching = (entry.get("stats") or {}).get("pitching") or {}
        throws_hand = (person.get("pitchHand") or {}).get("code")
        lines.append({
            "playerId": str(pid),
            "orderIndex": idx,
            "name": person.get("fullName"),
            "throwsHand": throws_hand if throws_hand in ("L", "R") else None,
            "numberOfPitches": parse_nonnegative_int(pitching.get("numberOfPitches")),
            "outs": parse_nonnegative_int(pitching.get("outs")),
            "saves": parse_nonnegative_int(pitching.get("saves")),
            "holds": parse_nonnegative_int(pitching.get("holds")),
            "runs": parse_nonnegative_int(pitching.get("runs")),
            "earnedRuns": parse_nonnegative_int(pitching.get("earnedRuns")),
            # Added for MLB-RSCH-0004 (starter workload/rest) -- purely
            # additive, new keys only, so MLB-RSCH-0003's already-committed
            # reuse of this function (via pitcher_lines_to_relief_appearances)
            # is unaffected. Requires a schema-extended re-fetch to be
            # populated for a given cached game -- see
            # scripts/edgelab/backtest/fetch_mlb_starter_workload_cache.py's
            # own module docstring for why this experiment uses a separate
            # boxscore cache namespace rather than mutating MLB-RSCH-0003's.
            "battersFaced": parse_nonnegative_int(pitching.get("battersFaced")),
            "strikeOuts": parse_nonnegative_int(pitching.get("strikeOuts")),
            "baseOnBalls": parse_nonnegative_int(pitching.get("baseOnBalls")),
            "hits": parse_nonnegative_int(pitching.get("hits")),
        })
    return lines


def relief_outcome_for_game(pitcher_lines):
    """
    Pure. POSTGAME evaluation target ONLY -- this is what the target
    game's own bullpen allowed, never a predictive feature for that
    same game (see docs/EDGELAB_RESEARCH_LAB.md's leakage examples: an
    outcome must never be fed back in as its own predictor). From one
    team's own pitcher_lines (extract_pitcher_lines' shape) for ONE
    game.

    Zero relievers used (the starter threw a complete game) is a real,
    well-defined zero, never "unavailable". Any relief appearance with
    a missing runs/earnedRuns/outs value invalidates the WHOLE outcome
    for that game (returns None) -- never a partial sum over only the
    well-formed relievers, which would silently understate the result.
    """
    if not pitcher_lines:
        return None
    relievers = [p for p in pitcher_lines if p["orderIndex"] > 0]
    full_game_runs = (
        sum(p["runs"] for p in pitcher_lines) if all(p["runs"] is not None for p in pitcher_lines) else None
    )
    if not relievers:
        return {
            "reliefRunsAllowed": 0, "reliefEarnedRunsAllowed": 0,
            "bullpenOuts": 0, "bullpenInningsPitched": 0.0,
            "numberOfRelieversUsed": 0,
            "fullGameTeamRunsAllowed": full_game_runs,
        }
    if any(p["runs"] is None or p["earnedRuns"] is None or p["outs"] is None for p in relievers):
        return None
    bullpen_outs = sum(p["outs"] for p in relievers)
    return {
        "reliefRunsAllowed": sum(p["runs"] for p in relievers),
        "reliefEarnedRunsAllowed": sum(p["earnedRuns"] for p in relievers),
        "bullpenOuts": bullpen_outs,
        "bullpenInningsPitched": round(bullpen_outs / 3, 3),
        "numberOfRelieversUsed": len(relievers),
        "fullGameTeamRunsAllowed": full_game_runs,
    }


def pitcher_lines_to_relief_appearances(pitcher_lines):
    """
    Pure. Converts extract_pitcher_lines()'s output shape into
    lib.edgelab.bullpen_usage.extract_relief_appearances()'s own output
    shape (starter, orderIndex 0, excluded; "outs" renamed to
    "outsRecorded"). Needed because this backtest's on-disk cache
    (scripts/edgelab/backtest/fetch_mlb_multiseason_bullpen_cache.py)
    stores extract_pitcher_lines()'s COMPACT extraction, not the raw
    boxscore payload extract_relief_appearances itself parses -- so
    lib.edgelab.bullpen_usage.summarize_team_bullpen_usage (which
    expects extract_relief_appearances' shape) is fed through this
    adapter rather than being handed the cache's raw records directly.
    """
    return [
        {
            "playerId": p["playerId"], "name": p.get("name"), "throwsHand": p.get("throwsHand"),
            "numberOfPitches": p.get("numberOfPitches"), "outsRecorded": p.get("outs"),
            "saves": p.get("saves"), "holds": p.get("holds"),
        }
        for p in pitcher_lines if p["orderIndex"] > 0
    ]


# ── PIT-safe feature reconstruction ─────────────────────────────────────

def _team_appearances_by_date(games):
    by_date = {}
    for g in games:
        by_date.setdefault(g["date"], []).extend(g.get("appearances") or [])
    return by_date


def reconstruct_workload_features(all_team_games, as_of_game, lookback_days=DEFAULT_LOOKBACK_DAYS):
    """
    Pure. `all_team_games`: EVERY game for one team (past, the target
    game itself, and future games all allowed in -- this function does
    its own filtering via prior_games()/is_strictly_before(), never
    trusts the caller). Each entry: {"date", "gameNumber", "gamePk",
    "appearances"} where "appearances" is extract_relief_appearances()'s
    own output for that team's side of that game.
    `as_of_game`: {"date", "gameNumber", "doubleHeader"} for the TARGET
    game -- descriptive context only (doubleHeader/gameNumber are known
    pregame from the schedule, hence legitimately PIT-safe as auxiliary
    metadata, never derived from any outcome).

    Returns the full feature dict: `productionFormulaInput` (PRIMARY --
    lib.edgelab.bullpen_usage.summarize_team_bullpen_usage's own output,
    reused unchanged, windowed to lookback_days) plus the calendar-day
    EXPLORATORY features spec section 3 lists by name.
    """
    prior = prior_games(all_team_games, as_of_game)
    as_of_date = as_of_game["date"]

    windowed = [g for g in prior if g["date"] >= _shift_date(as_of_date, -lookback_days)]
    games_with_appearances = [{"date": g["date"], "appearances": g.get("appearances") or []} for g in windowed]
    production_input = summarize_team_bullpen_usage(games_with_appearances)

    by_date = _team_appearances_by_date(windowed)

    def pitches_on(date_str):
        return sum((a.get("numberOfPitches") or 0) for a in by_date.get(date_str, []))

    def reliever_ids_on(date_str):
        return {a["playerId"] for a in by_date.get(date_str, []) if a.get("playerId") is not None}

    d1, d2, d3 = _shift_date(as_of_date, -1), _shift_date(as_of_date, -2), _shift_date(as_of_date, -3)

    ids_d1, ids_d2, ids_d3 = reliever_ids_on(d1), reliever_ids_on(d2), reliever_ids_on(d3)
    back_to_back_ids = ids_d1 & ids_d2
    # "3 consecutive days, if present" -- only meaningful if the team
    # actually has a recorded game on all three of d1/d2/d3; otherwise
    # there is no genuine 3-day streak to report (None, not 0/false).
    team_played_d3 = d3 in by_date
    three_consecutive_ids = (ids_d1 & ids_d2 & ids_d3) if team_played_d3 else None

    hl_ids_in_window = {
        a["playerId"] for appearances in by_date.values() for a in appearances
        if a.get("playerId") is not None and ((a.get("saves") or 0) > 0 or (a.get("holds") or 0) > 0)
    }
    hl_used_prev_day = hl_ids_in_window & ids_d1
    hl_back_to_back = hl_ids_in_window & back_to_back_ids

    # Reused threshold, not reinvented: lib.edgelab.bullpen_availability's
    # own HEAVY_USE_PITCH_THRESHOLD (35) is the PRIMARY spec's cutoff --
    # see compute_bullpen_workload_adjustment's own component 2.
    from lib.edgelab.bullpen_availability import HEAVY_USE_PITCH_THRESHOLD
    heavy_usage_reliever_count = sum(
        1 for p in (production_input.get("recentPitchCounts") or [])
        if (p.get("totalPitches") or 0) >= HEAVY_USE_PITCH_THRESHOLD
    )

    prior_dates_played = sorted({g["date"] for g in prior})
    days_since_last_game = None
    if prior_dates_played:
        last_date = datetime.strptime(prior_dates_played[-1], "%Y-%m-%d")
        as_of_dt = datetime.strptime(as_of_date, "%Y-%m-%d")
        days_since_last_game = (as_of_dt - last_date).days

    return {
        "asOfDate": as_of_date,
        "doubleHeader": as_of_game.get("doubleHeader"),
        "gameNumber": as_of_game.get("gameNumber"),
        "daysSinceLastGame": days_since_last_game,
        "bullpenPitchesPrevDay1": pitches_on(d1),
        "bullpenPitchesPrevDays2": pitches_on(d1) + pitches_on(d2),
        "bullpenPitchesPrevDays3": pitches_on(d1) + pitches_on(d2) + pitches_on(d3),
        "relieversUsedPrevDay1": len(ids_d1),
        "relieversUsedPrevDays2": len(ids_d1 | ids_d2),
        "backToBackRelieverCount": len(back_to_back_ids),
        "threeConsecutiveDayRelieverCount": (len(three_consecutive_ids) if three_consecutive_ids is not None else None),
        "heavyUsageRelieverCount": heavy_usage_reliever_count,
        "highLeverageUsedPrevDayCount": len(hl_used_prev_day),
        "highLeverageBackToBackCount": len(hl_back_to_back),
        "productionFormulaInput": production_input,
    }


def current_production_multiplier(features):
    """
    Calls lib.edgelab.bullpen_availability.compute_bullpen_workload_adjustment
    UNCHANGED on this feature dict's own productionFormulaInput -- "the
    exact CURRENT bullpen workload adjustment components/formula without
    changing them" (mission spec section 8). Never reimplemented.
    """
    return compute_bullpen_workload_adjustment(features["productionFormulaInput"])
