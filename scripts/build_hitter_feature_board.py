#!/usr/bin/env python3
"""
scripts/build_hitter_feature_board.py
========================================
Hitter Projection Engine -- canonical feature foundation (Phase 1 +
Phase 2 raw-pitch/bat-tracking wiring + Phase 3 environment/contact-
conversion context).

I/O-only wrapper around lib.research.hitter_feature_context.
build_hitter_feature_context() -- all schema/field logic lives there;
this script reads data/slate.json, data/weather.json, data/savant_team.json
(including Phase 2's battersDiscipline map), each confirmed batter's
archived raw pitch history (lib.research.statcast_pitch_store, as-of
this run's date -- no leakage), bat-tracking history
(lib.research.bat_tracking_store), and Phase 3's defense/sprint-speed/
catcher-framing histories + umpire assignments, then calls the pure
feature-context builder once per offense side per game and writes the
combined result as a new, additive pipeline artifact. Every new lookup
here is READ-ONLY against already-archived data -- this script never
triggers a live fetch itself, so a slate run never blocks on Savant/MLB
Stats API network calls; populating those archives is each dedicated
scripts/fetch_*.py's job, run separately and ahead of time.

Writes data/pipeline/<date>/hitter_features.json via
lib.pipeline_artifacts.write_stage_artifact() -- mirrors
scripts/build_projection_board.py's exact failure posture: never touches
data/slate.json, bets.json, config/rules.json, marketLedger, or any
settlement/staking/risk-gate file, and never fails the pipeline -- a
missing input file or write error is reported and the script exits 0.

This script is deliberately NOT wired into .github/workflows/fetch-slate.yml
in this phase -- it is a standalone, safe-to-run-anytime foundation
artifact, not yet a required pipeline stage (see
docs/HITTER_FEATURE_FOUNDATION.md, docs/HITTER_STATCAST_FOUNDATION.md).
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
from lib.research.statcast_pitch_store import load_pitches_for_batter  # noqa: E402
from lib.research.bat_tracking_store import load_history as load_bat_tracking_history  # noqa: E402
from lib.research.defense_store import latest_snapshot as latest_defense_snapshot  # noqa: E402
from lib.research.sprint_speed_store import latest_snapshot as latest_sprint_speed_snapshot  # noqa: E402
from lib.research.catcher_framing_store import latest_snapshot as latest_catcher_framing_snapshot  # noqa: E402
from scripts.fetch_umpire_assignment import load_umpire_assignment  # noqa: E402

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
        return {}, {}, None
    return doc.get("batters") or {}, doc.get("battersDiscipline") or {}, doc.get("fetchedAt")


def _confirmed_batter_ids(slate_doc):
    ids = set()
    for g in slate_doc.get("games") or []:
        for side in ("awayTeamStats", "homeTeamStats"):
            for h in ((g.get(side) or {}).get("confirmedLineup") or []):
                pid = h.get("playerId")
                if pid is not None:
                    ids.add(str(pid))
    return ids


def _load_raw_pitches(batter_ids, as_of_date):
    """
    Load each confirmed batter's archived raw pitch history, bounded by
    as_of_date (no-leakage: only pitches strictly before this run's
    date). Reads from the local archive only -- never triggers a fetch;
    populating the archive is scripts/fetch_statcast_pitch_log.py's job,
    run separately (and ahead of time) so a slate run never blocks on a
    live Statcast fetch.
    """
    out = {}
    for pid in batter_ids:
        pitches = load_pitches_for_batter(pid, as_of=as_of_date)
        if pitches:
            out[pid] = pitches
    return out


def _load_bat_tracking(batter_ids, as_of_date):
    out = {}
    for pid in batter_ids:
        history = load_bat_tracking_history(pid, as_of=as_of_date)
        if history:
            out[pid] = {"latest": history[-1], "history": history}
    return out


def _confirmed_team_abbrs(slate_doc):
    abbrs = set()
    for g in slate_doc.get("games") or []:
        for side in ("away", "home"):
            abbr = (g.get(side) or {}).get("abbr")
            if abbr:
                abbrs.add(abbr)
    return abbrs


def _confirmed_catcher_ids(slate_doc):
    ids = set()
    for g in slate_doc.get("games") or []:
        for side in ("awayTeamStats", "homeTeamStats"):
            for h in ((g.get(side) or {}).get("confirmedLineup") or []):
                if h.get("position") == "C" and h.get("playerId") is not None:
                    ids.add(str(h["playerId"]))
    return ids


def _load_defense(team_abbrs, as_of_date):
    out = {}
    for abbr in team_abbrs:
        snapshot = latest_defense_snapshot(abbr, as_of=as_of_date)
        if snapshot:
            out[abbr] = snapshot
    return out


def _load_sprint_speed(batter_ids, as_of_date):
    out = {}
    for pid in batter_ids:
        snapshot = latest_sprint_speed_snapshot(pid, as_of=as_of_date)
        if snapshot:
            out[pid] = snapshot
    return out


def _load_catcher_framing(catcher_ids, as_of_date):
    out = {}
    for pid in catcher_ids:
        snapshot = latest_catcher_framing_snapshot(pid, as_of=as_of_date)
        if snapshot:
            out[pid] = snapshot
    return out


def _load_umpire_assignments(slate_doc):
    """
    Only loads an ALREADY-captured assignment (scripts/fetch_umpire_assignment.py
    populates data/umpire_assignments.jsonl separately, ahead of time) --
    this function never fetches, so a slate run never blocks on a live
    MLB Stats API call here.
    """
    out = {}
    for g in slate_doc.get("games") or []:
        game_id = g.get("gameId")
        if game_id is None:
            continue
        record = load_umpire_assignment(game_id)
        if record:
            out[game_id] = record
    return out


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
    savant_batters, savant_batters_discipline, savant_fetched_at = _savant_batters(savant_team_path)

    batter_ids = _confirmed_batter_ids(slate_doc)
    team_abbrs = _confirmed_team_abbrs(slate_doc)
    catcher_ids = _confirmed_catcher_ids(slate_doc)
    raw_pitches_by_batter = _load_raw_pitches(batter_ids, date_str) if date_str else {}
    bat_tracking_by_batter = _load_bat_tracking(batter_ids, date_str) if date_str else {}
    defense_by_team = _load_defense(team_abbrs, date_str) if date_str else {}
    sprint_speed_by_batter = _load_sprint_speed(batter_ids, date_str) if date_str else {}
    catcher_framing_by_catcher = _load_catcher_framing(catcher_ids, date_str) if date_str else {}
    umpire_by_game = _load_umpire_assignments(slate_doc)

    source_meta = {
        "weatherUpdatedAt": weather_updated_at,
        "savantTeamFetchedAt": savant_fetched_at,
        "asOfDate": date_str,
        "savantBatters": savant_batters,
        "savantBattersDiscipline": savant_batters_discipline,
        "rawPitchesByBatter": raw_pitches_by_batter,
        "batTrackingByBatter": bat_tracking_by_batter,
        "defenseByTeam": defense_by_team,
        "sprintSpeedByBatter": sprint_speed_by_batter,
        "catcherFramingByCatcher": catcher_framing_by_catcher,
        "umpireByGame": umpire_by_game,
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
        games_out.append({"gameId": g.get("gameId"), "away": away_ctx, "home": home_ctx})

    summary = {
        "date": date_str,
        "totalGames": len(games_out),
        "gamesWithConfirmedHitters": confirmed_games,
        "totalHitterRecords": total_hitters,
        "battersWithRawPitchArchive": len(raw_pitches_by_batter),
        "battersWithBatTrackingHistory": len(bat_tracking_by_batter),
        "teamsWithDefenseSnapshot": len(defense_by_team),
        "battersWithSprintSpeedSnapshot": len(sprint_speed_by_batter),
        "catchersWithFramingSnapshot": len(catcher_framing_by_catcher),
        "gamesWithUmpireAssignment": len(umpire_by_game),
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
