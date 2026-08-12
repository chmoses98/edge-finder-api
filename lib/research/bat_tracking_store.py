#!/usr/bin/env python3
"""
lib/research/bat_tracking_store.py
=====================================
Hitter Projection Engine -- Phase 2 bat-tracking history.

Storage design: one dated snapshot row per (playerId, asOfDate) in a
single append-with-dedup JSONL file. A snapshot is never overwritten by
a later fetch -- appending a new date's snapshot for a player is
additive, so trend comparisons (rolling recent state vs. a longer
baseline, per this mission's "recent change support" requirement) have
real history to compare against instead of only ever seeing today's value.

Every field in a snapshot is either the real value Baseball Savant's
bat-tracking leaderboard returned for that player on that date, or None
-- see api/savantbattracking.js's own docstring for the column-name
verification caveat this module inherits.

Phase 3: this module is now a thin wrapper around the generic
lib.research.player_metric_snapshot_store.MetricSnapshotStore (the same
engine Phase 3's new defense/sprint-speed/catcher-framing stores use) --
its own storage/dedup logic was extracted there rather than copied a
second, third, and fourth time. Every function below keeps its original
Phase 2 signature and behavior unchanged.
"""

import os
from typing import Optional

from lib.research.player_metric_snapshot_store import MetricSnapshotStore

BAT_TRACKING_PATH = os.path.join("data", "bat_tracking_history.jsonl")

BAT_TRACKING_FIELDS = (
    "avgBatSpeed", "maxBatSpeed", "fastSwingPct", "squaredUpRate",
    "squaredUpPerSwing", "blastRate", "swingLength", "attackAngle",
    "idealAttackAngleRate", "attackDirection", "swingTilt", "attempts",
)

_store = MetricSnapshotStore(BAT_TRACKING_PATH, BAT_TRACKING_FIELDS, id_field_name="battingTrackingId")


def snapshot_id(player_id, as_of_date: str) -> str:
    return _store.snapshot_id(player_id, as_of_date)


def record_snapshot(player_id, as_of_date: str, fetched_at: str, fields: dict) -> dict:
    """Build one snapshot row (does not write it -- callers batch via ingest_snapshots)."""
    return _store.record_snapshot(player_id, as_of_date, fetched_at, fields)


def ingest_snapshots(rows) -> tuple:
    """Append a batch of snapshot rows (see record_snapshot), deduplicated by (playerId, asOfDate). Idempotent."""
    return _store.ingest_snapshots(rows)


def load_history(player_id, as_of: Optional[str] = None):
    """
    All archived snapshots for `player_id`, sorted oldest-first,
    optionally bounded to asOfDate < as_of (same exclusive-cutoff
    contract as statcast_pitch_store.load_pitches_for_batter).
    """
    return _store.load_history(player_id, as_of=as_of)


def latest_snapshot(player_id, as_of: Optional[str] = None) -> Optional[dict]:
    return _store.latest_snapshot(player_id, as_of=as_of)
