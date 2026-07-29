#!/usr/bin/env python3
"""
tests/test_kalshi_discovery.py
===================================
Model Performance Phase 2A -- unit tests for the pure
discover_unknown_series() helper (lib/kalshi_discovery.py), extracted
from scripts/build_kalshi_registry.py specifically so it can be tested
without triggering that script's unconditional top-level live Kalshi
HTTP calls.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.kalshi_discovery import discover_unknown_series


class TestUnknownSeriesRetention:

    def test_known_series_markets_are_skipped(self):
        result = discover_unknown_series(
            {"KXMLBGAME": [{"ticker": "KXMLBGAME-26JUL29-PIT"}]},
            known_series=["KXMLBGAME"],
            kalshi_date="26JUL29",
            search_json={},
        )
        assert result == []

    def test_unknown_series_from_direct_pull_retained(self):
        result = discover_unknown_series(
            {"KXUNKNOWNF3": [{"ticker": "KXUNKNOWNF3-26JUL29ABCXYZ-TIE"}]},
            known_series=["KXMLBGAME", "KXMLBF5"],
            kalshi_date="26JUL29",
            search_json={},
        )
        assert len(result) == 1
        assert result[0]["ticker"] == "KXUNKNOWNF3-26JUL29ABCXYZ-TIE"
        assert result[0]["_discoverySource"] == "build_kalshi_registry_direct_pull"

    def test_unknown_series_from_search_json_retained(self):
        result = discover_unknown_series(
            {},
            known_series=["KXMLBGAME"],
            kalshi_date="26JUL29",
            search_json={
                "discoveredUnknownSeriesMarkets": [
                    {"market_ticker": "KXOTHER-26JUL29ABCXYZ-TIE", "event_ticker": "KXOTHER-26JUL29ABCXYZ"}
                ]
            },
        )
        assert len(result) == 1
        assert result[0]["market_ticker"] == "KXOTHER-26JUL29ABCXYZ-TIE"
        assert result[0]["_discoverySource"] == "kalshisearch_broad_discovery"

    def test_search_json_markets_filtered_by_date(self):
        result = discover_unknown_series(
            {},
            known_series=["KXMLBGAME"],
            kalshi_date="26JUL29",
            search_json={
                "discoveredUnknownSeriesMarkets": [
                    {"market_ticker": "KXOTHER-26JUN01ABCXYZ-TIE", "event_ticker": "KXOTHER-26JUN01ABCXYZ"}
                ]
            },
        )
        assert result == []

    def test_deduplicates_ticker_seen_in_both_sources(self):
        result = discover_unknown_series(
            {"KXUNKNOWNF3": [{"ticker": "KXUNKNOWNF3-26JUL29ABCXYZ-TIE"}]},
            known_series=["KXMLBGAME"],
            kalshi_date="26JUL29",
            search_json={
                "discoveredUnknownSeriesMarkets": [
                    {"market_ticker": "KXUNKNOWNF3-26JUL29ABCXYZ-TIE", "event_ticker": "KXUNKNOWNF3-26JUL29ABCXYZ"}
                ]
            },
        )
        assert len(result) == 1
        assert result[0]["_discoverySource"] == "build_kalshi_registry_direct_pull"

    def test_missing_ticker_never_raises_or_included(self):
        result = discover_unknown_series(
            {"KXUNKNOWNF3": [{"title": "no ticker field"}]},
            known_series=["KXMLBGAME"],
            kalshi_date="26JUL29",
            search_json={},
        )
        assert result == []

    def test_empty_inputs_return_empty_list(self):
        assert discover_unknown_series(None, [], "26JUL29", None) == []
        assert discover_unknown_series({}, [], "26JUL29", {}) == []

    def test_pure_no_mutation_of_inputs(self):
        all_by_series = {"KXUNKNOWNF3": [{"ticker": "T-1"}]}
        search_json = {"discoveredUnknownSeriesMarkets": []}
        before_series = dict(all_by_series)
        before_search = dict(search_json)
        discover_unknown_series(all_by_series, ["KXMLBGAME"], "26JUL29", search_json)
        assert all_by_series == before_series
        assert search_json == before_search

    def test_deterministic(self):
        all_by_series = {"KXUNKNOWNF3": [{"ticker": "T-1"}, {"ticker": "T-2"}]}
        r1 = discover_unknown_series(all_by_series, ["KXMLBGAME"], "26JUL29", {})
        r2 = discover_unknown_series(all_by_series, ["KXMLBGAME"], "26JUL29", {})
        assert r1 == r2
