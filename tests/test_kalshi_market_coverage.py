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
    index_hitter_board_by_ticker, index_hitter_snapshots_by_ticker,
    select_prospective_hitter_snapshot, _minutes_between,
    pregame_view, full_accounting,
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


def mkt(ticker, event_ticker, title, yes_bid=40.0, yes_ask=45.0, status="active", snapshot_ts=None):
    m = {"market_ticker": ticker, "event_ticker": event_ticker, "title": title,
         "subtitle": "", "status": status, "yes_bid": yes_bid, "yes_ask": yes_ask,
         "close_time": "2026-08-20T00:00:00Z", "volume": 50.0}
    if snapshot_ts:
        m["snapshot_ts"] = snapshot_ts
    return m


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


def board_row(ticker, projection_status="PROJECTED", model_prob=0.4, price=0.45,
              generated_at="2026-08-19T19:10:32Z", **overrides):
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
        "checkpoint": "T_MINUS_60",
        "projectionGeneratedAt": generated_at,
        "marketObservedAt": generated_at,
        "sourceCapturePath": "data/kalshi_registry_snapshots/kalshi_search_2026-08-19_standalone.json",
    }
    row.update(overrides)
    return row


def snapshot_row(ticker, checkpoint="T_MINUS_60", snapshot_generated_at="2026-08-19T14:00:00Z",
                  model_prob=0.4, price=0.45, **overrides):
    """One row shaped exactly like a real
    data/edgelab/hitter_projection_snapshots/<date>.jsonl line (the
    PRIMARY hitter research source -- see
    lib.kalshi_market_coverage's "HITTER RESEARCH PROVENANCE" docstring).
    `price`/`marketObservedAt` here are the price AT PROJECTION TIME --
    never used for current economics, only retained as
    projectionTimeExecutablePrice/projectionTimeMarketObservedAt."""
    row = {
        "marketTicker": ticker,
        "checkpoint": checkpoint,
        "projectionStatus": "PROJECTED",
        "projectionStatusReason": None,
        "modelProbability": model_prob,
        "monteCarloStderr": 0.006,
        "researchRunId": f"HITTER_PROSPECTIVE_SNAPSHOT_{snapshot_generated_at.replace(':', '').replace('-', '')}",
        "snapshotGeneratedAt": snapshot_generated_at,
        "projectionGeneratedAt": snapshot_generated_at,
        "executableKalshiPrice": price,
        "marketObservedAt": snapshot_generated_at,
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


# ── Prospective hitter snapshot selection (primary research source) ────────

class TestLoadHitterProspectiveSnapshots:

    def test_reads_jsonl_lines(self, tmp_path):
        from lib.kalshi_market_coverage import load_hitter_prospective_snapshots
        import json as _json
        p = tmp_path / "2026-08-19.jsonl"
        p.write_text(
            _json.dumps({"marketTicker": "T1", "modelProbability": 0.4}) + "\n"
            + _json.dumps({"marketTicker": "T2", "modelProbability": 0.5}) + "\n"
        )
        rows = load_hitter_prospective_snapshots("2026-08-19", path=str(p))
        assert len(rows) == 2
        assert {r["marketTicker"] for r in rows} == {"T1", "T2"}

    def test_missing_file_returns_empty_list_not_error(self, tmp_path):
        from lib.kalshi_market_coverage import load_hitter_prospective_snapshots
        rows = load_hitter_prospective_snapshots("2026-08-19", path=str(tmp_path / "missing.jsonl"))
        assert rows == []

    def test_malformed_line_skipped_not_fatal(self, tmp_path):
        from lib.kalshi_market_coverage import load_hitter_prospective_snapshots
        import json as _json
        p = tmp_path / "2026-08-19.jsonl"
        p.write_text(
            _json.dumps({"marketTicker": "T1"}) + "\n"
            + "not valid json\n"
            + "\n"
            + _json.dumps({"marketTicker": "T2"}) + "\n"
        )
        rows = load_hitter_prospective_snapshots("2026-08-19", path=str(p))
        assert {r["marketTicker"] for r in rows} == {"T1", "T2"}


class TestProspectiveSnapshotSelection:

    def test_latest_snapshot_at_or_before_market_observation_chosen(self):
        ticker = "T1"
        rows = [
            snapshot_row(ticker, checkpoint="T_MINUS_90", snapshot_generated_at="2026-08-19T14:00:00Z", model_prob=0.30),
            snapshot_row(ticker, checkpoint="T_MINUS_60", snapshot_generated_at="2026-08-19T15:00:00Z", model_prob=0.35),
            snapshot_row(ticker, checkpoint="T_MINUS_30", snapshot_generated_at="2026-08-19T16:00:00Z", model_prob=0.40),
        ]
        idx = index_hitter_snapshots_by_ticker(rows)
        selected, status = select_prospective_hitter_snapshot(ticker, idx, "2026-08-19T15:30:00Z")
        assert status == "SELECTED"
        assert selected["checkpoint"] == "T_MINUS_60"
        assert selected["modelProbability"] == 0.35

    def test_future_snapshot_excluded_no_leakage(self):
        ticker = "T1"
        rows = [snapshot_row(ticker, snapshot_generated_at="2026-08-19T20:00:00Z", model_prob=0.9)]
        idx = index_hitter_snapshots_by_ticker(rows)
        selected, status = select_prospective_hitter_snapshot(ticker, idx, "2026-08-19T15:00:00Z")
        assert selected is None
        assert status == "NO_SNAPSHOT_AT_OR_BEFORE_MARKET_OBSERVATION"

    def test_different_checkpoint_capture_order_does_not_affect_selection(self):
        """Checkpoints can be captured out of chronological order under a
        catch-up run -- selection must go by snapshotGeneratedAt, never by
        insertion order or checkpoint-name order."""
        ticker = "T1"
        rows = [
            snapshot_row(ticker, checkpoint="T_MINUS_30", snapshot_generated_at="2026-08-19T16:00:00Z", model_prob=0.5),
            snapshot_row(ticker, checkpoint="T_MINUS_90", snapshot_generated_at="2026-08-19T14:00:00Z", model_prob=0.3),
            snapshot_row(ticker, checkpoint="LINEUP_CONFIRMATION", snapshot_generated_at="2026-08-19T17:00:00Z", model_prob=0.6),
        ]
        idx = index_hitter_snapshots_by_ticker(rows)
        selected, status = select_prospective_hitter_snapshot(ticker, idx, "2026-08-19T16:30:00Z")
        assert selected["checkpoint"] == "T_MINUS_30"  # 16:00, latest <= 16:30
        selected2, _ = select_prospective_hitter_snapshot(ticker, idx, "2026-08-19T18:00:00Z")
        assert selected2["checkpoint"] == "LINEUP_CONFIRMATION"  # 17:00, latest <= 18:00

    def test_no_eligible_snapshot_when_ticker_never_checkpointed(self):
        idx = index_hitter_snapshots_by_ticker([snapshot_row("OTHER")])
        selected, status = select_prospective_hitter_snapshot("T1", idx, "2026-08-19T16:00:00Z")
        assert selected is None
        assert status == "NO_SNAPSHOTS_FOR_TICKER"

    def test_exact_timestamp_equality_is_eligible(self):
        ticker = "T1"
        rows = [snapshot_row(ticker, snapshot_generated_at="2026-08-19T16:00:00Z", model_prob=0.42)]
        idx = index_hitter_snapshots_by_ticker(rows)
        selected, status = select_prospective_hitter_snapshot(ticker, idx, "2026-08-19T16:00:00Z")
        assert status == "SELECTED"
        assert selected["modelProbability"] == 0.42

    def test_different_ticker_never_linked(self):
        idx = index_hitter_snapshots_by_ticker([snapshot_row("T1", model_prob=0.99)])
        selected, status = select_prospective_hitter_snapshot("T2", idx, "2026-08-19T20:00:00Z")
        assert selected is None
        assert status == "NO_SNAPSHOTS_FOR_TICKER"

    def test_unknown_market_observation_time_never_guesses(self):
        ticker = "T1"
        rows = [snapshot_row(ticker, snapshot_generated_at="2026-08-19T14:00:00Z")]
        idx = index_hitter_snapshots_by_ticker(rows)
        selected, status = select_prospective_hitter_snapshot(ticker, idx, None)
        assert selected is None
        assert status == "NO_SNAPSHOT_AT_OR_BEFORE_MARKET_OBSERVATION"


# ── Hitter research linkage (prospective snapshot primary, board fallback) ──

class TestHitterResearchLinkage:

    def test_exact_ticker_match_becomes_research_model_only(self):
        ticker = "KXMLBHIT-26AUG192040BOSNYY-DEVERS1"
        markets = [mkt(ticker, "KXMLBHIT-26AUG192040BOSNYY", "Devers over 1.5 hits?",
                       snapshot_ts="2026-08-19T16:00:00Z")]
        search_doc = make_search_doc(markets)
        snapshots = [snapshot_row(ticker, snapshot_generated_at="2026-08-19T15:30:00Z", model_prob=0.55, price=0.40)]

        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game()]}, snapshots)
        row = ledger_rows[0]
        assert row["finalCoverageState"] == RESEARCH_MODEL_ONLY
        assert row["productionModelSupportStatus"] == "UNSUPPORTED"
        assert row["researchModelSupportStatus"] == "PROJECTED"
        assert row["hitterProjectionSourceType"] == "PROSPECTIVE_SNAPSHOT"
        assert row["hitterModelProbability"] == 0.55

    def test_no_production_betting_side_effect(self):
        """Linking to hitter research must never make the contract
        production real-money eligible -- realMoneyEligibilityStatus is
        forced RESEARCH_ONLY for the whole family, and modelSupportStatus
        (the actual production adapter verdict) is untouched."""
        ticker = "KXMLBHIT-26AUG192040BOSNYY-DEVERS1"
        markets = [mkt(ticker, "KXMLBHIT-26AUG192040BOSNYY", "Devers over 1.5 hits?", snapshot_ts="2026-08-19T16:00:00Z")]
        search_doc = make_search_doc(markets)
        snapshots = [snapshot_row(ticker, snapshot_generated_at="2026-08-19T15:30:00Z")]
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game()]}, snapshots)
        row = ledger_rows[0]
        assert row["realMoneyEligibilityStatus"] == "RESEARCH_ONLY"
        assert row["modelSupportStatus"] == "UNSUPPORTED"  # discover()'s own field, unchanged

    def test_alternate_thresholds_preserved_independently(self):
        t1 = "KXMLBHIT-26AUG192040BOSNYY-DEVERS1"
        t2 = "KXMLBHIT-26AUG192040BOSNYY-DEVERS2"
        markets = [
            mkt(t1, "KXMLBHIT-26AUG192040BOSNYY", "Devers over 1.5 hits?", snapshot_ts="2026-08-19T16:00:00Z"),
            mkt(t2, "KXMLBHIT-26AUG192040BOSNYY", "Devers over 2.5 hits?", snapshot_ts="2026-08-19T16:00:00Z"),
        ]
        search_doc = make_search_doc(markets)
        snapshots = [
            snapshot_row(t1, snapshot_generated_at="2026-08-19T15:30:00Z", model_prob=0.55),
            snapshot_row(t2, snapshot_generated_at="2026-08-19T15:30:00Z", model_prob=0.2),
        ]
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game()]}, snapshots)
        by_ticker = {r["ticker"]: r for r in ledger_rows}
        assert by_ticker[t1]["hitterModelProbability"] == 0.55
        assert by_ticker[t2]["hitterModelProbability"] == 0.2
        assert by_ticker[t1]["finalCoverageState"] == RESEARCH_MODEL_ONLY
        assert by_ticker[t2]["finalCoverageState"] == RESEARCH_MODEL_ONLY

    def test_ambiguous_snapshot_linkage_never_guessed(self):
        ticker = "KXMLBHIT-26AUG192040BOSNYY-DEVERS1"
        markets = [mkt(ticker, "KXMLBHIT-26AUG192040BOSNYY", "Devers over 1.5 hits?", snapshot_ts="2026-08-19T16:00:00Z")]
        search_doc = make_search_doc(markets)
        snapshots = [snapshot_row(ticker, snapshot_generated_at="2026-08-19T15:30:00Z",
                                   projectionStatus="AMBIGUOUS_TICKER_MATCH", model_prob=None, price=None)]
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game()]}, snapshots)
        row = ledger_rows[0]
        assert row["finalCoverageState"] == AMBIGUOUS_TICKER_MATCH
        assert row["hitterModelProbability"] is None  # never fabricated

    def test_no_snapshots_and_no_board_falls_back_to_unsupported_model_family(self):
        ticker = "KXMLBHIT-26AUG192040BOSNYY-DEVERS1"
        markets = [mkt(ticker, "KXMLBHIT-26AUG192040BOSNYY", "Devers over 1.5 hits?", snapshot_ts="2026-08-19T16:00:00Z")]
        search_doc = make_search_doc(markets)
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game()]}, None, None)
        row = ledger_rows[0]
        assert row["finalCoverageState"] == UNSUPPORTED_MODEL_FAMILY
        assert row["researchModelSupportStatus"] == "NO_SNAPSHOTS_FOR_TICKER"
        assert row["realMoneyEligibilityStatus"] == "RESEARCH_ONLY"

    def test_no_snapshot_row_for_this_specific_ticker_falls_back_to_unsupported(self):
        ticker = "KXMLBHIT-26AUG192040BOSNYY-DEVERS1"
        other_ticker = "KXMLBHIT-26AUG192040BOSNYY-BOGAERTS1"
        markets = [mkt(ticker, "KXMLBHIT-26AUG192040BOSNYY", "Devers over 1.5 hits?", snapshot_ts="2026-08-19T16:00:00Z")]
        search_doc = make_search_doc(markets)
        snapshots = [snapshot_row(other_ticker)]
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game()]}, snapshots)
        row = ledger_rows[0]
        assert row["researchModelSupportStatus"] == "NO_SNAPSHOTS_FOR_TICKER"
        assert row["finalCoverageState"] == UNSUPPORTED_MODEL_FAMILY

    def test_lineup_unconfirmed_maps_to_missing_required_context(self):
        ticker = "KXMLBHIT-26AUG192040BOSNYY-DEVERS1"
        markets = [mkt(ticker, "KXMLBHIT-26AUG192040BOSNYY", "Devers over 1.5 hits?", snapshot_ts="2026-08-19T16:00:00Z")]
        search_doc = make_search_doc(markets)
        snapshots = [snapshot_row(ticker, snapshot_generated_at="2026-08-19T15:30:00Z",
                                   projectionStatus="LINEUP_UNCONFIRMED", model_prob=None, price=None)]
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game()]}, snapshots)
        assert ledger_rows[0]["finalCoverageState"] == MISSING_REQUIRED_CONTEXT

    def test_started_game_decided_by_this_runs_own_signal_not_a_stale_snapshot(self):
        """Hitter research provenance mission: whether the game has
        started is decided EXCLUSIVELY from this run's own slate/game
        context -- never from a research snapshot's own (possibly much
        older) observation. A snapshot has no game-started concept in the
        primary source at all, so this is naturally always true for
        PROSPECTIVE_SNAPSHOT-sourced evidence; this test locks in that a
        contract with no local game match (regardless of hitter research
        state) reports GAME_MAPPING_UNRESOLVED, not a research-derived
        started/excluded status."""
        ticker = "KXMLBHIT-26AUG192040BOSNYY-DEVERS1"
        markets = [mkt(ticker, "KXMLBHIT-26AUG192040BOSNYY", "Devers over 1.5 hits?", snapshot_ts="2026-08-19T16:00:00Z")]
        search_doc = make_search_doc(markets)
        snapshots = [snapshot_row(ticker, snapshot_generated_at="2026-08-19T15:30:00Z", model_prob=0.55)]
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": []}, snapshots)
        assert ledger_rows[0]["finalCoverageState"] == GAME_MAPPING_UNRESOLVED

    def test_stolen_bases_family_has_no_research_engine_stays_unsupported(self):
        ticker = "KXMLBSB-26AUG192040BOSNYY-DEVERS1"
        markets = [mkt(ticker, "KXMLBSB-26AUG192040BOSNYY", "Devers steals a base?", snapshot_ts="2026-08-19T16:00:00Z")]
        search_doc = make_search_doc(markets)
        snapshots = [snapshot_row(ticker)]  # even if a row happened to exist, family isn't linked
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game()]}, snapshots)
        row = ledger_rows[0]
        assert row["researchModelSupportStatus"] is None
        assert row["finalCoverageState"] == UNSUPPORTED_MODEL_FAMILY

    def test_index_hitter_board_by_ticker_handles_none_and_missing_rows(self):
        assert index_hitter_board_by_ticker(None) == {}
        assert index_hitter_board_by_ticker({}) == {}
        assert index_hitter_board_by_ticker({"rows": [{"marketTicker": "T1"}, {"noTicker": True}]}) == {
            "T1": {"marketTicker": "T1"}
        }

    def test_index_hitter_snapshots_by_ticker_handles_empty_and_missing_ticker(self):
        assert index_hitter_snapshots_by_ticker(None) == {}
        assert index_hitter_snapshots_by_ticker([]) == {}
        assert index_hitter_snapshots_by_ticker([{"noTicker": True}]) == {}


class TestLegacyBoardFallback:

    def test_board_used_only_when_no_qualifying_snapshot_exists(self):
        ticker = "KXMLBHIT-26AUG192040BOSNYY-DEVERS1"
        markets = [mkt(ticker, "KXMLBHIT-26AUG192040BOSNYY", "Devers over 1.5 hits?", snapshot_ts="2026-08-19T16:00:00Z")]
        search_doc = make_search_doc(markets)
        board = hitter_board([board_row(ticker, model_prob=0.6, price=0.5, generated_at="2026-08-19T15:00:00Z")])
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game()]}, None, board)
        row = ledger_rows[0]
        assert row["finalCoverageState"] == RESEARCH_MODEL_ONLY
        assert row["hitterProjectionSourceType"] == "LEGACY_BOARD_FALLBACK"
        assert row["hitterModelProbability"] == 0.6

    def test_snapshot_preferred_over_board_when_both_available(self):
        ticker = "KXMLBHIT-26AUG192040BOSNYY-DEVERS1"
        markets = [mkt(ticker, "KXMLBHIT-26AUG192040BOSNYY", "Devers over 1.5 hits?", snapshot_ts="2026-08-19T16:00:00Z")]
        search_doc = make_search_doc(markets)
        snapshots = [snapshot_row(ticker, snapshot_generated_at="2026-08-19T15:30:00Z", model_prob=0.55)]
        board = hitter_board([board_row(ticker, model_prob=0.99, generated_at="2026-08-19T15:00:00Z")])
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game()]}, snapshots, board)
        row = ledger_rows[0]
        assert row["hitterProjectionSourceType"] == "PROSPECTIVE_SNAPSHOT"
        assert row["hitterModelProbability"] == 0.55

    def test_future_dated_board_row_rejected_same_as_a_future_snapshot(self):
        ticker = "KXMLBHIT-26AUG192040BOSNYY-DEVERS1"
        markets = [mkt(ticker, "KXMLBHIT-26AUG192040BOSNYY", "Devers over 1.5 hits?", snapshot_ts="2026-08-19T14:00:00Z")]
        search_doc = make_search_doc(markets)
        board = hitter_board([board_row(ticker, model_prob=0.6, generated_at="2026-08-19T19:00:00Z")])  # after market obs
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game()]}, None, board)
        row = ledger_rows[0]
        assert row["finalCoverageState"] == UNSUPPORTED_MODEL_FAMILY
        assert row["hitterModelProbability"] is None

    def test_board_fallback_requires_known_market_observation_time(self):
        ticker = "KXMLBHIT-26AUG192040BOSNYY-DEVERS1"
        markets = [mkt(ticker, "KXMLBHIT-26AUG192040BOSNYY", "Devers over 1.5 hits?")]  # no snapshot_ts
        search_doc = make_search_doc(markets)
        board = hitter_board([board_row(ticker, model_prob=0.6, generated_at="2026-08-19T15:00:00Z")])
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game()]}, None, board)
        row = ledger_rows[0]
        assert row["hitterModelProbability"] is None
        assert row["finalCoverageState"] == UNSUPPORTED_MODEL_FAMILY


# ── Current-price economics: never mixing projection-time and current data ──

class TestCurrentPriceEconomics:

    def _ledger_row(self, snapshot_price=0.20, current_yes_ask=0.45, current_no_ask=0.58, model_prob=0.55):
        ticker = "KXMLBHIT-26AUG192040BOSNYY-DEVERS1"
        markets = [mkt(ticker, "KXMLBHIT-26AUG192040BOSNYY", "Devers over 1.5 hits?",
                       yes_ask=current_yes_ask, snapshot_ts="2026-08-19T16:00:00Z")]
        search_doc = make_search_doc(markets)
        snapshots = [snapshot_row(ticker, snapshot_generated_at="2026-08-19T14:00:00Z",
                                   model_prob=model_prob, price=snapshot_price)]
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game()]}, snapshots)
        return ledger_rows[0]

    def test_model_probability_comes_from_research_snapshot(self):
        row = self._ledger_row(model_prob=0.55)
        assert row["hitterModelProbability"] == 0.55

    def test_current_ev_uses_current_price_not_projection_time_price(self):
        # Snapshot-time price (0.20) and current price (0.45) are
        # materially different -- currentFeeAwareNetExpectedValuePerDollar
        # must be computed off 0.45, not 0.20.
        row = self._ledger_row(snapshot_price=0.20, current_yes_ask=0.45, model_prob=0.55)
        from lib.edgelab.kalshi_fees import net_expected_value_per_dollar
        expected_current = net_expected_value_per_dollar(0.55, 0.45)
        expected_if_stale_price_were_used = net_expected_value_per_dollar(0.55, 0.20)
        assert row["currentFeeAwareNetExpectedValuePerDollar"] == expected_current
        assert row["currentFeeAwareNetExpectedValuePerDollar"] != expected_if_stale_price_were_used

    def test_historical_projection_price_retained_separately_never_used_for_current_fields(self):
        row = self._ledger_row(snapshot_price=0.20, current_yes_ask=0.45)
        assert row["projectionTimeExecutablePrice"] == 0.20
        assert row["currentExecutableKalshiPrice"] == 0.45
        assert row["currentYesPrice"] == 0.45
        assert row["projectionTimeExecutablePrice"] != row["currentExecutableKalshiPrice"]

    def test_current_raw_edge_uses_current_price(self):
        row = self._ledger_row(snapshot_price=0.20, current_yes_ask=0.45, model_prob=0.55)
        assert row["currentRawProbabilityEdge"] == round(0.55 - 0.45, 4)

    def test_fee_aware_break_even_uses_canonical_utility_and_current_price(self):
        row = self._ledger_row(current_yes_ask=0.45)
        from lib.edgelab.kalshi_fees import fee_adjusted_break_even_probability
        assert row["currentFeeAdjustedBreakEvenProbability"] == fee_adjusted_break_even_probability(0.45)

    def test_current_bet_up_to_uses_canonical_helper_and_model_probability(self):
        row = self._ledger_row(model_prob=0.55)
        from lib.edgelab.kalshi_fees import fee_adjusted_bet_up_to_price
        assert row["currentFeeAwareBetUpToPrice"] == fee_adjusted_bet_up_to_price(0.55)

    def test_no_current_price_available_never_fabricates_economics(self):
        ticker = "KXMLBHIT-26AUG192040BOSNYY-DEVERS1"
        markets = [{"market_ticker": ticker, "event_ticker": "KXMLBHIT-26AUG192040BOSNYY",
                    "title": "Devers over 1.5 hits?", "status": "active", "close_time": "2026-08-20T00:00:00Z",
                    "volume": 10.0, "snapshot_ts": "2026-08-19T16:00:00Z"}]  # no yes_bid/yes_ask at all
        search_doc = make_search_doc(markets)
        snapshots = [snapshot_row(ticker, snapshot_generated_at="2026-08-19T14:00:00Z", model_prob=0.55)]
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game()]}, snapshots)
        row = ledger_rows[0]
        assert row["hitterModelProbability"] == 0.55  # research evidence still present
        assert row["currentExecutableKalshiPrice"] is None
        assert row["currentFeeAwareNetExpectedValuePerDollar"] is None
        assert row["currentFeeAwareBetUpToPrice"] is not None  # only needs model_prob


# ── Provenance fields: projection age, checkpoint, source type ─────────────

class TestHitterProvenanceFields:

    def test_provenance_fields_present_and_correct(self):
        ticker = "KXMLBHIT-26AUG192040BOSNYY-DEVERS1"
        markets = [mkt(ticker, "KXMLBHIT-26AUG192040BOSNYY", "Devers over 1.5 hits?", snapshot_ts="2026-08-19T16:43:00Z")]
        search_doc = make_search_doc(markets)
        snapshots = [snapshot_row(ticker, checkpoint="T_MINUS_60", snapshot_generated_at="2026-08-19T16:00:00Z", model_prob=0.5)]
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game()]}, snapshots)
        row = ledger_rows[0]
        assert row["hitterProjectionCheckpoint"] == "T_MINUS_60"
        assert row["hitterProjectionSnapshotGeneratedAt"] == "2026-08-19T16:00:00Z"
        assert row["currentMarketObservedAt"] == "2026-08-19T16:43:00Z"
        assert row["hitterProjectionAgeMinutes"] == 43.0

    def test_projection_age_never_negative_for_valid_selection(self):
        """Selection guarantees snapshotGeneratedAt <= market_observed_at,
        so age (market - snapshot) must always be >= 0 for a SELECTED row."""
        ticker = "KXMLBHIT-26AUG192040BOSNYY-DEVERS1"
        markets = [mkt(ticker, "KXMLBHIT-26AUG192040BOSNYY", "Devers over 1.5 hits?", snapshot_ts="2026-08-19T16:00:00Z")]
        search_doc = make_search_doc(markets)
        snapshots = [snapshot_row(ticker, snapshot_generated_at="2026-08-19T16:00:00Z")]  # exact equality
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game()]}, snapshots)
        assert ledger_rows[0]["hitterProjectionAgeMinutes"] == 0.0

    def test_missing_timestamps_never_fabricate_an_age(self):
        assert _minutes_between(None, "2026-08-19T16:00:00Z") is None
        assert _minutes_between("2026-08-19T16:00:00Z", None) is None
        assert _minutes_between(None, None) is None

    def test_source_type_prospective_vs_fallback(self):
        ticker = "KXMLBHIT-26AUG192040BOSNYY-DEVERS1"
        markets = [mkt(ticker, "KXMLBHIT-26AUG192040BOSNYY", "Devers over 1.5 hits?", snapshot_ts="2026-08-19T16:00:00Z")]
        search_doc = make_search_doc(markets)
        snapshots = [snapshot_row(ticker, snapshot_generated_at="2026-08-19T15:00:00Z")]
        ledger_rows, _ = build_coverage_ledger("2026-08-19", search_doc, {"games": [make_game()]}, snapshots)
        assert ledger_rows[0]["hitterProjectionSourceType"] == "PROSPECTIVE_SNAPSHOT"


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
        hit_ticker = "KXMLBHIT-26AUG192040BOSNYY-DEVERS1"
        markets = [
            mkt("KXMLBGAME-26AUG192040BOSNYY-BOS", "KXMLBGAME-26AUG192040BOSNYY", "x"),
            mkt(hit_ticker, "KXMLBHIT-26AUG192040BOSNYY", "hits?", snapshot_ts="2026-08-19T16:00:00Z"),
        ]
        search_doc = make_search_doc(markets)
        snapshots = [snapshot_row(hit_ticker, snapshot_generated_at="2026-08-19T15:30:00Z")]
        result = full_accounting("2026-08-19", search_doc, {"games": [make_game()]}, snapshots)
        assert len(result["ledgerRows"]) == 2
        assert result["coverageAccounting"]["unaccountedCount"] == 0
        assert result["rawArchiveAccounting"]["trueSilentRemainderCount"] == 0
        assert result["pregameView"]["validPregameMarkets"] == 2
        by_ticker = {r["ticker"]: r for r in result["ledgerRows"]}
        assert by_ticker[hit_ticker]["finalCoverageState"] == RESEARCH_MODEL_ONLY

    def test_combines_every_layer_with_board_fallback(self):
        hit_ticker = "KXMLBHIT-26AUG192040BOSNYY-DEVERS1"
        markets = [mkt(hit_ticker, "KXMLBHIT-26AUG192040BOSNYY", "hits?", snapshot_ts="2026-08-19T16:00:00Z")]
        search_doc = make_search_doc(markets)
        board = hitter_board([board_row(hit_ticker, generated_at="2026-08-19T15:00:00Z")])
        result = full_accounting("2026-08-19", search_doc, {"games": [make_game()]}, None, board)
        by_ticker = {r["ticker"]: r for r in result["ledgerRows"]}
        assert by_ticker[hit_ticker]["finalCoverageState"] == RESEARCH_MODEL_ONLY
        assert by_ticker[hit_ticker]["hitterProjectionSourceType"] == "LEGACY_BOARD_FALLBACK"
