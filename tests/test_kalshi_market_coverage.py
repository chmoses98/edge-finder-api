#!/usr/bin/env python3
"""
tests/test_kalshi_market_coverage.py
=======================================
MLB slate coverage audit: lib.kalshi_market_coverage's terminal-state
classification and the "no silent remainder" accounting invariant --
archivedTotal must always equal the sum of every terminal-state bucket,
for every family Kalshi actually returns (full-game, F3/F5/F7, alternate
ladders, pitcher props, hitter props, a parse failure, an unclassified
series, an unmatched game, and a started game).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.kalshi_market_coverage import (  # noqa: E402
    build_coverage_ledger, classify_terminal_state, coverage_accounting,
    ALL_TERMINAL_STATES, FULLY_EVALUATED, MISSING_REQUIRED_CONTEXT,
    UNSUPPORTED_MODEL_FAMILY, PARSER_UNRESOLVED, GAME_MAPPING_UNRESOLVED,
    STARTED_GAME_EXCLUDED, NOT_APPLICABLE, NOT_EVALUATED_BUG,
)


def make_game(game_id=1, away="BOS", home="NYY", start="2026-08-19T20:40:00Z", status="Scheduled"):
    return {
        "gameId": game_id,
        "away": {"abbr": away, "pitcherSavant": {"xFIP": 3.8, "avgIPperStart": 6.0, "kPct": 0.24}},
        "home": {"abbr": home, "pitcherSavant": {"xFIP": 4.0, "avgIPperStart": 6.0, "kPct": 0.22}},
        "awayTeamStats": {"offenseBaselineAdj": 4.6},
        "homeTeamStats": {"offenseBaselineAdj": 4.3},
        "park": {"parkFactor": 100},
        "startTime": start,
        "status": status,
    }


def make_search_doc(markets, date_str="2026-08-19", unknown=None):
    return {"date": date_str, "markets": markets, "discoveredUnknownSeriesMarkets": unknown or []}


def mkt(ticker, event_ticker, title, yes_bid=40.0, yes_ask=45.0, status="active"):
    return {"market_ticker": ticker, "event_ticker": event_ticker, "title": title,
            "subtitle": "", "status": status, "yes_bid": yes_bid, "yes_ask": yes_ask,
            "close_time": "2026-08-20T00:00:00Z", "volume": 50.0}


# ── classify_terminal_state: pure, isolated per-state tests ────────────────

class TestClassifyTerminalStatePure:

    def test_parse_error_contract(self):
        c = {"classificationStatus": "parse_error"}
        assert classify_terminal_state(c) == PARSER_UNRESOLVED

    def test_unclassified_series_contract(self):
        c = {"classificationStatus": "unclassified", "gameMatched": True, "gameStatus": "Scheduled"}
        assert classify_terminal_state(c) == PARSER_UNRESOLVED

    def test_different_slate_date_contract(self):
        c = {"classificationStatus": "different_slate_date"}
        assert classify_terminal_state(c) == NOT_APPLICABLE

    def test_unmatched_game_is_mapping_unresolved(self):
        c = {"classificationStatus": "classified", "gameMatched": False, "modelSupportStatus": "SUPPORTED"}
        assert classify_terminal_state(c) == GAME_MAPPING_UNRESOLVED

    def test_started_game_excluded_even_if_modeled(self):
        c = {"classificationStatus": "classified", "gameMatched": True,
             "gameStatus": "In Progress", "modelSupportStatus": "SUPPORTED"}
        assert classify_terminal_state(c) == STARTED_GAME_EXCLUDED

    def test_final_game_status_excluded(self):
        c = {"classificationStatus": "classified", "gameMatched": True,
             "gameStatus": "Final", "modelSupportStatus": "UNSUPPORTED"}
        assert classify_terminal_state(c) == STARTED_GAME_EXCLUDED

    def test_pregame_status_not_excluded(self):
        c = {"classificationStatus": "classified", "gameMatched": True,
             "gameStatus": "Warmup", "modelSupportStatus": "SUPPORTED"}
        assert classify_terminal_state(c) == FULLY_EVALUATED

    def test_supported_is_fully_evaluated(self):
        c = {"classificationStatus": "classified", "gameMatched": True,
             "gameStatus": "Scheduled", "modelSupportStatus": "SUPPORTED"}
        assert classify_terminal_state(c) == FULLY_EVALUATED

    def test_missing_data_is_missing_required_context(self):
        c = {"classificationStatus": "classified", "gameMatched": True,
             "gameStatus": "Scheduled", "modelSupportStatus": "MISSING_DATA"}
        assert classify_terminal_state(c) == MISSING_REQUIRED_CONTEXT

    def test_unsupported_is_unsupported_model_family(self):
        c = {"classificationStatus": "classified", "gameMatched": True,
             "gameStatus": "Scheduled", "modelSupportStatus": "UNSUPPORTED"}
        assert classify_terminal_state(c) == UNSUPPORTED_MODEL_FAMILY

    def test_unrecognized_shape_falls_to_bug_bucket_not_dropped(self):
        c = {"classificationStatus": "classified", "gameMatched": True,
             "gameStatus": "Scheduled", "modelSupportStatus": "SOMETHING_NEW"}
        assert classify_terminal_state(c) == NOT_EVALUATED_BUG

    def test_every_state_is_a_known_state(self):
        # Guards against a future classify_terminal_state edit returning a
        # string not registered in ALL_TERMINAL_STATES (would silently
        # break coverage_accounting's byState pre-seeding).
        cases = [
            {"classificationStatus": "parse_error"},
            {"classificationStatus": "different_slate_date"},
            {"classificationStatus": "classified", "gameMatched": False},
            {"classificationStatus": "classified", "gameMatched": True, "gameStatus": "Final"},
            {"classificationStatus": "classified", "gameMatched": True, "gameStatus": "Scheduled",
             "modelSupportStatus": "SUPPORTED"},
        ]
        for c in cases:
            assert classify_terminal_state(c) in ALL_TERMINAL_STATES


# ── build_coverage_ledger / coverage_accounting: end-to-end, no silent gap ──

class TestNoSilentRemainder:

    def test_mixed_universe_every_contract_accounted_for(self):
        game = make_game()
        markets = [
            # Full-game ML — modeled.
            mkt("KXMLBGAME-26AUG192040BOSNYY-BOS", "KXMLBGAME-26AUG192040BOSNYY", "x"),
            mkt("KXMLBGAME-26AUG192040BOSNYY-NYY", "KXMLBGAME-26AUG192040BOSNYY", "x"),
            # Full-game total, two alternate lines — modeled.
            mkt("KXMLBTOTAL-26AUG192040BOSNYY-8", "KXMLBTOTAL-26AUG192040BOSNYY", "Total over 8?"),
            mkt("KXMLBTOTAL-26AUG192040BOSNYY-9", "KXMLBTOTAL-26AUG192040BOSNYY", "Total over 9?"),
            # F5 winner (three-way) — modeled.
            mkt("KXMLBF5-26AUG192040BOSNYY-BOS", "KXMLBF5-26AUG192040BOSNYY", "x"),
            # Pitcher strikeouts — family has a model, but no game context
            # supplied for subject resolution here -> MISSING_DATA.
            mkt("KXMLBKS-26AUG192040BOSNYY-GRAY5", "KXMLBKS-26AUG192040BOSNYY", "Sonny Gray over 5.5 Ks?"),
            # Hitter hits — confirmed real series, genuinely no model.
            mkt("KXMLBHIT-26AUG192040BOSNYY-DEVERS1", "KXMLBHIT-26AUG192040BOSNYY", "Devers over 1.5 hits?"),
            # Unclassified/unknown series.
            mkt("KXMLBFOOBAR-26AUG192040BOSNYY-X", "KXMLBFOOBAR-26AUG192040BOSNYY", "unrecognized market"),
        ]
        search_doc = make_search_doc(markets)
        slate_doc = {"games": [game]}

        ledger_rows, discovery_summary = build_coverage_ledger("2026-08-19", search_doc, slate_doc)
        accounting = coverage_accounting(ledger_rows)

        assert accounting["archivedTotal"] == len(markets)
        assert accounting["accountedTotal"] == len(markets)
        assert accounting["unaccountedCount"] == 0
        assert accounting["byState"][NOT_EVALUATED_BUG] == 0

        by_ticker = {r["ticker"]: r for r in ledger_rows}
        assert by_ticker["KXMLBGAME-26AUG192040BOSNYY-BOS"]["finalCoverageState"] == FULLY_EVALUATED
        assert by_ticker["KXMLBTOTAL-26AUG192040BOSNYY-8"]["finalCoverageState"] == FULLY_EVALUATED
        assert by_ticker["KXMLBTOTAL-26AUG192040BOSNYY-9"]["finalCoverageState"] == FULLY_EVALUATED
        assert by_ticker["KXMLBF5-26AUG192040BOSNYY-BOS"]["finalCoverageState"] == FULLY_EVALUATED
        assert by_ticker["KXMLBHIT-26AUG192040BOSNYY-DEVERS1"]["finalCoverageState"] == UNSUPPORTED_MODEL_FAMILY

        # Alternate lines both preserved as distinct rows, not deduped.
        totals = [r for r in ledger_rows if r.get("marketFamily") == "game_total"]
        assert len(totals) == 2
        assert {r["line"] for r in totals} == {8, 9}

    def test_started_game_markets_excluded_but_still_accounted_for(self):
        started = make_game(game_id=2, away="LAD", home="COL", status="In Progress")
        markets = [
            mkt("KXMLBGAME-26AUG192040LADCOL-LAD", "KXMLBGAME-26AUG192040LADCOL", "x"),
        ]
        search_doc = make_search_doc(markets)
        slate_doc = {"games": [started]}
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, slate_doc)
        accounting = coverage_accounting(ledger_rows)
        assert accounting["unaccountedCount"] == 0
        assert accounting["byState"][STARTED_GAME_EXCLUDED] == 1
        assert accounting["byState"][FULLY_EVALUATED] == 0

    def test_no_slate_at_all_still_fully_accounted_for(self):
        markets = [
            mkt("KXMLBGAME-26AUG192040BOSNYY-BOS", "KXMLBGAME-26AUG192040BOSNYY", "x"),
            mkt("KXMLBHIT-26AUG192040BOSNYY-DEVERS1", "KXMLBHIT-26AUG192040BOSNYY", "Devers over 1.5 hits?"),
        ]
        search_doc = make_search_doc(markets)
        slate_doc = {"games": []}
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, slate_doc)
        accounting = coverage_accounting(ledger_rows)
        assert accounting["archivedTotal"] == 2
        assert accounting["unaccountedCount"] == 0
        # No slate match at all -> GAME_MAPPING_UNRESOLVED for the ML leg;
        # the hitter-hits contract is unsupported regardless of match.
        by_ticker = {r["ticker"]: r for r in ledger_rows}
        assert by_ticker["KXMLBGAME-26AUG192040BOSNYY-BOS"]["finalCoverageState"] == GAME_MAPPING_UNRESOLVED

    def test_different_date_contract_not_applicable_never_dropped(self):
        markets = [
            mkt("KXMLBGAME-26AUG202040BOSNYY-BOS", "KXMLBGAME-26AUG202040BOSNYY", "tomorrow's game"),
        ]
        search_doc = make_search_doc(markets, date_str="2026-08-19")
        ledger_rows, summary = build_coverage_ledger("2026-08-19", search_doc, {"games": []})
        assert len(ledger_rows) == 1
        assert ledger_rows[0]["finalCoverageState"] == NOT_APPLICABLE
        assert summary["discovered"] == 1
        assert summary["otherDateExcluded"] == 1
        accounting = coverage_accounting(ledger_rows)
        assert accounting["unaccountedCount"] == 0

    def test_empty_universe_is_trivially_accounted_for(self):
        ledger_rows, _ = build_coverage_ledger("2026-08-19", make_search_doc([]), {"games": []})
        accounting = coverage_accounting(ledger_rows)
        assert accounting["archivedTotal"] == 0
        assert accounting["unaccountedCount"] == 0
