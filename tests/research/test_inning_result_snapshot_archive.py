#!/usr/bin/env python3
"""
tests/research/test_inning_result_snapshot_archive.py
==========================================================
Model Performance Phase 2A Part 13 -- tests for
lib/research/inning_result_snapshot_archive.py.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lib.research.inning_result_snapshot_archive import (
    build_snapshot_record,
    merge_snapshots,
    apply_settlement,
)

ROW = {
    "date": "2026-07-29", "gameId": "SEALAD", "matchup": "SEA@LAD", "scope": "F5",
    "marketStructure": "THREE_WAY", "outcome": "Away", "ticker": "KXMLBF5-X-SEA",
    "canonicalModelProb": 0.42, "legacyConditionalProb": 0.52,
    "yesBid": 0.4, "yesAsk": 0.42, "noBid": 0.58, "noAsk": 0.6, "spread": 0.02, "volume": 100,
    "settlementStatus": "inferred", "snapshotTimestamp": "2026-07-29T22:10:00Z",
}


class TestBuildSnapshotRecord:

    def test_record_id_is_stable_composite_key(self):
        r = build_snapshot_record(ROW)
        assert r["recordId"] == "2026-07-29:SEALAD:F5:Away:KXMLBF5-X-SEA"

    def test_settlement_fields_always_none_at_projection_time(self):
        r = build_snapshot_record(ROW, {"projectionTimestamp": "2026-07-29T20:00:00Z"})
        assert r["settlementResult"] is None
        assert r["settlementTimestamp"] is None

    def test_raw_and_normalized_fields_both_present(self):
        r = build_snapshot_record(ROW)
        for key in ("yesBid", "yesAsk", "noBid", "noAsk", "canonicalModelProb", "legacyConditionalProb"):
            assert key in r

    def test_deterministic(self):
        r1 = build_snapshot_record(ROW)
        r2 = build_snapshot_record(ROW)
        assert r1 == r2

    def test_default_projection_version_and_distribution_family(self):
        r = build_snapshot_record(ROW)
        assert r["projectionVersion"] == "phase2a_v1"
        assert r["distributionFamily"] == "independent_poisson"


class TestMergeSnapshotsIdempotent:

    def test_empty_existing_plus_new_returns_new(self):
        r = build_snapshot_record(ROW)
        merged = merge_snapshots([], [r])
        assert merged == [r]

    def test_rerun_with_same_records_is_a_true_noop(self):
        r = build_snapshot_record(ROW)
        merged1 = merge_snapshots([], [r])
        merged2 = merge_snapshots(merged1, [r])
        assert merged1 == merged2

    def test_no_uncontrolled_duplicates(self):
        r = build_snapshot_record(ROW)
        merged = merge_snapshots([r], [r])
        assert len(merged) == 1

    def test_distinct_records_both_kept(self):
        r1 = build_snapshot_record(ROW)
        row2 = dict(ROW, outcome="Home", ticker="KXMLBF5-X-LAD")
        r2 = build_snapshot_record(row2)
        merged = merge_snapshots([], [r1, r2])
        assert len(merged) == 2

    def test_sorted_by_record_id(self):
        row_b = dict(ROW, gameId="ZZZ")
        row_a = dict(ROW, gameId="AAA")
        merged = merge_snapshots([], [build_snapshot_record(row_b), build_snapshot_record(row_a)])
        assert merged[0]["gameId"] == "AAA"

    def test_does_not_mutate_inputs(self):
        existing = [build_snapshot_record(ROW)]
        existing_copy = [dict(existing[0])]
        merge_snapshots(existing, [])
        assert existing == existing_copy


class TestApplySettlement:

    def test_settlement_attached_to_matching_record(self):
        r = build_snapshot_record(ROW)
        existing = [r]
        updated = apply_settlement(existing, r["recordId"], "Away", "2026-07-30T02:00:00Z")
        assert updated[0]["settlementResult"] == "Away"
        assert updated[0]["settlementTimestamp"] == "2026-07-30T02:00:00Z"

    def test_original_list_not_mutated(self):
        r = build_snapshot_record(ROW)
        existing = [r]
        apply_settlement(existing, r["recordId"], "Away", "2026-07-30T02:00:00Z")
        assert existing[0]["settlementResult"] is None

    def test_missing_record_id_raises_key_error(self):
        r = build_snapshot_record(ROW)
        with pytest.raises(KeyError):
            apply_settlement([r], "nonexistent:id", "Away", "2026-07-30T02:00:00Z")

    def test_no_future_leakage_other_records_untouched(self):
        r1 = build_snapshot_record(ROW)
        row2 = dict(ROW, outcome="Home", ticker="KXMLBF5-X-LAD")
        r2 = build_snapshot_record(row2)
        updated = apply_settlement([r1, r2], r1["recordId"], "Away", "2026-07-30T02:00:00Z")
        other = [r for r in updated if r["recordId"] == r2["recordId"]][0]
        assert other["settlementResult"] is None
