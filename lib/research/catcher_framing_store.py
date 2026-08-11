#!/usr/bin/env python3
"""
lib/research/catcher_framing_store.py
========================================
Hitter Projection Engine -- Phase 3 catcher framing/called-strike history.

Same dated-snapshot-never-overwritten pattern as
lib.research.bat_tracking_store, via lib.research.player_metric_snapshot_store.
Keyed by catcher playerId. blocking/pop-time are intentionally NOT
included as fields here -- this mission's spec explicitly deprioritizes
them ("do not make blocking/pop-time a priority... unless already
available cheaply"), and no cheap/already-fetched source for them exists.
"""

import os
from typing import Optional

from lib.research.player_metric_snapshot_store import MetricSnapshotStore

CATCHER_FRAMING_HISTORY_PATH = os.path.join("data", "catcher_framing_history.jsonl")

CATCHER_FRAMING_FIELDS = ("framingRunsExtra", "strikeRatePlusMinus")

_store = MetricSnapshotStore(CATCHER_FRAMING_HISTORY_PATH, CATCHER_FRAMING_FIELDS, id_field_name="catcherFramingSnapshotId")


def record_snapshot(player_id, as_of_date: str, fetched_at: str, fields: dict) -> dict:
    return _store.record_snapshot(player_id, as_of_date, fetched_at, fields)


def ingest_snapshots(rows) -> tuple:
    return _store.ingest_snapshots(rows)


def load_history(player_id, as_of: Optional[str] = None):
    return _store.load_history(player_id, as_of=as_of)


def latest_snapshot(player_id, as_of: Optional[str] = None) -> Optional[dict]:
    return _store.latest_snapshot(player_id, as_of=as_of)
