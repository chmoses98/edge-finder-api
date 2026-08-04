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
    # "d" is pending but CANCELLED -- a cancelled bet is not a genuinely
    # open wager, so it must be excluded here even though status=="pending".
    assert {b["betId"] for b in query.unsettled(BETS)} == {"a"}
    assert {b["betId"] for b in query.settled(BETS)} == {"b"}
    assert {b["betId"] for b in query.voided(BETS)} == {"c"}


def test_unsettled_excludes_cancelled_bets_even_though_status_is_pending():
    bets = [
        {"betId": "x", "status": "pending", "recordStatus": "ACTIVE"},
        {"betId": "y", "status": "pending", "recordStatus": "CANCELLED"},
    ]
    assert {b["betId"] for b in query.unsettled(bets)} == {"x"}


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


# ---------------------------------------------------------------------------
# Part 6: Research Query Surface
# ---------------------------------------------------------------------------

OBSERVATIONS = [
    {"marketTicker": "T1", "gameId": "g1", "marketFamily": "team_total", "marketHorizon": "FULL_GAME",
     "threshold": 3.5, "team": "SF", "player": None, "checkpoint": "FIRST_DAILY", "capturedAt": "2026-08-03T18:00:00Z",
     "isClosingCandidate": True, "yesAsk": 0.50},
    {"marketTicker": "T2", "gameId": "g1", "marketFamily": "team_total", "marketHorizon": "FULL_GAME",
     "threshold": 4.5, "team": "SF", "player": None, "checkpoint": "T_MINUS_30", "capturedAt": "2026-08-03T19:00:00Z",
     "isClosingCandidate": True, "yesAsk": 0.45},
    {"marketTicker": "T3", "gameId": "g1", "marketFamily": "pitcher_strikeouts", "marketHorizon": "FULL_GAME",
     "threshold": 6.5, "team": None, "player": "P1", "checkpoint": "LINEUP_CONFIRMATION", "capturedAt": "2026-08-03T18:30:00Z",
     "isClosingCandidate": False, "yesAsk": 0.52},
    {"marketTicker": "T1", "gameId": "g1", "marketFamily": "team_total", "marketHorizon": "FULL_GAME",
     "threshold": 3.5, "team": "SF", "player": None, "checkpoint": "CLOSING", "capturedAt": "2026-08-03T22:00:00Z",
     "isClosingCandidate": True, "yesAsk": 0.60},
]

RECOMMENDATIONS = [
    {"marketTicker": "T1", "status": "RECOMMENDED", "betPlaced": False},
    {"marketTicker": "T3", "status": "NOT_EVALUATED", "betPlaced": False},
]

SETTLEMENTS = [
    {"marketTicker": "T1", "marketFamily": "team_total", "settlementStatus": "SETTLED", "result": "YES",
     "hypotheticalReturnsByCheckpoint": [{"checkpoint": "CLOSING", "yesPrice": 0.6, "hypotheticalYesReturn": 0.6667}]},
    {"marketTicker": "T3", "marketFamily": "pitcher_strikeouts", "settlementStatus": "SETTLEMENT_UNRESOLVED",
     "unavailableReason": "player_prop_settlement_not_implemented", "result": None},
]

GAMES = [{"gameId": "g1", "gameDate": "2026-08-03"}]


def test_observed_markets_for_game():
    rows = query.observed_markets_for_game(OBSERVATIONS, "g1")
    assert len(rows) == 4


def test_alternate_thresholds_sorted_by_threshold():
    rows = query.alternate_thresholds(OBSERVATIONS, "team_total")
    assert [r["threshold"] for r in rows] == [3.5, 4.5]


def test_pitcher_strikeout_closings_reports_unresolved_not_fabricated():
    rows = query.pitcher_strikeout_closings(OBSERVATIONS, SETTLEMENTS)
    assert rows == [{
        "marketTicker": "T3", "settlementStatus": "SETTLEMENT_UNRESOLVED",
        "result": None, "unavailableReason": "player_prop_settlement_not_implemented",
    }]


def test_checkpoint_price_comparison_first_lineup_closing():
    result = query.checkpoint_price_comparison(OBSERVATIONS, "T1")
    assert result["firstObserved"]["checkpoint"] == "FIRST_DAILY"
    assert result["closing"]["checkpoint"] == "CLOSING"
    assert result["lineupConfirmed"] is None  # T1 never had a LINEUP_CONFIRMATION tick -- not guessed


def test_observed_never_recommended():
    assert query.observed_never_recommended(OBSERVATIONS, RECOMMENDATIONS) == ["T2"]


def test_recommended_not_placed():
    rows = query.recommended_not_placed(RECOMMENDATIONS, [])
    assert [r["marketTicker"] for r in rows] == ["T1"]


def test_manual_bets_without_slate():
    bets = [
        {"betId": "x", "gameId": "g1", "recordStatus": "ACTIVE"},
        {"betId": "y", "gameId": "g_unknown", "recordStatus": "ACTIVE"},
    ]
    rows = query.manual_bets_without_slate(bets, GAMES)
    assert [b["betId"] for b in rows] == ["y"]


def test_performance_by_family_all_observed_includes_unbet_markets():
    result = query.performance_by_family_all_observed(SETTLEMENTS, [])
    assert result["team_total"]["marketsSettled"] == 1
    assert result["team_total"]["hypotheticalReturnSum"] == 0.6667
    assert result["team_total"]["betsPlaced"] == 0
    assert "pitcher_strikeouts" not in result  # SETTLEMENT_UNRESOLVED is never counted as a settled market


def test_market_corpus_capture_for_bet():
    bet = {"marketObservationLinkage": {"linkageStatus": "LINKED", "marketCorpusRunId": "RUN1"}}
    runs_by_id = {"RUN1": {"runId": "RUN1", "status": "success"}}
    result = query.market_corpus_capture_for_bet(bet, runs_by_id)
    assert result["captureRun"]["status"] == "success"


def test_postmortem_for_date_and_for_bet():
    pm = {"gameDate": "2026-08-03", "linkedBetIds": ["betA"]}
    assert query.postmortem_for_date({"2026-08-03": pm}, "2026-08-03") is pm
    assert query.postmortem_for_bet("betA", [pm]) is pm
    assert query.postmortem_for_bet("betZ", [pm]) is None
