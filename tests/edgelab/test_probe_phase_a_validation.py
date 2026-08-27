import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts", "edgelab", "backtest")):
    if p not in sys.path:
        sys.path.insert(0, p)

import probe_phase_a_validation as probe  # noqa: E402
import clv_update  # noqa: E402


class TestReuseNotReimplementation:
    def test_odds_api_key_derived_from_clv_update_binding(self):
        assert probe.ODDS_API_KEY == clv_update.ODDS_API_KEY.strip()

    def test_base_url_sport_api_get_are_the_real_clv_update_bindings(self):
        assert probe.BASE_URL is clv_update.BASE_URL
        assert probe.SPORT is clv_update.SPORT
        assert probe.api_get is clv_update.api_get


class TestMainFailsGracefullyWithoutKey:
    def test_main_returns_nonzero_without_key(self, monkeypatch, capsys):
        monkeypatch.setattr(probe, "ODDS_API_KEY", "")
        exit_code = probe.main()
        assert exit_code == 1
        assert "ODDS_API_KEY not set" in capsys.readouterr().out


class TestResolution2025DatesArePreregisteredAndFixed:
    def test_five_dates_spread_across_season(self):
        assert len(probe.RESOLUTION_2025_DATES) == 5
        months = sorted({d.split("-")[1] for d in probe.RESOLUTION_2025_DATES})
        assert len(months) >= 4  # genuinely spread, not clustered

    def test_all_dates_are_2025(self):
        assert all(d.startswith("2025-") for d in probe.RESOLUTION_2025_DATES)


class TestF5ProbeReusesAuditsOwnValidatedDates:
    def test_f5_test_dates_match_the_audits_own_phase2_sample(self):
        assert probe.F5_TEST_DATES == ["2024-06-10", "2024-06-11", "2024-06-12"]

    def test_f5_markets_are_the_three_requested_families(self):
        assert probe.F5_MARKETS == "h2h_1st_5_innings,spreads_1st_5_innings,totals_1st_5_innings"


class TestProbe2025DateHandlesFailureHonestly:
    def test_reachable_false_on_api_failure(self, monkeypatch):
        monkeypatch.setattr(probe, "api_get", lambda url: (None, None))
        result = probe.probe_2025_date("2025-04-15")
        assert result["reachable"] is False
        assert result["eventCount"] == 0

    def test_reachable_true_with_real_shaped_response(self, monkeypatch):
        monkeypatch.setattr(probe, "api_get", lambda url: ({"data": [{"id": "e1"}]}, "100"))
        result = probe.probe_2025_date("2025-04-15")
        assert result["reachable"] is True
        assert result["eventCount"] == 1


class TestProbeF5ForEventHandlesShapesHonestly:
    def test_no_data_returns_empty_markets_found(self, monkeypatch):
        monkeypatch.setattr(probe, "api_get", lambda url: (None, None))
        result = probe.probe_f5_for_event("evt1", "2024-06-10")
        assert result["reachable"] is False
        assert result["f5MarketsFound"] == []

    def test_pinnacle_present_with_f5_markets(self, monkeypatch):
        response = {
            "data": {
                "home_team": "NYY", "away_team": "BOS", "commence_time": "2024-06-10T23:05:00Z",
                "bookmakers": [{
                    "key": "pinnacle",
                    "markets": [
                        {"key": "h2h_1st_5_innings", "last_update": "t", "outcomes": [{"name": "NYY"}, {"name": "BOS"}]},
                        {"key": "totals_1st_5_innings", "last_update": "t", "outcomes": [{"name": "Over"}, {"name": "Under"}]},
                    ],
                }],
            }
        }
        monkeypatch.setattr(probe, "api_get", lambda url: (response, "50"))
        result = probe.probe_f5_for_event("evt1", "2024-06-10")
        found_keys = [m["key"] for m in result["f5MarketsFound"]]
        assert "h2h_1st_5_innings" in found_keys
        assert "totals_1st_5_innings" in found_keys

    def test_no_pinnacle_bookmaker_returns_empty(self, monkeypatch):
        response = {"data": {"home_team": "NYY", "away_team": "BOS", "bookmakers": [{"key": "draftkings", "markets": []}]}}
        monkeypatch.setattr(probe, "api_get", lambda url: (response, "50"))
        result = probe.probe_f5_for_event("evt1", "2024-06-10")
        assert result["f5MarketsFound"] == []
