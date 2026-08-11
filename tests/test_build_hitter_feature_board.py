#!/usr/bin/env python3
"""
tests/test_build_hitter_feature_board.py
============================================
Tests for scripts/build_hitter_feature_board.py -- the I/O shell that
writes the Hitter Projection Engine Phase 1 feature artifact.

Verifies: the artifact is written via lib.pipeline_artifacts (matching
scripts/build_projection_board.py's own precedent), the script never
touches data/slate.json or any other existing pipeline file, missing
input files degrade gracefully instead of raising, and the resulting
artifact round-trips through lib.pipeline_artifacts.read_stage_artifact.
"""
import json
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scripts.build_hitter_feature_board as build_hitter_feature_board
from lib.pipeline_artifacts import read_stage_artifact


def _slate_doc():
    return {
        "date": "2026-08-11",
        "games": [
            {
                "gameId": 1,
                "away": {"team": "Away Team", "abbr": "AWY"},
                "home": {
                    "team": "Home Team", "abbr": "HOM",
                    "pitcher": {"id": 999, "name": "Starter", "pitchHand": "L"},
                    "pitcherSavant": {"xERA": 3.5},
                    "bullpen": {"era": 3.8},
                },
                "awayTeamStats": {
                    "lineupConfirmedOfficial": True,
                    "teamSeasonWOBA": 0.315,
                    "confirmedLineup": [
                        {"order": 1, "playerId": "p1", "name": "Hitter One", "batSide": "R",
                         "seasonWOBA": 0.330, "seasonPA": 400, "platoonSplits": {"vsLHP": None, "vsRHP": None}},
                    ],
                },
                "homeTeamStats": {"lineupConfirmedOfficial": False},
                "park": {"name": "Test Park", "dome": False, "parkFactor": 100},
            },
            {
                "gameId": 2,
                "away": {"team": "Unconfirmed Away", "abbr": "UAW"},
                "home": {"team": "Unconfirmed Home", "abbr": "UHM", "pitcher": {}, "pitcherSavant": {}, "bullpen": {}},
                "awayTeamStats": {"lineupConfirmedOfficial": False},
                "homeTeamStats": {"lineupConfirmedOfficial": False},
                "park": {"name": "Other Park", "dome": True, "parkFactor": 95},
            },
        ],
    }


class TestConfirmedHitterArtifact:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.slate_path = os.path.join(self.tmpdir, "slate.json")
        with open(self.slate_path, "w") as f:
            json.dump(_slate_doc(), f)
        self.cwd = os.getcwd()
        os.chdir(self.tmpdir)

    def teardown_method(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_writes_pipeline_artifact_for_confirmed_and_unconfirmed_games(self):
        result = build_hitter_feature_board.main(slate_path=self.slate_path)
        assert result["totalGames"] == 2
        assert result["gamesWithConfirmedHitters"] == 1
        assert result["totalHitterRecords"] == 1
        assert "artifactPath" in result

        envelope = read_stage_artifact("hitter_features", "2026-08-11")
        assert envelope["meta"]["stage"] == "hitter_features"
        assert envelope["meta"]["schemaVersion"] == "1.0"
        games = envelope["data"]["games"]
        assert len(games) == 2
        game1 = next(g for g in games if g["gameId"] == 1)
        assert game1["away"]["status"] == "OK"
        assert len(game1["away"]["hitters"]) == 1
        game2 = next(g for g in games if g["gameId"] == 2)
        assert game2["away"]["status"] == "LINEUP_UNCONFIRMED"
        assert game2["away"]["hitters"] == []

    def test_does_not_touch_slate_json(self):
        before = json.load(open(self.slate_path))
        build_hitter_feature_board.main(slate_path=self.slate_path)
        after = json.load(open(self.slate_path))
        assert before == after

    def test_dry_run_does_not_write_artifact(self):
        result = build_hitter_feature_board.main(slate_path=self.slate_path, dry_run=True)
        assert "artifactPath" not in result
        assert not os.path.exists(os.path.join("data", "pipeline", "2026-08-11", "hitter_features.json"))


class TestPhase2RawArchiveWiring:
    """
    Hitter Projection Engine Phase 2: build_hitter_feature_board.py now
    loads each confirmed batter's raw pitch archive
    (lib.research.statcast_pitch_store) and bat-tracking history
    (lib.research.bat_tracking_store) before calling
    build_hitter_feature_context(), as-of this run's slate date -- and
    the resulting artifact reflects that real data, not just Phase 1's
    season-wOBA-only baseline.
    """
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.slate_path = os.path.join(self.tmpdir, "slate.json")
        with open(self.slate_path, "w") as f:
            json.dump(_slate_doc(), f)
        self.cwd = os.getcwd()
        os.chdir(self.tmpdir)

        from lib.research.statcast_pitch_store import ingest_game_pitches
        from lib.research.bat_tracking_store import record_snapshot, ingest_snapshots
        ingest_game_pitches(555, [{
            "gamePk": 555, "gameDate": "2026-06-01", "batterId": "p1", "pitcherId": "999",
            "batterHand": "R", "atBatIndex": 1, "pitchNumber": 1, "pitchType": "FF",
            "pitchName": "4-Seam Fastball", "releaseSpeed": 95.0, "balls": 1, "strikes": 1,
            "plateX": 0.0, "plateZ": 2.5, "szTop": 3.5, "szBot": 1.5,
            "pitchCallType": "in_play", "events": "single", "launchSpeed": 97.0, "launchAngle": 12.0,
        }])
        ingest_snapshots([record_snapshot("p1", "2026-06-01", "2026-06-01T00:00:00Z", {"avgBatSpeed": 72.0})])

    def teardown_method(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_raw_archive_and_bat_tracking_reach_the_artifact(self):
        result = build_hitter_feature_board.main(slate_path=self.slate_path)
        assert result["battersWithRawPitchArchive"] == 1
        assert result["battersWithBatTrackingHistory"] == 1

        envelope = read_stage_artifact("hitter_features", "2026-08-11")
        game1 = next(g for g in envelope["data"]["games"] if g["gameId"] == 1)
        hitter = game1["away"]["hitters"][0]
        assert hitter["pitchTypeMatchup"]["status"] == "AVAILABLE"
        assert hitter["batTracking"]["status"] == "AVAILABLE"
        assert hitter["baselineTalent"]["horizons"]["currentSeason"]["stats"]["H"] == 1

    def test_future_dated_archive_entries_excluded_from_pregame_slate(self):
        """As-of safety end-to-end: a pitch dated after the slate date must never reach this run's record."""
        from lib.research.statcast_pitch_store import ingest_game_pitches
        ingest_game_pitches(556, [{
            "gamePk": 556, "gameDate": "2026-09-01", "batterId": "p1", "pitcherId": "999",
            "batterHand": "R", "atBatIndex": 2, "pitchNumber": 1, "pitchType": "FF",
            "pitchName": "4-Seam Fastball", "releaseSpeed": 95.0, "balls": 0, "strikes": 0,
            "pitchCallType": "in_play", "events": "home_run", "launchSpeed": 108.0, "launchAngle": 27.0,
        }])
        envelope_data = build_hitter_feature_board.main(slate_path=self.slate_path)
        envelope = read_stage_artifact("hitter_features", "2026-08-11")
        game1 = next(g for g in envelope["data"]["games"] if g["gameId"] == 1)
        hitter = game1["away"]["hitters"][0]
        assert hitter["baselineTalent"]["horizons"]["currentSeason"]["stats"]["HR"] == 0
        assert hitter["baselineTalent"]["horizons"]["currentSeason"]["stats"]["H"] == 1


class TestMissingInputFilesDegradeGracefully:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.tmpdir)

    def teardown_method(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_slate_file_returns_status_without_raising(self):
        result = build_hitter_feature_board.main(slate_path=os.path.join(self.tmpdir, "nope.json"))
        assert result["status"] == "NO_SLATE_FILE"
        assert result["totalHitters"] == 0

    def test_no_weather_or_savant_file_still_produces_records(self):
        slate_path = os.path.join(self.tmpdir, "slate.json")
        with open(slate_path, "w") as f:
            json.dump(_slate_doc(), f)
        result = build_hitter_feature_board.main(
            slate_path=slate_path,
            weather_path=os.path.join(self.tmpdir, "no-weather.json"),
            savant_team_path=os.path.join(self.tmpdir, "no-savant.json"),
        )
        assert result["totalHitterRecords"] == 1
