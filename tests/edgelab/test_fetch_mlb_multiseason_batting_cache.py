#!/usr/bin/env python3
"""
tests/edgelab/test_fetch_mlb_multiseason_batting_cache.py
=========================================================
Coverage for scripts/edgelab/backtest/fetch_mlb_multiseason_batting_cache.py's
orchestration logic. Injects a fake fetch_team_boxscore (monkeypatched
module-level import) -- no real network access, matching the identical
convention tests/edgelab/test_fetch_mlb_multiseason_bullpen_cache.py
already established.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
for p in (
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts"),
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "edgelab", "backtest"),
):
    if p not in sys.path:
        sys.path.insert(0, p)

import fetch_mlb_multiseason_batting_cache as fbc  # noqa: E402
from lib.edgelab.storage import read_records  # noqa: E402


def _write_schedule(root, season, team_abbr, game_pks):
    path = os.path.join(root, str(season), "schedules", f"{team_abbr}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    games = [{
        "gamePk": pk, "gameNumber": 1,
        "status": {"detailedState": "Final"},
        "teams": {
            "away": {"team": {"id": 100}, "score": 3},
            "home": {"team": {"id": 200}, "score": 4},
        },
    } for pk in game_pks]
    with open(path, "w") as f:
        json.dump({"dates": [{"date": f"{season}-04-01", "games": games}]}, f)


class TestRunOrchestration:
    def test_reuses_existing_schedule_cache_and_fetches_only_new_boxscores(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(fbc, "BULLPEN_SCHEDULE_CACHE_ROOT", str(tmp_path / "sched"))
        monkeypatch.setattr(fbc, "CACHE_ROOT", str(tmp_path / "batting"))
        _write_schedule(str(tmp_path / "sched"), 2023, "NYY", [111])
        monkeypatch.setattr(fbc, "MLB_TEAM_ID_MAP", {"NYY": 100})  # away side of gamePk 111 -> team 100

        calls = []

        def fake_fetch(game_pk, timeout=15):
            calls.append(game_pk)
            return {"teams": {
                "away": {"teamStats": {"batting": {"plateAppearances": 38}}},
                "home": {"teamStats": {"batting": {"plateAppearances": 40}}},
            }}

        monkeypatch.setattr(fbc, "fetch_team_boxscore", fake_fetch)

        summaries = fbc.run([2023], rate_limit_seconds=0)
        assert calls == [111]
        assert summaries[0]["fetched"] == 1
        assert summaries[0]["schedulesMissing"] == []

        records = list(read_records(fbc.boxscore_cache_path(2023)))
        assert len(records) == 1
        assert records[0]["gamePk"] == 111
        assert records[0]["awayBatting"]["plateAppearances"] == 38

    def test_missing_schedule_is_recorded_not_fabricated(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(fbc, "BULLPEN_SCHEDULE_CACHE_ROOT", str(tmp_path / "sched"))
        monkeypatch.setattr(fbc, "CACHE_ROOT", str(tmp_path / "batting"))
        monkeypatch.setattr(fbc, "MLB_TEAM_ID_MAP", {"NYY": 100})
        monkeypatch.setattr(fbc, "fetch_team_boxscore", lambda pk, timeout=15: None)

        summaries = fbc.run([2023], rate_limit_seconds=0)
        assert summaries[0]["schedulesMissing"] == ["NYY"]
        assert summaries[0]["uniqueGamePksRequested"] == 0

    def test_rerun_is_idempotent_no_duplicate_fetch(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(fbc, "BULLPEN_SCHEDULE_CACHE_ROOT", str(tmp_path / "sched"))
        monkeypatch.setattr(fbc, "CACHE_ROOT", str(tmp_path / "batting"))
        _write_schedule(str(tmp_path / "sched"), 2023, "NYY", [111])
        monkeypatch.setattr(fbc, "MLB_TEAM_ID_MAP", {"NYY": 100})
        call_count = [0]

        def fake_fetch(game_pk, timeout=15):
            call_count[0] += 1
            return {"teams": {"away": {"teamStats": {"batting": {"plateAppearances": 38}}}, "home": {}}}

        monkeypatch.setattr(fbc, "fetch_team_boxscore", fake_fetch)
        fbc.run([2023], rate_limit_seconds=0)
        fbc.run([2023], rate_limit_seconds=0)
        assert call_count[0] == 1

    def test_failed_fetch_recorded_not_fatal(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(fbc, "BULLPEN_SCHEDULE_CACHE_ROOT", str(tmp_path / "sched"))
        monkeypatch.setattr(fbc, "CACHE_ROOT", str(tmp_path / "batting"))
        _write_schedule(str(tmp_path / "sched"), 2023, "NYY", [111])
        monkeypatch.setattr(fbc, "MLB_TEAM_ID_MAP", {"NYY": 100})
        monkeypatch.setattr(fbc, "fetch_team_boxscore", lambda pk, timeout=15: None)

        summaries = fbc.run([2023], rate_limit_seconds=0)
        assert summaries[0]["failed"] == 1
        assert summaries[0]["failedGamePks"] == [111]
