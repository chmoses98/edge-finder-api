#!/usr/bin/env python3
"""
scripts/build_hitter_feature_board.py
========================================
Hitter Projection Engine -- Phase 1 canonical feature foundation.

I/O-only wrapper around lib.research.hitter_feature_context.
build_hitter_feature_context() -- all schema/field logic lives there;
this script only reads data/slate.json (and, optionally,
data/weather.json / data/savant_team.json for the caller-supplied
lookups that module accepts), calls it once per offense side per game,
and writes the combined result as a new, additive pipeline artifact.

Writes data/pipeline/<date>/hitter_features.json via
lib.pipeline_artifacts.write_stage_artifact() -- mirrors
scripts/build_projection_board.py's exact failure posture: never touches
data/slate.json, bets.json, config/rules.json, marketLedger, or any
settlement/staking/risk-gate file, and never fails the pipeline -- a
missing input file or write error is reported and the script exits 0.

This script is deliberately NOT wired into .github/workflows/fetch-slate.yml
in this phase -- it is a standalone, safe-to-run-anytime foundation
artifact, not yet a required pipeline stage (see docs/HITTER_FEATURE_FOUNDATION.md).
"""
import json
import os
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from lib.research.hitter_feature_context import build_hitter_feature_context  # noqa: E402
from lib.pipeline_artifacts import write_stage_artifact  # noqa: E402

DEFAULT_SLATE_PATH = os.path.join("data", "slate.json")
DEFAULT_WEATHER_PATH = os.path.join("data", "weather.json")
DEFAULT_SAVANT_TEAM_PATH = os.path.join("data", "savant_team.json")


def load_json(path):
    with open(path) as f:
        return json.load(f)


def _weather_by_team(weather_path):
    try:
        doc = load_json(weather_path)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}, None
    parks = doc.get("parks") or []
    return {p.get("team"): p for p in parks if p.get("team")}, doc.get("updatedAt")


def _savant_batters(savant_team_path):
    try:
        doc = load_json(savant_team_path)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}, None
    return doc.get("batters") or {}, doc.get("fetchedAt")


def main(date_str=None, slate_path=None, weather_path=None, savant_team_path=None, dry_run=False):
    slate_path = slate_path or DEFAULT_SLATE_PATH
    weather_path = weather_path or DEFAULT_WEATHER_PATH
    savant_team_path = savant_team_path or DEFAULT_SAVANT_TEAM_PATH

    try:
        slate_doc = load_json(slate_path)
    except FileNotFoundError:
        print(f"[build_hitter_feature_board] No slate file at {slate_path} — nothing to build")
        return {"date": date_str, "status": "NO_SLATE_FILE", "totalHitters": 0}

    date_str = date_str or slate_doc.get("date")
    weather_lookup, weather_updated_at = _weather_by_team(weather_path)
    savant_batters, savant_fetched_at = _savant_batters(savant_team_path)
    source_meta = {
        "weatherUpdatedAt": weather_updated_at,
        "savantTeamFetchedAt": savant_fetched_at,
        "savantBatters": savant_batters,
    }

    games_out = []
    total_hitters = 0
    confirmed_games = 0
    for g in slate_doc.get("games") or []:
        away_ctx = build_hitter_feature_context(g, "away", weather_by_team=weather_lookup, source_meta=source_meta)
        home_ctx = build_hitter_feature_context(g, "home", weather_by_team=weather_lookup, source_meta=source_meta)
        total_hitters += len(away_ctx.get("hitters") or []) + len(home_ctx.get("hitters") or [])
        if away_ctx.get("hitters") or home_ctx.get("hitters"):
            confirmed_games += 1
        # dataFreshness.savantBatters is a large lookup table duplicated
        # onto every hitter for the pure function's own convenience --
        # strip it back out of the persisted artifact so the file doesn't
        # balloon with the same batter->xwOBA map repeated per hitter.
        for ctx in (away_ctx, home_ctx):
            for hitter in ctx.get("hitters") or []:
                hitter.get("dataFreshness", {}).pop("savantBatters", None)
        games_out.append({"gameId": g.get("gameId"), "away": away_ctx, "home": home_ctx})

    summary = {
        "date": date_str,
        "totalGames": len(games_out),
        "gamesWithConfirmedHitters": confirmed_games,
        "totalHitterRecords": total_hitters,
    }

    if not dry_run and date_str:
        try:
            path = write_stage_artifact(
                "hitter_features", date_str, {"games": games_out, "summary": summary},
                produced_by="scripts/build_hitter_feature_board.py",
                source_stage="market_ledger",
            )
            summary = dict(summary, artifactPath=path)
        except Exception as e:
            print(f"[build_hitter_feature_board] WARNING: failed to write pipeline artifact: {e}")
            summary = dict(summary, artifactWriteError=str(e))

    return summary


if __name__ == "__main__":
    arg_date = sys.argv[1] if len(sys.argv) > 1 else None
    result = main(date_str=arg_date)
    print(json.dumps(result, indent=2))
