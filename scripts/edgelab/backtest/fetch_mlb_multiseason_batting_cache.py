#!/usr/bin/env python3
"""
scripts/edgelab/backtest/fetch_mlb_multiseason_batting_cache.py
====================================================================
Research Lab MLB-RSCH-0012: network adapter + deterministic on-disk
cache builder for team-level batting box scores (offense-talent
estimation candidates O2/O3/O4).

REUSES the ALREADY-CACHED schedules MLB-RSCH-0003's own fetch script
left at data/research_cache/bullpen_backtest/<season>/schedules/<TEAM>.json
(read-only here, never re-fetched) to build the season's gamePk
universe -- the ONLY new network calls this script makes are
per-gamePk boxscore fetches (lib.edgelab.bullpen_usage.fetch_team_boxscore,
UNCHANGED, the same endpoint the bullpen fetch already uses), extracted
via lib.edgelab.backtest.team_batting_reconstruction.extract_team_batting_line
instead of extract_pitcher_lines.

CACHE LAYOUT (idempotent, resumable, safe to re-run)
------------------------------------------------------------
  data/research_cache/batting_backtest/<season>/boxscores.jsonl.gz
      ONE compact record per gamePk -- {"gamePk", "awayBatting":
      line_or_None, "homeBatting": line_or_None} -- deduplicated by
      lib.edgelab.storage.append_records (id_field="gamePk"), so a game
      shared by two teams' schedules is fetched exactly once, and a
      rerun (e.g. a workflow retry, or extending to a new season later)
      only fetches gamePks not already present.

RATE LIMITING: same conservative default (0.25s) and same non-fatal-
per-game-failure contract as fetch_mlb_multiseason_bullpen_cache.py --
see .github/workflows/research-multiseason-batting-backtest.yml for how
this is dispatched with real network access (this repository's own
CI/local dev environments have no outbound access to statsapi.mlb.com,
confirmed identically to MLB-RSCH-0003's own finding).
"""
import argparse
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab.bullpen_usage import fetch_team_boxscore, MLB_TEAM_ID_MAP
from lib.edgelab.backtest.bullpen_backtest_reconstruction import extract_team_games_from_schedule
from lib.edgelab.backtest.team_batting_reconstruction import extract_team_batting_line
from lib.edgelab.storage import append_records, read_records

BULLPEN_SCHEDULE_CACHE_ROOT = os.path.join(_ROOT, "data", "research_cache", "bullpen_backtest")
CACHE_ROOT = os.path.join(_ROOT, "data", "research_cache", "batting_backtest")
DEFAULT_RATE_LIMIT_SECONDS = 0.25


def schedule_cache_path(season, team_abbr):
    """Read-only: MLB-RSCH-0003's own already-fetched schedule cache. Never written here."""
    return os.path.join(BULLPEN_SCHEDULE_CACHE_ROOT, str(season), "schedules", f"{team_abbr}.json")


def boxscore_cache_path(season):
    return os.path.join(CACHE_ROOT, str(season), "boxscores.jsonl.gz")


def load_cached_schedule(season, team_abbr):
    path = schedule_cache_path(season, team_abbr)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def already_cached_game_pks(season):
    path = boxscore_cache_path(season)
    return {row.get("gamePk") for row in read_records(path) if row.get("gamePk") is not None}


def fetch_and_cache_boxscores(season, game_pks, rate_limit_seconds=DEFAULT_RATE_LIMIT_SECONDS, max_games=None):
    """
    Fetches+caches every gamePk in `game_pks` not already cached for
    this season. Never raises on an individual game's fetch failure
    (recorded in failedGamePks instead) -- one bad game must never
    abort a multi-hour multi-season run.
    """
    already = already_cached_game_pks(season)
    unique_requested = sorted(set(pk for pk in game_pks if pk is not None))
    to_fetch = [pk for pk in unique_requested if pk not in already]
    if max_games is not None:
        to_fetch = to_fetch[:max_games]

    fetched_records, failures = [], []
    for pk in to_fetch:
        box = fetch_team_boxscore(pk)
        if box is None:
            failures.append(pk)
            continue
        fetched_records.append({
            "gamePk": pk,
            "awayBatting": extract_team_batting_line(box, "away"),
            "homeBatting": extract_team_batting_line(box, "home"),
        })
        if rate_limit_seconds:
            time.sleep(rate_limit_seconds)

    written, skipped = append_records(boxscore_cache_path(season), fetched_records, id_field="gamePk")
    return {
        "season": season,
        "uniqueGamePksRequested": len(unique_requested),
        "alreadyCached": len(already & set(unique_requested)),
        "attempted": len(to_fetch),
        "fetched": written,
        "failed": len(failures),
        "failedGamePks": failures,
    }


def run(seasons, rate_limit_seconds=DEFAULT_RATE_LIMIT_SECONDS, max_games_per_season=None):
    """
    Orchestrates one full multi-season cache build. Reads MLB-RSCH-0003's
    already-cached schedules (never re-fetches them); if a season/team
    schedule isn't cached yet, that team's games for that season are
    simply skipped for THIS run (never fabricated) -- a caller wanting
    full coverage should first run fetch_mlb_multiseason_bullpen_cache.py
    for the same seasons, which this script's own workflow does NOT
    duplicate since MLB-RSCH-0003 already ran it for 2022-2026.
    """
    summaries = []
    for season in seasons:
        team_game_pks = set()
        schedules_missing = []
        for team_abbr, team_id in sorted(MLB_TEAM_ID_MAP.items()):
            schedule = load_cached_schedule(season, team_abbr)
            if not schedule:
                schedules_missing.append(team_abbr)
                continue
            for g in extract_team_games_from_schedule(schedule, team_id):
                if g.get("gamePk") is not None:
                    team_game_pks.add(g["gamePk"])

        boxscore_summary = fetch_and_cache_boxscores(
            season, team_game_pks, rate_limit_seconds=rate_limit_seconds, max_games=max_games_per_season,
        )
        boxscore_summary["schedulesMissing"] = schedules_missing
        boxscore_summary["schedulesTotal"] = len(MLB_TEAM_ID_MAP)
        summaries.append(boxscore_summary)
    return summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seasons", required=True, help="Comma-separated seasons, e.g. 2022,2023,2024,2025,2026")
    parser.add_argument("--rate-limit-seconds", type=float, default=DEFAULT_RATE_LIMIT_SECONDS)
    parser.add_argument("--max-games-per-season", type=int, default=None, help="Cap new boxscore fetches per season (testing/partial runs)")
    args = parser.parse_args()

    seasons = [int(s.strip()) for s in args.seasons.split(",") if s.strip()]
    summaries = run(
        seasons, rate_limit_seconds=args.rate_limit_seconds, max_games_per_season=args.max_games_per_season,
    )
    print(json.dumps(summaries, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
