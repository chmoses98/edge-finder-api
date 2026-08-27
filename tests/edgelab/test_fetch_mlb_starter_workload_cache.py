import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab", "backtest")):
    if p not in sys.path:
        sys.path.insert(0, p)

import fetch_mlb_starter_workload_cache as fetcher  # noqa: E402


def test_boxscore_cache_path_is_scoped_by_season(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher, "STARTER_CACHE_ROOT", str(tmp_path))
    assert fetcher.boxscore_cache_path(2024) == os.path.join(str(tmp_path), "2024", "boxscores.jsonl.gz")


class TestReusesExistingScheduleCacheReadOnly:
    def test_loads_from_the_bullpen_backtest_namespace(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetcher, "BULLPEN_CACHE_ROOT", str(tmp_path))
        sched_dir = tmp_path / "2024" / "schedules"
        sched_dir.mkdir(parents=True)
        with open(sched_dir / "NYY.json", "w") as f:
            json.dump({"dates": ["reused"]}, f)
        assert fetcher.load_reused_schedule(2024, "NYY") == {"dates": ["reused"]}

    def test_missing_schedule_returns_none_without_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetcher, "BULLPEN_CACHE_ROOT", str(tmp_path))
        assert fetcher.load_reused_schedule(2024, "NYY") is None

    def test_run_never_calls_a_schedule_network_fetcher(self):
        """run() must only ever READ the existing schedule cache -- there
        is no schedule-fetching function imported into this module's
        namespace at all (confirmed structurally, not just textually --
        the docstring mentions fetch_team_recent_schedule by name, but
        the module itself never binds it)."""
        assert not hasattr(fetcher, "fetch_team_recent_schedule")
        assert hasattr(fetcher, "fetch_team_boxscore")


class TestFetchAndCacheBoxscores:
    def test_dedupes_across_teams_and_fetches_each_game_once(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetcher, "STARTER_CACHE_ROOT", str(tmp_path))
        calls = []

        def fake_fetch(game_pk):
            calls.append(game_pk)
            return {"teams": {"away": {"pitchers": [], "players": {}}, "home": {"pitchers": [], "players": {}}}}

        monkeypatch.setattr(fetcher, "fetch_team_boxscore", fake_fetch)
        summary = fetcher.fetch_and_cache_boxscores(2024, [100, 100, 101], rate_limit_seconds=0)
        assert sorted(calls) == [100, 101]
        assert summary["fetched"] == 2

    def test_already_cached_games_are_not_refetched(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetcher, "STARTER_CACHE_ROOT", str(tmp_path))
        monkeypatch.setattr(fetcher, "fetch_team_boxscore", lambda pk: {"teams": {}})
        fetcher.fetch_and_cache_boxscores(2024, [100], rate_limit_seconds=0)

        calls = []
        monkeypatch.setattr(fetcher, "fetch_team_boxscore", lambda pk: calls.append(pk) or {"teams": {}})
        summary = fetcher.fetch_and_cache_boxscores(2024, [100, 101], rate_limit_seconds=0)
        assert calls == [101]
        assert summary["alreadyCached"] == 1

    def test_a_single_game_fetch_failure_is_non_fatal(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetcher, "STARTER_CACHE_ROOT", str(tmp_path))

        def flaky_fetch(pk):
            return None if pk == 100 else {"teams": {}}

        monkeypatch.setattr(fetcher, "fetch_team_boxscore", flaky_fetch)
        summary = fetcher.fetch_and_cache_boxscores(2024, [100, 101], rate_limit_seconds=0)
        assert summary["failed"] == 1
        assert summary["failedGamePks"] == [100]
        assert summary["fetched"] == 1

    def test_cached_records_carry_extended_pitcher_fields(self, tmp_path, monkeypatch):
        """Confirms this cache genuinely uses the extended
        extract_pitcher_lines (battersFaced/strikeOuts/baseOnBalls/hits),
        not a stale narrower shape."""
        monkeypatch.setattr(fetcher, "STARTER_CACHE_ROOT", str(tmp_path))
        box = {"teams": {"away": {"pitchers": ["1"], "players": {"ID1": {
            "person": {"fullName": "P", "pitchHand": {"code": "R"}},
            "stats": {"pitching": {"numberOfPitches": 90, "outs": 18, "battersFaced": 25,
                                    "strikeOuts": 6, "baseOnBalls": 2, "hits": 5, "runs": 2, "earnedRuns": 2}},
        }}}, "home": {"pitchers": [], "players": {}}}}
        monkeypatch.setattr(fetcher, "fetch_team_boxscore", lambda pk: box)
        fetcher.fetch_and_cache_boxscores(2024, [100], rate_limit_seconds=0)
        from lib.edgelab.storage import read_records
        rows = list(read_records(fetcher.boxscore_cache_path(2024)))
        assert rows[0]["awayPitchers"][0]["battersFaced"] == 25
        assert rows[0]["awayPitchers"][0]["strikeOuts"] == 6
