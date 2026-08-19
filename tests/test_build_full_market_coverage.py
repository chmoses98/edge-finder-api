#!/usr/bin/env python3
"""
tests/test_build_full_market_coverage.py
============================================
CLI-level coverage for scripts/build_full_market_coverage.py: writes both
artifact shapes (the immutable pipeline-stage envelope and the flat
data/kalshi/discovery/<date>_coverage.json sibling file), never touches
data/slate.json or bets.json, and reports a clean zero-unaccounted
accounting for a small mixed-family fixture universe.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scripts.build_full_market_coverage import main  # noqa: E402
from lib.pipeline_artifacts import read_stage_artifact  # noqa: E402


def _write(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f)


def _search_doc():
    return {"date": "2026-08-19", "markets": [
        {"market_ticker": "KXMLBGAME-26AUG192040BOSNYY-BOS",
         "event_ticker": "KXMLBGAME-26AUG192040BOSNYY", "title": "x", "status": "active",
         "yes_bid": 40.0, "yes_ask": 45.0, "close_time": "2026-08-20T00:00:00Z", "volume": 10.0},
        {"market_ticker": "KXMLBHIT-26AUG192040BOSNYY-DEVERS1",
         "event_ticker": "KXMLBHIT-26AUG192040BOSNYY", "title": "Devers over 1.5 hits?",
         "status": "active", "yes_bid": 40.0, "yes_ask": 45.0,
         "close_time": "2026-08-20T00:00:00Z", "volume": 10.0},
    ]}


def _slate_doc():
    return {"games": [{
        "gameId": 1,
        "away": {"abbr": "BOS", "pitcherSavant": {"xFIP": 3.8, "avgIPperStart": 6.0}},
        "home": {"abbr": "NYY", "pitcherSavant": {"xFIP": 4.0, "avgIPperStart": 6.0}},
        "awayTeamStats": {"offenseBaselineAdj": 4.6},
        "homeTeamStats": {"offenseBaselineAdj": 4.3},
        "park": {"parkFactor": 100},
        "startTime": "2026-08-19T20:40:00Z",
        "status": "Scheduled",
    }]}


class TestBuildFullMarketCoverageCLI:

    def test_writes_flat_sibling_file_and_pipeline_artifact(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        search_path = tmp_path / "search.json"
        slate_path = tmp_path / "slate.json"
        out_dir = tmp_path / "discovery"
        _write(search_path, _search_doc())
        _write(slate_path, _slate_doc())

        result = main(date_str="2026-08-19", search_path=str(search_path),
                       slate_path=str(slate_path), out_dir=str(out_dir))

        assert result["status"] == "OK"
        assert result["accounting"]["archivedTotal"] == 2
        assert result["accounting"]["unaccountedCount"] == 0

        flat_path = out_dir / "2026-08-19_coverage.json"
        assert flat_path.exists()
        flat = json.loads(flat_path.read_text())
        assert len(flat["ledger"]) == 2

        envelope = read_stage_artifact("full_market_coverage", "2026-08-19")
        assert envelope["meta"]["stage"] == "full_market_coverage"
        assert envelope["meta"]["producedBy"] == "scripts/build_full_market_coverage.py"
        assert envelope["data"]["accounting"]["unaccountedCount"] == 0

    def test_never_writes_slate_json_or_bets_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        search_path = tmp_path / "search.json"
        slate_path = tmp_path / "slate.json"
        out_dir = tmp_path / "discovery"
        _write(search_path, _search_doc())
        _write(slate_path, _slate_doc())
        before = slate_path.read_text()

        main(date_str="2026-08-19", search_path=str(search_path),
             slate_path=str(slate_path), out_dir=str(out_dir))

        assert slate_path.read_text() == before
        assert not (tmp_path / "bets.json").exists()

    def test_missing_search_file_returns_clean_status(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = main(date_str="2026-08-19", search_path=str(tmp_path / "missing.json"),
                       slate_path=str(tmp_path / "slate.json"), out_dir=str(tmp_path / "discovery"))
        assert result["status"] == "NO_SEARCH_FILE"

    def test_missing_slate_falls_back_to_no_games_but_still_accounted_for(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        search_path = tmp_path / "search.json"
        _write(search_path, _search_doc())
        out_dir = tmp_path / "discovery"

        result = main(date_str="2026-08-19", search_path=str(search_path),
                       slate_path=str(tmp_path / "no_such_slate.json"), out_dir=str(out_dir))

        assert result["status"] == "OK"
        assert result["accounting"]["unaccountedCount"] == 0
