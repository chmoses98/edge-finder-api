#!/usr/bin/env python3
"""
lib/research/bat_tracking_store.py
=====================================
Hitter Projection Engine -- Phase 2 bat-tracking history.

Storage design: one dated snapshot row per (playerId, asOfDate) in a
single append-with-dedup JSONL file, reusing lib.edgelab.storage's
existing primitives (same reuse rationale as
lib.research.statcast_pitch_store). A snapshot is never overwritten by a
later fetch -- appending a new date's snapshot for a player is additive,
so trend comparisons (rolling recent state vs. a longer baseline, per
this mission's "recent change support" requirement) have real history to
compare against instead of only ever seeing today's value.

Every field in a snapshot is either the real value Baseball Savant's
bat-tracking leaderboard returned for that player on that date, or None
-- see api/savantbattracking.js's own docstring for the column-name
verification caveat this module inherits.
"""

import os
from typing import Optional

from lib.edgelab.storage import append_records, read_records

BAT_TRACKING_PATH = os.path.join("data", "bat_tracking_history.jsonl")

BAT_TRACKING_FIELDS = (
    "avgBatSpeed", "maxBatSpeed", "fastSwingPct", "squaredUpRate",
    "squaredUpPerSwing", "blastRate", "swingLength", "attackAngle",
    "idealAttackAngleRate", "attackDirection", "swingTilt", "attempts",
)


def snapshot_id(player_id, as_of_date: str) -> str:
    return f"{player_id}:{as_of_date}"


def record_snapshot(player_id, as_of_date: str, fetched_at: str, fields: dict) -> dict:
    """Build one snapshot row (does not write it -- callers batch via ingest_snapshots)."""
    row = {"battingTrackingId": snapshot_id(player_id, as_of_date), "playerId": str(player_id),
           "asOfDate": as_of_date, "fetchedAt": fetched_at}
    for f in BAT_TRACKING_FIELDS:
        row[f] = fields.get(f)
    return row


def ingest_snapshots(rows) -> tuple:
    """Append a batch of snapshot rows (see record_snapshot), deduplicated by (playerId, asOfDate). Idempotent."""
    return append_records(BAT_TRACKING_PATH, rows, id_field="battingTrackingId")


def load_history(player_id, as_of: Optional[str] = None):
    """
    All archived snapshots for `player_id`, sorted oldest-first,
    optionally bounded to asOfDate < as_of (same exclusive-cutoff
    contract as statcast_pitch_store.load_pitches_for_batter).
    """
    player_id_str = str(player_id)
    rows = [
        r for r in read_records(BAT_TRACKING_PATH)
        if r.get("playerId") == player_id_str and (as_of is None or r.get("asOfDate") < as_of)
    ]
    return sorted(rows, key=lambda r: r.get("asOfDate") or "")


def latest_snapshot(player_id, as_of: Optional[str] = None) -> Optional[dict]:
    history = load_history(player_id, as_of=as_of)
    return history[-1] if history else None
