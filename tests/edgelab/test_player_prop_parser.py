#!/usr/bin/env python3
"""
tests/edgelab/test_player_prop_parser.py
=============================================
Coverage for lib/research/player_prop_parser.py (GitHub issue #43).
Every real-archived-ticker example is taken verbatim from
data/kalshi_registry_snapshots/kalshi_search_2026-08-02.json (audited
directly, not assumed from sportsbook conventions).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.research.player_prop_parser import (
    TEAM_RESOLVED,
    TEAM_UNRESOLVED_CONFLICT,
    TEAM_UNRESOLVED_NO_CONTEXT,
    normalize_player_name,
    normalized_name_variants,
    parse_player_prop_market,
)

BOS_LAD_EVENT = "KXMLBKS-26AUG021920BOSLAD"


def test_pitcher_strikeouts_real_example():
    r = parse_player_prop_market(
        "KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9", BOS_LAD_EVENT,
        "Emmet Sheehan: 9+ strikeouts?", away_team="BOS", home_team="LAD",
    )
    assert r["parseStatus"] == "PARSED"
    assert r["teamAbbr"] == "LAD"
    assert r["rawPlayerToken"] == "LADESHEEHAN80"
    assert r["threshold"] == 9
    assert r["comparisonOperator"] == "AT_LEAST"
    assert r["displayNameRaw"] == "Emmet Sheehan"
    assert r["normalizedNameVariants"] == {"emmet sheehan"}


def test_pitcher_outs_real_example():
    r = parse_player_prop_market(
        "KXMLBOUTS-26AUG021920BOSLAD-LADESHEEHAN80-17", "KXMLBOUTS-26AUG021920BOSLAD",
        "Emmet Sheehan: 17+ Outs Recorded?", away_team="BOS", home_team="LAD",
    )
    assert r["parseStatus"] == "PARSED"
    assert r["teamAbbr"] == "LAD"
    assert r["threshold"] == 17
    assert r["displayNameRaw"] == "Emmet Sheehan"


def test_hitter_hits_real_example():
    r = parse_player_prop_market(
        "KXMLBHIT-26AUG021920BOSLAD-LADSOHTANI17-2", "KXMLBHIT-26AUG021920BOSLAD",
        "Shohei Ohtani: 2+ hits?", away_team="BOS", home_team="LAD",
    )
    assert r["parseStatus"] == "PARSED"
    assert r["teamAbbr"] == "LAD"
    assert r["rawPlayerToken"] == "LADSOHTANI17"
    assert r["threshold"] == 2
    assert r["displayNameRaw"] == "Shohei Ohtani"


def test_hitter_total_bases_real_example():
    r = parse_player_prop_market(
        "KXMLBTB-26AUG021920BOSLAD-LADSOHTANI17-5", "KXMLBTB-26AUG021920BOSLAD",
        "Shohei Ohtani: 5+ total bases?", away_team="BOS", home_team="LAD",
    )
    assert r["parseStatus"] == "PARSED"
    assert r["threshold"] == 5
    assert r["displayNameRaw"] == "Shohei Ohtani"


def test_hitter_hits_runs_rbis_real_example():
    r = parse_player_prop_market(
        "KXMLBHRR-26AUG021920BOSLAD-LADSOHTANI17-3", "KXMLBHRR-26AUG021920BOSLAD",
        "Shohei Ohtani: 3+ hits + runs + RBIs?", away_team="BOS", home_team="LAD",
    )
    assert r["parseStatus"] == "PARSED"
    assert r["threshold"] == 3
    assert r["displayNameRaw"] == "Shohei Ohtani"


def test_hitter_rbis_real_example():
    r = parse_player_prop_market(
        "KXMLBRBI-26AUG021920BOSLAD-LADSOHTANI17-2", "KXMLBRBI-26AUG021920BOSLAD",
        "Shohei Ohtani: 2+ RBIs?", away_team="BOS", home_team="LAD",
    )
    assert r["parseStatus"] == "PARSED"
    assert r["threshold"] == 2
    assert r["displayNameRaw"] == "Shohei Ohtani"


def test_hitter_stolen_bases_real_example():
    r = parse_player_prop_market(
        "KXMLBSB-26AUG021920BOSLAD-LADSOHTANI17-1", "KXMLBSB-26AUG021920BOSLAD",
        "Shohei Ohtani: 1+ stolen bases?", away_team="BOS", home_team="LAD",
    )
    assert r["parseStatus"] == "PARSED"
    assert r["threshold"] == 1
    assert r["displayNameRaw"] == "Shohei Ohtani"


def test_unicode_player_name_accented():
    r = parse_player_prop_market(
        "KXMLBRBI-26AUG021920BOSLAD-BOSCNARVEZ75-1", "KXMLBRBI-26AUG021920BOSLAD",
        "Carlos Narváez: 1+ RBIs?", away_team="BOS", home_team="LAD",
    )
    assert r["displayNameRaw"] == "Carlos Narváez"
    assert r["normalizedNameVariants"] == {"carlos narvaez"}


def test_unicode_player_name_second_accent_example():
    r = parse_player_prop_market(
        "KXMLBSB-26AUG032005LADCHC-LADTHERNANDEZ37-1", "KXMLBSB-26AUG032005LADCHC",
        "Teoscar Hernández: 1+ stolen bases?", away_team="LAD", home_team="CHC",
    )
    assert r["displayNameRaw"] == "Teoscar Hernández"
    assert r["normalizedNameVariants"] == {"teoscar hernandez"}


def test_parenthetical_team_suffix_stripped():
    r = parse_player_prop_market(
        "KXMLBHIT-26AUG021920BOSLAD-LADMMUNCY13-1", "KXMLBHIT-26AUG021920BOSLAD",
        "Max Muncy (LAD): 1+ hits?", away_team="BOS", home_team="LAD",
    )
    assert r["displayNameRaw"] == "Max Muncy"
    assert r["displayNameParentheticalTeam"] == "LAD"
    assert r["normalizedNameVariants"] == {"max muncy"}


def test_apostrophe_and_suffix_name():
    r = parse_player_prop_market(
        "KXMLBRBI-26AUG042140SDAZ-SDFTATIS23-1", "KXMLBRBI-26AUG042140SDAZ",
        "Fernando Tatis Jr.: 1+ RBIs?", away_team="SD", home_team="AZ",
    )
    assert r["displayNameRaw"] == "Fernando Tatis Jr."
    # Both the full form and the suffix-stripped "core name" are accepted.
    assert r["normalizedNameVariants"] == {"fernando tatis jr", "fernando tatis"}


def test_apostrophe_in_last_name():
    r = parse_player_prop_market(
        "KXMLBTB-26JUL311810PITCIN-PITROHEARN29-2", "KXMLBTB-26JUL311810PITCIN",
        "Ryan O'Hearn: 2+ total bases?", away_team="PIT", home_team="CIN",
    )
    assert r["displayNameRaw"] == "Ryan O'Hearn"
    assert r["normalizedNameVariants"] == {"ryan ohearn"}


def test_threshold_extraction_matches_ticker_and_title():
    r = parse_player_prop_market(
        "KXMLBOUTS-26AUG041840ATHCIN-ATHJGINN35-16", "KXMLBOUTS-26AUG041840ATHCIN",
        "J.T. Ginn: 16+ Outs Recorded?", away_team="ATH", home_team="CIN",
    )
    assert r["threshold"] == 16
    assert r["titleThreshold"] == 16
    assert r["thresholdMismatch"] is False


def test_team_extraction_two_letter_abbreviation():
    r = parse_player_prop_market(
        "KXMLBRBI-26AUG042010TORHOU-TORVGUERRERO27-1", "KXMLBRBI-26AUG042010TORHOU",
        "Vladimir Guerrero Jr.: 1+ RBIs?", away_team="TOR", home_team="HOU",
    )
    assert r["teamAbbr"] == "TOR"


def test_raw_player_token_preserved_verbatim():
    r = parse_player_prop_market(
        "KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9", BOS_LAD_EVENT,
        "Emmet Sheehan: 9+ strikeouts?", away_team="BOS", home_team="LAD",
    )
    assert r["rawPlayerToken"] == "LADESHEEHAN80"
    assert r["tokenFirstInitial"] == "E"
    assert r["tokenLastNameCompact"] == "SHEEHAN"
    assert r["tokenNumericSuffix"] == "80"


def test_at_least_semantics_always_set_when_parsed():
    r = parse_player_prop_market(
        "KXMLBHIT-26AUG021920BOSLAD-LADSOHTANI17-2", "KXMLBHIT-26AUG021920BOSLAD",
        "Shohei Ohtani: 2+ hits?", away_team="BOS", home_team="LAD",
    )
    assert r["comparisonOperator"] == "AT_LEAST"


def test_malformed_ticker_missing_event_prefix_is_unparseable():
    r = parse_player_prop_market("NOT-A-REAL-TICKER-9", "KXMLBKS-26AUG021920BOSLAD", "Foo: 9+ strikeouts?")
    assert r["parseStatus"] == "UNPARSEABLE"
    assert r["unparseableReason"] == "ticker_missing_event_prefix"


def test_malformed_ticker_no_second_hyphen_is_unparseable():
    r = parse_player_prop_market(
        "KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80", BOS_LAD_EVENT, "Emmet Sheehan: 9+ strikeouts?",
    )
    assert r["parseStatus"] == "UNPARSEABLE"
    assert r["unparseableReason"] == "ticker_suffix_not_two_part"


def test_malformed_ticker_non_numeric_threshold_is_unparseable():
    r = parse_player_prop_market(
        "KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-NINE", BOS_LAD_EVENT, "Emmet Sheehan: 9+ strikeouts?",
    )
    assert r["parseStatus"] == "UNPARSEABLE"
    assert r["unparseableReason"] == "ticker_threshold_not_numeric"


def test_unsupported_title_shape_leaves_display_name_none_but_ticker_still_parsed():
    """
    A ticker that parses fine but a title that doesn't match the "Name:
    N+ stat?" shape still yields team/threshold (ticker-derived), just
    no player name signal -- downstream player resolution then correctly
    finds zero candidates (see test_player_prop_settlement.py), rather
    than this parser fabricating a guess.
    """
    r = parse_player_prop_market(
        "KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9", BOS_LAD_EVENT,
        "This is not a recognizable title format", away_team="BOS", home_team="LAD",
    )
    assert r["parseStatus"] == "PARSED"
    assert r["threshold"] == 9
    assert r["displayNameRaw"] is None
    assert r["normalizedNameVariants"] == frozenset()


def test_no_title_at_all_still_parses_ticker_structure():
    r = parse_player_prop_market(
        "KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9", BOS_LAD_EVENT, None,
        away_team="BOS", home_team="LAD",
    )
    assert r["parseStatus"] == "PARSED"
    assert r["teamAbbr"] == "LAD"
    assert r["displayNameRaw"] is None


def test_team_abbr_falls_back_to_heuristic_without_known_teams():
    """No away/home context supplied -- falls back to the 2-vs-3-letter heuristic."""
    r = parse_player_prop_market(
        "KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9", BOS_LAD_EVENT, "Emmet Sheehan: 9+ strikeouts?",
    )
    assert r["parseStatus"] == "PARSED"
    assert r["teamAbbr"] == "LAD"  # 3-letter fallback guess, happens to match here


def test_normalize_player_name_strips_accents_punctuation_and_case():
    assert normalize_player_name("Carlos Narváez") == "carlos narvaez"
    assert normalize_player_name("Ryan O'Hearn") == "ryan ohearn"
    assert normalize_player_name("A.J. Ewing") == "aj ewing"
    assert normalize_player_name("  Multiple   Spaces  ") == "multiple spaces"


def test_normalized_name_variants_includes_suffix_stripped_form():
    variants = normalized_name_variants("Bobby Witt Jr.")
    assert "bobby witt jr" in variants
    assert "bobby witt" in variants


def test_normalized_name_variants_empty_for_falsy_name():
    assert normalized_name_variants(None) == frozenset()
    assert normalized_name_variants("") == frozenset()


def test_deterministic():
    args = ("KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9", BOS_LAD_EVENT, "Emmet Sheehan: 9+ strikeouts?")
    kwargs = {"away_team": "BOS", "home_team": "LAD"}
    assert parse_player_prop_market(*args, **kwargs) == parse_player_prop_market(*args, **kwargs)


def test_never_raises_on_garbage_input():
    assert parse_player_prop_market(None, None, None)["parseStatus"] == "UNPARSEABLE"
    assert parse_player_prop_market("", "", "")["parseStatus"] == "UNPARSEABLE"


# ── Integrity fields (GitHub issue #43 correction round) ────────────────────

def test_team_resolution_status_resolved_when_ticker_matches_known_side():
    r = parse_player_prop_market(
        "KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9", BOS_LAD_EVENT, "Emmet Sheehan: 9+ strikeouts?",
        away_team="BOS", home_team="LAD",
    )
    assert r["teamResolutionStatus"] == TEAM_RESOLVED
    assert r["teamAbbr"] == "LAD"


def test_team_resolution_status_conflict_when_neither_side_matches():
    r = parse_player_prop_market(
        "KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9", BOS_LAD_EVENT, "Emmet Sheehan: 9+ strikeouts?",
        away_team="NYY", home_team="TOR",
    )
    assert r["teamResolutionStatus"] == TEAM_UNRESOLVED_CONFLICT
    assert r["teamAbbr"] is None


def test_team_resolution_status_no_context_when_teams_not_supplied():
    r = parse_player_prop_market(
        "KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9", BOS_LAD_EVENT, "Emmet Sheehan: 9+ strikeouts?",
    )
    assert r["teamResolutionStatus"] == TEAM_UNRESOLVED_NO_CONTEXT


def test_token_malformed_when_remainder_has_no_letters():
    r = parse_player_prop_market(
        "KXMLBKS-26AUG021920BOSLAD-LAD123-9", BOS_LAD_EVENT, "Someone: 9+ strikeouts?",
        away_team="BOS", home_team="LAD",
    )
    assert r["tokenMalformed"] is True


def test_token_not_malformed_for_real_example():
    r = parse_player_prop_market(
        "KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9", BOS_LAD_EVENT, "Emmet Sheehan: 9+ strikeouts?",
        away_team="BOS", home_team="LAD",
    )
    assert r["tokenMalformed"] is False


def test_title_parse_status_not_provided_when_no_title():
    r = parse_player_prop_market("KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9", BOS_LAD_EVENT, None)
    assert r["titleParseStatus"] == "NOT_PROVIDED"


def test_title_parse_status_unparseable_for_unrecognized_shape():
    r = parse_player_prop_market(
        "KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9", BOS_LAD_EVENT, "not a recognizable title",
    )
    assert r["titleParseStatus"] == "UNPARSEABLE"


def test_title_parse_status_parsed_for_real_example():
    r = parse_player_prop_market(
        "KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9", BOS_LAD_EVENT, "Emmet Sheehan: 9+ strikeouts?",
    )
    assert r["titleParseStatus"] == "PARSED"


def test_parenthetical_team_conflict_true_when_it_disagrees_with_ticker_team():
    r = parse_player_prop_market(
        "KXMLBHIT-26AUG021920BOSLAD-LADMMUNCY13-1", "KXMLBHIT-26AUG021920BOSLAD",
        "Max Muncy (BOS): 1+ hits?", away_team="BOS", home_team="LAD",
    )
    assert r["teamAbbr"] == "LAD"
    assert r["displayNameParentheticalTeam"] == "BOS"
    assert r["parentheticalTeamConflict"] is True


def test_parenthetical_team_no_conflict_when_it_agrees_with_ticker_team():
    r = parse_player_prop_market(
        "KXMLBHIT-26AUG021920BOSLAD-LADMMUNCY13-1", "KXMLBHIT-26AUG021920BOSLAD",
        "Max Muncy (LAD): 1+ hits?", away_team="BOS", home_team="LAD",
    )
    assert r["parentheticalTeamConflict"] is False


def test_stat_text_family_mismatch_true_when_family_disagrees_with_title_wording():
    r = parse_player_prop_market(
        "KXMLBHIT-26AUG021920BOSLAD-LADSOHTANI17-5", "KXMLBHIT-26AUG021920BOSLAD",
        "Shohei Ohtani: 5+ total bases?", away_team="BOS", home_team="LAD", family="hitter_hits",
    )
    assert r["statText"] == "total bases"
    assert r["statTextFamilyMismatch"] is True


def test_stat_text_family_mismatch_false_when_family_matches_title_wording():
    r = parse_player_prop_market(
        "KXMLBHIT-26AUG021920BOSLAD-LADSOHTANI17-2", "KXMLBHIT-26AUG021920BOSLAD",
        "Shohei Ohtani: 2+ hits?", away_team="BOS", home_team="LAD", family="hitter_hits",
    )
    assert r["statTextFamilyMismatch"] is False


def test_stat_text_family_mismatch_false_when_family_not_supplied():
    r = parse_player_prop_market(
        "KXMLBHIT-26AUG021920BOSLAD-LADSOHTANI17-5", "KXMLBHIT-26AUG021920BOSLAD",
        "Shohei Ohtani: 5+ total bases?", away_team="BOS", home_team="LAD",
    )
    assert r["statTextFamilyMismatch"] is False


def test_all_seven_families_stat_text_matches_family_real_examples():
    cases = [
        ("pitcher_strikeouts", "KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9", "Emmet Sheehan: 9+ strikeouts?"),
        ("pitcher_outs", "KXMLBOUTS-26AUG021920BOSLAD-LADESHEEHAN80-17", "Emmet Sheehan: 17+ Outs Recorded?"),
        ("hitter_hits", "KXMLBHIT-26AUG021920BOSLAD-LADSOHTANI17-2", "Shohei Ohtani: 2+ hits?"),
        ("hitter_total_bases", "KXMLBTB-26AUG021920BOSLAD-LADSOHTANI17-5", "Shohei Ohtani: 5+ total bases?"),
        ("hitter_hits_runs_rbis", "KXMLBHRR-26AUG021920BOSLAD-LADSOHTANI17-3", "Shohei Ohtani: 3+ hits + runs + RBIs?"),
        ("hitter_rbis", "KXMLBRBI-26AUG021920BOSLAD-LADSOHTANI17-2", "Shohei Ohtani: 2+ RBIs?"),
        ("hitter_stolen_bases", "KXMLBSB-26AUG021920BOSLAD-LADSOHTANI17-1", "Shohei Ohtani: 1+ stolen bases?"),
    ]
    for family, ticker, title in cases:
        et = ticker.rsplit("-", 2)[0]
        r = parse_player_prop_market(ticker, et, title, away_team="BOS", home_team="LAD", family=family)
        assert r["statTextFamilyMismatch"] is False, f"{family}: {r['statText']!r}"


def test_threshold_mismatch_true_when_ticker_and_title_disagree():
    r = parse_player_prop_market(
        "KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9", BOS_LAD_EVENT, "Emmet Sheehan: 8+ strikeouts?",
    )
    assert r["threshold"] == 9
    assert r["titleThreshold"] == 8
    assert r["thresholdMismatch"] is True
