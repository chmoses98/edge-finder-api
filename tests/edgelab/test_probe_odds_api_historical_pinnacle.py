import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts", "edgelab", "backtest")):
    if p not in sys.path:
        sys.path.insert(0, p)

import probe_odds_api_historical_pinnacle as probe  # noqa: E402
import clv_update  # noqa: E402


class TestReuseNotReimplementation:
    def test_odds_api_key_is_the_real_clv_update_binding(self):
        assert probe.ODDS_API_KEY is clv_update.ODDS_API_KEY

    def test_base_url_is_the_real_clv_update_constant(self):
        assert probe.BASE_URL is clv_update.BASE_URL

    def test_sport_is_the_real_clv_update_constant(self):
        assert probe.SPORT is clv_update.SPORT

    def test_api_get_is_the_real_clv_update_function(self):
        assert probe.api_get is clv_update.api_get

    def test_script_does_not_redefine_api_get(self):
        source = open(probe.__file__).read()
        assert "def api_get" not in source


class TestMainFailsGracefullyWithoutKey:
    def test_main_returns_nonzero_and_never_calls_network_when_key_missing(self, monkeypatch, capsys):
        monkeypatch.setattr(probe, "ODDS_API_KEY", "")
        exit_code = probe.main()
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "ODDS_API_KEY not set" in captured.out


class TestSummarizePinnacleCoverage:
    def _game(self, home="NYY", away="BOS", bookmakers=None):
        return {"home_team": home, "away_team": away, "commence_time": "2024-06-10T23:05:00Z", "bookmakers": bookmakers or []}

    def test_pinnacle_absent_when_no_pinnacle_bookmaker(self):
        day_result = {"games": [self._game(bookmakers=[{"key": "draftkings", "markets": []}])]}
        summary = probe.summarize_pinnacle_coverage(day_result)
        assert summary[0]["pinnaclePresent"] is False

    def test_pinnacle_present_with_both_sides(self):
        pinnacle_book = {
            "key": "pinnacle", "last_update": "2024-06-10T22:00:00Z",
            "markets": [{"key": "h2h", "last_update": "2024-06-10T22:00:00Z", "outcomes": [
                {"name": "NYY", "price": -120}, {"name": "BOS", "price": 110},
            ]}],
        }
        day_result = {"games": [self._game(bookmakers=[pinnacle_book])]}
        summary = probe.summarize_pinnacle_coverage(day_result)
        assert summary[0]["pinnaclePresent"] is True
        assert summary[0]["marketsSeen"]["h2h"]["bothSidesPresent"] is True
        assert summary[0]["marketsSeen"]["h2h"]["outcomeCount"] == 2

    def test_single_sided_market_flagged_not_both_sides(self):
        pinnacle_book = {
            "key": "pinnacle", "last_update": "2024-06-10T22:00:00Z",
            "markets": [{"key": "totals", "last_update": "2024-06-10T22:00:00Z", "outcomes": [{"name": "Over", "price": -110}]}],
        }
        day_result = {"games": [self._game(bookmakers=[pinnacle_book])]}
        summary = probe.summarize_pinnacle_coverage(day_result)
        assert summary[0]["marketsSeen"]["totals"]["bothSidesPresent"] is False

    def test_no_games_returns_empty_summary(self):
        assert probe.summarize_pinnacle_coverage({"games": []}) == []


class TestProbeYearEventsHandlesFailureHonestly:
    def test_reachable_false_when_api_get_returns_none(self, monkeypatch):
        monkeypatch.setattr(probe, "api_get", lambda url: (None, None))
        result = probe.probe_year_events(2022, "2022-06-15")
        assert result["reachable"] is False
        assert result["eventCount"] == 0

    def test_reachable_true_with_event_count_from_real_shaped_response(self, monkeypatch):
        monkeypatch.setattr(probe, "api_get", lambda url: ({"data": [{"id": "e1"}, {"id": "e2"}]}, "9995"))
        result = probe.probe_year_events(2024, "2024-06-15")
        assert result["reachable"] is True
        assert result["eventCount"] == 2
        assert result["creditsRemaining"] == "9995"


class TestPhase2SkipsUnreachableYears:
    def test_main_skips_phase2_for_a_year_phase1_found_unreachable(self, monkeypatch, capsys):
        monkeypatch.setattr(probe, "ODDS_API_KEY", "fake-key-for-test")
        monkeypatch.setattr(probe, "PHASE1_PROBE_DATES", {2024: "2024-06-15"})
        monkeypatch.setattr(probe, "PHASE2_SAMPLE_DATES", {2024: ["2024-06-10"]})
        monkeypatch.setattr(probe, "probe_year_events", lambda year, date_str: {"year": year, "probeDate": date_str, "reachable": False, "eventCount": 0, "creditsRemaining": "10"})

        def _fail_if_called(date_str):
            raise AssertionError("phase 2 must not spend credits on an unreachable year")

        monkeypatch.setattr(probe, "probe_day_odds_pinnacle", _fail_if_called)
        monkeypatch.setattr(probe, "CACHE_ROOT", "/tmp/probe_test_cache_never_written")
        import json as _json
        original_dump = _json.dump
        monkeypatch.setattr(_json, "dump", lambda *a, **k: None)
        exit_code = probe.main()
        monkeypatch.setattr(_json, "dump", original_dump)
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "Skipping 2024" in captured.out
