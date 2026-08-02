#!/usr/bin/env python3
"""
tests/test_clv_update_run_summary.py
========================================
Production Reliability and Settlement Recovery milestone: coverage for
clv_update.py's new machine-readable data/clv_update_run_summary.json
output (the observability item from the milestone). Purely additive --
every field is read from a variable main() already computed; no
settlement/CLV/pricing logic is touched or exercised differently.

Runs main() fully offline: fetch_scores (the only network call reached
when there are zero bets for the target date) is monkeypatched to a
no-op, so this never depends on network access.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture
def cu(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "dummy-test-key")
    if "clv_update" in sys.modules:
        del sys.modules["clv_update"]
    import clv_update as _cu
    monkeypatch.setattr(_cu, "fetch_scores", lambda date_str: {})
    return _cu


def _wire(cu, tmp_path, monkeypatch, bets):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    with open(tmp_path / "bets.json", "w") as f:
        json.dump(bets, f)
    return tmp_path


class TestZeroRecordsExplained:

    def test_no_bets_for_date_writes_summary_with_reason(self, cu, tmp_path, monkeypatch):
        root = _wire(cu, tmp_path, monkeypatch, bets=[
            {"id": "2026-01-01-001", "date": "2026-01-01", "game": "AAA @ BBB", "market": "ML",
             "status": "settled", "result": "WIN"},
        ])
        sys.argv = ["clv_update.py", "2026-08-02"]  # no bets logged for this date
        cu.main()

        summary_path = root / "data" / "clv_update_run_summary.json"
        assert summary_path.exists()
        summary = json.loads(summary_path.read_text())
        assert summary["recordsReadForDate"] == 0
        assert summary["zeroRecordsReason"] == "No bets logged for 2026-08-02"
        assert summary["date"] == "2026-08-02"
        assert summary["filesWritten"] == ["bets.json", "BET_LOG.md"]

    def test_summary_is_valid_json_and_atomic_no_stray_tmp(self, cu, tmp_path, monkeypatch):
        root = _wire(cu, tmp_path, monkeypatch, bets=[])
        sys.argv = ["clv_update.py", "2026-08-02"]
        cu.main()
        data_dir = root / "data"
        entries = os.listdir(data_dir)
        assert "clv_update_run_summary.json" in entries
        assert not any(name.startswith(".clv_update_run_summary.json.") for name in entries)


class TestSummaryReflectsRealCounts:

    def test_settled_bets_for_date_counted_correctly(self, cu, tmp_path, monkeypatch):
        bets = [
            {"id": "2026-08-02-001", "date": "2026-08-02", "game": "AAA @ BBB", "market": "ML",
             "status": "settled", "result": "WIN", "pl": 5.0},
            {"id": "2026-08-02-002", "date": "2026-08-02", "game": "CCC @ DDD", "market": "ML",
             "status": "settled", "result": "LOSS", "pl": -5.0},
        ]
        root = _wire(cu, tmp_path, monkeypatch, bets=bets)
        sys.argv = ["clv_update.py", "2026-08-02"]
        cu.main()
        summary = json.loads((root / "data" / "clv_update_run_summary.json").read_text())
        assert summary["recordsReadForDate"] == 2
        assert summary["zeroRecordsReason"] is None
        assert summary["recordsRealSettledTotal"] == 2

    def test_repeated_run_is_deterministic_given_unchanged_input(self, cu, tmp_path, monkeypatch):
        root = _wire(cu, tmp_path, monkeypatch, bets=[
            {"id": "2026-08-02-001", "date": "2026-08-02", "game": "AAA @ BBB", "market": "ML",
             "status": "settled", "result": "WIN", "pl": 5.0},
        ])
        sys.argv = ["clv_update.py", "2026-08-02"]
        cu.main()
        first = json.loads((root / "data" / "clv_update_run_summary.json").read_text())
        del first["generatedAt"]

        cu.main()
        second = json.loads((root / "data" / "clv_update_run_summary.json").read_text())
        del second["generatedAt"]
        assert first == second
