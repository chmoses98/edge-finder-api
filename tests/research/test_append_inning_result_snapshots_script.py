#!/usr/bin/env python3
"""
tests/research/test_append_inning_result_snapshots_script.py
==================================================================
Model Performance Phase 2A Part 13 -- tests for
scripts/research/append_inning_result_snapshots.py.
"""
import hashlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS_RESEARCH_DIR = os.path.join(ROOT, "scripts", "research")
sys.path.insert(0, ROOT)
sys.path.insert(0, SCRIPTS_RESEARCH_DIR)

import append_inning_result_snapshots as sn


def _hash_dir(path):
    if not os.path.isdir(path):
        return {}
    out = {}
    for name in sorted(os.listdir(path)):
        p = os.path.join(path, name)
        if os.path.isfile(p):
            with open(p, "rb") as f:
                out[name] = hashlib.sha256(f.read()).hexdigest()
    return out


class TestBuildRecordsByDate:

    def test_groups_by_date(self):
        rows = [
            {"date": "2026-07-24", "gameId": "G1", "matchup": "A@B", "scope": "F5",
             "marketStructure": "THREE_WAY", "outcome": "Away", "ticker": "T-1",
             "settlementStatus": "inferred", "snapshotTimestamp": "ts1"},
            {"date": "2026-07-25", "gameId": "G2", "matchup": "C@D", "scope": "F5",
             "marketStructure": "THREE_WAY", "outcome": "Home", "ticker": "T-2",
             "settlementStatus": "inferred", "snapshotTimestamp": "ts2"},
        ]
        by_date = sn.build_records_by_date(rows)
        assert set(by_date.keys()) == {"2026-07-24", "2026-07-25"}
        assert len(by_date["2026-07-24"]) == 1


class TestRunIdempotent:

    def test_rerun_produces_byte_identical_files(self):
        before = sn.run()
        before_hashes = _hash_dir(sn.SNAPSHOTS_DIR)
        after = sn.run()
        after_hashes = _hash_dir(sn.SNAPSHOTS_DIR)
        assert before == after
        assert before_hashes == after_hashes


class TestNoProductionMutation:

    def test_no_slate_or_bets_json_touched(self):
        def _hash(p):
            if not os.path.exists(p):
                return None
            with open(p, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()

        slate_path = os.path.join(ROOT, "data", "slate.json")
        bets_path = os.path.join(ROOT, "bets.json")
        before = (_hash(slate_path), _hash(bets_path))
        sn.run()
        after = (_hash(slate_path), _hash(bets_path))
        assert before == after

    def test_output_dir_under_data_research_only(self):
        assert os.path.normpath(sn.SNAPSHOTS_DIR).endswith(
            os.path.normpath("data/research/inning_result_snapshots")
        )
