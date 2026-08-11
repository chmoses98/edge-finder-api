#!/usr/bin/env python3
"""
lib/research/player_metric_snapshot_store.py
================================================
Hitter Projection Engine -- Phase 3 generic dated-snapshot store.

Generalizes the exact pattern PR #79's lib/research/bat_tracking_store.py
introduced (one dated row per (playerId, asOfDate), never overwritten,
reused via lib.edgelab.storage's append-with-dedup JSONL primitives) so
Phase 3's new leaderboard-style metrics (defensive OAA, sprint speed,
catcher framing) don't each reimplement the same ~40 lines. bat_tracking_store
itself now delegates here (see that module) rather than duplicating this
logic a second time.

Every metric family gets its own JSONL path and its own field allowlist
(so an unresolved column stays an honest `None`, never silently absent),
but shares this module's snapshot_id/ingest/load/latest functions.
"""

import os
from typing import Optional

from lib.edgelab.storage import append_records, read_records


class MetricSnapshotStore:
    """One instance per metric family (e.g. bat-tracking, OAA, sprint speed)."""

    def __init__(self, path: str, fields, id_field_name: str = "snapshotId"):
        self.path = path
        self.fields = tuple(fields)
        self.id_field_name = id_field_name

    def snapshot_id(self, player_id, as_of_date: str) -> str:
        return f"{player_id}:{as_of_date}"

    def record_snapshot(self, player_id, as_of_date: str, fetched_at: str, values: dict) -> dict:
        row = {
            self.id_field_name: self.snapshot_id(player_id, as_of_date),
            "playerId": str(player_id),
            "asOfDate": as_of_date,
            "fetchedAt": fetched_at,
        }
        for f in self.fields:
            row[f] = values.get(f)
        return row

    def ingest_snapshots(self, rows) -> tuple:
        return append_records(self.path, rows, id_field=self.id_field_name)

    def load_history(self, player_id, as_of: Optional[str] = None):
        player_id_str = str(player_id)
        rows = [
            r for r in read_records(self.path)
            if r.get("playerId") == player_id_str and (as_of is None or r.get("asOfDate") < as_of)
        ]
        return sorted(rows, key=lambda r: r.get("asOfDate") or "")

    def latest_snapshot(self, player_id, as_of: Optional[str] = None) -> Optional[dict]:
        history = self.load_history(player_id, as_of=as_of)
        return history[-1] if history else None


def game_dated_store_path(name: str) -> str:
    return os.path.join("data", f"{name}_history.jsonl")
