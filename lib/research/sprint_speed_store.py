#!/usr/bin/env python3
"""
lib/research/sprint_speed_store.py
=====================================
Hitter Projection Engine -- Phase 3 hitter sprint-speed/baserunning history.

Same dated-snapshot-never-overwritten pattern as
lib.research.bat_tracking_store, via lib.research.player_metric_snapshot_store.
Keyed by batter playerId. Explicitly NOT used for stolen-base modeling in
this milestone (out of scope -- see docs/HITTER_ENVIRONMENT_FOUNDATION.md);
this is foundation data for future infield-hit/BABIP/extra-base-hit work only.
"""

import os
from typing import Optional

from lib.research.player_metric_snapshot_store import MetricSnapshotStore

SPRINT_SPEED_HISTORY_PATH = os.path.join("data", "sprint_speed_history.jsonl")

SPRINT_SPEED_FIELDS = ("sprintSpeedFtPerSec", "homeToFirstSec", "boltPct")

_store = MetricSnapshotStore(SPRINT_SPEED_HISTORY_PATH, SPRINT_SPEED_FIELDS, id_field_name="sprintSpeedSnapshotId")


def record_snapshot(player_id, as_of_date: str, fetched_at: str, fields: dict) -> dict:
    return _store.record_snapshot(player_id, as_of_date, fetched_at, fields)


def ingest_snapshots(rows) -> tuple:
    return _store.ingest_snapshots(rows)


def load_history(player_id, as_of: Optional[str] = None):
    return _store.load_history(player_id, as_of=as_of)


def latest_snapshot(player_id, as_of: Optional[str] = None) -> Optional[dict]:
    return _store.latest_snapshot(player_id, as_of=as_of)
