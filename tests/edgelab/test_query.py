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


# ---------------------------------------------------------------------------
# Full-Universe Research Engine
# ---------------------------------------------------------------------------
# T1: team_total 3.5 -- observed, recommended, BET PLACED, settled YES.
# T2: team_total 4.5 -- observed, recommended (WATCH), NOT bet, settled NO.
# T3: pitcher_strikeouts 6.5 -- observed, NEVER recommended, settlement
#     unresolved (player-prop settlement is a documented, honest gap).
# T4: team_total 5.5 -- observed, never recommended, VOID settlement, but a
#     bet still exists on it with no CLV computed (bet-exists-but-CLV-
#     unavailable, distinct from no-bet-at-all).
# T5: game_total 8.5 -- observed, never recommended, settled YES -- exists
#     purely to prove market-family filtering excludes it from team_total
#     queries.
RESEARCH_OBSERVATIONS = [
    {"marketTicker": "T1", "gameId": "g1", "marketFamily": "team_total", "marketHorizon": "FULL_GAME",
     "threshold": 3.5, "team": "SF", "player": None, "comparisonOperator": "OVER", "capturedAt": "2026-08-03T22:00:00Z"},
    {"marketTicker": "T2", "gameId": "g1", "marketFamily": "team_total", "marketHorizon": "FULL_GAME",
     "threshold": 4.5, "team": "SF", "player": None, "comparisonOperator": "OVER", "capturedAt": "2026-08-03T22:00:00Z"},
    {"marketTicker": "T3", "gameId": "g1", "marketFamily": "pitcher_strikeouts", "marketHorizon": "FULL_GAME",
     "threshold": 6.5, "team": None, "player": "P1", "comparisonOperator": "AT_LEAST", "capturedAt": "2026-08-03T21:00:00Z"},
    {"marketTicker": "T4", "gameId": "g1", "marketFamily": "team_total", "marketHorizon": "FULL_GAME",
     "threshold": 5.5, "team": "SF", "player": None, "comparisonOperator": "OVER", "capturedAt": "2026-08-03T21:00:00Z"},
    {"marketTicker": "T5", "gameId": "g1", "marketFamily": "game_total", "marketHorizon": "FULL_GAME",
     "threshold": 8.5, "team": None, "player": None, "comparisonOperator": "OVER", "capturedAt": "2026-08-03T21:00:00Z"},
]

RESEARCH_SETTLEMENTS = [
    {"marketTicker": "T1", "marketFamily": "team_total", "settlementStatus": "SETTLED", "result": "YES",
     "wasRecommended": True, "wasPlaced": True, "hypotheticalReturnsByCheckpoint": [
         {"checkpoint": "FIRST_DAILY", "yesPrice": 0.55, "hypotheticalYesReturn": 0.8182},
         {"checkpoint": "CLOSING", "yesPrice": 0.5, "hypotheticalYesReturn": 1.0},
     ]},
    {"marketTicker": "T2", "marketFamily": "team_total", "settlementStatus": "SETTLED", "result": "NO",
     "wasRecommended": True, "wasPlaced": False, "hypotheticalReturnsByCheckpoint": [
         {"checkpoint": "CLOSING", "yesPrice": 0.4, "hypotheticalYesReturn": -1.0},
     ]},
    {"marketTicker": "T3", "marketFamily": "pitcher_strikeouts", "settlementStatus": "SETTLEMENT_UNRESOLVED",
     "unavailableReason": "player_prop_settlement_not_implemented", "result": None,
     "wasRecommended": False, "wasPlaced": False, "hypotheticalReturnsByCheckpoint": []},
    {"marketTicker": "T4", "marketFamily": "team_total", "settlementStatus": "VOID", "result": None,
     "wasRecommended": False, "wasPlaced": True, "hypotheticalReturnsByCheckpoint": []},
    {"marketTicker": "T5", "marketFamily": "game_total", "settlementStatus": "SETTLED", "result": "YES",
     "wasRecommended": False, "wasPlaced": False, "hypotheticalReturnsByCheckpoint": [
         {"checkpoint": "CLOSING", "yesPrice": 0.45, "hypotheticalYesReturn": 1.2222},
     ]},
]

RESEARCH_EVALUATIONS = [
    {"marketTicker": "T1", "evaluationStatus": "EVALUATED", "modelFairProbability": 60,
     "marketImpliedProbability": 50, "estimatedEdge": 10, "confidence": "HIGH", "thesisTags": ["STARTER_EDGE"]},
    {"marketTicker": "T2", "evaluationStatus": "NOT_EVALUATED", "modelFairProbability": None,
     "marketImpliedProbability": None, "estimatedEdge": None, "confidence": None, "thesisTags": []},
]

RESEARCH_RECOMMENDATIONS = [
    {"marketTicker": "T1", "status": "BET_PLACED", "betPlaced": True, "passReason": None},
    {"marketTicker": "T2", "status": "WATCH", "betPlaced": False, "passReason": None},
]

RESEARCH_BETS = [
    {"betId": "bet1", "marketTicker": "T1", "side": "YES", "stake": 10, "entryPrice": 0.52,
     "result": "WIN", "netProfitLoss": 8.0, "clv": 2.0, "status": "settled", "recordStatus": "ACTIVE"},
    {"betId": "bet4", "marketTicker": "T4", "side": "YES", "stake": 5, "entryPrice": 0.3,
     "result": "VOID", "netProfitLoss": 0.0, "clv": None, "status": "void", "recordStatus": "ACTIVE"},
]

RESEARCH_GAMES = [{"gameId": "g1", "gameDate": "2026-08-03"}]


def _build_full_rows():
    return query.build_research_rows(
        RESEARCH_OBSERVATIONS, RESEARCH_SETTLEMENTS, evaluations=RESEARCH_EVALUATIONS,
        recommendations=RESEARCH_RECOMMENDATIONS, bets=RESEARCH_BETS, games=RESEARCH_GAMES,
    )


def test_build_research_rows_covers_full_observed_market_population():
    """Every observed ticker gets a row -- never recommended (T3), recommended-not-bet (T2), and actually-bet (T1) alike."""
    rows = _build_full_rows()
    assert {r["marketTicker"] for r in rows} == {"T1", "T2", "T3", "T4", "T5"}
    by_ticker = {r["marketTicker"]: r for r in rows}
    assert by_ticker["T1"]["gameDate"] == "2026-08-03"
    assert by_ticker["T3"]["wasRecommended"] is False
    assert by_ticker["T2"]["wasRecommended"] is True and by_ticker["T2"]["wasPlaced"] is False
    assert by_ticker["T1"]["wasPlaced"] is True


def test_standardized_pregame_price_prefers_closing_over_other_checkpoints():
    t1_settlement = RESEARCH_SETTLEMENTS[0]
    price = query.standardized_pregame_price(t1_settlement)
    assert price["checkpoint"] == "CLOSING"
    assert price["yesPrice"] == 0.5


def test_standardized_pregame_price_never_uses_post_start():
    settlement = {"hypotheticalReturnsByCheckpoint": [
        {"checkpoint": "POST_START", "yesPrice": 0.9, "hypotheticalYesReturn": -1.0},
        {"checkpoint": "INTERMEDIATE", "yesPrice": 0.7, "hypotheticalYesReturn": -1.0},
    ]}
    assert query.standardized_pregame_price(settlement) is None


def test_missing_clv_and_fair_probability_remain_unavailable_not_fabricated():
    rows = {r["marketTicker"]: r for r in _build_full_rows()}
    # T2 has no bet at all -- betClvStatus is None (never guessed "UNAVAILABLE"), and the
    # model never evaluated it (NOT_EVALUATED) -- modelFairProbability stays None.
    assert rows["T2"]["betClvStatus"] is None
    assert rows["T2"]["modelFairProbability"] is None
    # T4 HAS a bet, but that bet's own clv field is null -- betClvStatus is explicitly
    # "UNAVAILABLE", distinct from "no bet at all".
    assert rows["T4"]["wasPlaced"] is True
    assert rows["T4"]["betClvStatus"] == "UNAVAILABLE"


def test_market_family_filtering():
    rows = query.filter_research_rows(_build_full_rows(), market_family="team_total")
    assert {r["marketTicker"] for r in rows} == {"T1", "T2", "T4"}


def test_threshold_filtering_exact_and_range():
    team_total_rows = query.filter_research_rows(_build_full_rows(), market_family="team_total")
    exact = query.filter_research_rows(team_total_rows, threshold=4.5)
    assert {r["marketTicker"] for r in exact} == {"T2"}
    ranged = query.filter_research_rows(team_total_rows, min_threshold=4.0, max_threshold=5.0)
    assert {r["marketTicker"] for r in ranged} == {"T2"}


def test_price_and_edge_filtering():
    team_total_rows = query.filter_research_rows(_build_full_rows(), market_family="team_total")
    by_price = query.filter_research_rows(team_total_rows, min_price=0.45, max_price=0.55)
    assert {r["marketTicker"] for r in by_price} == {"T1"}  # T2's 0.4 is below min; T4 has no price at all
    by_edge = query.filter_research_rows(_build_full_rows(), min_edge=5)
    assert {r["marketTicker"] for r in by_edge} == {"T1"}


def test_recommended_vs_passed_vs_bet_separation():
    agg = query.aggregate_research_rows(_build_full_rows())
    assert agg["recommendationBreakdown"] == {
        "neverRecommended": 3,  # T3, T4, T5
        "recommendedNotBet": 1,  # T2
        "betPlaced": 2,  # T1, T4 (a bet exists on T4 even though the market later voided)
        "recommendationStatusUnknown": 0,
    }
    # Actual bet P/L is reported separately and never blended into the hypothetical figures.
    assert agg["actualBetPerformance"]["betCount"] == 2
    assert agg["actualBetPerformance"]["stake"] == 15.0


def test_standardized_hypothetical_roi_math_excludes_void():
    team_total_rows = query.filter_research_rows(_build_full_rows(), market_family="team_total")
    agg = query.aggregate_research_rows(team_total_rows)
    # T1 wins (+1.0), T2 loses (-1.0); T4 is VOID and contributes to neither.
    assert agg["sampleSize"] == 2
    assert agg["wins"] == 1
    assert agg["losses"] == 1
    assert agg["void"] == 1
    assert agg["standardizedHypotheticalStake"] == 2.0
    assert agg["hypotheticalNetPnl"] == 0.0
    assert agg["hypotheticalReturn"] == 2.0
    assert agg["hypotheticalRoiPct"] == 0.0


def test_unresolved_settlement_excluded_from_roi_but_counted():
    agg = query.aggregate_research_rows(_build_full_rows())
    assert agg["unresolved"] == 1  # T3
    assert agg["sampleSize"] == 3  # T1, T2, T5 -- T3's SETTLEMENT_UNRESOLVED never enters the ROI math
    assert agg["observedCount"] == 5


def test_small_sample_status_tiers_reuse_calibration_module_thresholds():
    def _priced_rows(n):
        return [
            {"settlementStatus": "SETTLED", "settlementResult": "YES", "hypotheticalYesReturn": 0.5,
             "estimatedEdge": None, "evaluationStatus": None, "modelFairProbability": None,
             "wasPlaced": False, "wasRecommended": None, "betClvStatus": None, "betClv": None,
             "betResult": None, "betStake": None, "betNetProfitLoss": None}
            for _ in range(n)
        ]

    assert query.aggregate_research_rows(_priced_rows(5))["sampleSizeStatus"] == "INSUFFICIENT_SAMPLE"
    assert query.aggregate_research_rows(_priced_rows(5))["smallSampleWarning"] is True
    assert query.aggregate_research_rows(_priced_rows(5))["smallSampleMessage"] is not None
    assert query.aggregate_research_rows(_priced_rows(25))["sampleSizeStatus"] == "DESCRIPTIVE_ONLY"
    assert query.aggregate_research_rows(_priced_rows(150))["sampleSizeStatus"] == "CALIBRATED"
    assert query.aggregate_research_rows(_priced_rows(150))["smallSampleWarning"] is False


def test_aggregate_research_rows_by_group_by():
    breakdown = query.aggregate_research_rows_by(_build_full_rows(), "marketFamily")
    assert set(breakdown) == {"team_total", "pitcher_strikeouts", "game_total"}
    assert breakdown["team_total"]["observedCount"] == 3
    assert breakdown["pitcher_strikeouts"]["unresolved"] == 1


def test_disagreement_label_and_filter():
    rows = _build_full_rows()
    by_ticker = {r["marketTicker"]: r for r in rows}
    assert by_ticker["T1"]["modelVsMarketDisagreement"] == "MODEL_HIGHER"  # 60 vs 50
    assert by_ticker["T2"]["modelVsMarketDisagreement"] is None  # no evaluation probabilities at all
    filtered = query.filter_research_rows(rows, disagreement="MODEL_HIGHER")
    assert {r["marketTicker"] for r in filtered} == {"T1"}


def test_brier_score_uses_only_evaluated_rows_with_a_settled_result():
    agg = query.aggregate_research_rows(_build_full_rows())
    # Only T1 is EVALUATED with a modelFairProbability and a settled YES/NO result.
    assert agg["brierSampleSize"] == 1
    assert agg["brierScore"] == round((0.60 - 1.0) ** 2, 6)
