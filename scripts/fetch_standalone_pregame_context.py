#!/usr/bin/env python3
"""
scripts/fetch_standalone_pregame_context.py
================================================
Hitter Projection Engine Phase 5 correction -- standalone, run-scoped
pregame context (today's schedule + official starting lineups),
completely independent of data/slate.json and the traditional
fetch-slate.yml pipeline.

WHY THIS EXISTS: the original Phase 5 standalone hitter research run
depended on whatever `lineupConfirmedOfficial` state already happened
to exist in data/slate.json -- meaning the promised UX ("run the
standalone Kalshi price check once -> get real hitter projections")
silently degraded every hitter market to LINEUP_UNCONFIRMED whenever
the traditional slate pipeline hadn't already populated that file that
day. This module independently resolves today's MLB schedule and
official starting lineups directly from the MLB Stats API, producing a
NEW, run-scoped, slate-COMPATIBLE artifact -- it never reads, writes,
or mutates data/slate.json.

REUSE, NOT DUPLICATION: lineup fetching/parsing itself is NOT
reimplemented here. scripts.fetch_lineups.fetch_boxscore() (network
adapter) and .parse_lineup_response() (pure parser -- battingOrder,
confirmedLineup with each hitter's playerId/name/batSide/position
including catcher, and the authoritative lineupConfirmedOfficial flag)
are imported and called directly; they are already the ONE place MLB
Stats API boxscore -> confirmedLineup parsing lives in this repository,
already tested (tests/test_fetch_lineups*.py), and structured as pure
functions with no dependency on data/slate.json already existing. This
module's only genuinely new logic is the SCHEDULE DISCOVERY step --
scripts/fetch_lineups.py has never needed one, because it has always
received its gamePks pre-populated by the traditional pipeline's own
earlier /api/slate fetch -- and assembling the result into a
slate-compatible shape so scripts/build_hitter_feature_board.py and
scripts/build_hitter_projection_board.py can consume it via their
EXISTING `slate_path=` parameter with zero changes to that
already-tested board-building code.

Team ID <-> abbreviation mapping reuses
scripts.fetch_opp_quality.MLB_ID_TO_ABBR (the one canonical mapping
already used elsewhere in this repo), not a second copy.

HONEST DEGRADATION: every field access is defensive (`.get()` with
None/empty defaults); a fetch failure at the schedule level returns an
empty games list (never fabricated games), and a fetch failure at the
per-game boxscore level yields that game's own
`scripts.fetch_lineups.missing_lineup_fields(...)` block (never a
fabricated or guessed lineup) -- the exact same honest-degradation
convention `scripts/fetch_lineups.py` itself already uses.
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from scripts.fetch_lineups import fetch_boxscore, parse_lineup_response, missing_lineup_fields  # noqa: E402
from scripts.fetch_opp_quality import MLB_ID_TO_ABBR  # noqa: E402

MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}&gameType=R&hydrate=probablePitcher"

# MLB Stats API's own team codes occasionally differ from the abbreviation Kalshi's own
# tickers/other parts of this repo use for the same team -- same two known discrepancies
# scripts/enrich_data.py's ABBR_NORMALIZE already documents (MLB API uses ARI/OAK; Kalshi
# tickers and data/teamstats.json use AZ/ATH). Duplicated here as a tiny, stable literal
# rather than imported, since scripts/enrich_data.py is a bare top-level script (executes
# file I/O at import time) and is not safely importable.
KALSHI_ABBR_NORMALIZE = {"ARI": "AZ", "OAK": "ATH"}


def _to_kalshi_abbr(mlb_abbr):
    return KALSHI_ABBR_NORMALIZE.get(mlb_abbr, mlb_abbr) if mlb_abbr else mlb_abbr


def fetch_json(url, timeout=20):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"  fetch error: {e}")
        return None


def discover_todays_schedule(date_str: str) -> list:
    """
    Returns a list of raw per-game schedule dicts for `date_str`
    (gamePk, detailedState, gameDate, both teams' id/abbr/name, both
    teams' probablePitcher) -- completely independent of
    data/slate.json. Never raises; returns [] on any fetch failure.
    """
    data = fetch_json(MLB_SCHEDULE_URL.format(date=date_str))
    if not data:
        return []
    games = []
    for day in data.get("dates", []):
        for g in day.get("games", []):
            away_team = (g.get("teams", {}).get("away", {}) or {}).get("team", {}) or {}
            home_team = (g.get("teams", {}).get("home", {}) or {}).get("team", {}) or {}
            away_id, home_id = away_team.get("id"), home_team.get("id")
            games.append({
                "gamePk": g.get("gamePk"),
                "detailedState": (g.get("status", {}) or {}).get("detailedState"),
                "gameDate": g.get("gameDate"),
                "awayAbbr": _to_kalshi_abbr(MLB_ID_TO_ABBR.get(away_id)),
                "homeAbbr": _to_kalshi_abbr(MLB_ID_TO_ABBR.get(home_id)),
                "awayTeamName": away_team.get("name"), "homeTeamName": home_team.get("name"),
                "awayProbablePitcher": (g.get("teams", {}).get("away", {}) or {}).get("probablePitcher"),
                "homeProbablePitcher": (g.get("teams", {}).get("home", {}) or {}).get("probablePitcher"),
            })
    return games


def _starter_from_boxscore_or_probable(boxscore_data, side, probable_pitcher):
    """
    Prefers the boxscore's own actual starting pitcher (teams.<side>.pitchers[0], which also
    carries pitchHand once that player's own entry is populated in the same response); falls back
    to the schedule's own probablePitcher id/name (pitchHand honestly None, never guessed) when
    the boxscore doesn't have a starter listed yet (very early pregame).
    """
    team_data = (boxscore_data or {}).get("teams", {}).get(side, {}) or {}
    pitchers = team_data.get("pitchers") or []
    players = team_data.get("players") or {}
    if pitchers:
        pid = str(pitchers[0])
        player = players.get(f"ID{pid}", {}) or {}
        person = player.get("person") or {}
        pitch_hand = (person.get("pitchHand") or {}).get("code")
        return {"id": pid, "name": person.get("fullName"), "pitchHand": pitch_hand}
    if probable_pitcher:
        return {"id": str(probable_pitcher.get("id")) if probable_pitcher.get("id") is not None else None,
                "name": probable_pitcher.get("fullName"), "pitchHand": None}
    return {"id": None, "name": None, "pitchHand": None}


def build_standalone_slate(date_str: str, sleep_between: float = 0.0) -> dict:
    """
    Top-level orchestration: discovers today's schedule, then fetches +
    parses OFFICIAL lineups for every game via scripts.fetch_lineups's
    own reused functions, and assembles a slate-COMPATIBLE dict
    {"date":, "games": [...]} -- the exact shape
    build_hitter_feature_board.py/build_hitter_projection_board.py
    already read via their own `slate_path=` parameter. NEVER reads or
    writes data/slate.json.
    """
    schedule_games = discover_todays_schedule(date_str)
    games_out = []
    for sg in schedule_games:
        game_pk = sg["gamePk"]
        away_abbr, home_abbr = sg["awayAbbr"], sg["homeAbbr"]
        boxscore = fetch_boxscore(game_pk) if game_pk else None
        if sleep_between:
            time.sleep(sleep_between)
        lineup_result = parse_lineup_response(boxscore, away_abbr, home_abbr, {}, {})

        if lineup_result is None:
            away_ts = missing_lineup_fields("MLB Stats API returned no boxscore data for this game")
            home_ts = missing_lineup_fields("MLB Stats API returned no boxscore data for this game")
        else:
            away_ts = lineup_result.get("away") or missing_lineup_fields("No away-side lineup data")
            home_ts = lineup_result.get("home") or missing_lineup_fields("No home-side lineup data")

        games_out.append({
            "gameId": game_pk,
            "startTime": sg["gameDate"],
            "scheduleDetailedState": sg["detailedState"],
            "away": {"abbr": away_abbr, "team": sg["awayTeamName"],
                      "pitcher": _starter_from_boxscore_or_probable(boxscore, "away", sg["awayProbablePitcher"])},
            "home": {"abbr": home_abbr, "team": sg["homeTeamName"],
                      "pitcher": _starter_from_boxscore_or_probable(boxscore, "home", sg["homeProbablePitcher"])},
            "awayTeamStats": away_ts,
            "homeTeamStats": home_ts,
            "park": {},
        })

    return {
        "date": date_str,
        "source": "standalone_mlb_stats_api",
        "generatedAt": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "games": games_out,
    }


def main(date_str=None, output_path=None, sleep_between=0.2):
    date_str = date_str or datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    slate_compatible = build_standalone_slate(date_str, sleep_between=sleep_between)
    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(slate_compatible, f)
    return slate_compatible


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None)
    parser.add_argument("--output-path", required=True)
    args = parser.parse_args()
    result = main(date_str=args.date, output_path=args.output_path)
    print(json.dumps({"date": result["date"], "totalGames": len(result["games"]),
                       "outputPath": args.output_path}, indent=2))
