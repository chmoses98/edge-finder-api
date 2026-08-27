#!/usr/bin/env python3
"""
scripts/edgelab/backtest/fetch_mlb_multiseason_bullpen_cache.py
====================================================================
Research Lab MLB-RSCH-0003: network adapter + deterministic on-disk
cache builder for the multi-season bullpen workload backtest.

Reuses lib.edgelab.bullpen_usage's existing MLB Stats API network
adapters (fetch_team_recent_schedule, fetch_team_boxscore) and team-ID
map UNCHANGED -- this script only adds season-scale orchestration
(30 teams x N seasons), idempotent per-game caching, and a
compact per-game extraction (lib.edgelab.backtest.
bullpen_backtest_reconstruction.extract_pitcher_lines) on top.

CACHE LAYOUT (efficient, deterministic, safe to re-run)
------------------------------------------------------------
  data/research_cache/bullpen_backtest/<season>/schedules/<TEAM>.json
      Raw schedule response for one team's full regular season, cached
      once (skipped on a rerun unless --force-refresh-schedules).
  data/research_cache/bullpen_backtest/<season>/boxscores.jsonl.gz
      ONE compact record per gamePk, deduplicated by
      lib.edgelab.storage.append_records (id_field="gamePk") -- a game
      shared by two teams is fetched exactly once, never twice, and a
      rerun only fetches gamePks not already present. Each record is a
      compact EXTRACTION (away/home pitcher lines only, via
      extract_pitcher_lines), not the raw MLB API payload -- keeps the
      committed cache small (this mission's own "do not create
      enormous noisy commits" instruction).

RATE LIMITING
----------------
A fixed, conservative delay (default 0.25s) between requests --
this endpoint is public/free but this script is a considerate citizen,
not a maximum-throughput scraper. A multi-season run is expected to
take on the order of an hour, primarily bounded by this delay, not by
CPU. See .github/workflows/research-multiseason-bullpen-backtest.yml
for how this is dispatched with real network access (this repository's
own CI/local dev environments do not have outbound access to
statsapi.mlb.com).
"""
import argparse
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab.bullpen_usage import fetch_team_recent_schedule, fetch_team_boxscore, MLB_TEAM_ID_MAP
from lib.edgelab.backtest.bullpen_backtest_reconstruction import (
    extract_team_games_from_schedule,
    extract_pitcher_lines,
)
from lib.edgelab.storage import append_records, read_records

CACHE_ROOT = os.path.join(_ROOT, "data", "research_cache", "bullpen_backtest")
DEFAULT_RATE_LIMIT_SECONDS = 0.25
DEFAULT_SEASON_START_MMDD = "03-01"  # spring training onward -- gameType=R already filters to regular season
DEFAULT_SEASON_END_MMDD = "11-30"    # past the latest plausible World Series date


def season_date_range(season):
    return f"{season}-{DEFAULT_SEASON_START_MMDD}", f"{season}-{DEFAULT_SEASON_END_MMDD}"


def schedule_cache_path(season, team_abbr):
    return os.path.join(CACHE_ROOT, str(season), "schedules", f"{team_abbr}.json")


def boxscore_cache_path(season):
    return os.path.join(CACHE_ROOT, str(season), "boxscores.jsonl.gz")


def load_cached_schedule(season, team_abbr):
    path = schedule_cache_path(season, team_abbr)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def fetch_and_cache_schedule(season, team_abbr, team_id, force_refresh=False, rate_limit_seconds=DEFAULT_RATE_LIMIT_SECONDS):
    """Returns (schedule_dict_or_None, was_fetched_bool)."""
    path = schedule_cache_path(season, team_abbr)
    if os.path.exists(path) and not force_refresh:
        with open(path) as f:
            return json.load(f), False
    start, end = season_date_range(season)
    schedule = fetch_team_recent_schedule(team_id, start, end)
    if schedule is None:
        return None, False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(schedule, f, sort_keys=True)
    if rate_limit_seconds:
        time.sleep(rate_limit_seconds)
    return schedule, True


def already_cached_game_pks(season):
    path = boxscore_cache_path(season)
    return {row.get("gamePk") for row in read_records(path) if row.get("gamePk") is not None}


def fetch_and_cache_boxscores(season, game_pks, rate_limit_seconds=DEFAULT_RATE_LIMIT_SECONDS, max_games=None):
    """
    Fetches+caches every gamePk in `game_pks` not already cached for
    this season, deduplicated (a game shared by two teams' schedules is
    only ever requested once). Returns a summary dict, never raises on
    an individual game's fetch failure (recorded in failedGamePks
    instead, matching every network adapter this repo already uses:
    non-fatal by design).
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


def run(seasons, force_refresh_schedules=False, rate_limit_seconds=DEFAULT_RATE_LIMIT_SECONDS, max_games_per_season=None):
    """Orchestrates one full multi-season cache build. Returns a
    per-season summary list -- the caller (this script's CLI, or the
    GitHub Actions workflow step) decides what to do with it (print,
    write a job summary, etc)."""
    summaries = []
    for season in seasons:
        team_game_pks = set()
        schedule_fetch_count = 0
        for team_abbr, team_id in sorted(MLB_TEAM_ID_MAP.items()):
            schedule, fetched = fetch_and_cache_schedule(
                season, team_abbr, team_id,
                force_refresh=force_refresh_schedules, rate_limit_seconds=rate_limit_seconds,
            )
            if fetched:
                schedule_fetch_count += 1
            if not schedule:
                continue
            for g in extract_team_games_from_schedule(schedule, team_id):
                if g.get("gamePk") is not None:
                    team_game_pks.add(g["gamePk"])

        boxscore_summary = fetch_and_cache_boxscores(
            season, team_game_pks, rate_limit_seconds=rate_limit_seconds, max_games=max_games_per_season,
        )
        boxscore_summary["schedulesFetchedThisRun"] = schedule_fetch_count
        boxscore_summary["schedulesTotal"] = len(MLB_TEAM_ID_MAP)
        summaries.append(boxscore_summary)
    return summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seasons", required=True, help="Comma-separated seasons, e.g. 2022,2023,2024,2025,2026")
    parser.add_argument("--force-refresh-schedules", action="store_true")
    parser.add_argument("--rate-limit-seconds", type=float, default=DEFAULT_RATE_LIMIT_SECONDS)
    parser.add_argument("--max-games-per-season", type=int, default=None, help="Cap new boxscore fetches per season (testing/partial runs)")
    args = parser.parse_args()

    seasons = [int(s.strip()) for s in args.seasons.split(",") if s.strip()]
    summaries = run(
        seasons,
        force_refresh_schedules=args.force_refresh_schedules,
        rate_limit_seconds=args.rate_limit_seconds,
        max_games_per_season=args.max_games_per_season,
    )
    print(json.dumps(summaries, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
