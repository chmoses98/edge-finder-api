#!/usr/bin/env python3
"""
tests/test_clv_from_snapshot_official_and_late.py
=====================================================
Coverage for the new highest-priority path added to
scripts/clv_from_snapshot.py: registry official_closing_snapshot (Path
A.0), plus LATE_ONLY detection when only a post-first-pitch snapshot
exists. Complements the existing tests/test_clv_snapshot_pipeline.py
(Paths A/B/C, RFI side logic, CLV formula) without duplicating them.
"""
import json
import os
import sys
from datetime import datetime, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
sys.path.insert(0, ROOT)
sys.path.insert(0, SCRIPTS_DIR)


@pytest.fixture
def snap(tmp_path, monkeypatch):
    if "clv_from_snapshot" in sys.modules:
        del sys.modules["clv_from_snapshot"]
    import clv_from_snapshot as mod
    monkeypatch.setattr(mod, "REGISTRY_PATH", str(tmp_path / "registry.json"))
    return mod


def write_registry(path, registry):
    with open(path, "w") as f:
        json.dump({"registry": registry}, f)


class TestOfficialSnapshotPriority:

    def test_official_snapshot_wins_over_kalshi_search_archive(self, snap, tmp_path):
        write_registry(snap.REGISTRY_PATH, {
            "BOSATH": {
                "official_closing_snapshot": {
                    "snapshot_ts": "2026-07-31T01:35:00Z",
                    "prices": {"by_ticker": {
                        "T-1": {"ticker": "T-1", "mid": 0.55, "yes_ask": 0.56},
                    }},
                },
                "closing_snapshots": [],
            }
        })
        bet = {"marketTicker": "T-1", "betTimeLine": -120, "market": "ML",
               "date": "2026-07-30"}
        scheduled_ts = int(datetime(2026, 7, 31, 1, 40, tzinfo=timezone.utc).timestamp())
        # Archive ticker_index has a DIFFERENT (wrong) price — must be ignored.
        archive_index = {"T-1": {"market_ticker": "T-1", "mid": 0.20}}
        result = snap.resolve_clv_for_bet(bet, archive_index, "2026-07-31T01:00:00Z",
                                           "kalshi_search_2026-07-30.json", scheduled_ts)
        assert result["clvStatus"] == "OK"
        assert result["clvSource"].startswith("official_closing_snapshot")
        assert result["closingMidPct"] == 55.0

    def test_no_official_snapshot_falls_through_to_archive(self, snap, tmp_path):
        write_registry(snap.REGISTRY_PATH, {"BOSATH": {"closing_snapshots": []}})
        bet = {"marketTicker": "T-1", "betTimeLine": -120, "market": "ML",
               "date": "2026-07-30"}
        scheduled_ts = int(datetime(2026, 7, 31, 1, 40, tzinfo=timezone.utc).timestamp())
        archive_index = {"T-1": {"market_ticker": "T-1", "mid": 0.20, "yes_ask": 0.21}}
        result = snap.resolve_clv_for_bet(bet, archive_index, "2026-07-31T00:00:00Z",
                                           "kalshi_search_2026-07-30.json", scheduled_ts)
        assert result["clvStatus"] == "OK"
        assert "kalshi_registry_snapshot" in result["clvSource"]
        assert result["closingImpliedPct"] == 20.0


class TestLateOnlyDetection:

    def test_only_late_registry_snapshot_yields_late_only(self, snap, tmp_path):
        write_registry(snap.REGISTRY_PATH, {
            "BOSATH": {
                "closing_snapshots": [{
                    "snapshot_ts": "2026-07-31T01:42:00Z",  # after 01:40 first pitch
                    "capture_timing": "LATE",
                    "prices": {"by_ticker": {"T-1": {"ticker": "T-1", "mid": 0.60, "yes_ask": 0.61}}},
                }],
            }
        })
        bet = {"marketTicker": "T-1", "betTimeLine": -120, "market": "ML",
               "date": "2026-07-30"}
        scheduled_ts = int(datetime(2026, 7, 31, 1, 40, tzinfo=timezone.utc).timestamp())
        result = snap.resolve_clv_for_bet(bet, {}, "", "", scheduled_ts)
        assert result["clvStatus"] == "LATE_ONLY"
        assert result["clv"] is None
        assert result["closingPrice"] is None

    def test_no_data_at_all_yields_fail_no_snapshot_not_late_only(self, snap, tmp_path):
        write_registry(snap.REGISTRY_PATH, {"BOSATH": {"closing_snapshots": []}})
        bet = {"marketTicker": "T-NONEXISTENT", "betTimeLine": -120, "market": "ML",
               "date": "2026-07-30"}
        scheduled_ts = int(datetime(2026, 7, 31, 1, 40, tzinfo=timezone.utc).timestamp())
        result = snap.resolve_clv_for_bet(bet, {}, "", "", scheduled_ts)
        assert result["clvStatus"] == "FAIL_NO_SNAPSHOT_PRICE"


class TestAskAndMidBothStored:

    def test_ask_and_mid_pct_both_populated_on_success(self, snap, tmp_path):
        write_registry(snap.REGISTRY_PATH, {
            "BOSATH": {
                "official_closing_snapshot": {
                    "snapshot_ts": "2026-07-31T01:35:00Z",
                    "prices": {"by_ticker": {
                        "T-1": {"ticker": "T-1", "mid": 0.55, "yes_ask": 0.57, "yes_bid": 0.53},
                    }},
                },
                "closing_snapshots": [],
            }
        })
        bet = {"marketTicker": "T-1", "betTimeLine": -120, "market": "ML",
               "date": "2026-07-30"}
        scheduled_ts = int(datetime(2026, 7, 31, 1, 40, tzinfo=timezone.utc).timestamp())
        result = snap.resolve_clv_for_bet(bet, {}, "", "", scheduled_ts)
        assert result["closingMidPct"] == 55.0
        assert result["closingAskPct"] == 57.0
        assert "clvMidPct" in result
        assert "clvAskPct" in result
        assert result["clvAskPct"] != result["clvMidPct"]  # ask and mid diverge here
