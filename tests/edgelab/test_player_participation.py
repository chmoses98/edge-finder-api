#!/usr/bin/env python3
"""
tests/edgelab/test_player_participation.py
================================================
Coverage for lib/edgelab/player_participation.py (GitHub issue #43
correction round): being listed in the boxscore is not proof of
participation -- only positive gamesPlayed/gamesPitched/inningsPitched
evidence is.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.player_participation import (
    RESOLVED,
    UNVERIFIED,
    verify_hitter_participation,
    verify_participation,
    verify_pitcher_participation,
)


def test_pitcher_participation_resolved_via_games_pitched():
    status, reason, evidence = verify_pitcher_participation(
        {"stats": {"pitching": {"gamesPitched": 1, "strikeOuts": 0}}},
    )
    assert status == RESOLVED
    assert reason is None


def test_pitcher_participation_resolved_via_innings_pitched():
    status, reason, evidence = verify_pitcher_participation(
        {"stats": {"pitching": {"inningsPitched": "0.1", "strikeOuts": 0}}},
    )
    assert status == RESOLVED


def test_pitcher_participation_unverified_when_listed_but_unused():
    """A pitcher on the active roster who never entered the game: listed, but zero everywhere."""
    status, reason, evidence = verify_pitcher_participation(
        {"stats": {"pitching": {}}},
    )
    assert status == UNVERIFIED
    assert reason == "player_participation_unverified"


def test_pitcher_participation_unverified_when_entry_entirely_absent():
    status, reason, evidence = verify_pitcher_participation({})
    assert status == UNVERIFIED
    assert reason == "player_participation_unverified"


def test_pitcher_participation_unverified_for_zero_innings_pitched_string():
    status, reason, evidence = verify_pitcher_participation(
        {"stats": {"pitching": {"inningsPitched": "0.0"}}},
    )
    assert status == UNVERIFIED


def test_official_pitcher_appearance_with_zero_strikeouts_is_verified():
    """The exact scenario the task calls out: appeared, but recorded zero strikeouts."""
    status, reason, evidence = verify_pitcher_participation(
        {"stats": {"pitching": {"gamesPitched": 1, "strikeOuts": 0, "inningsPitched": "1.0"}}},
    )
    assert status == RESOLVED
    assert evidence["gamesPitched"] == 1


def test_hitter_participation_resolved_via_games_played():
    status, reason, evidence = verify_hitter_participation(
        {"stats": {"batting": {"gamesPlayed": 1, "hits": 0}}},
    )
    assert status == RESOLVED


def test_hitter_participation_resolved_via_at_bats():
    status, reason, evidence = verify_hitter_participation(
        {"stats": {"batting": {"atBats": 3, "hits": 0}}},
    )
    assert status == RESOLVED


def test_official_batter_appearance_with_zero_hits_is_verified():
    status, reason, evidence = verify_hitter_participation(
        {"stats": {"batting": {"gamesPlayed": 1, "atBats": 4, "hits": 0}}},
    )
    assert status == RESOLVED


def test_official_pinch_runner_appearance_with_zero_plate_appearances_is_verified():
    """
    A pinch runner who entered, scored/stole a base, but never batted:
    plateAppearances/atBats are both 0, but gamesPlayed is still 1 --
    must be verified, not treated as a non-participant.
    """
    status, reason, evidence = verify_hitter_participation(
        {"stats": {"batting": {"gamesPlayed": 1, "plateAppearances": 0, "atBats": 0,
                                "runs": 1, "stolenBases": 1, "hits": 0}}},
    )
    assert status == RESOLVED
    assert evidence["gamesPlayed"] == 1


def test_hitter_participation_unverified_when_listed_but_unused():
    """A bench player who never entered: listed on the roster, everything zero/absent."""
    status, reason, evidence = verify_hitter_participation({"stats": {"batting": {}}})
    assert status == UNVERIFIED
    assert reason == "player_participation_unverified"


def test_hitter_participation_unverified_when_entry_absent():
    status, reason, evidence = verify_hitter_participation({})
    assert status == UNVERIFIED


def test_hitter_participation_never_inferred_from_zero_filled_stat_object_alone():
    """A batting sub-object that IS present but entirely zero -- the exact shape of an unused bench player's entry."""
    status, reason, evidence = verify_hitter_participation(
        {"stats": {"batting": {"gamesPlayed": 0, "atBats": 0, "hits": 0, "plateAppearances": 0}}},
    )
    assert status == UNVERIFIED


def test_missing_participation_fields_entirely_is_unverified():
    status, reason, evidence = verify_hitter_participation({"stats": {}})
    assert status == UNVERIFIED
    assert evidence == {"gamesPlayed": None, "plateAppearances": None, "atBats": None}


def test_verify_participation_dispatches_by_stat_category():
    pitcher_entry = {"stats": {"pitching": {"gamesPitched": 1}}}
    hitter_entry = {"stats": {"batting": {"gamesPlayed": 1}}}
    assert verify_participation(pitcher_entry, "pitching")[0] == RESOLVED
    assert verify_participation(hitter_entry, "batting")[0] == RESOLVED


def test_verify_participation_unrecognized_category():
    status, reason, evidence = verify_participation({}, "something_else")
    assert status == UNVERIFIED
    assert reason == "unrecognized_stat_category"
