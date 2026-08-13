#!/usr/bin/env python3
"""
tests/edgelab/test_market_comparison.py
============================================
Coverage for lib/edgelab/market_comparison.py (EdgeLab Phase 2 Milestone 5
-- docs/EDGELAB_MARKET_COMPARISON.md): clustering, three-way tie
adjustment, dominated-market detection, the transparent comparison score,
comparison-status assignment, and the end-to-end build_comparisons()/
historical_analysis() wiring. Builds on the same DuckDB-over-tmp_path
fixture pattern tests/edgelab/test_calibration.py established.

NOTE ON SCALES (a real-data finding, confirmed against committed
ModelEvaluation records before writing this module -- see
lib/edgelab/market_comparison.py's own "NOTE ON SCALES" comment):
modelFairProbability/marketImpliedProbability are 0-100 percentages
(e.g. 64.93), NOT 0-1 fractions; estimatedEdge is a smaller
"percentage-edge" figure (real range roughly -11..+5); ClvQuote's
yesBid/yesAsk (the source of bidAskSpread) are ALSO 0-100 on disk
(EdgeLab Research Trustworthiness milestone follow-up -- corrected from
this file's earlier, unverified claim that they were 0-1) --
normalize_market_input() divides by 100 so bidAskSpread itself always
comes out 0-1. Every fixture below uses these real scales, not invented
ones.
"""
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.analytics import open_session
from lib.edgelab.market_comparison import (
    HORIZON_F5,
    HORIZON_FULL_GAME,
    HORIZON_UNKNOWN,
    STATUS_ALTERNATIVE_EXPRESSION,
    STATUS_BEST_EXPRESSION,
    STATUS_DISTINCT_THESIS,
    STATUS_DOMINATED_MARKET,
    STATUS_HIGH_TIE_RISK,
    STATUS_INCOMPLETE_COMPARISON,
    STATUS_LOW_DATA_QUALITY,
    STATUS_LOW_LIQUIDITY,
    STATUS_NO_MODEL_SUPPORT,
    STATUS_NOT_COMPARABLE,
    THESIS_PLAYER_PROP,
    THESIS_TEAM_TOTAL,
    THESIS_WIN,
    apply_three_way_adjustment,
    assign_comparison_statuses,
    build_clusters,
    build_comparisons,
    cluster_key,
    comparison_score,
    data_quality_rank,
    historical_analysis,
    is_dominated_by,
    latest_evaluations_per_market,
    market_horizon_and_team,
    normalize_market_input,
    thesis_group,
)


# ── Fixture builders ──────────────────────────────────────────────────────

def _eval_row(**overrides):
    row = {
        "modelEvaluationId": (overrides.get("marketTicker") or "me") + "-id",
        "runId": "run1",
        "gameId": "g1",
        "marketTicker": None,
        "canonicalMarketFamily": "game_result",
        "selection": "ML_Away",
        "side": None,
        "threshold": None,
        "evaluationStatus": "EVALUATED",
        "modelFairProbability": 60.0,
        "marketImpliedProbability": 50.0,
        "estimatedEdge": 5.0,
        "evPerDollar": None,
        "confidence": "HIGH",
        "dataQuality": "full",
        "lineupConfirmationState": "CONFIRMED",
        "modelVersion": "v1",
        "modelSource": "scripts/build_market_ledger.py",
        "thesisTags": [],
        "correlationGroups": [],
        "createdAt": "2026-07-30T12:00:00Z",
    }
    row.update(overrides)
    return row


def _raw_eval_row(**overrides):
    """
    A ModelEvaluation row in its REAL on-disk shape for JSONL fixtures
    read through open_session() -- unlike _eval_row() (which mimics
    v_model_evaluations' query-time OUTPUT, used for direct dict-based
    unit tests), the raw schema's family field is named `marketFamily`
    (canonicalized to `canonicalMarketFamily` only at query time via
    lib.edgelab.market_family_mapping -- see model_evaluation.schema.json).
    """
    family = overrides.pop("marketFamily", "game_result")
    row = _eval_row(**overrides)
    row.pop("canonicalMarketFamily", None)
    row["marketFamily"] = family
    return row


def _write_jsonl(path, records, compressed=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    opener = gzip.open if compressed else open
    with opener(path, "wt") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _session(tmp_path, evaluations=None, bets=None, clv_quotes=None):
    if evaluations is not None:
        _write_jsonl(str(tmp_path / "model_evaluations" / "evals.jsonl"), evaluations)
    if bets is not None:
        _write_jsonl(str(tmp_path / "bets" / "bets.jsonl"), bets)
    if clv_quotes is not None:
        _write_jsonl(str(tmp_path / "clv_quotes" / "quotes.jsonl"), clv_quotes)
    return open_session(root=str(tmp_path))


# ── market_horizon_and_team / thesis_group / data_quality_rank ──────────

def test_horizon_and_team_from_selection_fallback_when_no_ticker():
    assert market_horizon_and_team(None, "F5_ML_Home") == (HORIZON_F5, "HOME")
    assert market_horizon_and_team(None, "ML_Away") == (HORIZON_FULL_GAME, "AWAY")
    assert market_horizon_and_team(None, "Game_Total") == (HORIZON_FULL_GAME, None)


def test_horizon_and_team_unresolved_selection_and_no_ticker_is_unknown():
    assert market_horizon_and_team(None, None) == (HORIZON_UNKNOWN, None)
    assert market_horizon_and_team(None, "not_a_real_selection") == (HORIZON_UNKNOWN, None)


def test_horizon_from_ticker_team_backfilled_from_selection_when_classifier_gives_no_team():
    """
    Real-data finding: classify_market() resolves `scope` (horizon) for a
    game_result ticker but always returns team=None for that family. This
    must not stop the selection-name fallback from supplying team -- an
    earlier version of this function short-circuited on any successful
    classification and dropped the team entirely.
    """
    horizon, team = market_horizon_and_team("KXMLBGAME-26JUL312210MINSEA-SEA", "ML_Home")
    assert horizon == HORIZON_FULL_GAME
    assert team == "HOME"


def test_thesis_group_mapping():
    assert thesis_group("game_result") == THESIS_WIN
    assert thesis_group("team_total") == THESIS_TEAM_TOTAL
    assert thesis_group("pitcher_strikeouts") == THESIS_PLAYER_PROP
    assert thesis_group("unmapped_family") is None


def test_data_quality_rank_orders_full_above_partial_above_insufficient_above_none():
    assert data_quality_rank("full") > data_quality_rank("partial") > data_quality_rank("insufficient") > data_quality_rank("none")
    assert data_quality_rank(None) < data_quality_rank("none")


# ── normalize_market_input ────────────────────────────────────────────────

def test_normalize_market_input_missing_required_fields_reported():
    row = _eval_row(modelFairProbability=None, confidence=None)
    normalized = normalize_market_input(row)
    assert set(normalized["missingFields"]) == {"modelFairProbability", "confidence"}


def test_normalize_market_input_never_guesses_liquidity():
    row = _eval_row()
    normalized = normalize_market_input(row)
    assert normalized["liquidity"] is None


def test_normalize_market_input_bid_ask_spread_from_clv_quote():
    """ClvQuote.yesBid/yesAsk are 0-100 on disk (real-data finding); bidAskSpread must come out 0-1."""
    row = _eval_row(marketTicker="T1")
    clv = {"yesBid": 45.0, "yesAsk": 52.0}
    normalized = normalize_market_input(row, clv_row=clv)
    assert abs(normalized["bidAskSpread"] - 0.07) < 1e-9


def test_normalize_market_input_placed_bet_indicator_and_clv():
    row = _eval_row(marketTicker="T1", modelEvaluationId="me1")
    bet = {"betId": "b1", "entryPrice": 0.55, "closingPrice": 0.50, "clv": 5.0}
    normalized = normalize_market_input(row, bet_row=bet)
    assert normalized["placedBetIndicator"] is True
    assert normalized["betId"] == "b1"
    assert normalized["clv"] == 5.0


# ── latest_evaluations_per_market (multiple evaluations over time) ──────

def test_latest_evaluation_wins_by_created_at():
    old = _eval_row(marketTicker="T1", createdAt="2026-07-30T10:00:00Z", estimatedEdge=1.0)
    new = _eval_row(marketTicker="T1", createdAt="2026-07-31T10:00:00Z", estimatedEdge=9.0)
    latest = latest_evaluations_per_market([old, new])
    assert len(latest) == 1
    assert latest[0]["estimatedEdge"] == 9.0


def test_unresolved_ticker_rows_keyed_separately_not_collapsed():
    """Two PARSER_UNRESOLVED rows (marketTicker=None) for different markets in the same game must not collapse into one."""
    row_a = _eval_row(marketTicker=None, gameId="g1", selection="ML_Away", side=None, threshold=None)
    row_b = _eval_row(marketTicker=None, gameId="g1", selection="ML_Home", side=None, threshold=None)
    latest = latest_evaluations_per_market([row_a, row_b])
    assert len(latest) == 2


def test_doubleheader_isolation_different_game_ids_never_collapsed():
    game1 = _eval_row(marketTicker=None, gameId="g1-1", selection="ML_Away")
    game2 = _eval_row(marketTicker=None, gameId="g1-2", selection="ML_Away")
    latest = latest_evaluations_per_market([game1, game2])
    assert len(latest) == 2
    assert {r["gameId"] for r in latest} == {"g1-1", "g1-2"}


# ── Three-way market modeling (F3/F5/F7 win/tie/loss) ────────────────────

def test_three_way_adjustment_computes_tie_and_renormalized_price():
    away = normalize_market_input(_eval_row(marketTicker="F5A", selection="F5_ML_Away", canonicalMarketFamily="inning_result", modelFairProbability=55.0))
    home = normalize_market_input(_eval_row(marketTicker="F5H", selection="F5_ML_Home", canonicalMarketFamily="inning_result", modelFairProbability=40.0))
    apply_three_way_adjustment(away, home)
    assert away["tieProbability"] == 5.0
    assert home["tieProbability"] == 5.0
    assert away["winProbability"] == 55.0
    assert away["comparisonEligibility"] is True
    # tieAdjustedFairPrice renormalizes win/(win+loss) excluding the tie
    assert abs(away["tieAdjustedFairPrice"] - 55.0 / (55.0 + 40.0)) < 1e-9


def test_three_way_adjustment_ineligible_when_either_side_missing_probability():
    away = normalize_market_input(_eval_row(marketTicker="F5A", selection="F5_ML_Away", canonicalMarketFamily="inning_result", modelFairProbability=None))
    home = normalize_market_input(_eval_row(marketTicker="F5H", selection="F5_ML_Home", canonicalMarketFamily="inning_result", modelFairProbability=40.0))
    apply_three_way_adjustment(away, home)
    assert away["comparisonEligibility"] is False
    assert home["comparisonEligibility"] is False
    assert away["tieProbability"] is None


def test_three_way_adjustment_skipped_for_non_three_way_horizon():
    away = normalize_market_input(_eval_row(marketTicker="MLA", selection="ML_Away", modelFairProbability=55.0))
    home = normalize_market_input(_eval_row(marketTicker="MLH", selection="ML_Home", modelFairProbability=40.0))
    apply_three_way_adjustment(away, home)
    assert away["tieProbability"] is None  # full-game ML is two-way -- never adjusted


def test_two_way_market_never_compared_using_unadjusted_implied_probabilities():
    """A full-game (two-way) market's tieProbability must stay None -- it must never be silently treated as tie-free-adjustable like a three-way market."""
    ml = normalize_market_input(_eval_row(marketTicker="MLA", selection="ML_Away", canonicalMarketFamily="game_result"))
    assert ml["horizon"] == HORIZON_FULL_GAME
    assert ml["tieProbability"] is None
    assert ml["tieAdjustedFairPrice"] is None


# ── Clustering (item 3) ──────────────────────────────────────────────────

def test_cluster_key_win_thesis_groups_by_game_and_side_across_horizons():
    ml = normalize_market_input(_eval_row(marketTicker="ML", selection="ML_Away", canonicalMarketFamily="game_result"))
    f5 = normalize_market_input(_eval_row(marketTicker="F5", selection="F5_ML_Away", canonicalMarketFamily="inning_result"))
    rl = normalize_market_input(_eval_row(marketTicker="RL", selection="RL_Away", canonicalMarketFamily="winning_margin"))
    assert cluster_key(ml) == cluster_key(f5) == cluster_key(rl)


def test_cluster_key_different_sides_never_share_a_cluster():
    away = normalize_market_input(_eval_row(marketTicker="MLA", selection="ML_Away"))
    home = normalize_market_input(_eval_row(marketTicker="MLH", selection="ML_Home"))
    assert cluster_key(away) != cluster_key(home)


def test_cluster_key_team_total_vs_opposing_pitcher_outs_share_a_cluster():
    """
    An AWAY team's total joins the SAME cluster as the HOME pitcher's
    outs/strikeouts props (the home pitcher's outs suppress the away
    team's total -- same underlying edge, different expression).
    """
    away_total = normalize_market_input(_eval_row(marketTicker="TTA", selection="TT_Away_Over", canonicalMarketFamily="team_total"))
    home_pitcher_outs = normalize_market_input(_eval_row(marketTicker="POH", selection=None, canonicalMarketFamily="pitcher_outs"))
    home_pitcher_outs["team"] = "HOME"  # pitcher's own side
    assert away_total["team"] == "AWAY"
    assert cluster_key(away_total) == cluster_key(home_pitcher_outs)


def test_cluster_key_strikeouts_vs_outs_same_side_share_a_cluster_via_team_total():
    home_outs = normalize_market_input(_eval_row(marketTicker="PO", canonicalMarketFamily="pitcher_outs"))
    home_ks = normalize_market_input(_eval_row(marketTicker="PK", canonicalMarketFamily="pitcher_strikeouts"))
    home_outs["team"] = "HOME"
    home_ks["team"] = "HOME"
    assert cluster_key(home_outs) == cluster_key(home_ks)


def test_cluster_key_game_total_and_first_inning_are_game_level_not_side_level():
    game_total = normalize_market_input(_eval_row(marketTicker="GT", selection="Game_Total", canonicalMarketFamily="game_total"))
    nrfi = normalize_market_input(_eval_row(marketTicker="NRFI", selection="NRFI", canonicalMarketFamily="first_inning_run"))
    assert cluster_key(game_total) != cluster_key(nrfi)
    assert "GAME" in cluster_key(game_total)


def test_cluster_key_none_for_unresolved_side_or_unmapped_family():
    unmapped = normalize_market_input(_eval_row(marketTicker="X", canonicalMarketFamily="not_a_real_family"))
    assert cluster_key(unmapped) is None
    unresolved_side = normalize_market_input(_eval_row(marketTicker=None, selection="unmapped_selection", canonicalMarketFamily="game_result"))
    assert cluster_key(unresolved_side) is None


def test_build_clusters_groups_only_rows_with_a_resolvable_key():
    rows = [
        normalize_market_input(_eval_row(marketTicker="ML", selection="ML_Away")),
        normalize_market_input(_eval_row(marketTicker="F5", selection="F5_ML_Away", canonicalMarketFamily="inning_result")),
        normalize_market_input(_eval_row(marketTicker="X", canonicalMarketFamily="not_a_real_family")),
    ]
    clusters = build_clusters(rows)
    assert sum(len(v) for v in clusters.values()) == 2


# ── Dominated-market detection (item 6) ──────────────────────────────────

def test_f5_dominates_full_game_ml_under_bullpen_risk_with_better_edge():
    full_game = normalize_market_input(_eval_row(marketTicker="ML", selection="ML_Away", canonicalMarketFamily="game_result", estimatedEdge=2.0, dataQuality="full"))
    f5 = normalize_market_input(_eval_row(marketTicker="F5", selection="F5_ML_Away", canonicalMarketFamily="inning_result", estimatedEdge=3.0, dataQuality="full"))
    assert is_dominated_by(full_game, f5) is True
    assert is_dominated_by(f5, full_game) is False


def test_ml_vs_run_line_domination_is_purely_edge_and_quality_driven():
    ml = normalize_market_input(_eval_row(marketTicker="ML", selection="ML_Away", canonicalMarketFamily="game_result", estimatedEdge=1.0, dataQuality="full"))
    rl = normalize_market_input(_eval_row(marketTicker="RL", selection="RL_Away", canonicalMarketFamily="winning_margin", estimatedEdge=4.0, dataQuality="full"))
    assert is_dominated_by(ml, rl) is True


def test_run_line_vs_ml_domination_reverses_when_run_line_is_worse():
    ml = normalize_market_input(_eval_row(marketTicker="ML", selection="ML_Away", canonicalMarketFamily="game_result", estimatedEdge=4.0, dataQuality="full"))
    rl = normalize_market_input(_eval_row(marketTicker="RL", selection="RL_Away", canonicalMarketFamily="winning_margin", estimatedEdge=1.0, dataQuality="full"))
    assert is_dominated_by(rl, ml) is True
    assert is_dominated_by(ml, rl) is False


def test_alternate_totals_higher_edge_and_no_worse_quality_dominates():
    total_a = normalize_market_input(_eval_row(marketTicker="GT1", selection="Game_Total", canonicalMarketFamily="game_total", threshold=8.5, estimatedEdge=1.0, dataQuality="full"))
    total_b = normalize_market_input(_eval_row(marketTicker="GT2", selection="Game_Total", canonicalMarketFamily="game_total", threshold=9.5, estimatedEdge=3.0, dataQuality="full"))
    assert is_dominated_by(total_a, total_b) is True


def test_team_total_dominated_by_opposing_pitcher_outs_under():
    """docs' explicit test example: team total vs pitcher-outs -- same cluster (see cluster_key test above), domination follows the same edge/quality/risk rules."""
    away_total = normalize_market_input(_eval_row(marketTicker="TTA", selection="TT_Away_Over", canonicalMarketFamily="team_total", estimatedEdge=1.0, dataQuality="partial"))
    home_pitcher_outs = normalize_market_input(_eval_row(marketTicker="POH", canonicalMarketFamily="pitcher_outs", estimatedEdge=4.0, dataQuality="full"))
    home_pitcher_outs["team"] = "HOME"
    assert is_dominated_by(away_total, home_pitcher_outs) is True


def test_worse_data_quality_never_dominates_even_with_higher_edge():
    good = normalize_market_input(_eval_row(marketTicker="A", selection="ML_Away", estimatedEdge=1.0, dataQuality="full"))
    bad_but_higher_edge = normalize_market_input(_eval_row(marketTicker="B", selection="F5_ML_Away", canonicalMarketFamily="inning_result", estimatedEdge=9.0, dataQuality="insufficient"))
    assert is_dominated_by(good, bad_but_higher_edge) is False


def test_identical_markets_never_dominate_each_other():
    a = normalize_market_input(_eval_row(marketTicker="A", selection="ML_Away", estimatedEdge=2.0, dataQuality="full"))
    b = normalize_market_input(_eval_row(marketTicker="B", selection="ML_Away", estimatedEdge=2.0, dataQuality="full"))
    assert is_dominated_by(a, b) is False
    assert is_dominated_by(b, a) is False


def test_correlated_but_distinct_thesis_markets_never_marked_dominated():
    """Correlation alone must not make a market dominated -- team_total and game_total share correlationGroups conceptually but are DISTINCT_THESIS, never even clustered together, so domination is never evaluated between them."""
    team_total = normalize_market_input(_eval_row(marketTicker="TT", selection="TT_Away_Over", canonicalMarketFamily="team_total", correlationGroups=["TEAM_RUNS_OVER_AWAY"]))
    game_total = normalize_market_input(_eval_row(marketTicker="GT", selection="Game_Total", canonicalMarketFamily="game_total", correlationGroups=["GAME_OVER"]))
    assert cluster_key(team_total) != cluster_key(game_total)


def test_missing_estimated_edge_never_used_to_dominate_or_be_dominated():
    complete = normalize_market_input(_eval_row(marketTicker="A", selection="ML_Away", estimatedEdge=2.0, dataQuality="full"))
    incomplete = normalize_market_input(_eval_row(marketTicker="B", selection="F5_ML_Away", canonicalMarketFamily="inning_result", estimatedEdge=None, dataQuality="full"))
    assert is_dominated_by(complete, incomplete) is False
    assert is_dominated_by(incomplete, complete) is False


# ── Transparent deterministic comparison score (item 7) ──────────────────

def test_comparison_score_excludes_missing_components_and_renormalizes():
    row = normalize_market_input(_eval_row(marketTicker="A", selection="ML_Away", estimatedEdge=5.0, confidence="HIGH", dataQuality="full"))
    total, components = comparison_score(row, [row])
    assert total is not None
    assert components["liquidity"] is None  # always unavailable, documented
    assert 0.0 <= total <= 1.0


def test_comparison_score_none_when_no_component_available():
    row = normalize_market_input(_eval_row(marketTicker="A", selection=None, canonicalMarketFamily="not_a_real_family",
                                            estimatedEdge=None, confidence=None, dataQuality=None, marketImpliedProbability=None))
    total, components = comparison_score(row, [row])
    assert total is None
    assert all(v is None for v in components.values())


def test_comparison_score_is_deterministic_across_repeated_calls():
    row = normalize_market_input(_eval_row(marketTicker="A", selection="ML_Away", estimatedEdge=3.0, confidence="MEDIUM", dataQuality="partial"))
    results = [comparison_score(row, [row])[0] for _ in range(5)]
    assert len(set(results)) == 1


# ── Comparison statuses (item 8) ─────────────────────────────────────────

def test_incomplete_comparison_when_required_field_missing():
    row = normalize_market_input(_eval_row(marketTicker="A", selection="ML_Away", modelFairProbability=None))
    assign_comparison_statuses([row])
    assert row["comparisonStatus"] == STATUS_INCOMPLETE_COMPARISON


def test_no_model_support_status():
    row = normalize_market_input(_eval_row(marketTicker="A", selection="ML_Away", evaluationStatus="NO_MODEL_SUPPORT"))
    assign_comparison_statuses([row])
    assert row["comparisonStatus"] == STATUS_NO_MODEL_SUPPORT


def test_low_data_quality_status():
    row = normalize_market_input(_eval_row(marketTicker="A", selection="ML_Away", dataQuality="insufficient"))
    assign_comparison_statuses([row])
    assert row["comparisonStatus"] == STATUS_LOW_DATA_QUALITY


def test_high_tie_risk_status():
    away = normalize_market_input(_eval_row(marketTicker="F5A", selection="F5_ML_Away", canonicalMarketFamily="inning_result", modelFairProbability=45.0, dataQuality="full"))
    home = normalize_market_input(_eval_row(marketTicker="F5H", selection="F5_ML_Home", canonicalMarketFamily="inning_result", modelFairProbability=30.0, dataQuality="full"))
    apply_three_way_adjustment(away, home)  # tie = 25 -- above HIGH_TIE_RISK_THRESHOLD (20)
    assign_comparison_statuses([away, home])
    assert away["comparisonStatus"] == STATUS_HIGH_TIE_RISK
    assert home["comparisonStatus"] == STATUS_HIGH_TIE_RISK


def test_low_liquidity_status_from_wide_bid_ask_spread():
    row = normalize_market_input(_eval_row(marketTicker="A", selection="ML_Away", dataQuality="full"), clv_row={"yesBid": 30.0, "yesAsk": 55.0})
    assign_comparison_statuses([row])
    assert row["comparisonStatus"] == STATUS_LOW_LIQUIDITY


def test_normal_bid_ask_spread_does_not_trigger_low_liquidity():
    """A realistic 1-2 cent spread (yesBid=45, yesAsk=46 on the real 0-100 scale) must NOT read as LOW_LIQUIDITY -- regression test for the bidAskSpread scale bug."""
    row = normalize_market_input(_eval_row(marketTicker="A", selection="ML_Away", dataQuality="full"), clv_row={"yesBid": 45.0, "yesAsk": 46.0})
    assert abs(row["bidAskSpread"] - 0.01) < 1e-9
    assign_comparison_statuses([row])
    assert row["comparisonStatus"] != STATUS_LOW_LIQUIDITY


def test_distinct_thesis_status_for_game_total():
    row = normalize_market_input(_eval_row(marketTicker="GT", selection="Game_Total", canonicalMarketFamily="game_total", dataQuality="full"))
    assign_comparison_statuses([row])
    assert row["comparisonStatus"] == STATUS_DISTINCT_THESIS


def test_not_comparable_status_for_unmapped_family():
    row = normalize_market_input(_eval_row(marketTicker="X", canonicalMarketFamily="not_a_real_family", dataQuality="full"))
    assign_comparison_statuses([row])
    assert row["comparisonStatus"] == STATUS_NOT_COMPARABLE


def test_best_expression_and_alternative_expression_ranked_deterministically():
    """Both markets are FULL_GAME horizon (no bullpen-risk/tie-risk difference) with equal edge/dataQuality, so domination never triggers -- only the relative score (via differing confidence) decides BEST vs ALTERNATIVE."""
    a = normalize_market_input(_eval_row(marketTicker="A", selection="ML_Away", canonicalMarketFamily="game_result", estimatedEdge=2.0, dataQuality="full", confidence="HIGH"))
    b = normalize_market_input(_eval_row(marketTicker="B", selection="RL_Away", canonicalMarketFamily="winning_margin", estimatedEdge=2.0, dataQuality="full", confidence="MEDIUM"))
    assign_comparison_statuses([a, b])
    statuses = {a["comparisonStatus"], b["comparisonStatus"]}
    assert statuses == {STATUS_BEST_EXPRESSION, STATUS_ALTERNATIVE_EXPRESSION}
    best = a if a["comparisonStatus"] == STATUS_BEST_EXPRESSION else b
    assert best["comparisonRank"] == 1


def test_ranking_deterministic_across_repeated_calls():
    rows1 = [normalize_market_input(_eval_row(marketTicker="A", selection="ML_Away", canonicalMarketFamily="game_result", estimatedEdge=2.0, dataQuality="full")),
             normalize_market_input(_eval_row(marketTicker="B", selection="RL_Away", canonicalMarketFamily="winning_margin", estimatedEdge=2.0, dataQuality="full"))]
    rows2 = [normalize_market_input(_eval_row(marketTicker="A", selection="ML_Away", canonicalMarketFamily="game_result", estimatedEdge=2.0, dataQuality="full")),
             normalize_market_input(_eval_row(marketTicker="B", selection="RL_Away", canonicalMarketFamily="winning_margin", estimatedEdge=2.0, dataQuality="full"))]
    assign_comparison_statuses(rows1)
    assign_comparison_statuses(rows2)
    assert [r["comparisonStatus"] for r in rows1] == [r["comparisonStatus"] for r in rows2]
    assert [r["comparisonRank"] for r in rows1] == [r["comparisonRank"] for r in rows2]


def test_dominated_market_carries_dominant_ticker_and_reasons():
    full_game = normalize_market_input(_eval_row(marketTicker="ML", selection="ML_Away", canonicalMarketFamily="game_result", estimatedEdge=2.0, dataQuality="full"))
    f5 = normalize_market_input(_eval_row(marketTicker="F5", selection="F5_ML_Away", canonicalMarketFamily="inning_result", estimatedEdge=3.0, dataQuality="full"))
    assign_comparison_statuses([full_game, f5])
    assert full_game["comparisonStatus"] == STATUS_DOMINATED_MARKET
    assert full_game["dominantMarketTicker"] == "F5"
    assert "HIGHER_EV" in full_game["dominationReasons"]
    assert "LOWER_MATERIAL_RISK" in full_game["dominationReasons"]


# ── End-to-end build_comparisons() / historical_analysis() ──────────────

def test_build_comparisons_empty_when_no_model_evaluations(tmp_path):
    with _session(tmp_path) as session:
        assert build_comparisons(session) == []


def test_build_comparisons_end_to_end_marks_f5_as_best_expression(tmp_path):
    evaluations = [
        _raw_eval_row(marketTicker="ML", modelEvaluationId="me-ml", gameId="g1", selection="ML_Away",
                      marketFamily="game_result", estimatedEdge=2.0, dataQuality="full"),
        _raw_eval_row(marketTicker="F5", modelEvaluationId="me-f5", gameId="g1", selection="F5_ML_Away",
                      marketFamily="inning_result", estimatedEdge=3.0, dataQuality="full"),
    ]
    with _session(tmp_path, evaluations=evaluations) as session:
        comparisons = build_comparisons(session)
    by_ticker = {c["marketTicker"]: c for c in comparisons}
    assert by_ticker["F5"]["comparisonStatus"] == STATUS_BEST_EXPRESSION
    assert by_ticker["ML"]["comparisonStatus"] == STATUS_DOMINATED_MARKET


def test_build_comparisons_doubleheader_isolation(tmp_path):
    evaluations = [
        _raw_eval_row(marketTicker="ML-G1", modelEvaluationId="me-g1", gameId="g1-1", selection="ML_Away", estimatedEdge=2.0, dataQuality="full"),
        _raw_eval_row(marketTicker="ML-G2", modelEvaluationId="me-g2", gameId="g1-2", selection="ML_Away", estimatedEdge=2.0, dataQuality="full"),
    ]
    with _session(tmp_path, evaluations=evaluations) as session:
        comparisons = build_comparisons(session)
    cluster_ids = {c["marketTicker"]: c["clusterId"] for c in comparisons}
    assert cluster_ids["ML-G1"] != cluster_ids["ML-G2"]


def test_build_comparisons_multiple_evaluations_over_time_uses_latest(tmp_path):
    evaluations = [
        _raw_eval_row(marketTicker="ML", modelEvaluationId="me-old", gameId="g1", selection="ML_Away",
                      estimatedEdge=1.0, createdAt="2026-07-29T10:00:00Z"),
        _raw_eval_row(marketTicker="ML", modelEvaluationId="me-new", gameId="g1", selection="ML_Away",
                      estimatedEdge=9.0, createdAt="2026-07-30T10:00:00Z"),
    ]
    with _session(tmp_path, evaluations=evaluations) as session:
        comparisons = build_comparisons(session)
    assert len(comparisons) == 1
    assert comparisons[0]["estimatedEdge"] == 9.0


def test_placed_bet_vs_best_expression_audit(tmp_path):
    evaluations = [
        _raw_eval_row(marketTicker="ML", modelEvaluationId="me-ml", gameId="g1", selection="ML_Away",
                      marketFamily="game_result", estimatedEdge=2.0, dataQuality="full"),
        _raw_eval_row(marketTicker="F5", modelEvaluationId="me-f5", gameId="g1", selection="F5_ML_Away",
                      marketFamily="inning_result", estimatedEdge=3.0, dataQuality="full"),
    ]
    bets = [{
        "betId": "b1", "marketTicker": "ML", "marketFamily": "game_result", "selection": "ML_Away", "side": "YES",
        "stake": 10.0, "entryPrice": 0.5, "entryTimestamp": "2026-07-30T12:00:00Z", "source": "MODEL",
        "modelEvaluationId": "me-ml", "status": "settled", "result": "WIN", "netProfitLoss": 5.0, "clv": 2.0,
    }]
    with _session(tmp_path, evaluations=evaluations, bets=bets) as session:
        comparisons = build_comparisons(session)
        report = historical_analysis(comparisons)
    assert report["placedBetAuditSampleSize"] == 1
    assert report["placedBetNotTopRankedCount"] == 1  # the placed ML bet was dominated by F5, not top-ranked


def test_historical_analysis_reports_missing_data_blockers():
    rows = [normalize_market_input(_eval_row(marketTicker="A", selection="ML_Away", modelFairProbability=None))]
    assign_comparison_statuses(build_clusters(rows).get(cluster_key(rows[0]), rows))
    report = historical_analysis(rows)
    assert report["missingDataBlockers"]


def test_historical_analysis_applies_sample_size_gate():
    report = historical_analysis([])
    assert report["placedBetAuditSampleStatus"] == "INSUFFICIENT_SAMPLE"
    assert report["placedBetAuditSampleSize"] == 0
