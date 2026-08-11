#!/usr/bin/env python3
"""
lib/research/defense_store.py
================================
Hitter Projection Engine -- Phase 3 team/positional defense history.

Same dated-snapshot-never-overwritten pattern as
lib.research.bat_tracking_store, via the shared
lib.research.player_metric_snapshot_store engine. Keyed by TEAM
abbreviation (not an individual playerId) -- for hitter-matchup
purposes the relevant "opponent" is the fielding team's defense as a
whole (team OAA + aggregated infield/outfield range), not one specific
fielder, since this repo has no reliable, snapshot-safe source for
today's actual defensive alignment/lineup (see api/savantdefense.js's
own docstring for exactly what was attempted).
"""

import os
from typing import Optional

from lib.research.player_metric_snapshot_store import MetricSnapshotStore

DEFENSE_HISTORY_PATH = os.path.join("data", "defense_history.jsonl")

DEFENSE_FIELDS = ("teamOAA", "infieldOAA", "outfieldOAA", "armStrengthOAA")

_store = MetricSnapshotStore(DEFENSE_HISTORY_PATH, DEFENSE_FIELDS, id_field_name="defenseSnapshotId")


def record_snapshot(team_abbr, as_of_date: str, fetched_at: str, fields: dict) -> dict:
    return _store.record_snapshot(team_abbr, as_of_date, fetched_at, fields)


def ingest_snapshots(rows) -> tuple:
    return _store.ingest_snapshots(rows)


def load_history(team_abbr, as_of: Optional[str] = None):
    return _store.load_history(team_abbr, as_of=as_of)


def latest_snapshot(team_abbr, as_of: Optional[str] = None) -> Optional[dict]:
    return _store.latest_snapshot(team_abbr, as_of=as_of)
