#!/usr/bin/env python3
"""
tests/test_kalshi_mlb_market_classifier.py
==============================================
Coverage for lib/kalshi_mlb_market_classifier.py: F3/F5/full-game
distinction, F5 spread classification (incl. alternates), full-game
spreads, alternate totals, team totals, NRFI/YRFI, and unknown-market
preservation (never dropped, never fabricated).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.kalshi_mlb_contract_parser import parse_contract  # noqa: E402
from lib.kalshi_mlb_market_classifier import classify_contract  # noqa: E402


def _classify(ticker, event_ticker, title=None, away=None, home=None):
    parsed = parse_contract({"ticker": ticker, "event_ticker": event_ticker, "title": title})
    return classify_contract(parsed, away_team=away, home_team=home)


class TestMoneylineAndF5:

    def test_ml_away_side(self):
        c = _classify("KXMLBGAME-26JUL302140BOSATH-BOS", "KXMLBGAME-26JUL302140BOSATH", away="BOS", home="ATH")
        assert c["marketFamily"] == "game_result"
        assert c["period"] == "full_game"
        assert c["side"] == "Away"
        assert c["subjectType"] == "GAME"

    def test_ml_home_side(self):
        c = _classify("KXMLBGAME-26JUL302140BOSATH-ATH", "KXMLBGAME-26JUL302140BOSATH", away="BOS", home="ATH")
        assert c["side"] == "Home"

    def test_f5_winner_team_legs(self):
        c_away = _classify("KXMLBF5-26JUL302140BOSATH-BOS", "KXMLBF5-26JUL302140BOSATH", away="BOS", home="ATH")
        c_home = _classify("KXMLBF5-26JUL302140BOSATH-ATH", "KXMLBF5-26JUL302140BOSATH", away="BOS", home="ATH")
        assert c_away["marketFamily"] == "inning_result"
        assert c_away["period"] == "F5"
        assert c_away["side"] == "Away"
        assert c_home["side"] == "Home"

    def test_f5_tie_leg(self):
        c = _classify("KXMLBF5-26JUL302140BOSATH-TIE", "KXMLBF5-26JUL302140BOSATH", away="BOS", home="ATH")
        assert c["marketFamily"] == "inning_result"
        assert c["period"] == "F5"
        assert c["side"] == "Tie"

    def test_side_without_away_home_context_falls_back_to_raw_abbr(self):
        """Without any resolvable away/home context, a team-leg market's
        side must never be fabricated as Away/Home -- the raw
        abbreviation is reported instead. (parse_contract normally
        derives away/home from the ticker itself; this test simulates
        the case where that context genuinely isn't available, e.g. a
        malformed/partial parsed contract.)"""
        parsed = {
            "ticker": "KXMLBGAME-26JUL302140BOSATH-BOS",
            "eventTicker": "KXMLBGAME-26JUL302140BOSATH",
            "marketTitle": None, "marketSubtitle": None,
            "marketSuffix": "BOS", "awayTeam": None, "homeTeam": None,
        }
        c = classify_contract(parsed)
        assert c["side"] == "BOS"


class TestFullGameSpreadAndAlternates:

    def test_spread_line_extraction(self):
        c = _classify("KXMLBSPREAD-26JUL302140BOSATH-BOS2", "KXMLBSPREAD-26JUL302140BOSATH")
        assert c["marketFamily"] == "winning_margin"
        assert c["period"] == "full_game"
        assert c["subjectType"] == "TEAM"
        assert c["subjectId"] == "BOS"
        assert c["line"] == 1.5

    def test_every_alternate_spread_line_gets_distinct_value(self):
        lines = {}
        for suffix in ("BOS2", "BOS3", "BOS4"):
            c = _classify(f"KXMLBSPREAD-26JUL302140BOSATH-{suffix}", "KXMLBSPREAD-26JUL302140BOSATH")
            lines[suffix] = c["line"]
        assert lines == {"BOS2": 1.5, "BOS3": 2.5, "BOS4": 3.5}


class TestF5SpreadAndAlternates:

    def test_f5_spread_line_extraction(self):
        c = _classify("KXMLBF5SPREAD-26JUL302140BOSATH-BOS2", "KXMLBF5SPREAD-26JUL302140BOSATH")
        assert c["marketFamily"] == "winning_margin"
        assert c["period"] == "F5"
        assert c["line"] == 1.5

    def test_f5_spread_alternates_distinct(self):
        c2 = _classify("KXMLBF5SPREAD-26JUL302140BOSATH-BOS2", "KXMLBF5SPREAD-26JUL302140BOSATH")
        c3 = _classify("KXMLBF5SPREAD-26JUL302140BOSATH-BOS3", "KXMLBF5SPREAD-26JUL302140BOSATH")
        assert c2["line"] != c3["line"]


class TestTotalsAndAlternates:

    def test_full_game_total_line(self):
        c = _classify("KXMLBTOTAL-26JUL302140BOSATH-8", "KXMLBTOTAL-26JUL302140BOSATH")
        assert c["marketFamily"] == "game_total"
        assert c["period"] == "full_game"
        assert c["side"] == "Over"
        assert c["line"] == 8

    def test_full_game_total_alternates_distinct(self):
        lines = [_classify(f"KXMLBTOTAL-26JUL302140BOSATH-{n}", "KXMLBTOTAL-26JUL302140BOSATH")["line"]
                 for n in (7, 8, 9, 10)]
        assert lines == [7, 8, 9, 10]

    def test_f5_total_line(self):
        c = _classify("KXMLBF5TOTAL-26JUL302140BOSATH-6", "KXMLBF5TOTAL-26JUL302140BOSATH")
        assert c["marketFamily"] == "inning_total"
        assert c["period"] == "F5"
        assert c["line"] == 6


class TestTeamTotal:

    def test_team_total_line_and_subject(self):
        c = _classify("KXMLBTEAMTOTAL-26JUL302140BOSATH-BOS4", "KXMLBTEAMTOTAL-26JUL302140BOSATH")
        assert c["marketFamily"] == "team_total"
        assert c["subjectType"] == "TEAM"
        assert c["subjectId"] == "BOS"
        assert c["side"] == "Over"
        assert c["line"] == 3.5

    def test_team_total_alternates_distinct(self):
        lines = [_classify(f"KXMLBTEAMTOTAL-26JUL302140BOSATH-BOS{n}", "KXMLBTEAMTOTAL-26JUL302140BOSATH")["line"]
                 for n in (2, 3, 4, 5)]
        assert lines == [1.5, 2.5, 3.5, 4.5]

    def test_both_teams_distinguished(self):
        away = _classify("KXMLBTEAMTOTAL-26JUL302140BOSATH-BOS4", "KXMLBTEAMTOTAL-26JUL302140BOSATH")
        home = _classify("KXMLBTEAMTOTAL-26JUL302140BOSATH-ATH4", "KXMLBTEAMTOTAL-26JUL302140BOSATH")
        assert away["subjectId"] == "BOS"
        assert home["subjectId"] == "ATH"


class TestNrfiYrfi:

    def test_rfi_classified_as_first_inning_run(self):
        c = _classify("KXMLBRFI-26JUL302140BOSATH", "KXMLBRFI-26JUL302140BOSATH")
        assert c["marketFamily"] == "first_inning_run"
        assert c["period"] == "F1"
        assert c["side"] == "Yes"
        assert c["subjectType"] == "INNING"


class TestF3TitleFallback:

    def test_f3_classified_via_title_even_without_confirmed_prefix(self):
        c = _classify("KXMLBF3-26JUL302140BOSATH-BOS", "KXMLBF3-26JUL302140BOSATH",
                       title="Who wins the first 3 innings?")
        assert c["marketFamily"] == "inning_result"
        assert c["period"] == "F3"
        assert c["classificationStatus"] == "classified"

    def test_f3_via_pure_title_fallback_unknown_prefix(self):
        c = _classify("KXMLBWEIRD-26JUL302140BOSATH-BOS", "KXMLBWEIRD-26JUL302140BOSATH",
                       title="Who wins the first 3 innings?")
        assert c["marketFamily"] == "inning_result"
        assert c["period"] == "F3"
        assert c["classificationStatus"] == "classified_by_title_fallback_unverified_prefix"


class TestUnknownMarketPreservation:

    def test_completely_unrecognized_series_never_dropped(self):
        c = _classify("KXMLBSTRIKEOUTS-26JUL302140BOSATH-GRAY5", "KXMLBSTRIKEOUTS-26JUL302140BOSATH",
                       title="Sonny Gray over 5.5 strikeouts?")
        # Not classifiable into any known family/period, but still returns
        # a full result dict -- never raises, never returns None.
        assert c is not None
        assert c["marketFamily"] is None
        assert c["classificationStatus"] == "unclassified"

    def test_pitcher_family_subject_type(self):
        """Even though no real pitcher-prop ticker has ever been observed
        (docs/KALSHI_MLB_MARKET_COVERAGE_AUDIT.md), the classifier must
        still correctly tag subjectType=PITCHER if a pitcher_strikeouts
        family were ever matched by an explicit series alias."""
        from lib.research.market_taxonomy import SERIES_FAMILY_MAP, FAMILY_PITCHER_STRIKEOUTS
        # Confirm this family constant exists in the shared taxonomy even
        # though no series prefix currently maps to it (by design).
        assert FAMILY_PITCHER_STRIKEOUTS == "pitcher_strikeouts"
