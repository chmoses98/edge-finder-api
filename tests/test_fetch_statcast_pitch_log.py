#!/usr/bin/env python3
"""
tests/test_fetch_statcast_pitch_log.py
=========================================
Tests for scripts/fetch_statcast_pitch_log.py -- verifies already-
archived games are never re-fetched (no redownload on every run) and
that a successful fetch is ingested idempotently.
"""
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scripts.fetch_statcast_pitch_log as fetch_mod
from lib.research.statcast_pitch_store import has_game, load_pitches_for_game


class _TempCwd:
    def __enter__(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.tmpdir)
        return self.tmpdir

    def __exit__(self, *exc):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmpdir, ignore_errors=True)


def _fake_pitch(game_pk=555, pitch_number=1, at_bat_index=1):
    return {
        "gamePk": game_pk, "gameDate": "2026-06-01", "batterId": "1", "pitcherId": "2",
        "atBatIndex": at_bat_index, "pitchNumber": pitch_number, "pitchType": "FF",
        "releaseSpeed": 95.0, "balls": 0, "strikes": 0, "pitchCallType": "ball",
    }


class TestNoRedownloadOnAlreadyArchivedGames:
    def test_already_archived_game_skips_fetch_entirely(self, monkeypatch):
        with _TempCwd():
            from lib.research.statcast_pitch_store import ingest_game_pitches
            ingest_game_pitches(555, [_fake_pitch()])
            assert has_game(555) is True

            call_count = {"n": 0}

            def fake_fetch_json(url, timeout=55):
                call_count["n"] += 1
                return {"ok": True, "pitches": [_fake_pitch(pitch_number=2, at_bat_index=2)]}

            monkeypatch.setattr(fetch_mod, "fetch_json", fake_fetch_json)
            result = fetch_mod.main(game_pks=[555])
            assert call_count["n"] == 0
            assert result["alreadyArchived"] == 1
            assert result["ingested"] == 0

    def test_not_yet_archived_game_is_fetched_and_ingested(self, monkeypatch):
        with _TempCwd():
            def fake_fetch_json(url, timeout=55):
                return {"ok": True, "pitches": [_fake_pitch(game_pk=777)]}

            monkeypatch.setattr(fetch_mod, "fetch_json", fake_fetch_json)
            result = fetch_mod.main(game_pks=[777])
            assert result["ingested"] == 1
            assert has_game(777) is True
            assert len(load_pitches_for_game(777)) == 1

    def test_force_flag_refetches_even_when_archived(self, monkeypatch):
        with _TempCwd():
            from lib.research.statcast_pitch_store import ingest_game_pitches
            ingest_game_pitches(888, [_fake_pitch(game_pk=888)])

            call_count = {"n": 0}

            def fake_fetch_json(url, timeout=55):
                call_count["n"] += 1
                return {"ok": True, "pitches": [_fake_pitch(game_pk=888, pitch_number=2, at_bat_index=2)]}

            monkeypatch.setattr(fetch_mod, "fetch_json", fake_fetch_json)
            result = fetch_mod.main(game_pks=[888], force=True)
            assert call_count["n"] == 1
            assert result["ingested"] == 1
            # original pitch untouched, new pitch appended (dedup by identity, not a full overwrite)
            assert len(load_pitches_for_game(888)) == 2

    def test_fetch_failure_reported_not_raised(self, monkeypatch):
        with _TempCwd():
            def fake_fetch_json(url, timeout=55):
                return None

            monkeypatch.setattr(fetch_mod, "fetch_json", fake_fetch_json)
            result = fetch_mod.main(game_pks=[999])
            assert result["fetchFailed"] == 1
            assert has_game(999) is False

    def test_game_pks_derived_from_slate_when_not_given(self):
        with _TempCwd():
            import json
            with open("slate_for_test.json", "w") as f:
                json.dump({"games": [{"gameId": 111}, {"gameId": 222}]}, f)
            pks = fetch_mod.game_pks_from_slate("slate_for_test.json")
            assert pks == [111, 222]
