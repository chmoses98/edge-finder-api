#!/usr/bin/env python3
"""
tests/edgelab/test_mlb_boxscore.py
=======================================
Coverage for lib/edgelab/mlb_boxscore.py's pure parsing helpers (GitHub
issue #43). fetch_game_feed itself is a thin network adapter (mirrors
scripts/fetch_lineups.py's fetch_json convention) -- not exercised here
with live network, only its pure siblings.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.mlb_boxscore import (
    extract_boxscore_teams,
    extract_game_status,
    fetch_game_feed,
    is_final_status,
    payload_hash,
)


def _feed(status="Final", away_players=None, home_players=None):
    return {
        "gameData": {"status": {"detailedState": status}},
        "liveData": {"boxscore": {"teams": {
            "away": {"players": away_players or {}},
            "home": {"players": home_players or {}},
        }}},
    }


def test_extract_game_status():
    assert extract_game_status(_feed("Final")) == "Final"


def test_extract_game_status_none_for_falsy_feed():
    assert extract_game_status(None) is None
    assert extract_game_status({}) is None


def test_is_final_status_true_values():
    for status in ("Final", "Game Over", "Completed Early"):
        assert is_final_status(status) is True


def test_is_final_status_false_for_live_suspended_delayed_postponed():
    for status in ("In Progress", "Suspended", "Delayed", "Postponed", "Preview", "Scheduled", None):
        assert is_final_status(status) is False


def test_extract_boxscore_teams():
    feed = _feed(home_players={"ID1": {"person": {"id": 1, "fullName": "Someone"}}})
    teams = extract_boxscore_teams(feed)
    assert "away" in teams and "home" in teams
    assert teams["home"]["players"]["ID1"]["person"]["fullName"] == "Someone"


def test_extract_boxscore_teams_empty_for_falsy_feed():
    assert extract_boxscore_teams(None) == {}
    assert extract_boxscore_teams({}) == {}


def test_payload_hash_deterministic():
    feed = _feed()
    assert payload_hash(feed) == payload_hash(_feed())  # equal content -> equal hash


def test_payload_hash_differs_for_different_content():
    assert payload_hash(_feed("Final")) != payload_hash(_feed("In Progress"))


def test_payload_hash_none_for_falsy_feed():
    assert payload_hash(None) is None


def test_fetch_game_feed_none_for_falsy_game_pk():
    assert fetch_game_feed(None) is None
    assert fetch_game_feed(0) is None
