#!/usr/bin/env python3
"""
tests/edgelab/test_query.py
===============================
Coverage for lib/edgelab/query.py -- the cross-chat read-only query
interface (Canonical Placed-Bet Ledger milestone, requirement 9).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import query

BETS = [
    {"betId": "a", "gameDate": "2026-08-03", "entryTimestamp": "2026-08-03T18:00:00Z",
     "marketFamily": "FAMILY_GAME_RESULT", "status": "pending", "stake": 5, "source": "MANUAL",
     "modelEvaluationId": None, "modelSupported": None, "gameId": "g1", "recommendationId": None,
     "snapshotId": None, "recordStatus": "ACTIVE"},
    {"betId": "b", "gameDate": "2026-08-03", "entryTimestamp": "2026-08-03T19:00:00Z",
     "marketFamily": "FAMILY_INNING_RESULT", "status": "settled", "stake": 10, "source": "MODEL",
     "modelEvaluationId": "me1", "modelSupported": True, "gameId": "g2", "recommendationId": "rec1",
     "snapshotId": "snap1", "recordStatus": "ACTIVE"},
    {"betId": "c", "gameDate": "2026-08-02", "entryTimestamp": "2026-08-02T19:00:00Z",
     "marketFamily": "FAMILY_GAME_RESULT", "status": "void", "stake": 2, "source": "MANUAL",
     "modelEvaluationId": None, "modelSupported": None, "gameId": "g1", "recommendationId": None,
     "snapshotId": None, "recordStatus": "ACTIVE"},
    {"betId": "d", "gameDate": "2026-08-03", "entryTimestamp": "2026-08-03T20:00:00Z",
     "marketFamily": "FAMILY_GAME_RESULT", "status": "pending", "stake": 999, "source": "MANUAL",
     "modelEvaluationId": None, "modelSupported": None, "gameId": "g3", "recommendationId": None,
     "snapshotId": None, "recordStatus": "CANCELLED"},
]


def test_by_date():
    assert {b["betId"] for b in query.by_date(BETS, "2026-08-03")} == {"a", "b", "d"}


def test_by_date_range():
    assert {b["betId"] for b in query.by_date_range(BETS, "2026-08-01", "2026-08-02")} == {"c"}


def test_unsettled_settled_void():
    assert {b["betId"] for b in query.unsettled(BETS)} == {"a", "d"}
    assert {b["betId"] for b in query.settled(BETS)} == {"b"}
    assert {b["betId"] for b in query.voided(BETS)} == {"c"}


def test_by_market_family():
    assert {b["betId"] for b in query.by_market_family(BETS, "FAMILY_GAME_RESULT")} == {"a", "c", "d"}


def test_by_game():
    assert {b["betId"] for b in query.by_game(BETS, "g1")} == {"a", "c"}


def test_linked_to_snapshot_and_recommendation():
    assert {b["betId"] for b in query.linked_to_snapshot(BETS)} == {"b"}
    assert {b["betId"] for b in query.linked_to_snapshot(BETS, "snap1")} == {"b"}
    assert {b["betId"] for b in query.linked_to_recommendation(BETS)} == {"b"}


def test_manual_without_model_support():
    ids_ = {b["betId"] for b in query.manual_without_model_support(BETS)}
    assert ids_ == {"a", "c", "d"}


def test_active_excludes_cancelled():
    assert "d" not in {b["betId"] for b in query.active(BETS)}


def test_todays_card_totals_and_excludes_cancelled():
    card = query.todays_card(BETS, "2026-08-03")
    assert card["betCount"] == 2  # a, b -- d is CANCELLED
    assert card["totalStaked"] == 15
    assert card["pendingCount"] == 1
    assert card["settledCount"] == 1
    assert "FAMILY_GAME_RESULT" in card["byMarketFamily"]
    assert "FAMILY_INNING_RESULT" in card["byMarketFamily"]


def test_render_human_nonempty():
    text = query.render_human(BETS[:1], title="Test")
    assert "Test" in text
    assert "a" in text


def test_render_human_handles_empty_list():
    text = query.render_human([])
    assert "(none)" in text
