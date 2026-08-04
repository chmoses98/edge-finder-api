#!/usr/bin/env python3
"""
tests/edgelab/test_player_resolution.py
============================================
Coverage for lib/edgelab/player_resolution.py (GitHub issue #43):
exact-game, correct-team, exact-normalized-name resolution -- never an
unrestricted league-wide fuzzy match.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.player_resolution import AMBIGUOUS, NOT_FOUND, RESOLVED, resolve_player_in_game
from lib.research.player_prop_parser import normalized_name_variants


def _player(pid, name, jersey=None):
    return {"person": {"id": pid, "fullName": name}, "jerseyNumber": jersey, "stats": {}}


def _boxscore(away_players=None, home_players=None):
    return {
        "away": {"players": {f"ID{p['person']['id']}": p for p in (away_players or [])}},
        "home": {"players": {f"ID{p['person']['id']}": p for p in (home_players or [])}},
    }


def test_unique_exact_game_player_match():
    boxscore = _boxscore(home_players=[_player(660271, "Shohei Ohtani", "17")])
    result = resolve_player_in_game(
        boxscore, normalized_name_variants("Shohei Ohtani"),
        team_abbr="LAD", away_abbr="BOS", home_abbr="LAD",
    )
    assert result["status"] == RESOLVED
    assert result["candidate"]["playerId"] == 660271


def test_correct_team_disambiguation_same_last_name_both_sides():
    """Two different players both surnamed 'Smith' on opposite rosters -- team scoping picks the right one."""
    boxscore = _boxscore(
        away_players=[_player(1, "John Smith")],
        home_players=[_player(2, "Bob Smith")],
    )
    result = resolve_player_in_game(
        boxscore, normalized_name_variants("Bob Smith"),
        team_abbr="LAD", away_abbr="BOS", home_abbr="LAD",
    )
    assert result["status"] == RESOLVED
    assert result["candidate"]["playerId"] == 2


def test_unicode_normalization_matches_accented_boxscore_name():
    boxscore = _boxscore(away_players=[_player(5, "Carlos Narváez")])
    result = resolve_player_in_game(
        boxscore, normalized_name_variants("Carlos Narvaez"),  # title-side spelling without accent
        team_abbr="BOS", away_abbr="BOS", home_abbr="LAD",
    )
    assert result["status"] == RESOLVED
    assert result["candidate"]["playerId"] == 5


def test_ambiguous_same_name_candidates_both_teams_unresolved():
    boxscore = _boxscore(
        away_players=[_player(1, "Chris Taylor")],
        home_players=[_player(2, "Chris Taylor")],
    )
    result = resolve_player_in_game(
        boxscore, normalized_name_variants("Chris Taylor"),
        team_abbr=None, away_abbr="BOS", home_abbr="LAD",  # team unresolved -- searches both rosters
    )
    assert result["status"] == AMBIGUOUS
    assert len(result["candidates"]) == 2


def test_ambiguous_resolved_via_jersey_number_corroboration():
    boxscore = _boxscore(
        away_players=[_player(1, "Chris Taylor", jersey="3")],
        home_players=[_player(2, "Chris Taylor", jersey="9")],
    )
    result = resolve_player_in_game(
        boxscore, normalized_name_variants("Chris Taylor"),
        team_abbr=None, away_abbr="BOS", home_abbr="LAD", ticker_numeric_suffix="9",
    )
    assert result["status"] == RESOLVED
    assert result["candidate"]["playerId"] == 2
    assert result["corroboratedBy"] == "jersey_number"


def test_ambiguous_jersey_corroboration_still_ambiguous_if_it_doesnt_disambiguate():
    boxscore = _boxscore(
        away_players=[_player(1, "Chris Taylor", jersey="3")],
        home_players=[_player(2, "Chris Taylor", jersey="9")],
    )
    result = resolve_player_in_game(
        boxscore, normalized_name_variants("Chris Taylor"),
        team_abbr=None, away_abbr="BOS", home_abbr="LAD", ticker_numeric_suffix="99",
    )
    assert result["status"] == AMBIGUOUS


def test_player_absent_from_final_boxscore_is_not_found():
    boxscore = _boxscore(home_players=[_player(1, "Someone Else")])
    result = resolve_player_in_game(
        boxscore, normalized_name_variants("Shohei Ohtani"),
        team_abbr="LAD", away_abbr="BOS", home_abbr="LAD",
    )
    assert result["status"] == NOT_FOUND
    assert result["candidates"] == []


def test_numeric_ticker_suffix_never_treated_as_mlb_player_id():
    """
    A jersey-number-style ticker suffix must never itself become the
    resolved playerId -- only lib.edgelab.mlb_boxscore's real
    person.id ever does. Corroboration only narrows among name-based
    candidates; it never substitutes for one.
    """
    boxscore = _boxscore(home_players=[_player(660271, "Shohei Ohtani", jersey="17")])
    result = resolve_player_in_game(
        boxscore, normalized_name_variants("Shohei Ohtani"),
        team_abbr="LAD", away_abbr="BOS", home_abbr="LAD", ticker_numeric_suffix="17",
    )
    assert result["status"] == RESOLVED
    assert result["candidate"]["playerId"] == 660271  # the real MLBAM id, not "17"


def test_empty_name_variants_is_not_found_never_a_guess():
    boxscore = _boxscore(home_players=[_player(1, "Shohei Ohtani")])
    result = resolve_player_in_game(boxscore, frozenset(), team_abbr="LAD", away_abbr="BOS", home_abbr="LAD")
    assert result["status"] == NOT_FOUND


def test_team_unresolved_searches_both_rosters_not_wider():
    boxscore = _boxscore(away_players=[_player(1, "Unique Player")])
    result = resolve_player_in_game(
        boxscore, normalized_name_variants("Unique Player"), team_abbr=None, away_abbr="BOS", home_abbr="LAD",
    )
    assert result["status"] == RESOLVED
    assert result["candidate"]["playerId"] == 1


def test_deterministic():
    boxscore = _boxscore(home_players=[_player(1, "Shohei Ohtani")])
    variants = normalized_name_variants("Shohei Ohtani")
    r1 = resolve_player_in_game(boxscore, variants, team_abbr="LAD", away_abbr="BOS", home_abbr="LAD")
    r2 = resolve_player_in_game(boxscore, variants, team_abbr="LAD", away_abbr="BOS", home_abbr="LAD")
    assert r1 == r2
