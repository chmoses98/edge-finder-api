#!/usr/bin/env python3
"""
tests/test_enrich_data_bullpen_usage.py
============================================
Focused, isolated coverage for scripts/enrich_data.py's new
`recentUsage` merge (bullpen-context-for-pregame-analysis improvement):
proves data/bullpen.json's `bullpens[abbr].recentUsage` block (written
by scripts/fetch_bullpen_usage.py) reaches data/slate.json's
game[side].bullpen.recentUsage verbatim, and that a team with no
recent-usage data gets an explicit None (never a fabricated default).

Deliberately a SEPARATE sandbox from tests/test_end_to_end_pipeline_sandbox.py
(which locks a specific recommendation OUTCOME for its own synthetic
fixture and must not be touched by this data/context-only change) --
this file only runs scripts/enrich_data.py in isolation and never
touches build_market_ledger.py/risk_gate.py/recommendation logic.
"""
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
LIB_DIR = os.path.join(ROOT, "lib")


def _sandbox(base_dir, bullpens):
    scripts_dir = base_dir / "scripts"
    data_dir = base_dir / "data"
    scripts_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    shutil.copy(os.path.join(SCRIPTS_DIR, "enrich_data.py"), scripts_dir / "enrich_data.py")

    slate = {
        "date": "2026-08-08",
        "games": [{
            "gameId": "g1",
            "away": {"abbr": "KC", "bullpen": {}},
            "home": {"abbr": "WSH", "bullpen": {}},
            "awayTeamStats": {"lineupConfirmed": True},
            "homeTeamStats": {"lineupConfirmed": True},
        }],
    }
    with open(data_dir / "slate.json", "w") as f:
        json.dump(slate, f)
    with open(data_dir / "teamstats.json", "w") as f:
        json.dump({"teams": {"KC": {}, "WSH": {}}}, f)
    with open(data_dir / "bullpen.json", "w") as f:
        json.dump({"bullpens": bullpens}, f)

    return scripts_dir, data_dir


def _run_enrich(base_dir, bullpens):
    scripts_dir, data_dir = _sandbox(base_dir, bullpens)
    result = subprocess.run(
        [sys.executable, str(scripts_dir / "enrich_data.py")],
        cwd=str(base_dir), capture_output=True, text=True, env=dict(os.environ),
    )
    assert result.returncode == 0, f"enrich_data.py failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    return json.loads((data_dir / "slate.json").read_text())


class TestBullpenRecentUsageMerge:

    def test_recent_usage_reaches_slate_verbatim(self, tmp_path):
        usage = {
            "dataAvailable": True, "unavailableReason": None, "asOfDate": "2026-08-07",
            "gamesConsidered": 1,
            "relieversUsedLastGame": [{"playerId": "300", "name": "Closer Guy", "numberOfPitches": 12}],
            "backToBackRelievers": [],
            "recentPitchCounts": [{"playerId": "300", "name": "Closer Guy", "totalPitches": 12, "appearances": 1}],
            "highLeverageRecentUsage": [{"playerId": "300", "name": "Closer Guy", "saves": 1, "holds": 0, "totalPitches": 12}],
            "handednessMix": {"L": 1, "R": 0, "unknown": 0},
            "teamPitchCountLastGame": 12, "teamPitchCountWindow": 12,
        }
        slate = _run_enrich(tmp_path / "run1", {"KC": {"recentUsage": usage}})
        game = slate["games"][0]
        assert game["away"]["bullpen"]["recentUsage"] == usage

    def test_team_with_no_bullpen_entry_at_all_gets_no_recentUsage_key_added(self, tmp_path):
        """A team not present in data/bullpen.json at all (the pre-
        existing `abbr not in bullpens: continue` guard) must not have
        its bullpen block touched -- matches the existing hlXFIP/hlGrade
        behavior exactly."""
        slate = _run_enrich(tmp_path / "run1", {})
        game = slate["games"][0]
        assert game["away"]["bullpen"] == {}
        assert game["home"]["bullpen"] == {}

    def test_team_present_but_recentUsage_missing_is_explicit_none_never_fabricated(self, tmp_path):
        """A team WITH a bullpen.json entry (e.g. only season-quality
        fields, no recentUsage yet fetched) gets recentUsage=None
        explicitly -- never a guessed/default usage summary."""
        slate = _run_enrich(tmp_path / "run1", {"KC": {"era": 3.5, "xFIP": 3.6}})
        game = slate["games"][0]
        assert game["away"]["bullpen"]["recentUsage"] is None
        assert game["away"]["bullpen"]["hlXFIP"] is None  # pre-existing field, unaffected by this change

    def test_unavailable_recent_usage_data_is_preserved_not_dropped(self, tmp_path):
        """A team where the fetch ran but found no completed games in
        the window (dataAvailable=False) must still show that explicit
        state on the slate, not be silently omitted."""
        unavailable = {
            "dataAvailable": False, "unavailableReason": "no_completed_games_in_window",
            "asOfDate": None, "gamesConsidered": 0,
            "relieversUsedLastGame": [], "backToBackRelievers": [],
            "recentPitchCounts": [], "highLeverageRecentUsage": [],
            "handednessMix": {"L": 0, "R": 0, "unknown": 0},
            "teamPitchCountLastGame": None, "teamPitchCountWindow": None,
        }
        slate = _run_enrich(tmp_path / "run1", {"KC": {"recentUsage": unavailable}})
        game = slate["games"][0]
        assert game["away"]["bullpen"]["recentUsage"]["dataAvailable"] is False
        assert game["away"]["bullpen"]["recentUsage"]["unavailableReason"] == "no_completed_games_in_window"

    def test_bullpen_json_itself_is_never_written(self, tmp_path):
        """Data/context change only -- enrich_data.py READS
        data/bullpen.json (already true before this change) but must
        never WRITE to it; only scripts/fetch_bullpen_usage.py (and the
        pre-existing scripts/fetch_savant_bullpen_hl.py) ever write that
        file."""
        base = tmp_path / "run1"
        scripts_dir, data_dir = _sandbox(base, {"KC": {"recentUsage": {"dataAvailable": False}}})
        before = (data_dir / "bullpen.json").read_bytes()
        subprocess.run(
            [sys.executable, str(scripts_dir / "enrich_data.py")],
            cwd=str(base), capture_output=True, text=True, env=dict(os.environ),
        )
        after = (data_dir / "bullpen.json").read_bytes()
        assert before == after
