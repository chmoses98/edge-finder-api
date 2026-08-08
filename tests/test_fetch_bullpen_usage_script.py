#!/usr/bin/env python3
"""
tests/test_fetch_bullpen_usage_script.py
=============================================
Tests for scripts/fetch_bullpen_usage.py -- the merge-into-
data/bullpen.json orchestration (mirrors scripts/fetch_savant_bullpen_hl.py's
own merge pattern). Network calls are never made in these tests --
compute_team_recent_usage is monkeypatched to a deterministic stub, same
as this repo's convention for testing other MLB-Stats-API-driven
scripts without live network access.
"""
import ast
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
sys.path.insert(0, ROOT)
sys.path.insert(0, SCRIPTS_DIR)

import fetch_bullpen_usage as fbu

# Mirrors tests/test_check_kalshi_prices_safety_isolation.py's exact
# convention for a different data/context-only tool: this is bullpen
# CONTEXT for manual analysis, never a recommendation/staking/
# settlement/ledger input.
FORBIDDEN_MODULES = {
    "build_market_ledger", "risk_gate", "write_pending_bets",
    "protect_slate", "validate_slate_final",
}


def _imported_module_names(source_path):
    with open(source_path) as f:
        tree = ast.parse(f.read(), filename=source_path)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
    return names


class TestSafetyIsolation:

    def test_script_imports_no_forbidden_module(self):
        imported = _imported_module_names(os.path.join(SCRIPTS_DIR, "fetch_bullpen_usage.py"))
        assert not (imported & FORBIDDEN_MODULES), f"forbidden import: {imported & FORBIDDEN_MODULES}"

    def test_lib_module_imports_no_forbidden_module(self):
        imported = _imported_module_names(os.path.join(ROOT, "lib", "edgelab", "bullpen_usage.py"))
        assert not (imported & FORBIDDEN_MODULES), f"forbidden import: {imported & FORBIDDEN_MODULES}"


def _unavailable():
    return {
        "dataAvailable": False, "unavailableReason": "no_completed_games_in_window",
        "asOfDate": None, "gamesConsidered": 0,
        "relieversUsedLastGame": [], "backToBackRelievers": [],
        "recentPitchCounts": [], "highLeverageRecentUsage": [],
        "handednessMix": {"L": 0, "R": 0, "unknown": 0},
        "teamPitchCountLastGame": None, "teamPitchCountWindow": None,
    }


def _available(pitches=15):
    return {
        "dataAvailable": True, "unavailableReason": None, "asOfDate": "2026-08-07",
        "gamesConsidered": 1,
        "relieversUsedLastGame": [{"playerId": "1", "name": "Reliever", "numberOfPitches": pitches}],
        "backToBackRelievers": [],
        "recentPitchCounts": [{"playerId": "1", "name": "Reliever", "totalPitches": pitches, "appearances": 1}],
        "highLeverageRecentUsage": [],
        "handednessMix": {"L": 0, "R": 1, "unknown": 0},
        "teamPitchCountLastGame": pitches, "teamPitchCountWindow": pitches,
    }


class TestMainMerge:

    def test_merges_recent_usage_into_existing_teams_only(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs("data", exist_ok=True)
        with open("data/bullpen.json", "w") as f:
            json.dump({"bullpens": {"KC": {"era": 3.5}, "WSH": {"era": 4.1}}}, f)

        def fake_compute(abbr, team_id):
            return abbr, _available(pitches=10) if abbr == "KC" else _unavailable()

        monkeypatch.setattr(fbu, "compute_team_recent_usage", fake_compute)
        fbu.main()

        with open("data/bullpen.json") as f:
            result = json.load(f)
        assert result["bullpens"]["KC"]["recentUsage"]["dataAvailable"] is True
        assert result["bullpens"]["KC"]["recentUsage"]["teamPitchCountLastGame"] == 10
        assert result["bullpens"]["WSH"]["recentUsage"]["dataAvailable"] is False
        # Pre-existing fields (season-quality) must survive untouched.
        assert result["bullpens"]["KC"]["era"] == 3.5
        assert result["bullpens"]["WSH"]["era"] == 4.1

    def test_team_not_present_in_bullpen_json_is_skipped_not_added(self, tmp_path, monkeypatch):
        """Mirrors scripts/fetch_savant_bullpen_hl.py's own behavior: only
        teams api/bullpen.js already populated get enriched -- never a
        new team fabricated into bullpens{}."""
        monkeypatch.chdir(tmp_path)
        os.makedirs("data", exist_ok=True)
        with open("data/bullpen.json", "w") as f:
            json.dump({"bullpens": {"KC": {"era": 3.5}}}, f)

        monkeypatch.setattr(fbu, "compute_team_recent_usage", lambda abbr, team_id: (abbr, _available()))
        fbu.main()

        with open("data/bullpen.json") as f:
            result = json.load(f)
        assert set(result["bullpens"].keys()) == {"KC"}

    def test_missing_bullpen_json_does_not_crash(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs("data", exist_ok=True)
        monkeypatch.setattr(fbu, "compute_team_recent_usage", lambda abbr, team_id: (abbr, _available()))
        fbu.main()  # must return cleanly, not raise
        assert not os.path.exists("data/bullpen.json")

    def test_writes_top_level_fetch_timestamp_and_available_count(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs("data", exist_ok=True)
        with open("data/bullpen.json", "w") as f:
            json.dump({"bullpens": {"KC": {}, "WSH": {}}}, f)

        def fake_compute(abbr, team_id):
            return abbr, _available() if abbr == "KC" else _unavailable()

        monkeypatch.setattr(fbu, "compute_team_recent_usage", fake_compute)
        fbu.main()

        with open("data/bullpen.json") as f:
            result = json.load(f)
        assert "bullpenUsageFetchedAt" in result
        assert result["bullpenUsageAvailableCount"] == 1

    def test_only_bullpen_json_written(self, tmp_path, monkeypatch):
        """No slate/bets/ledger file is ever touched by this script."""
        monkeypatch.chdir(tmp_path)
        os.makedirs("data", exist_ok=True)
        with open("data/bullpen.json", "w") as f:
            json.dump({"bullpens": {"KC": {}}}, f)
        with open("data/slate.json", "w") as f:
            json.dump({"games": []}, f)
        before_slate = open("data/slate.json", "rb").read()

        monkeypatch.setattr(fbu, "compute_team_recent_usage", lambda abbr, team_id: (abbr, _available()))
        fbu.main()

        after_slate = open("data/slate.json", "rb").read()
        assert before_slate == after_slate
