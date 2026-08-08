#!/usr/bin/env python3
"""
tests/edgelab/test_reports.py
=================================
Coverage for lib/edgelab/reports.py: aggregation counts, markdown
rendering, and the calibration export.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.reports import build_calibration_rows, build_daily_report, build_postmortem, render_markdown, render_postmortem_markdown

DATE = "2026-07-31"


def _sample_inputs():
    games = [{"gameId": "g1"}, {"gameId": "g2"}]
    markets = [{"marketTicker": f"T{i}"} for i in range(5)]
    observations = [
        {"marketFamily": "game_result"}, {"marketFamily": "game_result"},
        {"marketFamily": "team_total"},
    ]
    recommendations = [
        {"status": "RECOMMENDED", "marketTicker": "T1", "modelFairProbability": 60, "marketImpliedProbability": 55, "marketFamily": "game_result"},
        {"status": "BET_PLACED", "marketTicker": "T2", "modelFairProbability": 70, "marketImpliedProbability": 60, "marketFamily": "game_result"},
        {"status": "PASS_NO_EDGE", "marketTicker": "T3", "modelFairProbability": None},
        {"status": "PASS_LOW_LIQUIDITY", "marketTicker": "T4", "modelFairProbability": None},
        {"status": "NOT_EVALUATED", "marketTicker": "T5", "modelFairProbability": None},
        {"status": "INSUFFICIENT_MODEL_SUPPORT", "marketTicker": "T6", "modelFairProbability": None},
    ]
    clv_quotes = [{"isClosingQuote": True}, {"isClosingQuote": True}, {"isClosingQuote": False}]
    settlements = [
        {"marketTicker": "T1", "settlementStatus": "SETTLED", "result": "YES", "unavailableReason": None},
        {"marketTicker": "T2", "settlementStatus": "SETTLED", "result": "NO", "unavailableReason": None},
        {"marketTicker": "T3", "settlementStatus": "VOID", "result": None, "unavailableReason": None},
        {"marketTicker": "T4", "settlementStatus": "SETTLEMENT_UNRESOLVED", "result": None, "unavailableReason": "missing_final_score"},
        {"marketTicker": "T5", "settlementStatus": "SETTLEMENT_UNRESOLVED", "result": None, "unavailableReason": "player_prop_settlement_not_implemented"},
    ]
    bets = [
        {"clv": 2.5, "betId": "b1"},
        {"clv": -1.0, "betId": "b2"},
        {"clv": None, "betId": "b3"},
    ]
    research_runs = [
        {"errors": ["fetch failed once"], "warnings": ["NEW_UNCLASSIFIED_MLB_SERIES: KXFOO (Some title)", "5 legacy bets skipped"]},
    ]
    return games, markets, observations, recommendations, clv_quotes, settlements, bets, research_runs


def test_build_daily_report_counts():
    report = build_daily_report(DATE, *_sample_inputs())
    assert report["gamesObserved"] == 2
    assert report["marketsObserved"] == 5
    assert report["quotesCaptured"] == 3
    assert report["marketFamilyCounts"] == {"game_result": 2, "team_total": 1}
    assert report["placedBets"] == 3
    assert report["recommendedBets"] == 2  # RECOMMENDED + BET_PLACED
    assert report["passCountsByReason"] == {"PASS_NO_EDGE": 1, "PASS_LOW_LIQUIDITY": 1}
    assert report["notEvaluatedCount"] == 1
    assert report["insufficientModelSupportCount"] == 1
    assert report["closingQuotesCaptured"] == 2


def test_games_observed_excludes_superseded_duplicate_identities():
    """
    A Game row marked supersededBy (lib.edgelab.market_universe.
    mark_superseded_game_identities) is a duplicate identity for a game
    already counted under its canonical row -- gamesObserved must count
    real games, not raw Game rows (the 2026-08-04 case: 15 real games
    produced 30 stored rows).
    """
    games, markets, observations, recommendations, clv_quotes, settlements, bets, research_runs = _sample_inputs()
    games = [
        {"gameId": "g1"},
        {"gameId": "g2"},
        {"gameId": "2026-08-04_NYM_CLE_1840", "supersededBy": {"canonicalGameId": "824403"}},
    ]
    report = build_daily_report(DATE, games, markets, observations, recommendations, clv_quotes, settlements, bets, research_runs)
    assert report["gamesObserved"] == 2


def test_clv_summary_ignores_bets_without_clv():
    report = build_daily_report(DATE, *_sample_inputs())
    clv = report["clvSummary"]
    assert clv["betsTotal"] == 3
    assert clv["betsWithClv"] == 2
    assert clv["avgClvCents"] == 0.75  # (2.5 + -1.0) / 2
    assert clv["positiveClvCount"] == 1
    assert clv["negativeClvCount"] == 1


def test_settlement_completion_and_unresolved_reasons():
    report = build_daily_report(DATE, *_sample_inputs())
    sc = report["settlementCompletion"]
    assert sc["settled"] == 2
    assert sc["void"] == 1
    assert sc["unresolved"] == 2
    assert sc["notYetAttempted"] == 0  # 5 markets, 5 settlement rows
    assert report["settlementUnresolvedReasons"]["player_prop_settlement_not_implemented"] == 1


def test_warnings_split_new_series_from_general_data_quality():
    report = build_daily_report(DATE, *_sample_inputs())
    assert report["apiErrors"] == ["fetch failed once"]
    assert len(report["newUnclassifiedSeriesWarnings"]) == 1
    assert "KXFOO" in report["newUnclassifiedSeriesWarnings"][0]
    assert report["dataQualityWarnings"] == ["5 legacy bets skipped"]


def test_render_markdown_is_nonempty_and_includes_key_sections():
    report = build_daily_report(DATE, *_sample_inputs())
    md = render_markdown(report)
    assert "EdgeLab Daily Research Report" in md
    assert "Market family counts" in md
    assert "CLV summary" in md
    assert "Settlement completion" in md
    assert "game_result: 2" in md


def test_calibration_rows_require_both_model_prob_and_settled_result():
    games, markets, observations, recommendations, clv_quotes, settlements, bets, research_runs = _sample_inputs()
    rows = build_calibration_rows(recommendations, settlements)
    tickers = {r["marketTicker"] for r in rows}
    assert tickers == {"T1", "T2"}  # T3-T6 lack either a model prob or a settled result
    by_ticker = {r["marketTicker"]: r for r in rows}
    assert by_ticker["T1"]["won"] is True
    assert by_ticker["T2"]["won"] is False


# ---------------------------------------------------------------------------
# build_postmortem / render_postmortem_markdown (Canonical Placed-Bet
# Ledger milestone, requirement 14). Built EXCLUSIVELY from PlacedBet rows
# -- never from a recommendation list -- so a recommendation never counts
# as a placed bet here.
# ---------------------------------------------------------------------------

def _postmortem_bets():
    return [
        {  # settled WIN, model-supported, recommended
            "betId": "b1", "gameDate": "2026-08-01", "marketTicker": "T1", "marketFamily": "FAMILY_GAME_RESULT",
            "selection": "PIT ML", "side": "YES", "stake": 10.0, "entryPrice": 0.5, "status": "settled",
            "result": "WIN", "netProfitLoss": 10.0, "clv": 2.0, "source": "MODEL", "entryMethod": "PRODUCTION_RECOMMENDATION_CONFIRMED",
            "modelSupported": True, "modelEvaluationId": "me1", "recommendationId": "rec1", "snapshotId": "snap1",
            "replayRunId": None, "trackingType": "REAL", "recordStatus": "ACTIVE",
        },
        {  # settled LOSS, manual, not recommended
            "betId": "b2", "gameDate": "2026-08-01", "marketTicker": "T2", "marketFamily": "FAMILY_GAME_RESULT",
            "selection": "CIN ML", "side": "YES", "stake": 5.0, "entryPrice": 0.4, "status": "settled",
            "result": "LOSS", "netProfitLoss": -5.0, "clv": -1.0, "source": "MANUAL", "entryMethod": "MANUAL_CHAT_CONFIRMED",
            "modelSupported": None, "modelEvaluationId": None, "recommendationId": None, "snapshotId": None,
            "replayRunId": None, "trackingType": "REAL", "recordStatus": "ACTIVE",
        },
        {  # still pending, manual
            "betId": "b3", "gameDate": "2026-08-01", "marketTicker": "T3", "marketFamily": "FAMILY_INNING_RESULT",
            "selection": "DET F5", "side": "NO", "stake": 3.0, "entryPrice": 0.6, "status": "pending",
            "result": None, "netProfitLoss": None, "clv": None, "source": "MANUAL", "entryMethod": "MANUAL_GITHUB_FORM",
            "modelSupported": None, "modelEvaluationId": None, "recommendationId": None, "snapshotId": None,
            "replayRunId": None, "trackingType": "REAL", "recordStatus": "ACTIVE",
        },
        {  # void
            "betId": "b4", "gameDate": "2026-08-01", "marketTicker": "T4", "marketFamily": "FAMILY_GAME_RESULT",
            "selection": "SEA ML", "side": "YES", "stake": 2.0, "entryPrice": 0.5, "status": "void",
            "result": "VOID", "netProfitLoss": 0.0, "clv": None, "source": "MANUAL", "entryMethod": "MANUAL_CHAT_CONFIRMED",
            "modelSupported": None, "modelEvaluationId": None, "recommendationId": None, "snapshotId": None,
            "replayRunId": None, "trackingType": "REAL", "recordStatus": "ACTIVE",
        },
        {  # different date -- must be excluded
            "betId": "b5", "gameDate": "2026-07-31", "marketTicker": "T5", "marketFamily": "FAMILY_GAME_RESULT",
            "selection": "NYY ML", "side": "YES", "stake": 100.0, "entryPrice": 0.5, "status": "settled",
            "result": "WIN", "netProfitLoss": 100.0, "clv": None, "source": "MANUAL", "entryMethod": "MANUAL_CHAT_CONFIRMED",
            "modelSupported": None, "modelEvaluationId": None, "recommendationId": None, "snapshotId": None,
            "replayRunId": None, "trackingType": "REAL", "recordStatus": "ACTIVE",
        },
        {  # cancelled -- must be excluded
            "betId": "b6", "gameDate": "2026-08-01", "marketTicker": "T6", "marketFamily": "FAMILY_GAME_RESULT",
            "selection": "BOS ML", "side": "YES", "stake": 1000.0, "entryPrice": 0.5, "status": "pending",
            "result": None, "netProfitLoss": None, "clv": None, "source": "MANUAL", "entryMethod": "MANUAL_CHAT_CONFIRMED",
            "modelSupported": None, "modelEvaluationId": None, "recommendationId": None, "snapshotId": None,
            "replayRunId": None, "trackingType": "REAL", "recordStatus": "CANCELLED",
        },
        {  # paper -- must never pollute real totals
            "betId": "b7", "gameDate": "2026-08-01", "marketTicker": "T7", "marketFamily": "FAMILY_GAME_RESULT",
            "selection": "TEX ML", "side": "YES", "stake": 500.0, "entryPrice": 0.5, "status": "settled",
            "result": "WIN", "netProfitLoss": 500.0, "clv": None, "source": "MANUAL", "entryMethod": "MANUAL_CHAT_CONFIRMED",
            "modelSupported": None, "modelEvaluationId": None, "recommendationId": None, "snapshotId": None,
            "replayRunId": None, "trackingType": "PAPER", "recordStatus": "ACTIVE",
        },
    ]


def test_postmortem_filters_to_date_excludes_cancelled_and_paper():
    report = build_postmortem("2026-08-01", _postmortem_bets())
    assert report["betsPlaced"] == 4  # b1-b4 only
    assert {b["betId"] for b in report["bets"]} == {"b1", "b2", "b3", "b4"}


def test_postmortem_daily_record_and_totals():
    report = build_postmortem("2026-08-01", _postmortem_bets())
    assert report["dailyRecord"] == {"wins": 1, "losses": 1, "pushes": 0, "voids": 1, "pending": 1}
    assert report["totalRisked"] == 20.0  # 10 + 5 + 3 + 2
    assert report["totalRiskedSettled"] == 15.0  # 10 + 5 -- b3 pending, b4 void (not "settled")
    assert report["totalNetProfitLoss"] == 5.0  # 10 - 5 + 0
    assert report["roiPct"] == round(5.0 / 15.0 * 100, 2)
    assert report["unresolvedCount"] == 1
    assert report["unresolvedBetIds"] == ["b3"]


def test_postmortem_never_substitutes_recommendations_for_placed_bets():
    """Only PlacedBet rows count -- a recommendation list, even if passed accidentally, has no signature build_postmortem accepts."""
    import inspect
    sig = inspect.signature(build_postmortem)
    assert "recommendations" not in sig.parameters


def test_postmortem_gross_return_and_clv():
    report = build_postmortem("2026-08-01", _postmortem_bets())
    by_id = {b["betId"]: b for b in report["bets"]}
    assert by_id["b1"]["grossReturn"] == 20.0  # stake 10 + netProfitLoss 10
    assert by_id["b2"]["grossReturn"] == 0.0  # a loss returns nothing
    assert by_id["b3"]["grossReturn"] is None  # still pending
    assert report["avgClvCents"] == 0.5  # (2.0 + -1.0) / 2, b3/b4 have no clv


def test_postmortem_model_supported_vs_manual():
    report = build_postmortem("2026-08-01", _postmortem_bets())
    ms = report["modelSupportedVsManual"]
    assert ms["modelSupported"]["count"] == 1
    assert ms["manual"]["count"] == 3


def test_postmortem_recommended_vs_non_recommended():
    report = build_postmortem("2026-08-01", _postmortem_bets())
    rv = report["recommendedVsNonRecommended"]
    assert rv["recommended"]["count"] == 1
    assert rv["nonRecommended"]["count"] == 3


def test_postmortem_snapshot_and_replay_linkage_counts():
    report = build_postmortem("2026-08-01", _postmortem_bets())
    assert report["snapshotLinkedCount"] == 1
    assert report["replayLinkedCount"] == 0


def test_postmortem_performance_by_market_family():
    report = build_postmortem("2026-08-01", _postmortem_bets())
    fam = report["performanceByMarketFamily"]["FAMILY_GAME_RESULT"]
    assert fam["count"] == 2  # b1, b2 settled in this family (b4 void isn't "settled")
    assert fam["wins"] == 1
    assert fam["losses"] == 1


def test_postmortem_with_no_bets_for_date_is_all_zero_not_an_error():
    report = build_postmortem("2099-01-01", _postmortem_bets())
    assert report["betsPlaced"] == 0
    assert report["totalRisked"] == 0.0
    assert report["roiPct"] is None


def test_postmortem_bankroll_is_passthrough_when_provided():
    bankroll = {"availableBankroll": 1.0, "settledBankroll": 2.0, "totalExposure": 1.0, "userReportedBalance": None}
    report = build_postmortem("2026-08-01", _postmortem_bets(), bankroll_summary=bankroll)
    assert report["bankroll"] == bankroll


def test_render_postmortem_markdown_includes_key_sections():
    report = build_postmortem("2026-08-01", _postmortem_bets())
    md = render_postmortem_markdown(report)
    assert "Daily Postmortem" in md
    assert "Performance by market family" in md
    assert "Model-supported vs. manual" in md
    assert "Recommended vs. non-recommended" in md
    assert "Unresolved bets" in md
