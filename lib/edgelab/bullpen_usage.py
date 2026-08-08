#!/usr/bin/env python3
"""
lib/edgelab/bullpen_usage.py
================================
Pure parsers + network adapters for RECENT bullpen usage -- previous-day
usage, back-to-back appearances, recent pitch counts, high-leverage
(save/hold) workload, and handedness mix. Part of the "bullpen context
for pregame analysis" improvement: data/bullpen.json and api/bullpen.js
already carry SEASON-AGGREGATE bullpen quality (era/xFIP/whip/grade) and
a high-leverage QUALITY split (hlXFIP) -- neither says anything about
whether today's bullpen is actually available/rested. This module fills
that specific gap.

Reuses the SAME network-adapter convention as lib/edgelab/mlb_boxscore.py
(a bare try/except returning None on any failure -- network error,
timeout, non-2xx, malformed JSON) and the SAME MLB_TEAM_ID_MAP as
scripts/fetch_opp_quality.py (deliberately duplicated, not imported --
identical reasoning to mlb_boxscore.py's own docstring: this is a
pure-adapter module other code and tests import freely, and must never
depend on a script module that could carry import-time side effects).
Every counting statistic is parsed via lib.edgelab.player_stats.
parse_nonnegative_int() -- never a bare int() conversion.

Data/context only: nothing here computes a recommendation, a "rested"
verdict, a betting signal, or any staking/threshold input -- it exposes
raw recent usage so a human (manual analysis) can judge bullpen
availability themselves. Never wired into scripts/build_market_ledger.py,
risk_gate.py, or any recommendation/settlement/ledger code.

Uses the lighter /v1/game/{gamePk}/boxscore endpoint (player stat lines
only, no play-by-play) -- the same endpoint
scripts/fetch_opp_quality.py's fetch_actual_starter() already calls for
starter identification -- not the heavier live-feed endpoint
lib.edgelab.mlb_boxscore uses for full settlement.
"""
import json
import urllib.request

from lib.edgelab.player_stats import parse_nonnegative_int

MLB_STATS_API = "https://statsapi.mlb.com/api/v1"

# Mirrors scripts/fetch_opp_quality.py's MLB_TEAM_ID_MAP exactly --
# deliberately duplicated, not imported (see module docstring).
MLB_TEAM_ID_MAP = {
    'LAA': 108, 'ARI': 109, 'BAL': 110, 'BOS': 111, 'CHC': 112, 'CIN': 113, 'CLE': 114,
    'COL': 115, 'DET': 116, 'HOU': 117, 'KC': 118, 'LAD': 119, 'WSH': 120, 'NYM': 121,
    'ATH': 133, 'PIT': 134, 'SD': 135, 'SEA': 136, 'SF': 137, 'STL': 138, 'TB': 139,
    'TEX': 140, 'TOR': 141, 'MIN': 142, 'PHI': 143, 'ATL': 144, 'CWS': 145, 'MIA': 146,
    'NYY': 147, 'MIL': 158,
}
MLB_ID_TO_ABBR = {v: k for k, v in MLB_TEAM_ID_MAP.items()}

# Mirrors lib.edgelab.mlb_boxscore.FINAL_DETAILED_STATES /
# scripts/fetch_opp_quality.py's COMPLETED_STATUSES exactly.
COMPLETED_STATUSES = frozenset({"Final", "Game Over", "Completed Early"})


def _fetch_json(url, timeout):
    """Bare try/except network fetch -- returns parsed JSON or None on
    ANY failure. Mirrors lib.edgelab.mlb_boxscore.fetch_game_feed's exact
    convention (deliberately duplicated, not imported -- see module
    docstring)."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "edge-finder-edgelab/1.0",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def fetch_team_recent_schedule(team_id, start_date, end_date, timeout=15):
    """Network adapter. Returns the raw schedule JSON for one team over
    [start_date, end_date] (YYYY-MM-DD strings, inclusive), or None on
    any failure. Same endpoint shape as
    scripts/fetch_opp_quality.py's fetch_recent_games()."""
    if not team_id:
        return None
    url = (f"{MLB_STATS_API}/schedule?sportId=1&teamId={team_id}"
           f"&startDate={start_date}&endDate={end_date}&gameType=R")
    return _fetch_json(url, timeout)


def fetch_team_boxscore(game_pk, timeout=15):
    """Network adapter. Returns the raw /v1/game/{game_pk}/boxscore
    JSON, or None on any failure -- same endpoint
    scripts/fetch_opp_quality.py's fetch_actual_starter() already uses."""
    if not game_pk:
        return None
    url = f"{MLB_STATS_API}/game/{game_pk}/boxscore"
    return _fetch_json(url, timeout)


def extract_completed_games_for_team(schedule, team_id):
    """
    Pure. From one team's raw schedule response, returns a list of
    {"gamePk", "date", "side"} for every COMPLETED game, oldest first.
    `side` ("away"/"home") tells the caller which half of that game's
    boxscore belongs to this team. Never guesses a game's status --
    only a COMPLETED_STATUSES detailedState is included; a live,
    postponed, suspended, or otherwise incomplete game is excluded, not
    approximated. Returns [] for a missing/malformed schedule.
    """
    if not schedule:
        return []
    games = []
    for day in schedule.get("dates") or []:
        for g in day.get("games") or []:
            status = (g.get("status") or {}).get("detailedState")
            if status not in COMPLETED_STATUSES:
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
            games.append({"gamePk": g.get("gamePk"), "date": day.get("date"), "side": side})
    games.sort(key=lambda g: (g["date"] or "", g["gamePk"] or 0))
    return games


def extract_relief_appearances(boxscore, side):
    """
    Pure. From one game's boxscore JSON, returns a list of relief-
    appearance dicts for every pitcher on `side` EXCEPT the first (the
    starter, per boxscore.teams.<side>.pitchers' own appearance order --
    the same convention scripts/fetch_opp_quality.py already relies on
    for starter identification). Each entry:

        {"playerId": str, "name": str|None, "throwsHand": "L"|"R"|None,
         "numberOfPitches": int|None, "outsRecorded": int|None,
         "saves": int|None, "holds": int|None}

    Every counting stat is parsed via parse_nonnegative_int() -- never a
    bare int() truncation, so a malformed value (e.g. a fractional or
    negative pitch count) becomes None, never a guessed/truncated
    number. Returns [] for a malformed/missing boxscore, or a team
    whose starter completed the game (no relief appearances at all) --
    never fabricated, never raises.
    """
    if not boxscore:
        return []
    team_block = (boxscore.get("teams") or {}).get(side) or {}
    pitcher_ids = team_block.get("pitchers") or []
    if len(pitcher_ids) <= 1:
        return []

    players = team_block.get("players") or {}
    appearances = []
    for pid in pitcher_ids[1:]:
        entry = players.get(f"ID{pid}") or {}
        person = entry.get("person") or {}
        pitching = (entry.get("stats") or {}).get("pitching") or {}
        throws_hand = (person.get("pitchHand") or {}).get("code")
        appearances.append({
            "playerId": str(pid),
            "name": person.get("fullName"),
            "throwsHand": throws_hand if throws_hand in ("L", "R") else None,
            "numberOfPitches": parse_nonnegative_int(pitching.get("numberOfPitches")),
            "outsRecorded": parse_nonnegative_int(pitching.get("outs")),
            "saves": parse_nonnegative_int(pitching.get("saves")),
            "holds": parse_nonnegative_int(pitching.get("holds")),
        })
    return appearances


def _empty_summary(reason):
    return {
        "dataAvailable": False, "unavailableReason": reason,
        "asOfDate": None, "gamesConsidered": 0,
        "relieversUsedLastGame": [], "backToBackRelievers": [],
        "recentPitchCounts": [], "highLeverageRecentUsage": [],
        "handednessMix": {"L": 0, "R": 0, "unknown": 0},
        "teamPitchCountLastGame": None, "teamPitchCountWindow": None,
    }


def summarize_team_bullpen_usage(games_with_appearances):
    """
    Pure. `games_with_appearances`: list of {"date": "YYYY-MM-DD",
    "appearances": [...]}, one entry per recent COMPLETED game for one
    team (oldest first or any order -- this function sorts by date),
    where "appearances" is extract_relief_appearances()'s own output
    for that game (an empty list is valid -- it means the starter threw
    a complete game, zero relievers used that day, a real and useful
    signal, not "unavailable").

    Returns:
        {
          "dataAvailable": bool,
          "unavailableReason": str|None,
          "asOfDate": str|None,               # most recent game date considered
          "gamesConsidered": int,
          "relieversUsedLastGame": [{"playerId","name","numberOfPitches"}],
          "backToBackRelievers": [{"playerId","name"}],
          "recentPitchCounts": [{"playerId","name","totalPitches","appearances"}],
          "highLeverageRecentUsage": [{"playerId","name","saves","holds","totalPitches"}],
          "handednessMix": {"L": int, "R": int, "unknown": int},
          "teamPitchCountLastGame": int|None,
          "teamPitchCountWindow": int|None,
        }

    dataAvailable=False (with an explicit unavailableReason) ONLY when
    the window found zero completed games at all (e.g. every fetch
    failed, or the team had no games in the lookback window) -- never
    guessed or approximated from a partial result. "highLeverageRecentUsage"
    is any reliever who recorded a save or hold in the window (the same
    save/hold signal api/enrich.js's existing HL-quality split already
    uses to identify a team's leverage relievers) -- not a fabricated
    leverage score.
    """
    if not games_with_appearances:
        return _empty_summary("no_completed_games_in_window")

    ordered = sorted(games_with_appearances, key=lambda g: g.get("date") or "")
    last_game = ordered[-1]
    last_appearances = last_game.get("appearances") or []

    relievers_used_last_game = [
        {"playerId": a["playerId"], "name": a["name"], "numberOfPitches": a["numberOfPitches"]}
        for a in last_appearances
    ]
    team_pitch_count_last_game = sum((a.get("numberOfPitches") or 0) for a in last_appearances)

    back_to_back = []
    if len(ordered) >= 2:
        second_last_ids = {a["playerId"] for a in (ordered[-2].get("appearances") or [])}
        seen = set()
        for a in last_appearances:
            if a["playerId"] in second_last_ids and a["playerId"] not in seen:
                back_to_back.append({"playerId": a["playerId"], "name": a["name"]})
                seen.add(a["playerId"])

    pitch_totals = {}
    hl_totals = {}
    hands = {"L": 0, "R": 0, "unknown": 0}
    seen_for_hand = set()
    team_pitch_count_window = 0
    for g in ordered:
        for a in (g.get("appearances") or []):
            pid = a["playerId"]
            entry = pitch_totals.setdefault(
                pid, {"playerId": pid, "name": a["name"], "totalPitches": 0, "appearances": 0},
            )
            pitches = a.get("numberOfPitches") or 0
            entry["totalPitches"] += pitches
            entry["appearances"] += 1
            team_pitch_count_window += pitches

            saves, holds = a.get("saves") or 0, a.get("holds") or 0
            if saves > 0 or holds > 0:
                hl = hl_totals.setdefault(
                    pid, {"playerId": pid, "name": a["name"], "saves": 0, "holds": 0, "totalPitches": 0},
                )
                hl["saves"] += saves
                hl["holds"] += holds
                hl["totalPitches"] += pitches

            if pid not in seen_for_hand:
                seen_for_hand.add(pid)
                hands[a.get("throwsHand") or "unknown"] += 1

    return {
        "dataAvailable": True, "unavailableReason": None,
        "asOfDate": last_game.get("date"),
        "gamesConsidered": len(ordered),
        "relieversUsedLastGame": relievers_used_last_game,
        "backToBackRelievers": back_to_back,
        "recentPitchCounts": sorted(pitch_totals.values(), key=lambda e: -e["totalPitches"]),
        "highLeverageRecentUsage": sorted(hl_totals.values(), key=lambda e: -e["totalPitches"]),
        "handednessMix": hands,
        "teamPitchCountLastGame": team_pitch_count_last_game,
        "teamPitchCountWindow": team_pitch_count_window,
    }
