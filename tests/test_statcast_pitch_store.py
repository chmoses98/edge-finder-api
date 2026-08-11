#!/usr/bin/env python3
"""
tests/test_statcast_pitch_store.py
=====================================
Unit tests for lib/research/statcast_pitch_store.py -- Hitter Projection
Engine Phase 2 raw pitch archive (identity, idempotency, as-of/no-leakage).
"""
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.research.statcast_pitch_store import (
    pitch_identity, has_game, ingest_game_pitches, load_pitches_for_game,
    load_pitches_for_batter,
)


def _pitch(**overrides):
    base = {
        "gamePk": 12345, "gameDate": "2026-06-01", "batterId": "660271", "pitcherId": "543037",
        "batterHand": "L", "pitcherHand": "R", "inning": 3, "atBatIndex": 10, "pitchNumber": 1,
        "balls": 0, "strikes": 0, "pitchType": "FF", "pitchName": "4-Seam Fastball",
        "releaseSpeed": 96.2, "spinRate": 2280, "inducedVertBreak": 15.8, "horizontalBreak": 7.2,
        "releaseHeight": 6.0, "releaseSide": -1.9, "extension": 6.4, "armAngle": 40.0,
        "plateX": 0.2, "plateZ": 2.4, "szTop": 3.4, "szBot": 1.6,
        "pitchCallType": "ball", "description": "ball", "events": None,
        "launchSpeed": None, "launchAngle": None, "hitCoordX": None, "hitCoordY": None,
        "battedBallType": None, "estimatedBA": None, "estimatedWOBA": None, "wobaValue": None,
    }
    base.update(overrides)
    return base


class _TempCwd:
    def __enter__(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.tmpdir)
        return self.tmpdir

    def __exit__(self, *exc):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestPitchIdentityAndFieldPreservation:
    def test_identity_preserves_batter_and_pitcher(self):
        with _TempCwd():
            p = _pitch()
            ingest_game_pitches(12345, [p])
            loaded = load_pitches_for_game(12345)
            assert len(loaded) == 1
            assert loaded[0]["batterId"] == "660271"
            assert loaded[0]["pitcherId"] == "543037"

    def test_pitch_type_preserved(self):
        with _TempCwd():
            ingest_game_pitches(1, [_pitch(pitchType="SL", pitchName="Slider")])
            loaded = load_pitches_for_game(1)
            assert loaded[0]["pitchType"] == "SL"

    def test_velocity_preserved(self):
        with _TempCwd():
            ingest_game_pitches(1, [_pitch(releaseSpeed=98.7)])
            loaded = load_pitches_for_game(1)
            assert loaded[0]["releaseSpeed"] == 98.7

    def test_movement_profile_preserved(self):
        with _TempCwd():
            ingest_game_pitches(1, [_pitch(inducedVertBreak=18.3, horizontalBreak=-6.1, spinRate=2600,
                                            releaseHeight=5.8, releaseSide=2.1, extension=6.9, armAngle=55.0)])
            loaded = load_pitches_for_game(1)
            p = loaded[0]
            assert p["inducedVertBreak"] == 18.3
            assert p["horizontalBreak"] == -6.1
            assert p["spinRate"] == 2600
            assert p["releaseHeight"] == 5.8
            assert p["releaseSide"] == 2.1
            assert p["extension"] == 6.9
            assert p["armAngle"] == 55.0

    def test_plate_xz_preserved(self):
        with _TempCwd():
            ingest_game_pitches(1, [_pitch(plateX=-0.83, plateZ=3.1)])
            loaded = load_pitches_for_game(1)
            assert loaded[0]["plateX"] == -0.83
            assert loaded[0]["plateZ"] == 3.1

    def test_exact_count_preserved(self):
        with _TempCwd():
            ingest_game_pitches(1, [_pitch(balls=2, strikes=1)])
            loaded = load_pitches_for_game(1)
            assert loaded[0]["balls"] == 2
            assert loaded[0]["strikes"] == 1

    def test_batted_ball_ev_la_preserved(self):
        with _TempCwd():
            p = _pitch(pitchCallType="in_play", description="hit_into_play", events="single",
                       launchSpeed=101.4, launchAngle=12.0)
            ingest_game_pitches(1, [p])
            loaded = load_pitches_for_game(1)
            assert loaded[0]["launchSpeed"] == 101.4
            assert loaded[0]["launchAngle"] == 12.0
            assert loaded[0]["events"] == "single"


class TestIdempotentIngestion:
    def test_duplicate_ingestion_writes_nothing_new(self):
        with _TempCwd():
            p = _pitch()
            first = ingest_game_pitches(12345, [p])
            assert first["pitchesWritten"] == 1
            assert first["pitchesSkipped"] == 0

            second = ingest_game_pitches(12345, [p])
            assert second["pitchesWritten"] == 0
            assert second["pitchesSkipped"] == 1
            assert len(load_pitches_for_game(12345)) == 1

    def test_has_game_true_only_after_ingestion(self):
        with _TempCwd():
            assert has_game(999) is False
            ingest_game_pitches(999, [_pitch(gamePk=999)])
            assert has_game(999) is True

    def test_identity_stable_across_two_separate_fetches_of_same_pitch(self):
        """Same logical pitch fetched twice (e.g. a re-run) must produce the same pitchId."""
        p1 = _pitch()
        p2 = _pitch()  # simulates re-fetching identical data
        assert pitch_identity(p1) == pitch_identity(p2)

    def test_different_pitch_number_yields_different_identity(self):
        assert pitch_identity(_pitch(pitchNumber=1)) != pitch_identity(_pitch(pitchNumber=2))


class TestAsOfNoLeakage:
    def test_future_pitch_excluded_from_as_of_load(self):
        with _TempCwd():
            past = _pitch(gamePk=1, gameDate="2026-06-01", batterId="1")
            future = _pitch(gamePk=2, gameDate="2026-08-15", batterId="1", atBatIndex=11)
            ingest_game_pitches(1, [past])
            ingest_game_pitches(2, [future])

            loaded = load_pitches_for_batter("1", as_of="2026-07-01")
            dates = {p["gameDate"] for p in loaded}
            assert "2026-06-01" in dates
            assert "2026-08-15" not in dates

    def test_as_of_is_exclusive_of_the_cutoff_date_itself(self):
        with _TempCwd():
            same_day = _pitch(gamePk=3, gameDate="2026-07-01", batterId="2")
            ingest_game_pitches(3, [same_day])
            loaded = load_pitches_for_batter("2", as_of="2026-07-01")
            assert loaded == []

    def test_since_bound_excludes_older_pitches(self):
        with _TempCwd():
            ingest_game_pitches(4, [_pitch(gamePk=4, gameDate="2026-01-01", batterId="3")])
            ingest_game_pitches(5, [_pitch(gamePk=5, gameDate="2026-06-01", batterId="3", atBatIndex=11)])
            loaded = load_pitches_for_batter("3", since="2026-03-01", as_of="2026-12-31")
            dates = {p["gameDate"] for p in loaded}
            assert dates == {"2026-06-01"}

    def test_other_batters_pitches_excluded(self):
        with _TempCwd():
            ingest_game_pitches(6, [_pitch(gamePk=6, batterId="AAA"), _pitch(gamePk=6, batterId="BBB", atBatIndex=11)])
            loaded = load_pitches_for_batter("AAA", as_of="2027-01-01")
            assert all(p["batterId"] == "AAA" for p in loaded)
            assert len(loaded) == 1

    def test_unfetched_batter_returns_empty_not_error(self):
        with _TempCwd():
            assert load_pitches_for_batter("nonexistent", as_of="2026-01-01") == []
