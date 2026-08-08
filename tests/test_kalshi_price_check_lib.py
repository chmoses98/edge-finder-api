#!/usr/bin/env python3
"""
tests/test_kalshi_price_check_lib.py
=========================================
Tests for lib/kalshi_price_check.py -- the pure, network-free core of
the standalone Kalshi price-check tool.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.kalshi_price_check import (
    parse_event_teams,
    normalize_market,
    normalize_batch,
    apply_filters,
    parse_selected_games,
    game_has_started,
    format_json_summary_block,
    group_inning_result_threeway,
    format_table,
    format_csv,
    format_threeway_groups,
    STATUS_INCLUDED,
    STATUS_MISSING_PRICE,
    STATUS_MALFORMED_RECORD,
    STATUS_CLASSIFICATION_UNKNOWN,
    STATUS_DUPLICATE_RECORD,
)

F5_AWAY = {"market_ticker": "KXMLBF5-26JUL292210SEALAD-SEA", "event_ticker": "KXMLBF5-26JUL292210SEALAD",
           "title": "Seattle first 5 innings winner?", "yes_bid": 0.42, "yes_ask": 0.44, "status": "open"}
F5_TIE = {"market_ticker": "KXMLBF5-26JUL292210SEALAD-TIE", "event_ticker": "KXMLBF5-26JUL292210SEALAD",
          "title": "tie after 5", "yes_bid": 0.17, "yes_ask": 0.19, "status": "open"}
F5_HOME = {"market_ticker": "KXMLBF5-26JUL292210SEALAD-LAD", "event_ticker": "KXMLBF5-26JUL292210SEALAD",
           "title": "LA D first 5 innings winner?", "yes_bid": 0.37, "yes_ask": 0.39, "status": "open"}
UNKNOWN_MKT = {"market_ticker": "KXSOMETHINGNEW-26JUL291000ABCXYZ-ABC", "title": "Weird new market", "status": "open"}
CLOSED_MKT = dict(F5_AWAY, status="closed")


class TestParseEventTeams:

    def test_three_letter_teams(self):
        assert parse_event_teams("KXMLBF5-26JUL292210SEALAD") == ("SEA", "LAD")

    def test_two_letter_away_team(self):
        assert parse_event_teams("KXMLBSPREAD-26JUN041410SFMIL") == ("SF", "MIL")

    def test_doubleheader_suffix_stripped(self):
        assert parse_event_teams("KXMLBF5-26JUL291310ATLNYMG1") == ("ATL", "NYM")

    def test_malformed_ticker_returns_none_none(self):
        assert parse_event_teams("garbage") == (None, None)

    def test_empty_returns_none_none(self):
        assert parse_event_teams(None) == (None, None)
        assert parse_event_teams("") == (None, None)


class TestNormalizeMarket:

    def test_f5_away_normalizes_with_included_status(self):
        record, status, reason = normalize_market(F5_AWAY)
        assert status == STATUS_INCLUDED
        assert reason is None
        assert record["family"] == "inning_result"
        assert record["scope"] == "F5"
        assert record["outcome"] == "Away"
        assert record["marketStructure"] == "THREE_WAY"

    def test_missing_ticker_is_malformed(self):
        record, status, reason = normalize_market({"title": "no ticker"})
        assert record is None
        assert status == STATUS_MALFORMED_RECORD

    def test_no_prices_is_missing_price_but_still_returned(self):
        record, status, reason = normalize_market({"market_ticker": "KXMLBGAME-X-SEA", "event_ticker": "KXMLBGAME-X"})
        assert record is not None
        assert status == STATUS_MISSING_PRICE

    def test_unrecognized_series_no_matching_title_is_classification_unknown(self):
        record, status, reason = normalize_market(UNKNOWN_MKT)
        assert record is not None
        assert status == STATUS_CLASSIFICATION_UNKNOWN
        assert record["family"] == "unknown"

    def test_yes_ask_cents_and_probability_present(self):
        record, _, _ = normalize_market(F5_AWAY)
        assert record["yesAskCents"] == 44
        assert record["yesAskProbability"] == 0.44

    def test_no_bid_ask_derived_from_yes_side(self):
        record, _, _ = normalize_market(F5_AWAY)
        assert record["noBid"] == pytest.approx(1.0 - 0.44)
        assert record["noAsk"] == pytest.approx(1.0 - 0.42)

    def test_deterministic(self):
        r1, _, _ = normalize_market(F5_AWAY)
        r2, _, _ = normalize_market(F5_AWAY)
        assert r1 == r2

    def test_line_threshold_populated_for_spread_market(self):
        """
        Regression test (final maintainer review): normalize_market()
        previously hardcoded "line": None unconditionally, so a
        spread/total/team-total market's threshold never reached the
        price checker's output even though the classifier resolves it.
        """
        record, _, _ = normalize_market({
            "market_ticker": "KXMLBSPREAD-26JUL292210SEALAD-SEA2",
            "event_ticker": "KXMLBSPREAD-26JUL292210SEALAD",
            "title": "Seattle wins by 1.5?", "yes_bid": 0.4, "yes_ask": 0.42, "status": "open",
        })
        assert record["line"] == 1.5
        assert record["participant"] == "SEA"

    def test_line_threshold_populated_for_total_market(self):
        record, _, _ = normalize_market({
            "market_ticker": "KXMLBTOTAL-26JUL292210SEALAD-8",
            "event_ticker": "KXMLBTOTAL-26JUL292210SEALAD",
            "title": "Total over 7.5?", "yes_bid": 0.4, "yes_ask": 0.42, "status": "open",
        })
        assert record["line"] == 8

    def test_line_is_none_for_a_family_with_no_threshold(self):
        record, _, _ = normalize_market(F5_AWAY)
        assert record["line"] is None


class TestNormalizeBatchNoSilentDrop:

    def test_counts_reconcile_exactly(self):
        raw = [F5_AWAY, F5_TIE, UNKNOWN_MKT, {"title": "no ticker at all"}]
        records, counts, malformed = normalize_batch(raw)
        assert sum(counts.values()) == len(raw)

    def test_duplicate_ticker_labeled_not_dropped(self):
        raw = [F5_AWAY, dict(F5_AWAY)]
        records, counts, malformed = normalize_batch(raw)
        assert len(records) == 2
        assert counts.get(STATUS_DUPLICATE_RECORD) == 1

    def test_malformed_records_logged_with_reason(self):
        raw = [{"title": "broken"}]
        records, counts, malformed = normalize_batch(raw)
        assert len(malformed) == 1
        assert malformed[0][1] == "missing market_ticker"

    def test_deterministic(self):
        raw = [F5_AWAY, F5_TIE, F5_HOME]
        r1, c1, m1 = normalize_batch(raw)
        r2, c2, m2 = normalize_batch(raw)
        assert r1 == r2 and c1 == c2


class TestApplyFilters:

    def setup_method(self):
        raw = [F5_AWAY, F5_TIE, F5_HOME, UNKNOWN_MKT, CLOSED_MKT]
        self.records, _, _ = normalize_batch(raw)

    def test_exact_ticker_takes_priority(self):
        kept, _ = apply_filters(self.records, {"ticker": "KXMLBF5-26JUL292210SEALAD-TIE", "team": "ZZZ"})
        assert len(kept) == 1
        assert kept[0]["outcome"] == "Tie"

    def test_ticker_filter_case_insensitive(self):
        kept, _ = apply_filters(self.records, {"ticker": "kxmlbf5-26jul292210sealad-tie"})
        assert len(kept) == 1

    def test_team_filter_matches_away_or_home(self):
        kept, _ = apply_filters(self.records, {"team": "sea"})
        assert len(kept) >= 3  # away/tie/home all belong to SEA@LAD

    def test_scope_filter(self):
        kept, _ = apply_filters(self.records, {"scope": "F5"})
        assert all(r["scope"] == "F5" for r in kept)

    def test_family_filter(self):
        kept, _ = apply_filters(self.records, {"family": "inning_result"})
        assert all(r["family"] == "inning_result" for r in kept)

    def test_outcome_filter(self):
        kept, _ = apply_filters(self.records, {"outcome": "tie"})
        assert len(kept) == 1
        assert kept[0]["outcome"] == "Tie"

    def test_combined_filters(self):
        kept, _ = apply_filters(self.records, {"team": "SEA", "outcome": "Away"})
        assert len(kept) == 1

    def test_case_insensitive_team_match(self):
        kept_lower, _ = apply_filters(self.records, {"team": "sea"})
        kept_upper, _ = apply_filters(self.records, {"team": "SEA"})
        assert kept_lower == kept_upper

    def test_unknown_markets_included_by_default(self):
        kept, _ = apply_filters(self.records, {})
        assert any(r["family"] == "unknown" for r in kept)

    def test_unknown_markets_excluded_when_requested(self):
        kept, _ = apply_filters(self.records, {"include_unknown": False})
        assert not any(r["family"] == "unknown" for r in kept)

    def test_closed_markets_excluded_by_default(self):
        kept, _ = apply_filters(self.records, {})
        assert not any(r["status"] == "closed" for r in kept)

    def test_closed_markets_included_when_requested(self):
        kept, _ = apply_filters(self.records, {"include_closed": True})
        assert any(r["status"] == "closed" for r in kept)

    def test_max_results_caps_output(self):
        kept, _ = apply_filters(self.records, {"max_results": 1})
        assert len(kept) == 1

    def test_participant_filter_matches_title(self):
        kept, _ = apply_filters(self.records, {"participant": "Seattle"})
        assert len(kept) >= 1


NYY_BOS = {"market_ticker": "KXMLBF5-26JUL292215NYYBOS-NYY", "event_ticker": "KXMLBF5-26JUL292215NYYBOS",
           "title": "New York first 5 innings winner?", "yes_bid": 0.5, "yes_ask": 0.52, "status": "open"}


class TestSelectedGamesFilter:
    """
    Market-integrity requirement: selected-game matching must be EXACT
    and unambiguous -- unlike the pre-existing `game` filter (a
    substring match on the matchup string), `games` must never match a
    partial/ambiguous string.
    """

    def setup_method(self):
        raw = [F5_AWAY, F5_TIE, F5_HOME, NYY_BOS]  # SEA@LAD (x3) + NYY@BOS
        self.records, _, _ = normalize_batch(raw)

    def test_exact_matchup_selects_only_that_game(self):
        kept, _ = apply_filters(self.records, {"games": ["SEA@LAD"]})
        assert len(kept) == 3
        assert all(r["matchup"] == "SEA@LAD" for r in kept)

    def test_multiple_selected_games_combine(self):
        kept, _ = apply_filters(self.records, {"games": ["SEA@LAD", "NYY@BOS"]})
        assert len(kept) == 4

    def test_case_insensitive_exact_match(self):
        kept, _ = apply_filters(self.records, {"games": ["sea@lad"]})
        assert len(kept) == 3

    def test_no_partial_or_substring_match(self):
        """A substring/partial matchup token must match NOTHING -- proves
        this filter is exact, never fuzzy, unlike the pre-existing `game`
        substring filter."""
        kept, _ = apply_filters(self.records, {"games": ["SEA"]})
        assert kept == []

    def test_unselected_game_excluded(self):
        kept, _ = apply_filters(self.records, {"games": ["NYY@BOS"]})
        assert len(kept) == 1
        assert kept[0]["matchup"] == "NYY@BOS"

    def test_none_or_empty_is_a_no_op(self):
        kept_none, _ = apply_filters(self.records, {"games": None})
        kept_empty, _ = apply_filters(self.records, {"games": []})
        assert len(kept_none) == len(self.records)
        assert len(kept_empty) == len(self.records)


class TestParseSelectedGames:

    def test_splits_and_trims_comma_separated_list(self):
        assert parse_selected_games("PIT@CIN, NYY@BOS") == ["PIT@CIN", "NYY@BOS"]

    def test_blank_input_returns_none(self):
        assert parse_selected_games("") is None
        assert parse_selected_games(None) is None
        assert parse_selected_games("   ") is None

    def test_trailing_commas_and_blanks_ignored(self):
        assert parse_selected_games("PIT@CIN,,  ,NYY@BOS,") == ["PIT@CIN", "NYY@BOS"]


class TestGameHasStarted:
    """
    Market-integrity requirement: started/completed games must be
    excludable using authoritative game status/start logic ALREADY
    PRESENT in the repo -- reuses lib.edgelab.checkpoints.
    classify_checkpoint's own POST_START classification, the exact same
    one lib.edgelab.market_universe already uses for "has this game's
    first pitch happened", rather than a new time-comparison
    implementation.
    """

    def test_true_when_as_of_is_after_scheduled_start(self):
        assert game_has_started("2026-08-08T18:00:00Z", "2026-08-08T20:00:00Z") is True

    def test_false_when_as_of_is_before_scheduled_start(self):
        assert game_has_started("2026-08-08T18:00:00Z", "2026-08-08T10:00:00Z") is False

    def test_false_when_scheduled_start_unknown_never_assumed_started(self):
        assert game_has_started(None, "2026-08-08T10:00:00Z") is False

    def test_false_when_as_of_unknown(self):
        assert game_has_started("2026-08-08T18:00:00Z", None) is False


PAST_START = {"market_ticker": "KXMLBF5-26JUL292210PITCIN-PIT", "event_ticker": "KXMLBF5-26JUL292210PITCIN",
              "title": "Pittsburgh first 5 innings winner?", "yes_bid": 0.42, "yes_ask": 0.44,
              "status": "open", "open_time": "2020-01-01T00:00:00Z"}
FUTURE_START = {"market_ticker": "KXMLBF5-26JUL292210NYYBOS-NYY", "event_ticker": "KXMLBF5-26JUL292210NYYBOS",
                "title": "New York first 5 innings winner?", "yes_bid": 0.5, "yes_ask": 0.52,
                "status": "open", "open_time": "2030-01-01T00:00:00Z"}
UNKNOWN_START = {"market_ticker": "KXMLBF5-26JUL292210SEALAD-SEA", "event_ticker": "KXMLBF5-26JUL292210SEALAD",
                  "title": "Seattle first 5 innings winner?", "yes_bid": 0.4, "yes_ask": 0.42, "status": "open"}


class TestExcludeStartedFilterStage:

    def setup_method(self):
        raw = [PAST_START, FUTURE_START, UNKNOWN_START]
        self.records, _, _ = normalize_batch(raw)
        self.as_of = "2026-08-08T00:00:00Z"  # after PAST_START, before FUTURE_START

    def test_excludes_only_started_games(self):
        kept, _ = apply_filters(self.records, {"exclude_started": True}, as_of=self.as_of)
        matchups = {r["matchup"] for r in kept}
        assert "PIT@CIN" not in matchups
        assert "NYY@BOS" in matchups

    def test_unknown_start_time_never_excluded(self):
        """Can't prove it started -- never guessed into exclusion."""
        kept, _ = apply_filters(self.records, {"exclude_started": True}, as_of=self.as_of)
        matchups = {r["matchup"] for r in kept}
        assert "SEA@LAD" in matchups

    def test_disabled_by_default(self):
        kept, _ = apply_filters(self.records, {}, as_of=self.as_of)
        assert len(kept) == len(self.records)

    def test_no_op_without_as_of(self):
        """exclude_started requested but no reference time supplied --
        nothing can be proven started, so nothing is excluded (not a crash)."""
        kept, _ = apply_filters(self.records, {"exclude_started": True})
        assert len(kept) == len(self.records)

    def test_combined_with_games_filter(self):
        kept, _ = apply_filters(
            self.records, {"games": ["PIT@CIN", "NYY@BOS"], "exclude_started": True}, as_of=self.as_of,
        )
        assert len(kept) == 1
        assert kept[0]["matchup"] == "NYY@BOS"


class TestFormatJsonSummaryBlock:

    def test_empty_records_returns_no_markets_message(self):
        assert format_json_summary_block([]) == "No markets matched the requested filters."

    def test_small_result_embeds_full_json_in_collapsible_block(self):
        records, _, _ = normalize_batch([F5_AWAY])
        block = format_json_summary_block(records)
        assert block.startswith("<details>")
        assert block.endswith("</details>")
        assert "```json" in block
        assert records[0]["ticker"] in block

    def test_oversized_result_is_never_truncated_into_invalid_json(self):
        records, _, _ = normalize_batch([F5_AWAY])
        block = format_json_summary_block(records, max_bytes=1)  # force the oversize path
        assert "<details>" not in block
        assert "too large" in block.lower()
        assert "kalshi-price-check-json" in block


class TestThreeWayGrouping:

    def test_groups_by_event_and_scope(self):
        records, _, _ = normalize_batch([F5_AWAY, F5_TIE, F5_HOME])
        groups = group_inning_result_threeway(records)
        assert len(groups) == 1
        assert groups[0]["away"] is not None
        assert groups[0]["tie"] is not None
        assert groups[0]["home"] is not None
        assert groups[0]["missingLegs"] == []

    def test_missing_leg_reported(self):
        records, _, _ = normalize_batch([F5_AWAY, F5_HOME])
        groups = group_inning_result_threeway(records)
        assert groups[0]["missingLegs"] == ["Tie"]

    def test_sum_yes_ask(self):
        records, _, _ = normalize_batch([F5_AWAY, F5_TIE, F5_HOME])
        groups = group_inning_result_threeway(records)
        assert groups[0]["sumYesAsk"] == pytest.approx(0.44 + 0.19 + 0.39)

    def test_f3_never_synthesizes_missing_leg(self):
        """
        Spread/F3-F7-correction mission: F3's structure was
        independently confirmed THREE_WAY via a live series-catalogue
        dispatch, so a single discovered Away leg now correctly reports
        the other two legs as real missing gaps (matching F5's
        pre-existing behavior) -- never synthesized, never hidden.
        """
        f3_away = {"market_ticker": "KXUNKNOWNF3-26JUL292210SEALAD-SEA", "event_ticker": "KXUNKNOWNF3-26JUL292210SEALAD",
                   "title": "first 3 innings winner?", "yes_bid": 0.4, "yes_ask": 0.42}
        records, _, _ = normalize_batch([f3_away])
        groups = group_inning_result_threeway(records)
        assert groups[0]["structure"] == "THREE_WAY"
        assert set(groups[0]["missingLegs"]) == {"Tie", "Home"}


class TestFormatting:

    def test_format_table_has_header(self):
        records, _, _ = normalize_batch([F5_AWAY])
        table = format_table(records)
        assert "Matchup" in table.splitlines()[0]

    def test_format_table_never_shows_midpoint_column(self):
        table = format_table([])
        assert "Midpoint" not in table

    def test_format_csv_valid(self):
        import csv
        import io
        records, _, _ = normalize_batch([F5_AWAY])
        csv_text = format_csv(records)
        rows = list(csv.DictReader(io.StringIO(csv_text)))
        assert len(rows) == 1
        assert rows[0]["ticker"] == F5_AWAY["market_ticker"]

    def test_format_csv_empty(self):
        csv_text = format_csv([])
        assert "ticker" in csv_text

    def test_format_threeway_groups_shows_warning_for_missing_leg(self):
        records, _, _ = normalize_batch([F5_AWAY, F5_HOME])
        groups = group_inning_result_threeway(records)
        text = format_threeway_groups(groups)
        assert "WARNING" in text
        assert "Tie" in text

    def test_format_threeway_groups_empty(self):
        assert format_threeway_groups([]) == ""
