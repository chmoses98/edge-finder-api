#!/usr/bin/env python3
"""
tests/test_phase3_metric_stores.py
=====================================
Unit tests for lib/research/player_metric_snapshot_store.py (the
generic engine) and its three Phase 3 instances -- defense_store
(keyed by team), sprint_speed_store (keyed by batter),
catcher_framing_store (keyed by catcher) -- verifying each attaches to
the correct entity, degrades honestly on an unresolved field, and
respects the as-of no-leakage cutoff.
"""
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.research.player_metric_snapshot_store import MetricSnapshotStore
import lib.research.defense_store as defense_store
import lib.research.sprint_speed_store as sprint_speed_store
import lib.research.catcher_framing_store as catcher_framing_store


class _TempCwd:
    def __enter__(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.tmpdir)
        return self.tmpdir

    def __exit__(self, *exc):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestGenericMetricSnapshotStoreEngine:
    def test_idempotent_same_date_reingest(self):
        with _TempCwd():
            store = MetricSnapshotStore("data/generic_test.jsonl", ("fieldA",), id_field_name="id")
            row = store.record_snapshot("p1", "2026-06-01", "t", {"fieldA": 1})
            w1, _ = store.ingest_snapshots([row])
            w2, s2 = store.ingest_snapshots([row])
            assert w1 == 1
            assert w2 == 0
            assert s2 == 1

    def test_unresolved_field_stays_none(self):
        with _TempCwd():
            store = MetricSnapshotStore("data/generic_test.jsonl", ("fieldA", "fieldB"), id_field_name="id")
            row = store.record_snapshot("p1", "2026-06-01", "t", {"fieldA": 1})  # fieldB omitted
            store.ingest_snapshots([row])
            snap = store.latest_snapshot("p1")
            assert snap["fieldB"] is None


class TestDefenseStoreAttachesToCorrectTeam:
    def test_snapshot_keyed_by_team_abbr(self):
        with _TempCwd():
            row_a = defense_store.record_snapshot("NYY", "2026-06-01", "t", {"teamOAA": 12.0})
            row_b = defense_store.record_snapshot("BOS", "2026-06-01", "t", {"teamOAA": -5.0})
            defense_store.ingest_snapshots([row_a, row_b])
            assert defense_store.latest_snapshot("NYY")["teamOAA"] == 12.0
            assert defense_store.latest_snapshot("BOS")["teamOAA"] == -5.0

    def test_missing_defense_field_fails_honestly(self):
        with _TempCwd():
            row = defense_store.record_snapshot("NYY", "2026-06-01", "t", {"teamOAA": 12.0})
            defense_store.ingest_snapshots([row])
            snap = defense_store.latest_snapshot("NYY")
            assert snap["outfieldOAA"] is None
            assert snap["armStrengthOAA"] is None

    def test_future_defense_snapshot_excluded_by_as_of(self):
        with _TempCwd():
            defense_store.ingest_snapshots([
                defense_store.record_snapshot("NYY", "2026-06-01", "t", {"teamOAA": 12.0}),
                defense_store.record_snapshot("NYY", "2026-09-01", "t", {"teamOAA": 20.0}),
            ])
            snap = defense_store.latest_snapshot("NYY", as_of="2026-07-01")
            assert snap["teamOAA"] == 12.0


class TestSprintSpeedStoreAttachesToCorrectBatter:
    def test_snapshot_keyed_by_playerId(self):
        with _TempCwd():
            sprint_speed_store.ingest_snapshots([
                sprint_speed_store.record_snapshot("p1", "2026-06-01", "t", {"sprintSpeedFtPerSec": 28.5}),
                sprint_speed_store.record_snapshot("p2", "2026-06-01", "t", {"sprintSpeedFtPerSec": 24.0}),
            ])
            assert sprint_speed_store.latest_snapshot("p1")["sprintSpeedFtPerSec"] == 28.5
            assert sprint_speed_store.latest_snapshot("p2")["sprintSpeedFtPerSec"] == 24.0

    def test_missing_field_fails_honestly(self):
        with _TempCwd():
            sprint_speed_store.ingest_snapshots([
                sprint_speed_store.record_snapshot("p1", "2026-06-01", "t", {"sprintSpeedFtPerSec": 28.5}),
            ])
            snap = sprint_speed_store.latest_snapshot("p1")
            assert snap["boltPct"] is None

    def test_future_snapshot_excluded_by_as_of(self):
        with _TempCwd():
            sprint_speed_store.ingest_snapshots([
                sprint_speed_store.record_snapshot("p1", "2026-06-01", "t", {"sprintSpeedFtPerSec": 28.5}),
                sprint_speed_store.record_snapshot("p1", "2026-09-01", "t", {"sprintSpeedFtPerSec": 29.5}),
            ])
            assert sprint_speed_store.latest_snapshot("p1", as_of="2026-07-01")["sprintSpeedFtPerSec"] == 28.5


class TestCatcherFramingStoreAttachesToCorrectCatcher:
    def test_snapshot_keyed_by_catcher_playerId(self):
        with _TempCwd():
            catcher_framing_store.ingest_snapshots([
                catcher_framing_store.record_snapshot("c1", "2026-06-01", "t", {"framingRunsExtra": 5.2}),
                catcher_framing_store.record_snapshot("c2", "2026-06-01", "t", {"framingRunsExtra": -3.1}),
            ])
            assert catcher_framing_store.latest_snapshot("c1")["framingRunsExtra"] == 5.2
            assert catcher_framing_store.latest_snapshot("c2")["framingRunsExtra"] == -3.1

    def test_missing_field_fails_honestly(self):
        with _TempCwd():
            catcher_framing_store.ingest_snapshots([
                catcher_framing_store.record_snapshot("c1", "2026-06-01", "t", {"framingRunsExtra": 5.2}),
            ])
            snap = catcher_framing_store.latest_snapshot("c1")
            assert snap["strikeRatePlusMinus"] is None

    def test_future_snapshot_excluded_by_as_of(self):
        with _TempCwd():
            catcher_framing_store.ingest_snapshots([
                catcher_framing_store.record_snapshot("c1", "2026-06-01", "t", {"framingRunsExtra": 5.2}),
                catcher_framing_store.record_snapshot("c1", "2026-09-01", "t", {"framingRunsExtra": 8.0}),
            ])
            assert catcher_framing_store.latest_snapshot("c1", as_of="2026-07-01")["framingRunsExtra"] == 5.2
