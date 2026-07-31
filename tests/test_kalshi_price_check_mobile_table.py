#!/usr/bin/env python3
"""
tests/test_kalshi_price_check_mobile_table.py
===================================================
Kalshi price-check mobile job-summary mission -- proves
lib.kalshi_price_check.format_mobile_markdown_table() renders every
returned market into a single, sorted, mobile-friendly Markdown table
(Game -> Market Family -> Scope -> Market), never fabricates data,
leaves blanks instead of "None", truncates at 250 rows while stating
how many more exist, and reports "No markets matched the requested
filters." on an empty input rather than a silent/blank table.

This is presentation-layer only -- every test here proves the FORMATTER
never re-derives or changes pricing/filtering/classification; it only
displays whatever normalize_batch()/apply_strict_game_registry() already
produced.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.kalshi_price_check import (
    normalize_batch,
    format_mobile_markdown_table,
    mobile_summary_sort_key,
    TEAM_DISPLAY_NAMES,
)


def _mkt(ticker, event_ticker=None, title=None, **kw):
    row = {"market_ticker": ticker, "event_ticker": event_ticker or ticker.rsplit("-", 1)[0],
           "title": title, "yes_bid": 0.4, "yes_ask": 0.42, "status": "open"}
    row.update(kw)
    return row


def _records(*raw):
    records, _, _ = normalize_batch(list(raw))
    return records


GAME_ML_AWAY = _mkt("KXMLBGAME-26JUL292210PITNYY-PIT", title="Pirates wins?")
GAME_ML_HOME = _mkt("KXMLBGAME-26JUL292210PITNYY-NYY", title="Yankees wins?")
F5_WINNER_AWAY = _mkt("KXMLBF5-26JUL292210PITNYY-PIT", title="Pirates first 5 innings winner?")
F5_WINNER_TIE = _mkt("KXMLBF5-26JUL292210PITNYY-TIE", title="First 5 innings tie?")
SPREAD = _mkt("KXMLBSPREAD-26JUL292210PITNYY-PIT2", title="Pirates wins by 1.5?")
F5_SPREAD = _mkt("KXMLBF5SPREAD-26JUL292210PITNYY-PIT1", title="Pirates F5 wins by 0.5?")
TOTAL = _mkt("KXMLBTOTAL-26JUL292210PITNYY-8", title="Total over 7.5?")
F5_TOTAL = _mkt("KXMLBF5TOTAL-26JUL292210PITNYY-4", title="F5 total over 3.5?")
TEAM_TOTAL = _mkt("KXMLBTEAMTOTAL-26JUL292210PITNYY-PIT4", title="Pirates team total over 3.5?")
NRFI = _mkt("KXMLBRFI-26JUL292210PITNYY-YES", title="Run in first inning?")
PITCHER_KS = _mkt("KXMLBKS-26JUL292210PITNYY-ABC", title="Pitcher over 6.5 strikeouts?")
HITTER_RBI = _mkt("KXMLBRBI-26JUL292210PITNYY-XYZ", title="Player over 1.5 RBIs?")


class TestTeamAndGameDisplay:

    def test_real_mlb_abbreviations_map_to_friendly_names(self):
        assert TEAM_DISPLAY_NAMES["PIT"] == "Pirates"
        assert TEAM_DISPLAY_NAMES["NYY"] == "Yankees"

    def test_game_header_uses_friendly_team_names(self):
        table = format_mobile_markdown_table(_records(GAME_ML_AWAY))
        assert "Pirates @ Yankees" in table
        assert "PIT@NYY" not in table

    def test_unmapped_team_falls_back_to_raw_abbreviation_not_dropped(self):
        mkt = _mkt("KXMLBGAME-26JUL292210ZZZNYY-ZZZ", title="Unknown team wins?")
        table = format_mobile_markdown_table(_records(mkt))
        assert "ZZZ @ Yankees" in table


class TestMarketFamilyAndScopeDisplay:

    def test_full_game_moneyline_is_winner_family_full_game_scope(self):
        table = format_mobile_markdown_table(_records(GAME_ML_AWAY))
        lines = table.splitlines()
        row = [l for l in lines if "Pirates @ Yankees" in l][0]
        assert "| Full Game |" in row
        assert "| Winner |" in row

    def test_f5_winner_is_winner_family_f5_scope(self):
        table = format_mobile_markdown_table(_records(F5_WINNER_AWAY))
        row = [l for l in table.splitlines() if "Pirates @ Yankees" in l][0]
        assert "| F5 |" in row
        assert "| Winner |" in row

    def test_spread_is_run_line_family(self):
        table = format_mobile_markdown_table(_records(SPREAD))
        row = [l for l in table.splitlines() if "Pirates @ Yankees" in l][0]
        assert "| Run Line |" in row

    def test_total_is_total_family(self):
        table = format_mobile_markdown_table(_records(TOTAL))
        row = [l for l in table.splitlines() if "Pirates @ Yankees" in l][0]
        assert "| Total |" in row

    def test_team_total_is_team_total_family(self):
        table = format_mobile_markdown_table(_records(TEAM_TOTAL))
        row = [l for l in table.splitlines() if "Pirates @ Yankees" in l][0]
        assert "| Team Total |" in row

    def test_nrfi_is_nrfi_yrfi_family(self):
        table = format_mobile_markdown_table(_records(NRFI))
        row = [l for l in table.splitlines() if "Pirates @ Yankees" in l][0]
        assert "NRFI/YRFI" in row

    def test_pitcher_prop_is_pitcher_props_family(self):
        table = format_mobile_markdown_table(_records(PITCHER_KS))
        row = [l for l in table.splitlines() if "Pirates @ Yankees" in l][0]
        assert "| Pitcher Props |" in row

    def test_hitter_prop_is_player_props_family(self):
        table = format_mobile_markdown_table(_records(HITTER_RBI))
        row = [l for l in table.splitlines() if "Pirates @ Yankees" in l][0]
        assert "| Player Props |" in row

    def test_unclassified_market_is_other_family(self):
        mkt = _mkt("KXSOMETHINGNEW-26JUL292210PITNYY-ABC", title="Weird new market")
        table = format_mobile_markdown_table(_records(mkt))
        row = [l for l in table.splitlines() if "Pirates @ Yankees" in l][0]
        assert "| Other |" in row


class TestMarketNameAndThreshold:

    def test_spread_shows_team_and_threshold(self):
        table = format_mobile_markdown_table(_records(SPREAD))
        assert "Pirates 1.5" in table

    def test_total_shows_threshold(self):
        table = format_mobile_markdown_table(_records(TOTAL))
        assert "Total 8" in table

    def test_team_total_shows_team_and_threshold(self):
        table = format_mobile_markdown_table(_records(TEAM_TOTAL))
        assert "Pirates Total 3.5" in table

    def test_tie_leg_shows_tie_not_a_team(self):
        table = format_mobile_markdown_table(_records(F5_WINNER_TIE))
        row = [l for l in table.splitlines() if "F5" in l and "Winner" in l][0]
        assert row.split("|")[4].strip() == "Tie F5"

    def test_pitcher_prop_falls_back_to_cleaned_title(self):
        table = format_mobile_markdown_table(_records(PITCHER_KS))
        assert "Pitcher over 6.5 strikeouts" in table
        assert "Pitcher over 6.5 strikeouts?" not in table


class TestOutcomeColumnAlwaysYes:

    def test_outcome_column_is_yes_for_every_row(self):
        table = format_mobile_markdown_table(_records(GAME_ML_AWAY, SPREAD, TOTAL, NRFI))
        rows = [l for l in table.splitlines() if l.startswith("| Pirates")]
        assert len(rows) == 4
        for row in rows:
            cells = [c.strip() for c in row.split("|")]
            assert "YES" in cells


class TestPriceFormattingAndBlanks:

    def test_yes_bid_ask_shown_as_cents(self):
        table = format_mobile_markdown_table(_records(GAME_ML_AWAY))
        row = [l for l in table.splitlines() if "Pirates @ Yankees" in l][0]
        # yes_bid=0.4 -> 40, yes_ask=0.42 -> 42
        assert "| 40 |" in row
        assert "| 42 |" in row

    def test_missing_last_price_is_blank_not_none(self):
        table = format_mobile_markdown_table(_records(GAME_ML_AWAY))
        assert "None" not in table

    def test_status_is_title_cased(self):
        table = format_mobile_markdown_table(_records(GAME_ML_AWAY))
        assert "| Open |" in table
        assert "| open |" not in table


class TestSortOrder:

    def test_all_families_for_one_game_are_contiguous_in_family_then_scope_order(self):
        records = _records(
            TOTAL, GAME_ML_AWAY, F5_WINNER_AWAY, TEAM_TOTAL, SPREAD, F5_SPREAD, F5_TOTAL, NRFI,
        )
        table = format_mobile_markdown_table(records)
        rows = [l for l in table.splitlines() if l.startswith("| Pirates @ Yankees")]
        families = [row.split("|")[3].strip() for row in rows]
        # Winner, Winner(F5), Run Line, Run Line(F5), Total, Total(F5), Team Total, NRFI/YRFI
        expected_order = ["Winner", "Winner", "Run Line", "Run Line", "Total", "Total", "Team Total", "NRFI/YRFI"]
        assert families == expected_order

    def test_scope_orders_f5_before_full_game_within_same_family(self):
        records = _records(F5_WINNER_AWAY, GAME_ML_AWAY)
        table = format_mobile_markdown_table(records)
        rows = [l for l in table.splitlines() if l.startswith("| Pirates")]
        scopes = [row.split("|")[2].strip() for row in rows]
        assert scopes == ["F5", "Full Game"]

    def test_different_games_sorted_alphabetically_by_display_name(self):
        other_game = _mkt("KXMLBGAME-26JUL292210AZCLE-AZ", title="Diamondbacks wins?")
        records = _records(GAME_ML_AWAY, other_game)
        table = format_mobile_markdown_table(records)
        idx_az = table.index("Diamondbacks @ Guardians")
        idx_pit = table.index("Pirates @ Yankees")
        assert idx_az < idx_pit  # "Diamondbacks..." < "Pirates..." alphabetically

    def test_sort_key_is_pure_and_deterministic(self):
        records = _records(GAME_ML_AWAY, SPREAD)
        keys1 = [mobile_summary_sort_key(r) for r in records]
        keys2 = [mobile_summary_sort_key(r) for r in records]
        assert keys1 == keys2


class TestTruncationAt250:

    def _many_markets(self, n):
        raw = []
        for i in range(n):
            ticker = f"KXMLBKS-26JUL292210PITNYY-P{i}"
            raw.append(_mkt(ticker, event_ticker="KXMLBKS-26JUL292210PITNYY", title=f"Pitcher {i} over 5.5 strikeouts?"))
        return _records(*raw)

    def test_more_than_250_shows_first_250_and_states_remainder(self):
        records = self._many_markets(260)
        table = format_mobile_markdown_table(records)
        row_lines = [l for l in table.splitlines() if l.startswith("| Pirates")]
        assert len(row_lines) == 250
        assert "10 more market(s)" in table

    def test_exactly_250_shows_no_remainder_note(self):
        records = self._many_markets(250)
        table = format_mobile_markdown_table(records)
        assert "more market(s)" not in table

    def test_custom_max_rows_respected(self):
        records = self._many_markets(10)
        table = format_mobile_markdown_table(records, max_rows=3)
        row_lines = [l for l in table.splitlines() if l.startswith("| Pirates")]
        assert len(row_lines) == 3
        assert "7 more market(s)" in table

    def test_truncation_never_touches_the_caller_supplied_full_list(self):
        """The formatter must never mutate or drop from the list the
        caller (the workflow's JSON/CSV artifacts) still holds."""
        records = self._many_markets(260)
        before_len = len(records)
        format_mobile_markdown_table(records)
        assert len(records) == before_len


class TestZeroMarkets:

    def test_empty_input_reports_no_markets_message(self):
        result = format_mobile_markdown_table([])
        assert result == "No markets matched the requested filters."

    def test_zero_markets_never_renders_a_blank_or_header_only_table(self):
        result = format_mobile_markdown_table([])
        assert "| Game |" not in result
