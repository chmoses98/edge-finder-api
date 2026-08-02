#!/usr/bin/env python3
"""
tests/edgelab/test_market_intelligence.py
==============================================
Coverage for lib/edgelab/market_intelligence.py (EdgeLab Phase 2
Milestone 6 -- docs/EDGELAB_MARKET_INTELLIGENCE.md): expression
performance profiles, opportunity-cost analysis, pass analysis, strategy
experiments (labeled hypothetical simulations), edge stability, and
market health scores. Builds on the same DuckDB-over-tmp_path fixture
pattern tests/edgelab/test_calibration.py and
tests/edgelab/test_market_comparison.py established.

Where a function only needs plain comparison-result dicts (the output
shape of lib.edgelab.market_comparison.build_comparisons()), tests
construct those dicts directly rather than re-deriving them through a
full ModelEvaluation fixture + clustering pass -- market_comparison's own
clustering/domination/scoring correctness is already covered by
tests/edgelab/test_market_comparison.py; this file tests
market_intelligence's OWN logic layered on top of that output shape.
"""
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.analytics import open_session
from lib.edgelab.market_comparison import HORIZON_F5, HORIZON_FULL_GAME, STATUS_BEST_EXPRESSION, STATUS_DOMINATED_MARKET
from lib.edgelab.market_intelligence import (
    SIMULATION_LABEL,
    STABLE,
    UNKNOWN_STABILITY,
    VOLATILE,
    edge_bucket,
    edge_stability,
    expression_performance_profiles,
    market_health_scores,
    opportunity_cost_analysis,
    pass_analysis,
    strategy_experiments,
)


# ── Fixture builders ──────────────────────────────────────────────────────

def _comparison_row(**overrides):
    row = {
        "marketTicker": "T1",
        "gameId": "g1",
        "modelEvaluationId": "me1",
        "canonicalMarketFamily": "game_result",
        "thesisGroup": "WIN",
        "horizon": HORIZON_FULL_GAME,
        "team": "AWAY",
        "evaluationStatus": "EVALUATED",
        "estimatedEdge": 2.0,
        "dataQuality": "full",
        "confidence": "HIGH",
        "clv": None,
        "placedBetIndicator": False,
        "betId": None,
        "clusterId": "g1:AWAY:WIN",
        "comparisonStatus": STATUS_BEST_EXPRESSION,
        "comparisonRank": 1,
        "dominantMarketTicker": None,
        "dominationReasons": [],
        "missingFields": [],
    }
    row.update(overrides)
    return row


def _write_jsonl(path, records, compressed=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    opener = gzip.open if compressed else open
    with opener(path, "wt") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _bet(bet_id, model_evaluation_id=None, market_ticker=None, market_family="game_result", side="YES",
          stake=10.0, net_profit_loss=5.0, clv=None, result="WIN", status="settled", thesis_tags=None,
          entry_timestamp="2026-07-01T12:00:00Z"):
    return {
        "betId": bet_id, "marketTicker": market_ticker or f"T-{bet_id}", "marketFamily": market_family,
        "selection": "x", "side": side, "stake": stake, "entryPrice": 0.5,
        "entryTimestamp": entry_timestamp, "source": "MODEL", "modelEvaluationId": model_evaluation_id,
        "thesisTags": thesis_tags or [], "status": status, "result": result,
        "netProfitLoss": net_profit_loss, "clv": clv,
    }


def _raw_eval_row(**overrides):
    row = {
        "modelEvaluationId": overrides.pop("modelEvaluationId", "me1"),
        "runId": "run1",
        "gameId": overrides.pop("gameId", "g1"),
        "marketTicker": overrides.pop("marketTicker", None),
        "marketFamily": overrides.pop("marketFamily", "game_result"),
        "selection": overrides.pop("selection", "ML_Away"),
        "side": overrides.pop("side", None),
        "threshold": overrides.pop("threshold", None),
        "evaluationStatus": overrides.pop("evaluationStatus", "EVALUATED"),
        "modelFairProbability": overrides.pop("modelFairProbability", 60.0),
        "marketImpliedProbability": overrides.pop("marketImpliedProbability", 50.0),
        "estimatedEdge": overrides.pop("estimatedEdge", 5.0),
        "confidence": overrides.pop("confidence", "HIGH"),
        "dataQuality": overrides.pop("dataQuality", "full"),
        "lineupConfirmationState": overrides.pop("lineupConfirmationState", "CONFIRMED"),
        "createdAt": overrides.pop("createdAt", "2026-07-30T12:00:00Z"),
    }
    row.update(overrides)
    return row


def _rec(recommendation_id, status="RECOMMENDED", market_ticker=None, market_family="game_result",
         model_evaluation_id=None, model_fair_probability=None, market_implied_probability=None):
    return {
        "recommendationId": recommendation_id, "runId": "run1", "gameId": "g1",
        "marketTicker": market_ticker, "marketFamily": market_family, "status": status,
        "modelEvaluationId": model_evaluation_id, "modelFairProbability": model_fair_probability,
        "marketImpliedProbability": market_implied_probability, "estimatedEdge": None,
        "confidence": None, "passReason": None, "betPlaced": status == "BET_PLACED",
        "betId": None, "createdAt": "2026-07-30T12:00:00Z",
    }


def _settlement(market_ticker, settlement_status="SETTLED", result="YES", market_family="game_result"):
    return {
        "settlementId": f"s-{market_ticker}", "gameId": "g1", "marketTicker": market_ticker,
        "marketFamily": market_family, "settlementStatus": settlement_status,
        "result": result, "settledAt": "2026-07-30T23:00:00Z",
    }


def _session(tmp_path, evaluations=None, bets=None, recommendations=None, settlements=None):
    if evaluations is not None:
        _write_jsonl(str(tmp_path / "model_evaluations" / "evals.jsonl"), evaluations)
    if bets is not None:
        _write_jsonl(str(tmp_path / "bets" / "bets.jsonl"), bets)
    if recommendations is not None:
        _write_jsonl(str(tmp_path / "recommendations" / "recs.jsonl"), recommendations)
    if settlements is not None:
        _write_jsonl(str(tmp_path / "settlements" / "settlements.jsonl"), settlements)
    return open_session(root=str(tmp_path))


# ── edge_bucket ───────────────────────────────────────────────────────────

def test_edge_bucket_boundaries_are_half_open():
    assert edge_bucket(1.9) == 0.0
    assert edge_bucket(2.0) == 2.0
    assert edge_bucket(-0.1) == -2.0
    assert edge_bucket(None) is None


# ── Expression performance profiles ──────────────────────────────────────

def test_expression_performance_profiles_frequencies(tmp_path):
    evaluations = [
        _raw_eval_row(modelEvaluationId="me1", marketTicker="A", marketFamily="game_result"),
        _raw_eval_row(modelEvaluationId="me2", marketTicker="B", marketFamily="game_result"),
    ]
    recs = [
        _rec("r1", status="BET_PLACED", market_ticker="A", market_family="game_result", model_evaluation_id="me1"),
        _rec("r2", status="PASS_NO_EDGE", market_ticker="B", market_family="game_result", model_evaluation_id="me2"),
    ]
    comparisons = [
        _comparison_row(marketTicker="A", modelEvaluationId="me1", clusterId="g1:AWAY:WIN", comparisonStatus=STATUS_BEST_EXPRESSION),
        _comparison_row(marketTicker="B", modelEvaluationId="me2", clusterId="g1:AWAY:WIN", comparisonStatus=STATUS_DOMINATED_MARKET),
    ]
    with _session(tmp_path, evaluations=evaluations, recommendations=recs) as session:
        profiles = expression_performance_profiles(session, comparisons)
    profile = next(p for p in profiles if p["canonicalMarketFamily"] == "game_result")
    assert profile["totalEvaluated"] == 2
    assert profile["recommendationFrequency"] == 0.5
    assert profile["passFrequency"] == 0.5
    assert profile["bestExpressionFrequency"] == 0.5
    assert profile["dominatedFrequency"] == 0.5


def test_expression_performance_profiles_deterministic(tmp_path):
    evaluations = [_raw_eval_row(modelEvaluationId="me1", marketTicker="A")]
    with _session(tmp_path, evaluations=evaluations) as session:
        a = expression_performance_profiles(session, [])
        b = expression_performance_profiles(session, [])
    assert a == b


# ── Opportunity cost analysis ────────────────────────────────────────────

def test_opportunity_cost_case_when_placed_bet_not_top_ranked(tmp_path):
    comparisons = [
        _comparison_row(marketTicker="ML", modelEvaluationId="me-ml", clusterId="g1:AWAY:WIN",
                        estimatedEdge=2.0, placedBetIndicator=True, betId="b1", comparisonRank=2,
                        comparisonStatus=STATUS_DOMINATED_MARKET, clv=-1.0),
        _comparison_row(marketTicker="F5", modelEvaluationId="me-f5", clusterId="g1:AWAY:WIN",
                        estimatedEdge=5.0, placedBetIndicator=False, comparisonRank=1,
                        comparisonStatus=STATUS_BEST_EXPRESSION, clv=None),
    ]
    with _session(tmp_path, bets=[_bet("b1", model_evaluation_id="me-ml", market_ticker="ML", clv=-1.0)]) as session:
        result = opportunity_cost_analysis(session, comparisons)
    assert result["sampleSize"] == 1
    assert result["opportunityCostCaseCount"] == 1
    case = result["cases"][0]
    assert case["betMarketTicker"] == "ML"
    assert case["betterExpressionMarketTicker"] == "F5"
    assert case["lostEstimatedEdge"] == 3.0
    assert case["dominatedByBestExpression"] is True
    assert case["lostClv"] is None  # F5 was never itself placed -- never fabricated
    assert case["lostRoi"] is None


def test_opportunity_cost_lost_clv_and_roi_only_when_alternative_also_placed(tmp_path):
    comparisons = [
        _comparison_row(marketTicker="ML", modelEvaluationId="me-ml", clusterId="g1:AWAY:WIN",
                        estimatedEdge=2.0, placedBetIndicator=True, betId="b1", comparisonRank=2,
                        comparisonStatus=STATUS_DOMINATED_MARKET, clv=-1.0),
        _comparison_row(marketTicker="F5", modelEvaluationId="me-f5", clusterId="g1:AWAY:WIN",
                        estimatedEdge=5.0, placedBetIndicator=True, betId="b2", comparisonRank=1,
                        comparisonStatus=STATUS_BEST_EXPRESSION, clv=3.0),
    ]
    bets = [
        _bet("b1", model_evaluation_id="me-ml", market_ticker="ML", clv=-1.0, stake=10.0, net_profit_loss=-10.0, result="LOSS"),
        _bet("b2", model_evaluation_id="me-f5", market_ticker="F5", clv=3.0, stake=10.0, net_profit_loss=8.0, result="WIN"),
    ]
    with _session(tmp_path, bets=bets) as session:
        result = opportunity_cost_analysis(session, comparisons)
    case = result["cases"][0]
    assert case["lostClv"] == 4.0
    assert abs(case["lostRoi"] - (0.8 - (-1.0))) < 1e-9


def test_opportunity_cost_no_case_when_placed_bet_is_top_ranked(tmp_path):
    comparisons = [
        _comparison_row(marketTicker="ML", modelEvaluationId="me-ml", clusterId="g1:AWAY:WIN",
                        placedBetIndicator=True, betId="b1", comparisonRank=1, comparisonStatus=STATUS_BEST_EXPRESSION),
        _comparison_row(marketTicker="F5", modelEvaluationId="me-f5", clusterId="g1:AWAY:WIN",
                        placedBetIndicator=False, comparisonRank=2),
    ]
    with _session(tmp_path, bets=[_bet("b1", model_evaluation_id="me-ml", market_ticker="ML")]) as session:
        result = opportunity_cost_analysis(session, comparisons)
    assert result["opportunityCostCaseCount"] == 0
    assert result["sampleSize"] == 1


def test_opportunity_cost_gated_by_sample_size(tmp_path):
    with _session(tmp_path) as session:
        result = opportunity_cost_analysis(session, [])
    assert result["sampleSize"] == 0
    assert result["sampleStatus"] == "INSUFFICIENT_SAMPLE"
    assert result["opportunityCostFrequency"] is None


# ── Pass analysis ─────────────────────────────────────────────────────────

def test_pass_analysis_categorizes_by_real_status_vocabulary(tmp_path):
    recs = [
        _rec("r1", status="RECOMMENDED"),
        _rec("r2", status="PASS_NO_EDGE"),
        _rec("r3", status="PASS_DATA_QUALITY"),
        _rec("r4", status="BET_PLACED"),  # not a "pass" category
    ]
    with _session(tmp_path, recommendations=recs) as session:
        results = pass_analysis(session, [])
    by_category = {r["category"]: r for r in results}
    assert by_category["RECOMMENDED_NOT_BET"]["n"] == 1
    assert by_category["PASS_NO_EDGE"]["n"] == 1
    assert by_category["INSUFFICIENT_SUPPORT"]["n"] == 1
    assert "BET_PLACED" not in by_category


def test_pass_analysis_dominated_category_from_comparisons(tmp_path):
    recs = [_rec("r1", status="RECOMMENDED", model_evaluation_id="me1", market_ticker="A")]
    comparisons = [_comparison_row(marketTicker="A", modelEvaluationId="me1", comparisonStatus=STATUS_DOMINATED_MARKET)]
    with _session(tmp_path, recommendations=recs) as session:
        results = pass_analysis(session, comparisons)
    by_category = {r["category"]: r for r in results}
    assert by_category["DOMINATED"]["n"] == 1


def test_pass_analysis_never_fabricates_a_hypothetical_win_loss(tmp_path):
    """Even with a real Settlement row present, pass_analysis must not compute a win/loss for an unbet market -- no side is ever knowable for it."""
    recs = [_rec("r1", status="PASS_NO_EDGE", market_ticker="A")]
    settlements = [_settlement("A", result="YES")]
    with _session(tmp_path, recommendations=recs, settlements=settlements) as session:
        results = pass_analysis(session, [])
    result = next(r for r in results if r["category"] == "PASS_NO_EDGE")
    assert "hypotheticalAvgReturn" not in result
    assert "hypotheticalWinRate" not in result
    assert result["settlementStatusCounts"] == {"SETTLED": 1}


def test_pass_analysis_empty_when_recommendations_unavailable(tmp_path):
    with _session(tmp_path) as session:
        assert pass_analysis(session, []) == []


# ── Strategy experiments (simulations) ───────────────────────────────────

def test_strategy_experiments_baseline_matches_real_settled_bets(tmp_path):
    bets = [
        _bet("b1", stake=10.0, net_profit_loss=5.0, result="WIN"),
        _bet("b2", stake=10.0, net_profit_loss=-10.0, result="LOSS"),
    ]
    with _session(tmp_path, bets=bets) as session:
        result = strategy_experiments(session, [])
    assert result["simulationLabel"] == SIMULATION_LABEL
    assert result["baseline"]["n"] == 2
    assert result["baseline"]["totalNetProfitLoss"] == -5.0


def test_strategy_experiments_dominated_replacement_swaps_only_when_alternative_also_settled(tmp_path):
    bets = [
        _bet("b1", model_evaluation_id="me-ml", market_ticker="ML", stake=10.0, net_profit_loss=-10.0, result="LOSS"),
        _bet("b2", model_evaluation_id="me-f5", market_ticker="F5", stake=20.0, net_profit_loss=16.0, result="WIN"),
    ]
    comparisons = [
        _comparison_row(marketTicker="ML", modelEvaluationId="me-ml", clusterId="g1:AWAY:WIN",
                        placedBetIndicator=True, comparisonStatus=STATUS_DOMINATED_MARKET, dominantMarketTicker="F5"),
        _comparison_row(marketTicker="F5", modelEvaluationId="me-f5", clusterId="g1:AWAY:WIN",
                        placedBetIndicator=True, comparisonStatus=STATUS_BEST_EXPRESSION, horizon=HORIZON_F5),
    ]
    with _session(tmp_path, bets=bets) as session:
        result = strategy_experiments(session, comparisons)
    experiment = next(e for e in result["experiments"] if e["name"] == "DOMINATED_MARKETS_REPLACED_WITH_BEST_EXPRESSION")
    assert experiment["swappedBetCount"] == 1
    # b1's stake (10.0) * F5's return-per-dollar (16/20=0.8) = 8.0, replacing the real -10.0 loss
    assert experiment["totalNetProfitLoss"] == 8.0 + 16.0


def test_strategy_experiments_never_full_game_ml_with_bullpen_disadvantage_excludes_tagged_bets(tmp_path):
    bets = [
        _bet("b1", stake=10.0, net_profit_loss=-10.0, result="LOSS", thesis_tags=["BULLPEN_DISADVANTAGE"]),
        _bet("b2", stake=10.0, net_profit_loss=5.0, result="WIN", thesis_tags=[]),
    ]
    with _session(tmp_path, bets=bets) as session:
        result = strategy_experiments(session, [])
    experiment = next(e for e in result["experiments"] if e["name"] == "NEVER_FULL_GAME_ML_WITH_BULLPEN_DISADVANTAGE")
    assert experiment["excludedBetCount"] == 1
    assert experiment["n"] == 1
    assert experiment["totalNetProfitLoss"] == 5.0


def test_strategy_experiments_remove_negative_clv_excludes_only_negative_clv(tmp_path):
    bets = [
        _bet("b1", stake=10.0, net_profit_loss=-5.0, result="LOSS", clv=-2.0),
        _bet("b2", stake=10.0, net_profit_loss=5.0, result="WIN", clv=1.0),
        _bet("b3", stake=10.0, net_profit_loss=5.0, result="WIN", clv=None),
    ]
    with _session(tmp_path, bets=bets) as session:
        result = strategy_experiments(session, [])
    experiment = next(e for e in result["experiments"] if e["name"] == "REMOVE_NEGATIVE_CLV_MARKETS")
    assert experiment["excludedBetCount"] == 1
    assert experiment["n"] == 2


def test_strategy_experiments_empty_when_bets_unavailable(tmp_path):
    with _session(tmp_path) as session:
        result = strategy_experiments(session, [])
    assert result["baseline"] is None
    assert result["experiments"] == []


def test_strategy_experiments_deterministic_across_repeated_calls(tmp_path):
    bets = [_bet("b1", stake=10.0, net_profit_loss=5.0, result="WIN")]
    with _session(tmp_path, bets=bets) as session:
        a = strategy_experiments(session, [])
        b = strategy_experiments(session, [])
    assert a == b


# ── Edge stability ────────────────────────────────────────────────────────

def test_edge_stability_unknown_with_a_single_snapshot_and_no_bet(tmp_path):
    evaluations = [_raw_eval_row(modelEvaluationId="me1", marketTicker="A", estimatedEdge=3.0)]
    with _session(tmp_path, evaluations=evaluations) as session:
        rows = edge_stability(session)
    row = next(r for r in rows if r["edgeBucket"] == 2.0)
    assert row["unknownCount"] == 1


def test_edge_stability_volatile_when_edge_bucket_changes_over_time(tmp_path):
    evaluations = [
        _raw_eval_row(modelEvaluationId="me1", marketTicker="A", estimatedEdge=3.0, createdAt="2026-07-29T10:00:00Z"),
        _raw_eval_row(modelEvaluationId="me2", marketTicker="A", estimatedEdge=-3.0, createdAt="2026-07-30T10:00:00Z"),
    ]
    with _session(tmp_path, evaluations=evaluations) as session:
        rows = edge_stability(session)
    row = next(r for r in rows if r["edgeBucket"] == 2.0)  # bucketed on the FIRST snapshot's edge
    assert row["volatileCount"] == 1


def test_edge_stability_volatile_when_lineup_confirmation_flips_edge_bucket(tmp_path):
    evaluations = [
        _raw_eval_row(modelEvaluationId="me1", marketTicker="A", estimatedEdge=3.0, lineupConfirmationState="PROJECTED", createdAt="2026-07-29T10:00:00Z"),
        _raw_eval_row(modelEvaluationId="me2", marketTicker="A", estimatedEdge=-5.0, lineupConfirmationState="CONFIRMED", createdAt="2026-07-30T10:00:00Z"),
    ]
    with _session(tmp_path, evaluations=evaluations) as session:
        rows = edge_stability(session)
    row = next(r for r in rows if r["edgeBucket"] == 2.0)
    assert row["volatileCount"] == 1


def test_edge_stability_stable_when_settled_bet_confirms_the_edge(tmp_path):
    evaluations = [_raw_eval_row(modelEvaluationId="me1", marketTicker="A", estimatedEdge=3.0)]
    bets = [_bet("b1", model_evaluation_id="me1", market_ticker="A", result="WIN")]
    with _session(tmp_path, evaluations=evaluations, bets=bets) as session:
        rows = edge_stability(session)
    row = next(r for r in rows if r["edgeBucket"] == 2.0)
    assert row["stableCount"] == 1


def test_edge_stability_false_edge_when_settled_bet_lost(tmp_path):
    evaluations = [_raw_eval_row(modelEvaluationId="me1", marketTicker="A", estimatedEdge=3.0)]
    bets = [_bet("b1", model_evaluation_id="me1", market_ticker="A", result="LOSS")]
    with _session(tmp_path, evaluations=evaluations, bets=bets) as session:
        rows = edge_stability(session)
    row = next(r for r in rows if r["edgeBucket"] == 2.0)
    assert row["falseEdgeCount"] == 1


def test_edge_stability_never_scores_an_unbet_market_from_raw_settlement(tmp_path):
    """
    Regression guard for a real bug found while building this module:
    Settlement.result is YES/NO (did THIS TICKER's YES side settle
    true), not WIN/LOSS, and turning that into a win/loss requires a
    known side -- which Recommendation/ModelEvaluation never record. A
    market that was never bet must stay UNKNOWN even when a real
    Settlement row (with settlementStatus=SETTLED, result=YES) exists,
    never STABLE/FALSE_EDGE.
    """
    evaluations = [_raw_eval_row(modelEvaluationId="me1", marketTicker="A", estimatedEdge=3.0)]
    settlements = [_settlement("A", result="YES")]
    with _session(tmp_path, evaluations=evaluations, settlements=settlements) as session:
        rows = edge_stability(session)
    row = next(r for r in rows if r["edgeBucket"] == 2.0)
    assert row["unknownCount"] == 1
    assert row["stableCount"] == 0
    assert row["falseEdgeCount"] == 0


def test_edge_stability_deterministic(tmp_path):
    evaluations = [_raw_eval_row(modelEvaluationId="me1", marketTicker="A", estimatedEdge=3.0)]
    with _session(tmp_path, evaluations=evaluations) as session:
        a = edge_stability(session)
        b = edge_stability(session)
    assert a == b


# ── Market health scores ──────────────────────────────────────────────────

def test_market_health_score_combines_available_components(tmp_path):
    evaluations = [_raw_eval_row(modelEvaluationId="me1", marketTicker="A", marketFamily="game_result", estimatedEdge=3.0)]
    bets = [_bet("b1", model_evaluation_id="me1", market_ticker="A", market_family="game_result", clv=2.0, result="WIN")]
    with _session(tmp_path, evaluations=evaluations, bets=bets) as session:
        scores = market_health_scores(session, [])
    row = next(s for s in scores if s["canonicalMarketFamily"] == "game_result")
    assert row["healthScore"] is not None
    assert 0.0 <= row["healthScore"] <= 1.0
    assert row["components"]["clvQuality"] == 1.0  # the one settled bet had positive CLV


def test_market_health_score_none_when_no_component_available(tmp_path):
    with _session(tmp_path) as session:
        scores = market_health_scores(session, [])
    assert scores == []


def test_market_health_scores_deterministic_and_reproducible(tmp_path):
    evaluations = [_raw_eval_row(modelEvaluationId="me1", marketTicker="A", marketFamily="game_result")]
    with _session(tmp_path, evaluations=evaluations) as session:
        a = market_health_scores(session, [])
        b = market_health_scores(session, [])
    assert a == b
