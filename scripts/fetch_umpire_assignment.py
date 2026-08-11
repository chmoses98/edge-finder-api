#!/usr/bin/env python3
"""
scripts/fetch_umpire_assignment.py
=====================================
Hitter Projection Engine -- Phase 3 umpire identity capture.

Reuses scripts/fetch_lineups.py's existing fetch_boxscore() (same MLB
Stats API host/endpoint every other pregame identity fetch in this repo
already uses -- no new vendor, no duplicated HTTP helper) and reads the
same boxscore response's `officials` list this repo has never parsed
before.

SNAPSHOT SAFETY (HISTORICALLY RECONSTRUCTABLE vs PROSPECTIVE SNAPSHOT
REQUIRED -- see this mission's own classification requirement):
umpire crew assignments are typically only known same-day, sometimes
only a few hours pregame -- MLB Stats API's boxscore endpoint always
has SOME officials data post-game (fully historically reconstructable
after the fact), but a historical BACKTEST must never pull "whoever the
API says worked that game" as if it had been knowable at projection
time for a date that has already been played. This script's storage
enforces that distinction structurally: data/umpire_assignments.jsonl is
written via lib.edgelab.storage.append_records() keyed on gamePk alone
-- the FIRST successful capture for a given gamePk is permanent (a
second call for the same gamePk is a guaranteed no-op, never an
overwrite), so this file only ever reflects "the earliest assignment
this pipeline actually observed," never a later, more-complete postgame
lookup silently replacing it. A caller doing historical replay must only
trust an assignment whose capturedAt precedes the game's own start time
(see build_hitter_feature_board.py's wiring) -- this script does not
enforce that itself (it has no game start-time input), it only
guarantees the row is never rewritten once written.

Non-fatal by design, matching every other MLB-Stats-API-dependent
script in this repo: a fetch failure or missing officials data is
reported and the script exits 0 with nothing written.
"""
import json
import os
import sys
from datetime import datetime, timezone

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from scripts.fetch_lineups import fetch_boxscore  # noqa: E402
from lib.edgelab.storage import append_records, read_records  # noqa: E402

UMPIRE_ASSIGNMENTS_PATH = os.path.join("data", "umpire_assignments.jsonl")

_HOME_PLATE_LABELS = {"Home Plate", "HP", "Home Plate Umpire"}


def parse_umpire_assignment(data, game_pk, captured_at=None) -> dict:
    """
    Pure function: given an already-fetched boxscore dict (or a falsy
    value if the fetch failed), returns the home-plate umpire record or
    a MISSING_DATA record -- never raises, never guesses an umpire when
    the boxscore doesn't have officials data yet.
    """
    captured_at = captured_at or datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not data:
        return {"gamePk": game_pk, "status": "MISSING_DATA", "capturedAt": captured_at,
                "umpireId": None, "umpireName": None, "reason": "boxscore fetch failed"}

    for official in (data.get("officials") or []):
        if official.get("officialType") in _HOME_PLATE_LABELS:
            person = official.get("official") or {}
            umpire_id = person.get("id")
            return {
                "gamePk": game_pk, "status": "AVAILABLE", "capturedAt": captured_at,
                "umpireId": str(umpire_id) if umpire_id is not None else None,
                "umpireName": person.get("fullName"),
                "reason": None,
            }

    return {"gamePk": game_pk, "status": "MISSING_DATA", "capturedAt": captured_at,
            "umpireId": None, "umpireName": None, "reason": "no home-plate official in boxscore yet"}


def load_umpire_assignment(game_pk):
    game_pk_str = str(game_pk)
    for row in read_records(UMPIRE_ASSIGNMENTS_PATH):
        if str(row.get("gamePk")) == game_pk_str:
            return row
    return None


def main(game_pks):
    results = []
    for game_pk in game_pks:
        existing = load_umpire_assignment(game_pk)
        if existing is not None:
            results.append({"gamePk": game_pk, "status": "ALREADY_CAPTURED"})
            continue
        data = fetch_boxscore(game_pk)
        record = parse_umpire_assignment(data, game_pk)
        written, _skipped = append_records(UMPIRE_ASSIGNMENTS_PATH, [record], id_field="gamePk")
        results.append({"gamePk": game_pk, "status": "CAPTURED" if written else "ALREADY_CAPTURED",
                         "umpireStatus": record["status"]})

    return {
        "totalGames": len(game_pks),
        "alreadyCaptured": sum(1 for r in results if r["status"] == "ALREADY_CAPTURED"),
        "newlyCaptured": sum(1 for r in results if r["status"] == "CAPTURED"),
        "results": results,
    }


if __name__ == "__main__":
    arg_pks = [int(x) for x in sys.argv[1:]] if len(sys.argv) > 1 else []
    result = main(arg_pks)
    print(json.dumps(result, indent=2))
