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

    def test_f7_total_title_fallback_line_populated(self):
        """
        Regression test: classify_contract() used to derive `line`
        independently from parsed_contract's own marketSuffix via
        private _extract_total_line()/_extract_margin_line()/
        _extract_team_total() helpers. Those were removed in favor of
        reusing classify_market()'s own `line` field (identical suffix
        math, verified equivalent) -- but classify_market()'s title-
        fallback TOTAL branch didn't populate `line` at all, which this
        test caught as a real regression before it was fixed.
        """
        c = _classify("KXUNKNOWNF7TOTAL-26JUL301910BOSNYY-4", "KXUNKNOWNF7TOTAL-26JUL301910BOSNYY",
                       title="First 7 innings total runs over 3.5?")
        assert c["marketFamily"] == "inning_total"
        assert c["period"] == "F7"
        assert c["line"] == 4


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
        """KXMLBKS is a real, CONFIRMED Kalshi series (see
        TestPitcherPropSubjectResolution below for full real-shaped
        ticker coverage) -- this just confirms the base classifier tags
        subjectType=PITCHER for it even with no game context supplied."""
        c = _classify("KXMLBKS-26JUL302140BOSATH-BOSGRAY54-6", "KXMLBKS-26JUL302140BOSATH",
                       title="Sonny Gray: 6+ strikeouts?", away="BOS", home="ATH")
        assert c["marketFamily"] == "pitcher_strikeouts"
        assert c["subjectType"] == "PITCHER"


class TestPitcherPropSubjectResolution:
    """
    Pitcher-prop discovery-wiring mission: classify_contract()'s
    subjectId/subjectName/side/line resolution for pitcher_strikeouts/
    pitcher_outs, given the matched slate `game` dict. All tickers here
    are real-shaped (lib.research.player_prop_parser's own 46,784-row-
    verified convention), not the placeholder "KXMLBSTRIKEOUTS" ticker
    used elsewhere in this file for unrelated unknown-series coverage.
    """

    def _game(self, away="BOS", home="ATH", away_pitcher=("Someone Else", "111111"),
              home_pitcher=("Sonny Gray", "543243")):
        return {
            "away": {"abbr": away, "pitcher": {"name": away_pitcher[0], "id": away_pitcher[1], "note": ""}},
            "home": {"abbr": home, "pitcher": {"name": home_pitcher[0], "id": home_pitcher[1], "note": ""}},
        }

    def test_strikeouts_resolves_subject_side_and_line_for_the_probable_starter(self):
        parsed = parse_contract({
            "ticker": "KXMLBKS-26JUL302140BOSATH-ATHGRAY54-6",
            "event_ticker": "KXMLBKS-26JUL302140BOSATH",
            "title": "Sonny Gray: 6+ strikeouts?",
        })
        c = classify_contract(parsed, game=self._game())
        assert c["marketFamily"] == "pitcher_strikeouts"
        assert c["subjectType"] == "PITCHER"
        assert c["subjectId"] == "543243"
        assert c["subjectName"] == "Sonny Gray"
        assert c["side"] == "Yes"
        assert c["line"] == 6

    def test_outs_resolves_subject_side_and_line_for_the_probable_starter(self):
        parsed = parse_contract({
            "ticker": "KXMLBOUTS-26JUL302140BOSATH-ATHGRAY54-17",
            "event_ticker": "KXMLBOUTS-26JUL302140BOSATH",
            "title": "Sonny Gray: 17+ Outs Recorded?",
        })
        c = classify_contract(parsed, game=self._game())
        assert c["marketFamily"] == "pitcher_outs"
        assert c["subjectId"] == "543243"
        assert c["subjectName"] == "Sonny Gray"
        assert c["side"] == "Yes"
        assert c["line"] == 17

    def test_away_side_pitcher_also_resolves(self):
        parsed = parse_contract({
            "ticker": "KXMLBKS-26JUL302140BOSATH-BOSGRAY54-6",
            "event_ticker": "KXMLBKS-26JUL302140BOSATH",
            "title": "Sonny Gray: 6+ strikeouts?",
        })
        game = self._game(away_pitcher=("Sonny Gray", "543243"), home_pitcher=("Someone Else", "111111"))
        c = classify_contract(parsed, game=game)
        assert c["subjectId"] == "543243"
        assert c["subjectName"] == "Sonny Gray"

    def test_no_game_context_leaves_subject_unresolved_but_still_resolves_side_and_line(self):
        """The exact prior behavior (classify_contract called without game=) must be unchanged -- but side/line are structural ticker facts, resolvable even with zero game context."""
        parsed = parse_contract({
            "ticker": "KXMLBKS-26JUL302140BOSATH-ATHGRAY54-6",
            "event_ticker": "KXMLBKS-26JUL302140BOSATH",
            "title": "Sonny Gray: 6+ strikeouts?",
        })
        c = classify_contract(parsed)
        assert c["subjectId"] is None
        assert c["subjectName"] is None
        assert c["side"] == "Yes"
        assert c["line"] == 6

    def test_name_not_matching_either_probable_starter_stays_unresolved(self):
        """Requirement: never fuzzy-match. A pitcher named in the ticker/title who
        isn't today's listed starter for the resolved team has exactly zero
        candidates to check against pre-game (no boxscore/roster search) -- unresolved, not guessed."""
        parsed = parse_contract({
            "ticker": "KXMLBKS-26JUL302140BOSATH-ATHBULLPENGUY7-4",
            "event_ticker": "KXMLBKS-26JUL302140BOSATH",
            "title": "Some Reliever: 4+ strikeouts?",
        })
        c = classify_contract(parsed, game=self._game())
        assert c["subjectId"] is None
        assert c["subjectName"] is None
        # side/line still resolve -- they don't depend on player identity.
        assert c["side"] == "Yes"
        assert c["line"] == 4

    def test_conflicting_team_token_never_guessed(self):
        """A ticker team-prefix that matches neither away nor home (TEAM_UNRESOLVED_CONFLICT) must never fall back to searching both rosters."""
        parsed = parse_contract({
            "ticker": "KXMLBKS-26JUL302140BOSATH-NYYGRAY54-6",
            "event_ticker": "KXMLBKS-26JUL302140BOSATH",
            "title": "Sonny Gray: 6+ strikeouts?",
        })
        c = classify_contract(parsed, game=self._game())
        assert c["subjectId"] is None
        assert c["subjectName"] is None

    def test_missing_title_leaves_subject_unresolved(self):
        """No display name signal at all (title absent/unparseable) -- nothing to match against, never guessed from the ticker token alone (which is explicitly documented as unreliable -- accents/typos)."""
        parsed = parse_contract({
            "ticker": "KXMLBKS-26JUL302140BOSATH-ATHGRAY54-6",
            "event_ticker": "KXMLBKS-26JUL302140BOSATH",
            "title": None,
        })
        c = classify_contract(parsed, game=self._game())
        assert c["subjectId"] is None
        assert c["subjectName"] is None

    def test_modeled_pitcher_prop_families_scoped_to_strikeouts_and_outs_only(self):
        """
        pitcher_hits_allowed/pitcher_earned_runs have no probability
        model (PR #58 only covers strikeouts/outs) -- subject
        resolution must never be attempted for them even if a future
        Kalshi series prefix mapping activates their classification, so
        they stay exactly as unresolved as before this mission.
        """
        from lib.kalshi_mlb_market_classifier import (
            _MODELED_PITCHER_PROP_FAMILIES, _PITCHER_FAMILIES,
        )
        from lib.research.market_taxonomy import FAMILY_PITCHER_HITS_ALLOWED, FAMILY_PITCHER_EARNED_RUNS
        assert _MODELED_PITCHER_PROP_FAMILIES == {"pitcher_strikeouts", "pitcher_outs"}
        assert FAMILY_PITCHER_HITS_ALLOWED in _PITCHER_FAMILIES
        assert FAMILY_PITCHER_HITS_ALLOWED not in _MODELED_PITCHER_PROP_FAMILIES
        assert FAMILY_PITCHER_EARNED_RUNS not in _MODELED_PITCHER_PROP_FAMILIES
