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
    extract_raw_ticker_index, raw_archive_accounting, link_hitter_research,
    index_hitter_board_by_ticker, pregame_view, full_accounting,
    ALL_TERMINAL_STATES, FULLY_EVALUATED, RESEARCH_MODEL_ONLY, MISSING_REQUIRED_CONTEXT,
    UNSUPPORTED_MODEL_FAMILY, PARSER_UNRESOLVED, GAME_MAPPING_UNRESOLVED, AMBIGUOUS_TICKER_MATCH,
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


# ── raw_archive_accounting: independent of discover()'s own output ─────────

def hitter_board(rows):
    return {"rows": rows}


def board_row(ticker, projection_status="PROJECTED", model_prob=0.4, price=0.45, **overrides):
    row = {
        "marketTicker": ticker,
        "projectionStatus": projection_status,
        "projectionStatusReason": None,
        "modelProbability": model_prob,
        "fairAmericanOdds": 150,
        "executableKalshiPrice": price,
        "rawProbabilityEdge": (model_prob - price) if (model_prob is not None and price is not None) else None,
        "expectedValuePerDollar": 0.02,
        "monteCarloStderr": 0.01,
        "researchRunId": "RUN123",
        "projectionGeneratedAt": "2026-08-19T19:10:32Z",
        "sourceCapturePath": "data/kalshi_registry_snapshots/kalshi_search_2026-08-19_standalone.json",
    }
    row.update(overrides)
    return row


class TestRawArchiveInvariant:

    def test_unique_ticker_extraction_matches_market_count(self):
        markets = [
            mkt("KXMLBGAME-26AUG192040BOSNYY-BOS", "KXMLBGAME-26AUG192040BOSNYY", "x"),
            mkt("KXMLBGAME-26AUG192040BOSNYY-NYY", "KXMLBGAME-26AUG192040BOSNYY", "x"),
        ]
        index, dup, no_ticker, total = extract_raw_ticker_index(make_search_doc(markets))
        assert len(index) == 2
        assert dup == 0
        assert no_ticker == 0
        assert total == 2

    def test_duplicate_raw_ticker_counted_not_folded_into_denominator(self):
        m = mkt("KXMLBGAME-26AUG192040BOSNYY-BOS", "KXMLBGAME-26AUG192040BOSNYY", "x")
        search_doc = make_search_doc([m, dict(m)])  # same ticker, appears twice
        index, dup, no_ticker, total = extract_raw_ticker_index(search_doc)
        assert len(index) == 1
        assert dup == 1
        assert total == 2

    def test_duplicate_across_markets_and_unknown_series_list_also_counted(self):
        m = mkt("KXMLBGAME-26AUG192040BOSNYY-BOS", "KXMLBGAME-26AUG192040BOSNYY", "x")
        search_doc = make_search_doc([m], unknown=[dict(m)])
        index, dup, no_ticker, total = extract_raw_ticker_index(search_doc)
        assert len(index) == 1
        assert dup == 1

    def test_entry_without_ticker_counted_separately(self):
        no_ticker_entry = {"title": "no ticker field at all", "status": "active"}
        search_doc = make_search_doc([no_ticker_entry])
        index, dup, no_ticker, total = extract_raw_ticker_index(search_doc)
        assert len(index) == 0
        assert no_ticker == 1
        assert total == 1

    def test_denominator_never_derived_from_ledger_rows(self):
        """rawArchivedUnique must come from search_doc alone -- passing an
        EMPTY ledger must not silently make the invariant trivially pass;
        it must instead report every raw ticker as unaccounted."""
        markets = [mkt("KXMLBGAME-26AUG192040BOSNYY-BOS", "KXMLBGAME-26AUG192040BOSNYY", "x")]
        search_doc = make_search_doc(markets)
        accounting = raw_archive_accounting(search_doc, ledger_rows=[])
        assert accounting["rawArchivedUnique"] == 1
        assert accounting["accountedTickerCount"] == 0
        assert accounting["trueSilentRemainderCount"] == 1
        assert accounting["missingTickers"] == ["KXMLBGAME-26AUG192040BOSNYY-BOS"]

    def test_regression_discover_dropping_one_ticker_fails_the_audit(self):
        """Item 1's required regression test: simulate a hypothetical bug
        where discover() silently drops one raw contract before returning
        it, and prove raw_archive_accounting (the INDEPENDENT invariant,
        never derived from discover()'s own output) catches it -- unlike
        coverage_accounting(), whose denominator IS len(ledger_rows) and
        would show a false 0-unaccounted in this exact scenario."""
        markets = [
            mkt("KXMLBGAME-26AUG192040BOSNYY-BOS", "KXMLBGAME-26AUG192040BOSNYY", "x"),
            mkt("KXMLBGAME-26AUG192040BOSNYY-NYY", "KXMLBGAME-26AUG192040BOSNYY", "x"),
        ]
        search_doc = make_search_doc(markets)
        slate_doc = {"games": [make_game()]}

        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, slate_doc)
        assert len(ledger_rows) == 2  # sanity: discover() behaves correctly here

        # Simulate the bug: one contract silently dropped before the
        # ledger is handed to accounting (exactly what a real extraction/
        # dedup/parser-routing/date-filter regression inside discover()
        # would look like from this module's point of view).
        buggy_ledger_rows = [r for r in ledger_rows if r["ticker"] != "KXMLBGAME-26AUG192040BOSNYY-NYY"]

        # The weaker, discover()-output-based check is fooled -- its own
        # denominator shrank along with the bug, so it reports a clean 0.
        weaker_accounting = coverage_accounting(buggy_ledger_rows)
        assert weaker_accounting["unaccountedCount"] == 0

        # The independent raw-archive invariant is NOT fooled: its
        # denominator came from search_doc directly, so it still expects
        # both tickers and flags the dropped one.
        strong_accounting = raw_archive_accounting(search_doc, buggy_ledger_rows)
        assert strong_accounting["rawArchivedUnique"] == 2
        assert strong_accounting["trueSilentRemainderCount"] == 1
        assert strong_accounting["missingTickers"] == ["KXMLBGAME-26AUG192040BOSNYY-NYY"]

    def test_date_mismatch_still_explicitly_accounted_in_raw_invariant(self):
        markets = [mkt("KXMLBGAME-26AUG202040BOSNYY-BOS", "KXMLBGAME-26AUG202040BOSNYY", "tomorrow")]
        search_doc = make_search_doc(markets, date_str="2026-08-19")
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": []})
        accounting = raw_archive_accounting(search_doc, ledger_rows)
        assert accounting["trueSilentRemainderCount"] == 0

    def test_unknown_series_preserved_in_raw_invariant(self):
        markets = [mkt("KXMLBFOOBAR-26AUG192040BOSNYY-X", "KXMLBFOOBAR-26AUG192040BOSNYY", "unrecognized")]
        search_doc = make_search_doc(markets)
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": []})
        accounting = raw_archive_accounting(search_doc, ledger_rows)
        assert accounting["trueSilentRemainderCount"] == 0
        assert ledger_rows[0]["finalCoverageState"] == PARSER_UNRESOLVED


# ── Hitter research linkage ─────────────────────────────────────────────────

class TestHitterResearchLinkage:

    def test_exact_ticker_and_threshold_match_becomes_research_model_only(self):
        ticker = "KXMLBHIT-26AUG192040BOSNYY-DEVERS1"
        markets = [mkt(ticker, "KXMLBHIT-26AUG192040BOSNYY", "Devers over 1.5 hits?")]
        search_doc = make_search_doc(markets)
        board = hitter_board([board_row(ticker, model_prob=0.55, price=0.5)])

        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game()]}, board)
        row = ledger_rows[0]
        assert row["finalCoverageState"] == RESEARCH_MODEL_ONLY
        assert row["productionModelSupportStatus"] == "UNSUPPORTED"
        assert row["researchModelSupportStatus"] == "PROJECTED"
        assert row["hitterModelProbability"] == 0.55
        assert row["hitterExecutableKalshiPrice"] == 0.5
        assert row["hitterFeeAwareNetExpectedValuePerDollar"] is not None

    def test_no_production_betting_side_effect(self):
        """Linking to the hitter board must never make the contract
        production real-money eligible -- realMoneyEligibilityStatus is
        forced RESEARCH_ONLY for the whole family, and modelSupportStatus
        (the actual production adapter verdict) is untouched."""
        ticker = "KXMLBHIT-26AUG192040BOSNYY-DEVERS1"
        markets = [mkt(ticker, "KXMLBHIT-26AUG192040BOSNYY", "Devers over 1.5 hits?")]
        search_doc = make_search_doc(markets)
        board = hitter_board([board_row(ticker)])
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game()]}, board)
        row = ledger_rows[0]
        assert row["realMoneyEligibilityStatus"] == "RESEARCH_ONLY"
        assert row["modelSupportStatus"] == "UNSUPPORTED"  # discover()'s own field, unchanged

    def test_alternate_thresholds_preserved_independently(self):
        t1 = "KXMLBHIT-26AUG192040BOSNYY-DEVERS1"
        t2 = "KXMLBHIT-26AUG192040BOSNYY-DEVERS2"
        markets = [
            mkt(t1, "KXMLBHIT-26AUG192040BOSNYY", "Devers over 1.5 hits?"),
            mkt(t2, "KXMLBHIT-26AUG192040BOSNYY", "Devers over 2.5 hits?"),
        ]
        search_doc = make_search_doc(markets)
        board = hitter_board([board_row(t1, model_prob=0.55), board_row(t2, model_prob=0.2)])
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game()]}, board)
        by_ticker = {r["ticker"]: r for r in ledger_rows}
        assert by_ticker[t1]["hitterModelProbability"] == 0.55
        assert by_ticker[t2]["hitterModelProbability"] == 0.2
        assert by_ticker[t1]["finalCoverageState"] == RESEARCH_MODEL_ONLY
        assert by_ticker[t2]["finalCoverageState"] == RESEARCH_MODEL_ONLY

    def test_ambiguous_board_linkage_never_guessed(self):
        ticker = "KXMLBHIT-26AUG192040BOSNYY-DEVERS1"
        markets = [mkt(ticker, "KXMLBHIT-26AUG192040BOSNYY", "Devers over 1.5 hits?")]
        search_doc = make_search_doc(markets)
        board = hitter_board([board_row(ticker, projection_status="AMBIGUOUS_TICKER_MATCH", model_prob=None,
                                         price=None, rawProbabilityEdge=None, expectedValuePerDollar=None)])
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game()]}, board)
        row = ledger_rows[0]
        assert row["finalCoverageState"] == AMBIGUOUS_TICKER_MATCH
        assert row["hitterModelProbability"] is None  # never fabricated

    def test_no_board_data_falls_back_to_unsupported_model_family(self):
        ticker = "KXMLBHIT-26AUG192040BOSNYY-DEVERS1"
        markets = [mkt(ticker, "KXMLBHIT-26AUG192040BOSNYY", "Devers over 1.5 hits?")]
        search_doc = make_search_doc(markets)
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game()]}, None)
        row = ledger_rows[0]
        assert row["finalCoverageState"] == UNSUPPORTED_MODEL_FAMILY
        assert row["researchModelSupportStatus"] == "NO_RESEARCH_BOARD_AVAILABLE"
        assert row["realMoneyEligibilityStatus"] == "RESEARCH_ONLY"

    def test_board_present_but_no_row_for_this_ticker(self):
        ticker = "KXMLBHIT-26AUG192040BOSNYY-DEVERS1"
        other_ticker = "KXMLBHIT-26AUG192040BOSNYY-BOGAERTS1"
        markets = [mkt(ticker, "KXMLBHIT-26AUG192040BOSNYY", "Devers over 1.5 hits?")]
        search_doc = make_search_doc(markets)
        board = hitter_board([board_row(other_ticker)])
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game()]}, board)
        row = ledger_rows[0]
        assert row["researchModelSupportStatus"] == "NOT_LINKED_NO_BOARD_DATA"
        assert row["finalCoverageState"] == UNSUPPORTED_MODEL_FAMILY

    def test_lineup_unconfirmed_maps_to_missing_required_context(self):
        ticker = "KXMLBHIT-26AUG192040BOSNYY-DEVERS1"
        markets = [mkt(ticker, "KXMLBHIT-26AUG192040BOSNYY", "Devers over 1.5 hits?")]
        search_doc = make_search_doc(markets)
        board = hitter_board([board_row(ticker, projection_status="LINEUP_UNCONFIRMED",
                                         model_prob=None, price=None,
                                         rawProbabilityEdge=None, expectedValuePerDollar=None)])
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game()]}, board)
        assert ledger_rows[0]["finalCoverageState"] == MISSING_REQUIRED_CONTEXT

    def test_hitter_board_game_started_maps_to_started_game_excluded_even_without_local_slate_match(self):
        ticker = "KXMLBHIT-26AUG192040BOSNYY-DEVERS1"
        markets = [mkt(ticker, "KXMLBHIT-26AUG192040BOSNYY", "Devers over 1.5 hits?")]
        search_doc = make_search_doc(markets)
        board = hitter_board([board_row(ticker, projection_status="GAME_STARTED",
                                         model_prob=None, price=None,
                                         rawProbabilityEdge=None, expectedValuePerDollar=None)])
        # No slate/game context supplied at all -- this run's own game
        # matching can't resolve anything, but the hitter board's own
        # independent game-started detection must still take effect.
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": []}, board)
        assert ledger_rows[0]["finalCoverageState"] == STARTED_GAME_EXCLUDED

    def test_stolen_bases_family_has_no_research_engine_stays_unsupported(self):
        ticker = "KXMLBSB-26AUG192040BOSNYY-DEVERS1"
        markets = [mkt(ticker, "KXMLBSB-26AUG192040BOSNYY", "Devers steals a base?")]
        search_doc = make_search_doc(markets)
        board = hitter_board([board_row(ticker)])  # even if a row happened to exist, family isn't linked
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game()]}, board)
        row = ledger_rows[0]
        assert row["researchModelSupportStatus"] is None
        assert row["finalCoverageState"] == UNSUPPORTED_MODEL_FAMILY

    def test_index_hitter_board_by_ticker_handles_none_and_missing_rows(self):
        assert index_hitter_board_by_ticker(None) == {}
        assert index_hitter_board_by_ticker({}) == {}
        assert index_hitter_board_by_ticker({"rows": [{"marketTicker": "T1"}, {"noTicker": True}]}) == {
            "T1": {"marketTicker": "T1"}
        }


# ── pregame_view: started/out-of-scope excluded, remaining states sum exactly

class TestPregameView:

    def test_started_and_different_date_excluded_from_denominator(self):
        started = make_game(game_id=2, away="LAD", home="COL", status="In Progress")
        markets = [
            mkt("KXMLBGAME-26AUG192040LADCOL-LAD", "KXMLBGAME-26AUG192040LADCOL", "x"),
            mkt("KXMLBGAME-26AUG202040BOSNYY-BOS", "KXMLBGAME-26AUG202040BOSNYY", "tomorrow"),
        ]
        search_doc = make_search_doc(markets, date_str="2026-08-19")
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [started]})
        view = pregame_view(ledger_rows)
        assert view["totalValidArchivedMlbMarkets"] == 1  # excludes the different-date market
        assert view["startedGameExcluded"] == 1
        assert view["validPregameMarkets"] == 0

    def test_pregame_states_sum_exactly_to_valid_pregame_markets(self):
        markets = [
            mkt("KXMLBGAME-26AUG192040BOSNYY-BOS", "KXMLBGAME-26AUG192040BOSNYY", "x"),
            mkt("KXMLBHIT-26AUG192040BOSNYY-DEVERS1", "KXMLBHIT-26AUG192040BOSNYY", "hits?"),
            mkt("KXMLBFOOBAR-26AUG192040BOSNYY-X", "KXMLBFOOBAR-26AUG192040BOSNYY", "unrecognized"),
            mkt("KXMLBKS-26AUG192040BOSNYY-GRAY5", "KXMLBKS-26AUG192040BOSNYY", "Ks?"),
        ]
        search_doc = make_search_doc(markets)
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game()]})
        view = pregame_view(ledger_rows)
        summed = (
            view["pregameFullyEvaluatedProduction"] + view["pregameResearchSupportedHitterMarkets"]
            + view["pregameMissingRequiredContext"] + view["pregameUnsupportedByAllModels"]
            + view["pregameParserUnresolved"] + view["pregameMappingUnresolved"]
            + view["pregameAmbiguousTickerMatch"] + view["pregameNotEvaluatedBug"]
        )
        assert summed == view["validPregameMarkets"]

    def test_true_silent_remainder_surfaced_from_raw_accounting(self):
        markets = [mkt("KXMLBGAME-26AUG192040BOSNYY-BOS", "KXMLBGAME-26AUG192040BOSNYY", "x")]
        search_doc = make_search_doc(markets)
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game()]})
        raw_accounting = raw_archive_accounting(search_doc, ledger_rows)
        view = pregame_view(ledger_rows, raw_accounting)
        assert view["trueSilentRemainder"] == 0
        assert raw_accounting["trueSilentRemainderCount"] == 0

    def test_pregame_view_defaults_true_silent_remainder_to_zero_without_raw_accounting(self):
        ledger_rows, _ = build_coverage_ledger("2026-08-19", make_search_doc([]), {"games": []})
        view = pregame_view(ledger_rows)
        assert view["trueSilentRemainder"] == 0


class TestPitcherPropCoverage:
    """Item 5's wiring-correctness requirement: given a matched game with
    real probable-starter identity (the same fixture convention
    tests/test_discover_kalshi_mlb_markets.py's own pitcher-prop tests
    use -- game[side]['pitcher']={'name', 'id'}), pitcher K/outs
    contracts must resolve to FULLY_EVALUATED with distinct thresholds
    preserved independently, and an unresolved/wrong-pitcher contract
    must land in an explicit non-silent state, never dropped."""

    def _game_with_starters(self):
        g = make_game(home="ATH")
        g["home"]["pitcherSavant"] = {"xFIP": 4.0, "avgIPperStart": 5.4, "kPct": 24.5, "bbPct": 7.8,
                                       "openerRole": False, "ttoSplit": 0.6, "ttoRisk": True}
        g["away"]["pitcher"] = {"name": "Someone Else", "id": "111111", "note": ""}
        g["home"]["pitcher"] = {"name": "Sonny Gray", "id": "543243", "note": ""}
        return g

    def test_strikeouts_and_outs_fully_evaluated_for_the_probable_starter(self):
        markets = [
            mkt("KXMLBKS-26AUG192040BOSATH-ATHGRAY54-6", "KXMLBKS-26AUG192040BOSATH", "Sonny Gray: 6+ strikeouts?"),
            mkt("KXMLBOUTS-26AUG192040BOSATH-ATHGRAY54-17", "KXMLBOUTS-26AUG192040BOSATH", "Sonny Gray: 17+ Outs Recorded?"),
        ]
        search_doc = make_search_doc(markets)
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [self._game_with_starters()]})
        by_family = {r["marketFamily"]: r for r in ledger_rows}
        assert by_family["pitcher_strikeouts"]["finalCoverageState"] == FULLY_EVALUATED
        assert by_family["pitcher_outs"]["finalCoverageState"] == FULLY_EVALUATED
        assert by_family["pitcher_strikeouts"]["fairProbabilityPct"] is not None
        assert by_family["pitcher_outs"]["fairProbabilityPct"] is not None

    def test_multiple_strikeout_thresholds_preserved_independently(self):
        markets = [
            mkt("KXMLBKS-26AUG192040BOSATH-ATHGRAY54-4", "KXMLBKS-26AUG192040BOSATH", "Sonny Gray: 4+ strikeouts?"),
            mkt("KXMLBKS-26AUG192040BOSATH-ATHGRAY54-6", "KXMLBKS-26AUG192040BOSATH", "Sonny Gray: 6+ strikeouts?"),
            mkt("KXMLBKS-26AUG192040BOSATH-ATHGRAY54-8", "KXMLBKS-26AUG192040BOSATH", "Sonny Gray: 8+ strikeouts?"),
        ]
        search_doc = make_search_doc(markets)
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [self._game_with_starters()]})
        assert len(ledger_rows) == 3
        assert {r["line"] for r in ledger_rows} == {4, 6, 8}
        assert all(r["finalCoverageState"] == FULLY_EVALUATED for r in ledger_rows)
        probs = {r["line"]: r["fairProbabilityPct"] for r in ledger_rows}
        # Higher threshold must never have a higher probability.
        assert probs[4] >= probs[6] >= probs[8]

    def test_pitcher_not_probable_starter_stays_missing_context_never_dropped(self):
        markets = [mkt("KXMLBKS-26AUG192040BOSATH-ATHRELIEVER99-6", "KXMLBKS-26AUG192040BOSATH",
                       "Some Reliever: 6+ strikeouts?")]
        search_doc = make_search_doc(markets)
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [self._game_with_starters()]})
        assert len(ledger_rows) == 1
        assert ledger_rows[0]["finalCoverageState"] == MISSING_REQUIRED_CONTEXT

    def test_no_slate_identity_leaves_pitcher_prop_explicit_not_silently_dropped(self):
        markets = [mkt("KXMLBKS-26AUG192040BOSATH-ATHGRAY54-6", "KXMLBKS-26AUG192040BOSATH", "Sonny Gray: 6+ strikeouts?")]
        search_doc = make_search_doc(markets)
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game(home="ATH")]})  # no pitcher field
        assert len(ledger_rows) == 1
        assert ledger_rows[0]["finalCoverageState"] == MISSING_REQUIRED_CONTEXT
        accounting = raw_archive_accounting(search_doc, ledger_rows)
        assert accounting["trueSilentRemainderCount"] == 0


class TestFullAccounting:

    def test_combines_every_layer(self):
        markets = [
            mkt("KXMLBGAME-26AUG192040BOSNYY-BOS", "KXMLBGAME-26AUG192040BOSNYY", "x"),
            mkt("KXMLBHIT-26AUG192040BOSNYY-DEVERS1", "KXMLBHIT-26AUG192040BOSNYY", "hits?"),
        ]
        search_doc = make_search_doc(markets)
        board = hitter_board([board_row("KXMLBHIT-26AUG192040BOSNYY-DEVERS1")])
        result = full_accounting("2026-08-19", search_doc, {"games": [make_game()]}, board)
        assert len(result["ledgerRows"]) == 2
        assert result["coverageAccounting"]["unaccountedCount"] == 0
        assert result["rawArchiveAccounting"]["trueSilentRemainderCount"] == 0
        assert result["pregameView"]["validPregameMarkets"] == 2
        by_ticker = {r["ticker"]: r for r in result["ledgerRows"]}
        assert by_ticker["KXMLBHIT-26AUG192040BOSNYY-DEVERS1"]["finalCoverageState"] == RESEARCH_MODEL_ONLY
