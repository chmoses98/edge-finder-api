import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab", "backtest")):
    if p not in sys.path:
        sys.path.insert(0, p)

import fetch_mlb_multiseason_bullpen_cache as fetcher  # noqa: E402


def test_season_date_range():
    assert fetcher.season_date_range(2024) == ("2024-03-01", "2024-11-30")


def test_cache_paths_are_scoped_by_season(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher, "CACHE_ROOT", str(tmp_path))
    assert fetcher.schedule_cache_path(2024, "NYY") == os.path.join(str(tmp_path), "2024", "schedules", "NYY.json")
    assert fetcher.boxscore_cache_path(2024) == os.path.join(str(tmp_path), "2024", "boxscores.jsonl.gz")


class TestFetchAndCacheSchedule:
    def test_fetches_and_writes_when_not_cached(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetcher, "CACHE_ROOT", str(tmp_path))
        calls = []

        def fake_fetch(team_id, start, end):
            calls.append((team_id, start, end))
            return {"dates": []}

        monkeypatch.setattr(fetcher, "fetch_team_recent_schedule", fake_fetch)
        schedule, fetched = fetcher.fetch_and_cache_schedule(2024, "NYY", 147, rate_limit_seconds=0)
        assert fetched is True
        assert schedule == {"dates": []}
        assert calls == [(147, "2024-03-01", "2024-11-30")]
        assert os.path.exists(fetcher.schedule_cache_path(2024, "NYY"))

    def test_skips_network_when_already_cached(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetcher, "CACHE_ROOT", str(tmp_path))
        path = fetcher.schedule_cache_path(2024, "NYY")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            json.dump({"dates": ["cached"]}, f)

        def fail_if_called(*a, **k):
            raise AssertionError("should not have called the network fetcher")

        monkeypatch.setattr(fetcher, "fetch_team_recent_schedule", fail_if_called)
        schedule, fetched = fetcher.fetch_and_cache_schedule(2024, "NYY", 147, rate_limit_seconds=0)
        assert fetched is False
        assert schedule == {"dates": ["cached"]}

    def test_force_refresh_bypasses_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetcher, "CACHE_ROOT", str(tmp_path))
        path = fetcher.schedule_cache_path(2024, "NYY")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            json.dump({"dates": ["stale"]}, f)

        monkeypatch.setattr(fetcher, "fetch_team_recent_schedule", lambda team_id, start, end: {"dates": ["fresh"]})
        schedule, fetched = fetcher.fetch_and_cache_schedule(2024, "NYY", 147, force_refresh=True, rate_limit_seconds=0)
        assert fetched is True
        assert schedule == {"dates": ["fresh"]}

    def test_network_failure_returns_none_without_writing_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetcher, "CACHE_ROOT", str(tmp_path))
        monkeypatch.setattr(fetcher, "fetch_team_recent_schedule", lambda team_id, start, end: None)
        schedule, fetched = fetcher.fetch_and_cache_schedule(2024, "NYY", 147, rate_limit_seconds=0)
        assert schedule is None and fetched is False
        assert not os.path.exists(fetcher.schedule_cache_path(2024, "NYY"))


class TestFetchAndCacheBoxscores:
    def test_dedupes_across_teams_and_fetches_each_game_once(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetcher, "CACHE_ROOT", str(tmp_path))
        calls = []

        def fake_fetch(game_pk):
            calls.append(game_pk)
            return {"teams": {"away": {"pitchers": [], "players": {}}, "home": {"pitchers": [], "players": {}}}}

        monkeypatch.setattr(fetcher, "fetch_team_boxscore", fake_fetch)
        # gamePk 100 shared by both teams' schedules -- must be fetched once
        summary = fetcher.fetch_and_cache_boxscores(2024, [100, 100, 101], rate_limit_seconds=0)
        assert sorted(calls) == [100, 101]
        assert summary["fetched"] == 2
        assert summary["uniqueGamePksRequested"] == 2

    def test_already_cached_games_are_not_refetched(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetcher, "CACHE_ROOT", str(tmp_path))
        monkeypatch.setattr(fetcher, "fetch_team_boxscore", lambda pk: {"teams": {}})
        fetcher.fetch_and_cache_boxscores(2024, [100], rate_limit_seconds=0)

        calls = []

        def fail_tracking_fetch(pk):
            calls.append(pk)
            return {"teams": {}}

        monkeypatch.setattr(fetcher, "fetch_team_boxscore", fail_tracking_fetch)
        summary = fetcher.fetch_and_cache_boxscores(2024, [100, 101], rate_limit_seconds=0)
        assert calls == [101]
        assert summary["alreadyCached"] == 1
        assert summary["fetched"] == 1

    def test_a_single_game_fetch_failure_is_non_fatal(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetcher, "CACHE_ROOT", str(tmp_path))

        def flaky_fetch(pk):
            return None if pk == 100 else {"teams": {}}

        monkeypatch.setattr(fetcher, "fetch_team_boxscore", flaky_fetch)
        summary = fetcher.fetch_and_cache_boxscores(2024, [100, 101], rate_limit_seconds=0)
        assert summary["failed"] == 1
        assert summary["failedGamePks"] == [100]
        assert summary["fetched"] == 1

    def test_max_games_caps_new_fetches_per_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetcher, "CACHE_ROOT", str(tmp_path))
        calls = []
        monkeypatch.setattr(fetcher, "fetch_team_boxscore", lambda pk: calls.append(pk) or {"teams": {}})
        summary = fetcher.fetch_and_cache_boxscores(2024, [100, 101, 102], rate_limit_seconds=0, max_games=2)
        assert len(calls) == 2
        assert summary["fetched"] == 2
