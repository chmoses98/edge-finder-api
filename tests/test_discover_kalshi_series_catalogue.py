#!/usr/bin/env python3
"""
tests/test_discover_kalshi_series_catalogue.py
====================================================
Coverage for scripts/discover_kalshi_series_catalogue.py -- the
prefix-agnostic series catalogue + F3/F7 broad text search. Every test
injects a fake HTTP layer (this sandbox has no real network egress to
Kalshi -- see the module docstring) so behavior is verified without a
live call.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import scripts.discover_kalshi_series_catalogue as dsc  # noqa: E402


def make_fake_http(series=None, markets_by_query=None):
    series = series if series is not None else []
    markets_by_query = markets_by_query or {}

    def fake(url):
        if "/series?" in url:
            return {"series": series}, None
        for needle, response in markets_by_query.items():
            if needle in url:
                return response, None
        return {"markets": []}, None
    return fake


class TestDiscoverSeriesCatalogue:

    def test_retains_known_mlb_series(self):
        http = make_fake_http(series=[{"ticker": "KXMLBGAME", "title": "MLB Game Winner"}])
        records, err = dsc.discover_series_catalogue(http_get=http)
        assert err is None
        assert len(records) == 1
        assert records[0]["knownAllowlisted"] is True

    def test_retains_new_series_outside_allowlist_by_ticker_prefix(self):
        http = make_fake_http(series=[{"ticker": "KXMLBF3", "title": "MLB First 3 Innings Winner"}])
        records, _ = dsc.discover_series_catalogue(http_get=http)
        assert len(records) == 1
        assert records[0]["knownAllowlisted"] is False
        assert records[0]["mlbAssociationEvidence"] == "ticker_prefix_KXMLB"

    def test_retains_series_via_title_text_even_with_unrelated_ticker(self):
        http = make_fake_http(series=[{"ticker": "KXSOMETHING123", "title": "MLB First 7 Innings Winner"}])
        records, _ = dsc.discover_series_catalogue(http_get=http)
        assert len(records) == 1
        assert records[0]["mlbAssociationEvidence"] in (
            "title_contains_mlb_or_baseball", "title_contains_f3_f7_horizon_language")

    def test_excludes_unrelated_series(self):
        http = make_fake_http(series=[{"ticker": "KXNFLGAME", "title": "NFL Game Winner"}])
        records, _ = dsc.discover_series_catalogue(http_get=http)
        assert records == []

    def test_series_query_error_reported_not_crashed(self):
        def fake(url):
            return None, "ConnectionError: no route to host"
        records, err = dsc.discover_series_catalogue(http_get=fake)
        assert records == []
        assert err is not None


class TestBroadF3F7TextSearch:

    def test_finds_f3_market_by_title_text(self):
        import datetime
        now = datetime.datetime(2026, 7, 30, tzinfo=datetime.timezone.utc)
        code = dsc._kalshi_date(now)
        http = make_fake_http(markets_by_query={
            "status=open": {"markets": [{
                "ticker": "KXUNKNOWN-" + code + "1200BOSNYY-BOS",
                "event_ticker": "KXUNKNOWN-" + code + "1200BOSNYY",
                "title": "Boston wins first 3 innings?",
            }]},
        })
        matches, meta = dsc.broad_f3_f7_text_search(http_get=http, now=now)
        assert len(matches) == 1
        assert matches[0]["horizon"] == "F3"

    def test_no_match_reports_empty_not_crash(self):
        import datetime
        now = datetime.datetime(2026, 7, 30, tzinfo=datetime.timezone.utc)
        http = make_fake_http()
        matches, meta = dsc.broad_f3_f7_text_search(http_get=http, now=now)
        assert matches == []
        assert meta["queriesAttempted"]

    def test_network_error_recorded_in_meta_not_raised(self):
        def fake(url):
            return None, "URLError: [Errno -3] Name or service not known"
        matches, meta = dsc.broad_f3_f7_text_search(http_get=fake)
        assert matches == []
        assert "open" in meta["errors"] or "closed" in meta["errors"]

    def test_date_outside_lookback_window_excluded(self):
        import datetime
        now = datetime.datetime(2026, 7, 30, tzinfo=datetime.timezone.utc)
        old_code = dsc._kalshi_date(now - datetime.timedelta(days=30))
        http = make_fake_http(markets_by_query={
            "status=open": {"markets": [{
                "ticker": "KXUNKNOWN-" + old_code + "1200BOSNYY-BOS",
                "event_ticker": "KXUNKNOWN-" + old_code + "1200BOSNYY",
                "title": "Boston wins first 3 innings?",
            }]},
        })
        matches, _ = dsc.broad_f3_f7_text_search(http_get=http, now=now, lookback_days=7)
        assert matches == []


class TestMain:

    def test_writes_both_artifacts(self, tmp_path):
        http = make_fake_http(series=[{"ticker": "KXMLBGAME", "title": "MLB Game Winner"}])
        result = dsc.main(date_str="2026-07-30", out_dir=str(tmp_path), http_get=http)
        assert os.path.exists(tmp_path / "2026-07-30_series_catalogue.json")
        assert os.path.exists(tmp_path / "2026-07-30_f3_f7_search.json")
        assert result["catalogue"]["mlbAssociatedSeriesCount"] == 1

    def test_no_matches_conclusion_is_exhaustive_search_not_silent(self, tmp_path):
        http = make_fake_http()
        result = dsc.main(date_str="2026-07-30", out_dir=str(tmp_path), http_get=http)
        assert result["f3f7Search"]["conclusion"] == "NOT_FOUND_AFTER_EXHAUSTIVE_SEARCH_THIS_RUN"
        assert result["f3f7Search"]["queriesAttempted"]

    def test_errors_produce_search_incomplete_conclusion_not_false_negative(self, tmp_path):
        def fake(url):
            return None, "no egress"
        result = dsc.main(date_str="2026-07-30", out_dir=str(tmp_path), http_get=fake)
        assert result["f3f7Search"]["conclusion"] == "SEARCH_INCOMPLETE_SEE_ERRORS"
