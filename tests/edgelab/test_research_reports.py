#!/usr/bin/env python3
"""
tests/edgelab/test_research_reports.py
============================================
Coverage for lib/edgelab/research_reports.py -- the A-H research report
generators over lib.edgelab.research_dataset rows.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import research_reports as rr
from lib.edgelab.research_dataset import build_opportunity_rows


def _obs(obs_id, ticker="T1", captured_at="2026-08-07T18:00:00Z", checkpoint="T_MINUS_5",
         scheduled_start="2026-08-07T18:30:00Z", yes_bid=44.0, yes_ask=46.0, no_bid=54.0, no_ask=56.0,
         market_family="KXMLBGAME", game_id="g1", player=None, team=None, threshold=None,
         comparison_operator=None, market_status="active", **overrides):
    row = {
        "marketObservationId": obs_id, "marketTicker": ticker, "capturedAt": captured_at,
        "checkpoint": checkpoint, "scheduledStart": scheduled_start, "gameId": game_id,
        "marketFamily": market_family, "yesBid": yes_bid, "yesAsk": yes_ask, "noBid": no_bid, "noAsk": no_ask,
        "lastPrice": yes_ask, "marketStatus": market_status, "isValidPregameObservation": True,
        "isClosingCandidate": True, "threshold": threshold, "comparisonOperator": comparison_operator,
        "team": team, "player": player, "outcomeLabel": None, "marketHorizon": "FULL_GAME",
        "lineupConfirmationState": None, "source": "test",
    }
    row.update(overrides)
    return row


def _settlement(ticker="T1", status="SETTLED", result="YES", game_id="g1", game_date="2026-08-07", **overrides):
    row = {"marketTicker": ticker, "settlementStatus": status, "result": result, "unavailableReason": None, "gameId": game_id}
    row.update(overrides)
    return row


def _evaluation(eval_id, ticker="T1", pipeline_run_id="2026-08-07T12:00:00Z", model_fair_probability=60.0,
                 market_implied_probability=50.0, selection="ML_Away", **overrides):
    row = {
        "modelEvaluationId": eval_id, "marketTicker": ticker, "pipelineRunId": pipeline_run_id,
        "modelFairProbability": model_fair_probability, "marketImpliedProbability": market_implied_probability,
        "selection": selection, "side": None, "threshold": None, "evaluationStatus": "EVALUATED",
        "confidence": "HIGH", "dataQuality": "full", "estimatedEdge": 5.0, "thesisTags": [], "correlationGroups": [],
    }
    row.update(overrides)
    return row


def _games(game_id="g1", game_date="2026-08-07"):
    return [{"gameId": game_id, "gameDate": game_date, "scheduledStartTime": "2026-08-07T18:30:00Z"}]


# ── Empty-input safety ────────────────────────────────────────────────────

def test_all_reports_handle_empty_rows():
    assert rr.market_calibration([])["overall"] == []
    assert rr.model_calibration([])["overall"] == []
    assert rr.edge_backtest([]) == []
    assert rr.market_family_research([]) == []
    assert rr.checkpoint_research([]) == []
    assert rr.ladder_research([]) == []
    dq = rr.research_data_quality([])
    assert dq["totalOpportunityRows"] == 0
    sv = rr.strategy_validation([])
    assert sv["totalDates"] == 0


# ── market_calibration ────────────────────────────────────────────────────

def test_market_calibration_full_universe_never_bet_never_recommended():
    rows = build_opportunity_rows([_obs("o1")], settlements=[_settlement()], recommendations=[], bets=[])
    report = rr.market_calibration(rows)
    assert report["overall"][0]["n"] == 1
    assert report["overall"][0]["actualYesRate"] == 1.0


def test_market_calibration_excludes_unresolved_and_void():
    rows = build_opportunity_rows(
        [_obs("o1", ticker="T1"), _obs("o2", ticker="T2"), _obs("o3", ticker="T3")],
        settlements=[
            _settlement(ticker="T1", status="SETTLED", result="YES"),
            _settlement(ticker="T2", status="SETTLEMENT_UNRESOLVED", result=None),
            _settlement(ticker="T3", status="VOID", result=None),
        ],
    )
    report = rr.market_calibration(rows)
    assert report["overall"][0]["n"] == 1  # only T1


# ── model_calibration ─────────────────────────────────────────────────────

def test_model_calibration_requires_causally_valid_evaluation():
    rows = build_opportunity_rows(
        [_obs("o1", captured_at="2026-08-07T10:00:00Z")],
        settlements=[_settlement()],
        evaluations=[_evaluation("e1", pipeline_run_id="2026-08-07T12:00:00Z")],  # AFTER the checkpoint
    )
    report = rr.model_calibration(rows)
    assert report["overall"] == []  # no causally-valid evaluation -> nothing eligible


def test_model_calibration_uses_normalized_0_1_scale():
    rows = build_opportunity_rows(
        [_obs("o1", captured_at="2026-08-07T18:00:00Z")],
        settlements=[_settlement(result="YES")],
        evaluations=[_evaluation("e1", model_fair_probability=64.0)],
    )
    report = rr.model_calibration(rows)
    overall = report["overall"][0]
    assert 0.0 <= overall["avgModelProbability"] <= 1.0
    assert abs(overall["avgModelProbability"] - 0.64) < 1e-6
    assert abs(overall["calibrationError"]) <= 1.0  # never a ~-47/-55-style scale-bug artifact


# ── edge_backtest: both sides ─────────────────────────────────────────────

def test_edge_backtest_produces_both_yes_and_no_opportunities():
    rows = build_opportunity_rows(
        [_obs("o1", yes_ask=40.0, no_ask=65.0)],
        settlements=[_settlement(result="YES")],
        evaluations=[_evaluation("e1", model_fair_probability=60.0)],
    )
    backtest = rr.edge_backtest(rows)
    total_n = sum(b["n"] for b in backtest)
    assert total_n == 2  # one YES opportunity + one NO (mirror) opportunity from the single row


def test_edge_backtest_side_filter():
    rows = build_opportunity_rows(
        [_obs("o1", yes_ask=40.0, no_ask=65.0)],
        settlements=[_settlement(result="YES")],
        evaluations=[_evaluation("e1", model_fair_probability=60.0)],
    )
    yes_only = rr.edge_backtest(rows, side_filter="YES")
    assert sum(b["n"] for b in yes_only) == 1


def test_edge_backtest_excludes_unresolved_settlement():
    rows = build_opportunity_rows(
        [_obs("o1")],
        settlements=[_settlement(status="SETTLEMENT_UNRESOLVED", result=None)],
        evaluations=[_evaluation("e1")],
    )
    assert rr.edge_backtest(rows) == []


def test_edge_backtest_never_uses_future_evaluation():
    rows = build_opportunity_rows(
        [_obs("o1", captured_at="2026-08-07T10:00:00Z")],
        settlements=[_settlement()],
        evaluations=[_evaluation("e1", pipeline_run_id="2026-08-07T12:00:00Z")],
    )
    assert rr.edge_backtest(rows) == []


# ── ladder_research ────────────────────────────────────────────────────

def test_ladder_monotonicity_violation_detected():
    observations = [
        _obs("o1", ticker="T1", checkpoint="CLOSING".replace("CLOSING", "T_MINUS_5"), threshold=1.5,
             comparison_operator="OVER", player="Player A", yes_ask=70.0),
        _obs("o2", ticker="T2", checkpoint="T_MINUS_5", threshold=2.5,
             comparison_operator="OVER", player="Player A", yes_ask=80.0),  # HIGHER threshold, HIGHER price -> violation
    ]
    rows = build_opportunity_rows(observations)
    ladders = rr.ladder_research(rows)
    assert len(ladders) == 1
    assert ladders[0]["isMonotonic"] is False
    assert len(ladders[0]["monotonicityViolations"]) == 1


def test_ladder_no_violation_for_proper_decreasing_over_ladder():
    observations = [
        _obs("o1", ticker="T1", checkpoint="T_MINUS_5", threshold=1.5, comparison_operator="OVER", player="Player A", yes_ask=70.0),
        _obs("o2", ticker="T2", checkpoint="T_MINUS_5", threshold=2.5, comparison_operator="OVER", player="Player A", yes_ask=40.0),
    ]
    rows = build_opportunity_rows(observations)
    ladders = rr.ladder_research(rows)
    assert ladders[0]["isMonotonic"] is True


def test_ladder_requires_at_least_two_rungs():
    observations = [_obs("o1", ticker="T1", threshold=1.5, comparison_operator="OVER", player="Player A")]
    rows = build_opportunity_rows(observations)
    assert rr.ladder_research(rows) == []


# ── research_data_quality ─────────────────────────────────────────────────

def test_data_quality_counts_are_sane():
    rows = build_opportunity_rows(
        [_obs("o1", ticker="T1"), _obs("o2", ticker="T2", checkpoint="T_MINUS_15")],
        settlements=[_settlement(ticker="T1", status="SETTLED", result="YES")],
        evaluations=[_evaluation("e1", ticker="T1")],
        games=_games(),
    )
    dq = rr.research_data_quality(rows)
    assert dq["uniqueGames"] == 1
    assert dq["uniqueMarketTickers"] == 2
    assert dq["totalOpportunityRows"] == 2
    assert dq["settlementStatusCounts"].get("SETTLED") == 1
    assert dq["settlementStatusCounts"].get("NOT_SETTLED") == 1


# ── strategy_validation ────────────────────────────────────────────────

def test_strategy_validation_partitions_never_overlap_dates():
    import datetime
    observations = []
    settlements = []
    for i in range(40):
        date = str(datetime.date(2026, 1, 1) + datetime.timedelta(days=i))
        ticker = f"T{i}"
        observations.append(_obs(f"o{i}", ticker=ticker, captured_at=f"{date}T18:00:00Z",
                                  scheduled_start=f"{date}T18:30:00Z", game_id=f"g{i}"))
        settlements.append(_settlement(ticker=ticker, status="SETTLED", result="YES", game_id=f"g{i}"))
    rows = build_opportunity_rows(observations, settlements=settlements, games=[
        {"gameId": f"g{i}", "gameDate": str(datetime.date(2026, 1, 1) + datetime.timedelta(days=i))} for i in range(40)
    ])
    result = rr.strategy_validation(rows)
    assert result["maturity"] == "USABLE"
    assert result["partitions"]["DEVELOPMENT"]["rowCount"] + result["partitions"]["VALIDATION"]["rowCount"] + result["partitions"]["HOLDOUT"]["rowCount"] == 40


def test_strategy_validation_small_corpus_labeled_framework_only():
    rows = build_opportunity_rows([_obs("o1")], settlements=[_settlement()], games=_games())
    result = rr.strategy_validation(rows)
    assert result["maturity"] == "FRAMEWORK_ONLY_INSUFFICIENT_DATES"
    assert "FRAMEWORK ONLY" in result["note"]


# ── Summary renders without crashing ──────────────────────────────────────

def test_render_summary_markdown_smoke():
    rows = build_opportunity_rows(
        [_obs("o1")], settlements=[_settlement()], evaluations=[_evaluation("e1")], games=_games(),
    )
    dq = rr.research_data_quality(rows)
    mc = rr.market_calibration(rows)
    mcal = rr.model_calibration(rows)
    eb = rr.edge_backtest(rows)
    sv = rr.strategy_validation(rows)
    text = rr.render_summary_markdown(dq, mc, mcal, eb, sv)
    assert "EdgeLab Research Trustworthiness Summary" in text
    assert "exploratory" in text.lower()
