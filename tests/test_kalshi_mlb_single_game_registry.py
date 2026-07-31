#!/usr/bin/env python3
"""
tests/test_kalshi_mlb_single_game_registry.py
==================================================
Kalshi price-checker correction mission -- proves
lib.kalshi_mlb_single_game_registry.classify_series_for_price_check()
correctly separates the 17 confirmed single-game MLB market families
from the ~162 other "MLB-associated" series a broad ticker-prefix/
title-text heuristic also flags (season leaders, awards, division/
pennant futures, other competitions), using the REAL evidence captured
in data/kalshi/discovery/2026-07-30_series_catalogue.json (a live
series-catalogue dispatch), not invented tickers.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.kalshi_mlb_single_game_registry import (
    classify_series_for_price_check,
    ALL_EXCLUSION_REASONS,
    SERIES_NOT_ALLOWLISTED,
    NON_MLB_COMPETITION,
    FUTURES_OR_AWARD,
)
from lib.research.market_taxonomy import SINGLE_GAME_SERIES_TICKERS


class TestApprovedSingleGameFamilies:

    def test_all_17_confirmed_series_are_allowed(self):
        for ticker in SINGLE_GAME_SERIES_TICKERS:
            allowed, reason = classify_series_for_price_check(ticker, "irrelevant title")
            assert allowed is True, f"{ticker} should be allowed"
            assert reason is None

    def test_full_game_moneyline_allowed(self):
        allowed, reason = classify_series_for_price_check("KXMLBGAME", "Professional Baseball Game")
        assert allowed is True and reason is None

    def test_f3_allowed(self):
        allowed, _ = classify_series_for_price_check("KXMLBF3", "First 3 Innings Winner")
        assert allowed is True

    def test_f7_allowed(self):
        allowed, _ = classify_series_for_price_check("KXMLBF7", "First 7 Innings Winner")
        assert allowed is True

    def test_player_props_allowed(self):
        for ticker, title in [
            ("KXMLBKS", "Pro Baseball Strikeouts"),
            ("KXMLBOUTS", "Pro Baseball Outs Recorded"),
            ("KXMLBHIT", "Pro Baseball Hits"),
            ("KXMLBTB", "Pro Baseball Total Bases"),
            ("KXMLBHRR", "Pro Baseball Hits Runs RBIs"),
            ("KXMLBRBI", "Pro Baseball RBIs"),
            ("KXMLBSB", "Pro Baseball Stolen Bases"),
        ]:
            allowed, reason = classify_series_for_price_check(ticker, title)
            assert allowed is True, f"{ticker} should be allowed"
            assert reason is None


class TestRequiredExclusions:
    """Every one of these is a REAL series from the live series-catalogue
    dispatch (data/kalshi/discovery/2026-07-30_series_catalogue.json)."""

    def test_college_baseball_golden_spikes_award_excluded(self):
        allowed, reason = classify_series_for_price_check(
            "KXNCAABBGS", "College Baseball Golden Spikes Award")
        assert allowed is False
        assert reason == NON_MLB_COMPETITION

    def test_college_baseball_game_excluded(self):
        allowed, reason = classify_series_for_price_check("KXNCAABBGAME", "College Baseball Game")
        assert allowed is False
        assert reason == NON_MLB_COMPETITION

    def test_college_baseball_championship_excluded(self):
        allowed, reason = classify_series_for_price_check(
            "KXNCAAMBACHAMP", "College Baseball Championship")
        assert allowed is False
        assert reason == NON_MLB_COMPETITION

    def test_world_baseball_classic_by_ticker_prefix_excluded(self):
        allowed, reason = classify_series_for_price_check("KXWBCGAME", "World Baseball Classic Game")
        assert allowed is False
        assert reason == NON_MLB_COMPETITION

    def test_world_baseball_classic_under_kxmlb_prefix_excluded_by_title(self):
        """
        KXMLBWORLD is titled "World Baseball Classic" despite sharing the
        KXMLB ticker prefix with genuine MLB series -- proves the
        classifier checks title text, not just ticker prefix, for this
        category (a pure ticker-prefix blacklist would have missed this).
        """
        allowed, reason = classify_series_for_price_check("KXMLBWORLD", "World Baseball Classic")
        assert allowed is False
        assert reason == NON_MLB_COMPETITION

    def test_mexican_baseball_league_excluded(self):
        allowed, reason = classify_series_for_price_check("KXLMBGAME", "Mexican Baseball League")
        assert allowed is False
        assert reason == NON_MLB_COMPETITION

    def test_congressional_baseball_game_excluded(self):
        allowed, reason = classify_series_for_price_check(
            "KXCONGRESSBASEBALL", "Congressional Baseball Game")
        assert allowed is False
        assert reason == NON_MLB_COMPETITION

    def test_award_series_excluded(self):
        for ticker, title in [
            ("KXMLBALCY", "Pro Baseball American League Cy Young"),
            ("KXMLBNLMVP", "Pro Baseball National League MVP"),
            ("KXMLBALROTY", "Pro Baseball American League Rookie of the Year"),
            ("KXMLBSS", "Pro Baseball Silver Slugger"),
            ("KXMLBGG", "Pro Baseball Gold Glove"),
        ]:
            allowed, reason = classify_series_for_price_check(ticker, title)
            assert allowed is False, f"{ticker} should be excluded"
            assert reason == FUTURES_OR_AWARD

    def test_season_leader_series_excluded(self):
        for ticker, title in [
            ("KXLEADERMLBHR", "MLB Home Runs Leader"),
            ("KXLEADERMLBRBI", "MLB RBIs Leader"),
            ("KXLEADERMLBERA", "MLB ERA Leader"),
        ]:
            allowed, reason = classify_series_for_price_check(ticker, title)
            assert allowed is False
            assert reason == FUTURES_OR_AWARD

    def test_division_and_pennant_futures_excluded(self):
        for ticker, title in [
            ("KXMLBALWEST", "American League West Winner"),
            ("KXMLBNLCENT", "National League Central Winner"),
            ("KXMLBAL", "MLB American League Championship"),
            ("KXWSAL", "MLB American League champion"),
        ]:
            allowed, reason = classify_series_for_price_check(ticker, title)
            assert allowed is False
            assert reason == FUTURES_OR_AWARD

    def test_season_win_totals_excluded(self):
        allowed, reason = classify_series_for_price_check(
            "KXMLBWINS-SD", "Pro baseball wins San Diego")
        assert allowed is False
        assert reason == FUTURES_OR_AWARD

    def test_draft_and_trade_excluded(self):
        allowed1, reason1 = classify_series_for_price_check("KXMLBDRAFTPICK", "Pro Baseball Draft Pick")
        allowed2, reason2 = classify_series_for_price_check("KXMLBTRADE", "Pro Baseball Trades")
        assert (allowed1, allowed2) == (False, False)
        assert reason1 == FUTURES_OR_AWARD
        assert reason2 == FUTURES_OR_AWARD

    def test_home_run_derby_excluded(self):
        allowed, reason = classify_series_for_price_check(
            "KXMLBHRDERBY", "Pro Baseball Homerun Derby")
        assert allowed is False
        assert reason == FUTURES_OR_AWARD

    def test_unmatched_non_game_series_falls_back_to_generic_reason(self):
        """An excluded series this module's pattern tables don't specifically
        recognize must still safely exclude via the generic fallback --
        never fall through to inclusion."""
        allowed, reason = classify_series_for_price_check("KXMLBMENTION", "MLB Announcers")
        assert allowed is False
        assert reason == SERIES_NOT_ALLOWLISTED


class TestRealCatalogueEvidence:
    """Re-derives the classification against the actual 179-entry live
    catalogue artifact this mission's fix was based on, proving exactly
    17 series are allowed and none of them is a non-game market."""

    def test_exactly_17_of_179_real_catalogue_series_are_allowed(self):
        import json
        path = os.path.join(ROOT, "data", "kalshi", "discovery", "2026-07-30_series_catalogue.json")
        with open(path) as f:
            data = json.load(f)
        series = data["mlbAssociatedSeries"]
        assert len(series) == 179

        allowed_tickers = set()
        for s in series:
            allowed, reason = classify_series_for_price_check(s["seriesTicker"], s["title"])
            if allowed:
                allowed_tickers.add(s["seriesTicker"])
            else:
                assert reason in ALL_EXCLUSION_REASONS

        assert allowed_tickers == SINGLE_GAME_SERIES_TICKERS & {s["seriesTicker"] for s in series}
        assert len(allowed_tickers) == 17
