#!/usr/bin/env python3
"""
tests/test_fetch_umpire_assignment.py
========================================
Tests for scripts/fetch_umpire_assignment.py -- umpire identity attaches
to the correct game, missing officials data fails honestly, and a
captured assignment can never be silently overwritten by a later
(e.g. postgame) lookup -- the structural guard against a historical
pregame backtest leaking postgame-only umpire identity.
"""
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scripts.fetch_umpire_assignment as fetch_mod


class _TempCwd:
    def __enter__(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.tmpdir)
        return self.tmpdir

    def __exit__(self, *exc):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmpdir, ignore_errors=True)


def _boxscore_with_officials(hp_id=123, hp_name="Test Umpire"):
    return {
        "officials": [
            {"official": {"id": 111, "fullName": "First Base Ump"}, "officialType": "First Base"},
            {"official": {"id": hp_id, "fullName": hp_name}, "officialType": "Home Plate"},
        ]
    }


class TestParseUmpireAssignment:
    def test_identity_attaches_to_correct_game(self):
        record = fetch_mod.parse_umpire_assignment(_boxscore_with_officials(), game_pk=555)
        assert record["gamePk"] == 555
        assert record["status"] == "AVAILABLE"
        assert record["umpireId"] == "123"
        assert record["umpireName"] == "Test Umpire"

    def test_missing_officials_fails_honestly(self):
        record = fetch_mod.parse_umpire_assignment({"officials": []}, game_pk=555)
        assert record["status"] == "MISSING_DATA"
        assert record["umpireId"] is None

    def test_no_home_plate_type_in_officials_fails_honestly(self):
        data = {"officials": [{"official": {"id": 1, "fullName": "X"}, "officialType": "First Base"}]}
        record = fetch_mod.parse_umpire_assignment(data, game_pk=555)
        assert record["status"] == "MISSING_DATA"

    def test_boxscore_fetch_failure_fails_honestly(self):
        record = fetch_mod.parse_umpire_assignment(None, game_pk=555)
        assert record["status"] == "MISSING_DATA"
        assert record["reason"] == "boxscore fetch failed"


class TestNeverOverwritesOnceCaptured:
    def test_second_call_for_same_game_is_a_no_op(self, monkeypatch):
        with _TempCwd():
            call_count = {"n": 0}

            def fake_fetch_boxscore(game_pk, timeout=15):
                call_count["n"] += 1
                # Different umpire on each call -- simulates a later,
                # possibly postgame, lookup returning different data.
                return _boxscore_with_officials(hp_id=100 + call_count["n"], hp_name=f"Ump{call_count['n']}")

            monkeypatch.setattr(fetch_mod, "fetch_boxscore", fake_fetch_boxscore)
            result1 = fetch_mod.main([555])
            assert result1["newlyCaptured"] == 1
            first = fetch_mod.load_umpire_assignment(555)

            result2 = fetch_mod.main([555])
            assert result2["alreadyCaptured"] == 1
            assert result2["newlyCaptured"] == 0
            second = fetch_mod.load_umpire_assignment(555)
            # frozen -- the second, different fetch never overwrote the first
            assert first == second
            assert call_count["n"] == 1

    def test_different_games_captured_independently(self, monkeypatch):
        with _TempCwd():
            def fake_fetch_boxscore(game_pk, timeout=15):
                return _boxscore_with_officials(hp_id=game_pk, hp_name=f"Ump for {game_pk}")

            monkeypatch.setattr(fetch_mod, "fetch_boxscore", fake_fetch_boxscore)
            fetch_mod.main([1, 2])
            assert fetch_mod.load_umpire_assignment(1)["umpireId"] == "1"
            assert fetch_mod.load_umpire_assignment(2)["umpireId"] == "2"

    def test_unknown_game_returns_none(self):
        with _TempCwd():
            assert fetch_mod.load_umpire_assignment(999) is None
