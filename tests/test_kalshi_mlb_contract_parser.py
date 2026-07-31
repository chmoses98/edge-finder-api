#!/usr/bin/env python3
"""
tests/test_kalshi_mlb_contract_parser.py
===========================================
Coverage for lib/kalshi_mlb_contract_parser.py: ticker/event-ticker
parsing, date derivation, team-abbreviation disambiguation, and
doubleheader isolation.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.kalshi_mlb_contract_parser import (  # noqa: E402
    kalshi_date_code_to_iso, parse_event_suffix, parse_contract,
    resolve_doubleheader_game_number,
)


class TestDateCode:

    def test_valid_code(self):
        assert kalshi_date_code_to_iso("26JUL30") == "2026-07-30"

    def test_invalid_month(self):
        assert kalshi_date_code_to_iso("26XXX30") is None

    def test_too_short(self):
        assert kalshi_date_code_to_iso("26JUL") is None

    def test_none_input(self):
        assert kalshi_date_code_to_iso(None) is None


class TestEventSuffixParsing:

    def test_three_letter_teams(self):
        result = parse_event_suffix("KXMLBGAME", "KXMLBGAME-26JUL302140BOSATH")
        assert result == {"date": "2026-07-30", "time_str": "2140", "away": "BOS", "home": "ATH"}

    def test_two_letter_away_team(self):
        # SF (2-letter) @ SD (2-letter) -- both in TWO_LETTER_TEAM_ABBRS
        result = parse_event_suffix("KXMLBGAME", "KXMLBGAME-26JUL302140SFSD")
        assert result["away"] == "SF"
        assert result["home"] == "SD"

    def test_two_letter_vs_three_letter(self):
        # TB (2-letter) @ MIA (3-letter)
        result = parse_event_suffix("KXMLBGAME", "KXMLBGAME-26JUL301340TBMIA")
        assert result["away"] == "TB"
        assert result["home"] == "MIA"

    def test_wrong_series_prefix_returns_empty(self):
        result = parse_event_suffix("KXMLBGAME", "KXMLBTOTAL-26JUL302140BOSATH")
        assert result == {"date": None, "time_str": None, "away": None, "home": None}

    def test_missing_inputs(self):
        assert parse_event_suffix(None, None) == {"date": None, "time_str": None, "away": None, "home": None}


class TestParseContract:

    def test_full_ml_contract(self):
        raw = {
            "ticker": "KXMLBGAME-26JUL302140BOSATH-BOS",
            "event_ticker": "KXMLBGAME-26JUL302140BOSATH",
            "title": "Boston vs A's Winner?",
            "status": "active",
            "yes_bid": 0.63, "yes_ask": 0.64,
            "close_time": "2026-08-03T01:40:00Z",
        }
        p = parse_contract(raw)
        assert p["ticker"] == raw["ticker"]
        assert p["eventTicker"] == raw["event_ticker"]
        assert p["seriesTicker"] == "KXMLBGAME"
        assert p["date"] == "2026-07-30"
        assert p["awayTeam"] == "BOS"
        assert p["homeTeam"] == "ATH"
        assert p["marketSuffix"] == "BOS"
        assert p["yesBid"] == 63.0
        assert p["yesAsk"] == 64.0
        assert p["gameId"] == "2026-07-30_BOS_ATH_2140"
        assert p["doubleheaderGameNumber"] is None

    def test_missing_fields_stay_none_not_zero(self):
        raw = {"ticker": "KXMLBGAME-26JUL302140BOSATH-BOS", "event_ticker": "KXMLBGAME-26JUL302140BOSATH"}
        p = parse_contract(raw)
        assert p["yesBid"] is None
        assert p["yesAsk"] is None
        assert p["volume"] is None
        assert p["marketStatus"] is None

    def test_prices_already_0_to_1_scale_normalized_to_pct(self):
        raw = {"ticker": "T-1", "event_ticker": "E-1", "yes_bid": 0.41, "yes_ask": 0.42}
        p = parse_contract(raw)
        assert p["yesBid"] == 41.0
        assert p["yesAsk"] == 42.0

    def test_prices_already_cents_scale_unchanged(self):
        raw = {"ticker": "T-1", "event_ticker": "E-1", "yes_bid": 41, "yes_ask": 42}
        p = parse_contract(raw)
        assert p["yesBid"] == 41.0
        assert p["yesAsk"] == 42.0

    def test_unparseable_ticker_never_raises(self):
        p = parse_contract({"ticker": "GARBAGE"})
        assert p["date"] is None
        assert p["awayTeam"] is None
        assert p["ticker"] == "GARBAGE"

    def test_empty_dict_never_raises(self):
        p = parse_contract({})
        assert p["ticker"] is None
        assert p["date"] is None


class TestDoubleheaderResolution:

    def test_no_known_games_returns_none(self):
        assert resolve_doubleheader_game_number("2026-07-30", "BOS", "NYY", "1600", None) is None

    def test_single_game_not_a_doubleheader(self):
        known = [{"date": "2026-07-30", "away": "BOS", "home": "NYY", "time_str": "1600"}]
        assert resolve_doubleheader_game_number("2026-07-30", "BOS", "NYY", "1600", known) is None

    def test_two_games_ordered_by_time(self):
        known = [
            {"date": "2026-07-30", "away": "BOS", "home": "NYY", "time_str": "1600"},
            {"date": "2026-07-30", "away": "BOS", "home": "NYY", "time_str": "1930"},
        ]
        assert resolve_doubleheader_game_number("2026-07-30", "BOS", "NYY", "1600", known) == 1
        assert resolve_doubleheader_game_number("2026-07-30", "BOS", "NYY", "1930", known) == 2

    def test_different_team_pair_not_confused(self):
        """A doubleheader between BOS/NYY must never be confused with an
        unrelated same-day SEA/LAD game."""
        known = [
            {"date": "2026-07-30", "away": "BOS", "home": "NYY", "time_str": "1600"},
            {"date": "2026-07-30", "away": "BOS", "home": "NYY", "time_str": "1930"},
            {"date": "2026-07-30", "away": "SEA", "home": "LAD", "time_str": "2210"},
        ]
        assert resolve_doubleheader_game_number("2026-07-30", "SEA", "LAD", "2210", known) is None

    def test_full_parse_contract_resolves_doubleheader_leg(self):
        known_games = [
            {"date": "2026-07-30", "away": "BOS", "home": "NYY", "time_str": "1600"},
            {"date": "2026-07-30", "away": "BOS", "home": "NYY", "time_str": "1930"},
        ]
        raw_g1 = {"ticker": "KXMLBGAME-26JUL301600BOSNYY-BOS", "event_ticker": "KXMLBGAME-26JUL301600BOSNYY"}
        raw_g2 = {"ticker": "KXMLBGAME-26JUL301930BOSNYY-BOS", "event_ticker": "KXMLBGAME-26JUL301930BOSNYY"}
        p1 = parse_contract(raw_g1, known_games=known_games)
        p2 = parse_contract(raw_g2, known_games=known_games)
        assert p1["doubleheaderGameNumber"] == 1
        assert p2["doubleheaderGameNumber"] == 2
        assert p1["gameId"] != p2["gameId"]
