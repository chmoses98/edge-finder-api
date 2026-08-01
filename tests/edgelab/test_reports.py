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

from lib.edgelab.reports import build_calibration_rows, build_daily_report, render_markdown

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
