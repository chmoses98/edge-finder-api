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
    detect_new_unclassified_mlb_series,
    ALL_EXCLUSION_REASONS,
    SERIES_NOT_ALLOWLISTED,
    NON_MLB_COMPETITION,
    FUTURES_OR_AWARD,
    NEW_UNCLASSIFIED_MLB_SERIES,
)
from lib.research.market_taxonomy import (
    SINGLE_GAME_SERIES_TICKERS,
    CONFIRMED_SINGLE_GAME_SERIES_TICKERS,
    SPECULATIVE_UNCONFIRMED_SERIES_TICKERS,
)


class TestApprovedSingleGameFamilies:

    def test_all_17_confirmed_series_are_allowed(self):
        assert len(CONFIRMED_SINGLE_GAME_SERIES_TICKERS) == 17
        for ticker in CONFIRMED_SINGLE_GAME_SERIES_TICKERS:
            allowed, reason = classify_series_for_price_check(ticker, "irrelevant title")
            assert allowed is True, f"{ticker} should be allowed"
            assert reason is None

    def test_speculative_unconfirmed_series_are_never_allowed(self):
        """
        4 ticker names were GUESSED at before F3/F7 existence was
        confirmed (KXMLBF3SPREAD/F3TOTAL/F7SPREAD/F7TOTAL) and are still
        recognized by the general classifier for future-proofing, but
        have never been directly observed as real Kalshi series -- the
        strict price-check registry must not include them just because
        they share a family shape with confirmed series.
        """
        assert SPECULATIVE_UNCONFIRMED_SERIES_TICKERS <= SINGLE_GAME_SERIES_TICKERS
        assert SPECULATIVE_UNCONFIRMED_SERIES_TICKERS.isdisjoint(CONFIRMED_SINGLE_GAME_SERIES_TICKERS)
        for ticker in SPECULATIVE_UNCONFIRMED_SERIES_TICKERS:
            allowed, reason = classify_series_for_price_check(ticker, "irrelevant title")
            assert allowed is False, f"{ticker} should NOT be allowed (unconfirmed)"

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

        assert allowed_tickers == CONFIRMED_SINGLE_GAME_SERIES_TICKERS & {s["seriesTicker"] for s in series}
        assert len(allowed_tickers) == 17


class TestNewUnclassifiedSeriesWarning:
    """Future-proofing safeguard (final maintainer review requirement
    #3): if Kalshi ever ships a genuinely new KXMLB*-prefixed series
    this repository has no evidence about, it must never be auto-
    included, and must instead raise a non-fatal, specific audit
    warning recommending manual review."""

    def _excluded(self, ticker, title=None, date="2026-08-01"):
        allowed, reason = classify_series_for_price_check(ticker, title)
        assert allowed is False
        return {"seriesTicker": ticker, "title": title, "date": date, "exclusionReason": reason}

    def test_new_kxmlb_series_with_no_known_pattern_triggers_warning(self):
        excluded = [self._excluded("KXMLBWALKS", "Pro Baseball Walks")]
        warnings = detect_new_unclassified_mlb_series(excluded)
        assert len(warnings) == 1
        assert warnings[0]["warning"] == NEW_UNCLASSIFIED_MLB_SERIES
        assert warnings[0]["seriesTicker"] == "KXMLBWALKS"
        assert warnings[0]["title"] == "Pro Baseball Walks"
        assert warnings[0]["detectedDate"] == "2026-08-01"
        assert "manual review" in warnings[0]["recommendation"].lower()

    def test_confirmed_series_never_triggers_warning(self):
        excluded = []  # a confirmed series is never in the excluded list at all
        warnings = detect_new_unclassified_mlb_series(excluded)
        assert warnings == []

    def test_recognized_non_mlb_competition_does_not_trigger_warning(self):
        """Golden Spikes Award etc. are already explained -- they are not
        'new and unclassified', they are confirmed non-game markets."""
        excluded = [self._excluded("KXNCAABBGS", "College Baseball Golden Spikes Award")]
        warnings = detect_new_unclassified_mlb_series(excluded)
        assert warnings == []

    def test_recognized_futures_or_award_does_not_trigger_warning(self):
        excluded = [self._excluded("KXMLBALCY", "Pro Baseball American League Cy Young")]
        warnings = detect_new_unclassified_mlb_series(excluded)
        assert warnings == []

    def test_non_kxmlb_unclassified_series_does_not_trigger_warning(self):
        """Scoped to KXMLB*-prefixed tickers only, per the explicit
        "if Kalshi introduces a new KXMLB* single-game series" wording --
        a non-KXMLB unrecognized ticker is out of scope for this specific
        warning (still safely excluded regardless)."""
        excluded = [self._excluded("KXSOMETHINGELSE", "Something else entirely")]
        warnings = detect_new_unclassified_mlb_series(excluded)
        assert warnings == []

    def test_deduplicates_by_series_ticker(self):
        excluded = [
            self._excluded("KXMLBWALKS", "Pro Baseball Walks", date="2026-08-01"),
            self._excluded("KXMLBWALKS", "Pro Baseball Walks", date="2026-08-02"),
        ]
        warnings = detect_new_unclassified_mlb_series(excluded)
        assert len(warnings) == 1

    def test_never_raises_on_missing_fields(self):
        warnings = detect_new_unclassified_mlb_series([
            {"exclusionReason": SERIES_NOT_ALLOWLISTED, "seriesTicker": "KXMLBWALKS"},
        ])
        assert len(warnings) == 1
        assert warnings[0]["title"] is None
        assert warnings[0]["detectedDate"] is None

    def test_multiple_new_series_sorted_deterministically(self):
        excluded = [
            self._excluded("KXMLBZZZ", "Z market"),
            self._excluded("KXMLBAAA", "A market"),
        ]
        warnings = detect_new_unclassified_mlb_series(excluded)
        assert [w["seriesTicker"] for w in warnings] == ["KXMLBAAA", "KXMLBZZZ"]

    def test_currently_ambiguous_real_catalogue_series_flagged_for_review(self):
        """
        Real evidence from the live catalogue: a handful of KXMLB*
        series (e.g. "Pro Baseball Home Runs") are genuinely ambiguous
        from title text alone -- neither confirmed single-game nor
        confidently classifiable as a known non-game pattern. These
        must surface as review candidates rather than being silently
        swallowed by the generic exclusion reason.
        """
        import json
        path = os.path.join(ROOT, "data", "kalshi", "discovery", "2026-07-30_series_catalogue.json")
        with open(path) as f:
            data = json.load(f)
        excluded = []
        for s in data["mlbAssociatedSeries"]:
            allowed, reason = classify_series_for_price_check(s["seriesTicker"], s["title"])
            if not allowed:
                excluded.append({"seriesTicker": s["seriesTicker"], "title": s["title"],
                                  "date": "2026-07-30", "exclusionReason": reason})
        warnings = detect_new_unclassified_mlb_series(excluded)
        tickers = {w["seriesTicker"] for w in warnings}
        assert "KXMLBHR" in tickers
        assert "KXMLBSTGAME" in tickers
        # Confirmed non-game patterns must NOT appear here.
        assert "KXNCAABBGS" not in tickers
        assert "KXMLBALCY" not in tickers
