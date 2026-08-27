#!/usr/bin/env python3
"""
scripts/edgelab/backtest/fetch_mlb_starter_workload_cache.py
====================================================================
Research Lab MLB-RSCH-0004: network adapter + deterministic on-disk
cache builder for the multi-season starter workload/rest backtest.

REUSE, NOT DUPLICATION
--------------------------
Reuses MLB-RSCH-0003's already-committed schedule cache
(data/research_cache/bullpen_backtest/<season>/schedules/<TEAM>.json)
READ-ONLY -- no new schedule fetch, since schedules need no new fields
for this milestone. Reuses lib.edgelab.bullpen_usage's
fetch_team_recent_schedule/fetch_team_boxscore/MLB_TEAM_ID_MAP and
lib.edgelab.backtest.bullpen_backtest_reconstruction's
extract_team_games_from_schedule/extract_pitcher_lines UNCHANGED --
this script only adds a SEPARATE boxscore cache under
data/research_cache/starter_workload/ because extract_pitcher_lines was
extended (battersFaced/strikeOuts/baseOnBalls/hits) for this milestone,
and MLB-RSCH-0003's already-committed boxscore cache predates that
extension -- a namespaced re-fetch is a schema migration, not a
redundant data system (see extract_pitcher_lines' own docstring note).

Same idempotent/resumable/rate-limited design as
fetch_mlb_multiseason_bullpen_cache.py (deduplicated by gamePk across
teams, safe to re-run, skips already-cached games).
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
from lib.edgelab.backtest.bullpen_backtest_reconstruction import (
    extract_team_games_from_schedule,
    extract_pitcher_lines,
)
from lib.edgelab.storage import append_records, read_records

BULLPEN_CACHE_ROOT = os.path.join(_ROOT, "data", "research_cache", "bullpen_backtest")
STARTER_CACHE_ROOT = os.path.join(_ROOT, "data", "research_cache", "starter_workload")
DEFAULT_RATE_LIMIT_SECONDS = 0.25


def reused_schedule_cache_path(season, team_abbr):
    """The EXISTING MLB-RSCH-0003 schedule cache -- read-only, never
    written here."""
    return os.path.join(BULLPEN_CACHE_ROOT, str(season), "schedules", f"{team_abbr}.json")


def load_reused_schedule(season, team_abbr):
    path = reused_schedule_cache_path(season, team_abbr)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def boxscore_cache_path(season):
    return os.path.join(STARTER_CACHE_ROOT, str(season), "boxscores.jsonl.gz")


def already_cached_game_pks(season):
    path = boxscore_cache_path(season)
    return {row.get("gamePk") for row in read_records(path) if row.get("gamePk") is not None}


def fetch_and_cache_boxscores(season, game_pks, rate_limit_seconds=DEFAULT_RATE_LIMIT_SECONDS, max_games=None):
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
            "awayPitchers": extract_pitcher_lines(box, "away"),
            "homePitchers": extract_pitcher_lines(box, "home"),
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
    summaries = []
    for season in seasons:
        team_game_pks = set()
        missing_schedules = []
        for team_abbr, team_id in sorted(MLB_TEAM_ID_MAP.items()):
            schedule = load_reused_schedule(season, team_abbr)
            if not schedule:
                missing_schedules.append(team_abbr)
                continue
            for g in extract_team_games_from_schedule(schedule, team_id):
                if g.get("gamePk") is not None:
                    team_game_pks.add(g["gamePk"])

        boxscore_summary = fetch_and_cache_boxscores(
            season, team_game_pks, rate_limit_seconds=rate_limit_seconds, max_games=max_games_per_season,
        )
        boxscore_summary["missingSchedules"] = missing_schedules
        summaries.append(boxscore_summary)
    return summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seasons", required=True, help="Comma-separated seasons, e.g. 2022,2023,2024,2025,2026")
    parser.add_argument("--rate-limit-seconds", type=float, default=DEFAULT_RATE_LIMIT_SECONDS)
    parser.add_argument("--max-games-per-season", type=int, default=None)
    args = parser.parse_args()

    seasons = [int(s.strip()) for s in args.seasons.split(",") if s.strip()]
    summaries = run(seasons, rate_limit_seconds=args.rate_limit_seconds, max_games_per_season=args.max_games_per_season)
    print(json.dumps(summaries, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
