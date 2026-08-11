#!/usr/bin/env python3
"""
tests/test_bat_tracking_store.py
===================================
Unit tests for lib/research/bat_tracking_store.py -- Hitter Projection
Engine Phase 2 bat-tracking history (dated snapshots, never overwritten).
"""
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.research.bat_tracking_store import record_snapshot, ingest_snapshots, load_history, latest_snapshot


class _TempCwd:
    def __enter__(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.tmpdir)
        return self.tmpdir

    def __exit__(self, *exc):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestBatTrackingHistory:
    def test_snapshot_never_overwritten_by_a_later_date(self):
        with _TempCwd():
            row1 = record_snapshot("1", "2026-06-01", "2026-06-01T00:00:00Z", {"avgBatSpeed": 71.0})
            row2 = record_snapshot("1", "2026-07-01", "2026-07-01T00:00:00Z", {"avgBatSpeed": 73.0})
            ingest_snapshots([row1])
            ingest_snapshots([row2])
            history = load_history("1")
            assert len(history) == 2
            assert history[0]["asOfDate"] == "2026-06-01"
            assert history[0]["avgBatSpeed"] == 71.0
            assert history[1]["asOfDate"] == "2026-07-01"
            assert history[1]["avgBatSpeed"] == 73.0

    def test_same_date_reingest_is_idempotent(self):
        with _TempCwd():
            row = record_snapshot("1", "2026-06-01", "2026-06-01T00:00:00Z", {"avgBatSpeed": 71.0})
            written1, _ = ingest_snapshots([row])
            written2, skipped2 = ingest_snapshots([row])
            assert written1 == 1
            assert written2 == 0
            assert skipped2 == 1

    def test_as_of_filters_future_snapshots(self):
        with _TempCwd():
            ingest_snapshots([
                record_snapshot("1", "2026-06-01", "t", {"avgBatSpeed": 71.0}),
                record_snapshot("1", "2026-08-01", "t", {"avgBatSpeed": 75.0}),
            ])
            history = load_history("1", as_of="2026-07-01")
            assert len(history) == 1
            assert history[0]["asOfDate"] == "2026-06-01"

    def test_latest_snapshot_respects_as_of(self):
        with _TempCwd():
            ingest_snapshots([
                record_snapshot("1", "2026-06-01", "t", {"avgBatSpeed": 71.0}),
                record_snapshot("1", "2026-08-01", "t", {"avgBatSpeed": 75.0}),
            ])
            assert latest_snapshot("1")["avgBatSpeed"] == 75.0
            assert latest_snapshot("1", as_of="2026-07-01")["avgBatSpeed"] == 71.0

    def test_unresolved_field_stays_none_not_fabricated(self):
        with _TempCwd():
            row = record_snapshot("1", "2026-06-01", "t", {"avgBatSpeed": 71.0})  # squaredUpRate omitted
            ingest_snapshots([row])
            snap = latest_snapshot("1")
            assert snap["squaredUpRate"] is None

    def test_no_history_returns_empty_list_and_none(self):
        with _TempCwd():
            assert load_history("nobody") == []
            assert latest_snapshot("nobody") is None
