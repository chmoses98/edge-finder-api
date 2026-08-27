"""
lib/edgelab/backtest/starter_workload_reconstruction.py
============================================================
Research Lab MLB-RSCH-0004: pure, deterministic PIT-safe feature and
outcome reconstruction for the multi-season starting-pitcher
workload/rest backtest.

No network I/O here -- operates entirely on already-cached raw MLB
Stats API data (see scripts/edgelab/backtest/
fetch_mlb_starter_workload_cache.py for the network adapter).

REUSE, NOT DUPLICATION
--------------------------
Reuses lib.edgelab.backtest.bullpen_backtest_reconstruction's
is_strictly_before() (leakage guard, doubleheader-safe ordering) and
extract_pitcher_lines() (per-pitcher boxscore extraction, extended for
this milestone with battersFaced/strikeOuts/baseOnBalls/hits) UNCHANGED
-- this module adds only the STARTER-specific indexing (one pitcher's
own start history, independent of which team they were on that day --
handles a mid-season trade correctly for free) and feature/outcome
definitions on top.

FIXED, PREREGISTERED THRESHOLDS (never tuned from observed results --
see docs/EDGELAB_MLB_RSCH_0004_STARTER_WORKLOAD.md)
-------------------------------------------------------------------------
  SHORT_REST_MAX_DAYS = 4        -- <=4 days since previous start
  EXTENDED_REST_MIN_DAYS = 6     -- >=6 days (5 is a standard rotation turn)
  HIGH_PITCH_COUNT_THRESHOLD = 100   -- the conventional "100-pitch" marker
  STRESSFUL_PITCHES_PER_OUT_THRESHOLD = 4.5  -- labored-outing proxy
  UNUSUALLY_LONG_REST_DAYS = 10  -- IL-stint/All-Star-break-scale gap

LEAKAGE DISCIPLINE
----------------------
Every function here accepts a pitcher's FULL start history (past, the
target start itself, future starts) and filters internally via
is_strictly_before() -- never trusts a caller to have pre-filtered.
Starts are indexed PER PITCHER (playerId), not per team, so a
mid-season trade is handled correctly: a start counts as "prior" to
another start by the same pitcher regardless of which team's boxscore
side it came from.
"""

from lib.edgelab.backtest.bullpen_backtest_reconstruction import is_strictly_before

SHORT_REST_MAX_DAYS = 4
EXTENDED_REST_MIN_DAYS = 6
HIGH_PITCH_COUNT_THRESHOLD = 100
STRESSFUL_PITCHES_PER_OUT_THRESHOLD = 4.5
UNUSUALLY_LONG_REST_DAYS = 10

REST_SHORT = "SHORT"
REST_NORMAL = "NORMAL"
REST_EXTENDED = "EXTENDED"


def build_pitcher_start_index(all_games):
    """
    Pure. `all_games`: every game in the loaded corpus (any order), each
    {"date", "gameNumber", "gamePk", "doubleHeader", "awayPitchers",
    "homePitchers"} (the starter_workload boxscore cache's own record
    shape -- both sides' full extract_pitcher_lines() output).

    Returns {playerId: [start, ...]}, each start a dict:
        {"playerId", "date", "gameNumber", "gamePk", "doubleHeader",
         "team": "away"|"home", "pitcherLine": <extract_pitcher_lines entry>}
    sorted per pitcher by (date, gameNumber) -- indexed by PLAYER, not
    team, so a mid-season trade is handled correctly for free (a
    pitcher's start for their new team is still ordered after their
    last start for their old team by date alone).
    """
    by_pitcher = {}
    for g in all_games:
        for side in ("away", "home"):
            lines = g.get(f"{side}Pitchers") or []
            starter = next((p for p in lines if p.get("orderIndex") == 0), None)
            if not starter or not starter.get("playerId"):
                continue
            entry = {
                "playerId": starter["playerId"], "date": g["date"], "gameNumber": g.get("gameNumber"),
                "gamePk": g["gamePk"], "doubleHeader": g.get("doubleHeader"), "team": side,
                "pitcherLine": starter,
            }
            by_pitcher.setdefault(starter["playerId"], []).append(entry)
    for starts in by_pitcher.values():
        starts.sort(key=lambda s: (s["date"] or "", s.get("gameNumber") or 1))
    return by_pitcher


def prior_starts(pitcher_starts, as_of_start):
    """Every start in pitcher_starts strictly before as_of_start, per
    is_strictly_before -- includes the target start and any future
    start ONLY to prove they are excluded."""
    return [s for s in pitcher_starts if is_strictly_before(s, as_of_start)]


def _days_between(date_a, date_b):
    from datetime import datetime
    return (datetime.strptime(date_b, "%Y-%m-%d") - datetime.strptime(date_a, "%Y-%m-%d")).days


def _innings(outs):
    return round(outs / 3, 3) if outs is not None else None


def reconstruct_starter_features(pitcher_starts, as_of_start, season_start_date):
    """
    Pure. `pitcher_starts`: ALL starts for one pitcher (past, target,
    future all allowed -- filtered internally). `as_of_start`: the
    target start's own {"date", "gameNumber", "doubleHeader"}.
    `season_start_date`: "YYYY-MM-DD" -- season-to-date features only
    accumulate starts on/after this date (keeps season workload from
    silently bleeding across an off-season).

    Returns None if there is no PRIOR start this season (the
    eligibility rule: a pitcher's first start of a season has no valid
    rest/previous-start feature -- excluded, not approximated with a
    fabricated rest value).
    """
    prior = [s for s in prior_starts(pitcher_starts, as_of_start) if (s["date"] or "") >= season_start_date]
    if not prior:
        return None

    as_of_date = as_of_start["date"]
    last = prior[-1]
    last_line = last["pitcherLine"]

    days_since_previous_start = _days_between(last["date"], as_of_date)
    if days_since_previous_start <= SHORT_REST_MAX_DAYS:
        rest_category = REST_SHORT
    elif days_since_previous_start >= EXTENDED_REST_MIN_DAYS:
        rest_category = REST_EXTENDED
    else:
        rest_category = REST_NORMAL

    prev_pitches = last_line.get("numberOfPitches")
    prev_outs = last_line.get("outs")
    prev_innings = _innings(prev_outs)
    prev_batters_faced = last_line.get("battersFaced")
    high_pitch_count_previous_start = (prev_pitches is not None and prev_pitches >= HIGH_PITCH_COUNT_THRESHOLD)
    pitches_per_out = (prev_pitches / prev_outs) if (prev_pitches is not None and prev_outs) else None
    stressful_previous_start = (pitches_per_out is not None and pitches_per_out >= STRESSFUL_PITCHES_PER_OUT_THRESHOLD)

    def _window_sum(n, field):
        window = prior[-n:]
        vals = [s["pitcherLine"].get(field) for s in window]
        return sum(vals) if all(v is not None for v in vals) and vals else None

    pitches_prior_2 = _window_sum(2, "numberOfPitches")
    pitches_prior_3 = _window_sum(3, "numberOfPitches")
    outs_prior_2 = _window_sum(2, "outs")
    outs_prior_3 = _window_sum(3, "outs")

    season_pitches = [s["pitcherLine"].get("numberOfPitches") for s in prior]
    season_outs = [s["pitcherLine"].get("outs") for s in prior]
    prior_season_to_date_starts = len(prior)
    prior_season_to_date_pitches = sum(season_pitches) if all(v is not None for v in season_pitches) else None
    prior_season_to_date_innings = (
        round(sum(season_outs) / 3, 3) if all(v is not None for v in season_outs) else None
    )
    own_baseline_pitches_per_start = (
        prior_season_to_date_pitches / prior_season_to_date_starts
        if prior_season_to_date_pitches is not None and prior_season_to_date_starts > 0 else None
    )
    workload_relative_to_own_baseline = (
        prev_pitches / own_baseline_pitches_per_start
        if prev_pitches is not None and own_baseline_pitches_per_start else None
    )

    own_baseline_runs_per_9 = None
    era_vals = []
    for s in prior:
        pl = s["pitcherLine"]
        er, outs = pl.get("earnedRuns"), pl.get("outs")
        if er is not None and outs:
            era_vals.append(er / outs * 27)
    if era_vals:
        own_baseline_runs_per_9 = round(sum(era_vals) / len(era_vals), 4)

    return {
        "asOfDate": as_of_date,
        "doubleHeader": as_of_start.get("doubleHeader"),
        "gameNumber": as_of_start.get("gameNumber"),
        "daysSincePreviousStart": days_since_previous_start,
        "restCategory": rest_category,
        "returnFromUnusuallyLongRest": days_since_previous_start >= UNUSUALLY_LONG_REST_DAYS,
        "previousStartPitches": prev_pitches,
        "previousStartInningsPitched": prev_innings,
        "previousStartBattersFaced": prev_batters_faced,
        "highPitchCountPreviousStart": high_pitch_count_previous_start,
        "previousStartPitchesPerOut": round(pitches_per_out, 4) if pitches_per_out is not None else None,
        "stressfulPreviousStart": stressful_previous_start,
        "pitchesOverPrior2Starts": pitches_prior_2,
        "pitchesOverPrior3Starts": pitches_prior_3,
        "inningsOverPrior2Starts": _innings(outs_prior_2) if outs_prior_2 is not None else None,
        "inningsOverPrior3Starts": _innings(outs_prior_3) if outs_prior_3 is not None else None,
        "priorSeasonToDateStarts": prior_season_to_date_starts,
        "priorSeasonToDatePitches": prior_season_to_date_pitches,
        "priorSeasonToDateInnings": prior_season_to_date_innings,
        "workloadRelativeToOwnBaseline": (
            round(workload_relative_to_own_baseline, 4) if workload_relative_to_own_baseline is not None else None
        ),
        "ownBaselineRunsPer9": own_baseline_runs_per_9,
    }


def starter_outcome_for_start(pitcher_line):
    """
    Pure. POSTGAME evaluation target ONLY -- never a predictive feature
    for that same start. From the target start's own pitcherLine.
    Returns None if runs/earnedRuns/outs is missing (never a partial/
    guessed outcome).
    """
    if not pitcher_line:
        return None
    runs, earned, outs = pitcher_line.get("runs"), pitcher_line.get("earnedRuns"), pitcher_line.get("outs")
    if runs is None or earned is None or outs is None or outs == 0:
        return None
    hits, walks = pitcher_line.get("hits"), pitcher_line.get("baseOnBalls")
    whip = round((hits + walks) / (outs / 3), 4) if (hits is not None and walks is not None) else None
    return {
        "starterRunsAllowed": runs,
        "starterEarnedRuns": earned,
        "starterInningsPitched": _innings(outs),
        "starterStrikeouts": pitcher_line.get("strikeOuts"),
        "starterWalks": walks,
        "starterHitsAllowed": hits,
        "starterRunsPer9": round(runs / outs * 27, 4),
        "starterEarnedRunsPer9": round(earned / outs * 27, 4),
        "whipLike": whip,
        "completedFiveInnings": outs >= 15,
    }
