import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts", "edgelab", "backtest")):
    if p not in sys.path:
        sys.path.insert(0, p)

import fetch_historical_pinnacle_cache as fetcher  # noqa: E402
import clv_update  # noqa: E402


class TestReuseNotReimplementation:
    def test_odds_api_key_derived_from_clv_update_binding(self):
        assert fetcher.ODDS_API_KEY == clv_update.ODDS_API_KEY.strip()

    def test_base_url_sport_api_get_are_the_real_clv_update_bindings(self):
        assert fetcher.BASE_URL is clv_update.BASE_URL
        assert fetcher.SPORT is clv_update.SPORT
        assert fetcher.api_get is clv_update.api_get


class TestCreditEstimate:
    def test_two_markets_two_snapshots(self):
        assert fetcher.credit_estimate_per_date(num_markets=2) == 2 * 2 * 10

    def test_scales_with_market_count(self):
        assert fetcher.credit_estimate_per_date(num_markets=1) == 2 * 1 * 10


class TestSnapshotTimesFixed:
    def test_exactly_two_preregistered_snapshot_times(self):
        assert len(fetcher.SNAPSHOT_TIMES_ET) == 2

    def test_markets_are_the_two_primary_families_only(self):
        assert fetcher.MARKETS == "h2h,totals"
        assert "spreads" not in fetcher.MARKETS


class TestEtToUtcConversion:
    def test_standard_evening_time(self):
        iso = fetcher._et_time_to_utc_iso("2024-06-10", "18:15")
        assert iso == "2024-06-10T22:15:00Z"

    def test_late_time_still_same_calendar_date_in_utc(self):
        iso = fetcher._et_time_to_utc_iso("2024-06-10", "21:15")
        assert iso == "2024-06-11T01:15:00Z"


class TestSeasonDateRange:
    def test_past_season_full_window(self):
        start, end = fetcher.season_date_range(2023)
        assert start == "2023-04-01"
        assert end == "2023-09-30"

    def test_current_season_capped_at_today(self):
        import datetime
        current_year = datetime.datetime.now(datetime.timezone.utc).year
        start, end = fetcher.season_date_range(current_year)
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        assert end <= today


class TestSampledDates:
    def test_stride_respected(self):
        dates = fetcher.sampled_dates(2023, stride=5)
        d0 = dates[0]
        import datetime
        d0_dt = datetime.datetime.strptime(d0, "%Y-%m-%d")
        d1_dt = datetime.datetime.strptime(dates[1], "%Y-%m-%d")
        assert (d1_dt - d0_dt).days == 5

    def test_starts_at_season_start(self):
        dates = fetcher.sampled_dates(2023, stride=5)
        assert dates[0] == "2023-04-01"

    def test_deterministic(self):
        assert fetcher.sampled_dates(2023, stride=5) == fetcher.sampled_dates(2023, stride=5)


class TestAlreadyCachedResumability:
    def test_uncached_date_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetcher, "CACHE_ROOT", str(tmp_path))
        assert fetcher.already_cached(2099, "2099-04-01") is False

    def test_cached_date_returns_true(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetcher, "CACHE_ROOT", str(tmp_path))
        season_dir = tmp_path / "2099"
        season_dir.mkdir()
        (season_dir / "2099-04-01.json").write_text("{}")
        assert fetcher.already_cached(2099, "2099-04-01") is True


class TestRunDryRun:
    def test_dry_run_makes_no_network_calls_and_estimates_cost(self, monkeypatch, tmp_path):
        monkeypatch.setattr(fetcher, "CACHE_ROOT", str(tmp_path))

        def _fail_if_called(date_str):
            raise AssertionError("dry_run must never call the network")

        monkeypatch.setattr(fetcher, "fetch_date_snapshots", _fail_if_called)
        result = fetcher.run(2099, ["2099-04-01", "2099-04-02"], dry_run=True)
        assert result["wouldFetch"] == 2
        assert result["estimatedCredits"] == 2 * fetcher.credit_estimate_per_date()

    def test_dry_run_skips_already_cached_dates(self, monkeypatch, tmp_path):
        monkeypatch.setattr(fetcher, "CACHE_ROOT", str(tmp_path))
        season_dir = tmp_path / "2099"
        season_dir.mkdir()
        (season_dir / "2099-04-01.json").write_text("{}")
        result = fetcher.run(2099, ["2099-04-01", "2099-04-02"], dry_run=True)
        assert result["wouldFetch"] == 1
        assert result["alreadyCached"] == 1


class TestRunResumable:
    def test_run_skips_dates_already_on_disk_no_network_call(self, monkeypatch, tmp_path):
        monkeypatch.setattr(fetcher, "CACHE_ROOT", str(tmp_path))
        season_dir = tmp_path / "2099"
        season_dir.mkdir()
        (season_dir / "2099-04-01.json").write_text(json.dumps({"date": "2099-04-01", "snapshots": []}))

        calls = []
        monkeypatch.setattr(fetcher, "fetch_date_snapshots", lambda d: calls.append(d) or {"date": d, "snapshots": []})
        fetcher.run(2099, ["2099-04-01"], dry_run=False)
        assert calls == []

    def test_run_fetches_and_caches_uncached_dates(self, monkeypatch, tmp_path):
        monkeypatch.setattr(fetcher, "CACHE_ROOT", str(tmp_path))
        fake_result = {"date": "2099-04-02", "snapshots": [{"requestedAtEt": "18:15", "gameCount": 3, "creditsRemaining": "100", "games": []}]}
        monkeypatch.setattr(fetcher, "fetch_date_snapshots", lambda d: fake_result)
        result = fetcher.run(2099, ["2099-04-02"], dry_run=False)
        assert result["newlyFetched"] == 1
        assert os.path.exists(fetcher.date_cache_path(2099, "2099-04-02"))


class TestFetchDateSnapshotsHandlesFailureHonestly:
    def test_failed_call_yields_empty_games_not_a_crash(self, monkeypatch):
        monkeypatch.setattr(fetcher, "api_get", lambda url: (None, None))
        result = fetcher.fetch_date_snapshots("2024-06-10")
        assert len(result["snapshots"]) == 2
        assert all(s["gameCount"] == 0 for s in result["snapshots"])

    def test_successful_call_extracts_game_count(self, monkeypatch):
        monkeypatch.setattr(fetcher, "api_get", lambda url: ({"data": [{"id": "g1"}, {"id": "g2"}]}, "500"))
        result = fetcher.fetch_date_snapshots("2024-06-10")
        assert result["snapshots"][0]["gameCount"] == 2
