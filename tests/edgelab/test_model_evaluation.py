#!/usr/bin/env python3
"""
tests/edgelab/test_model_evaluation.py
===========================================
Coverage for lib/edgelab/model_evaluation.py (EdgeLab Phase 2 Milestone 3
-- docs/EDGELAB_MODEL_EVALUATION.md): evaluation-status classification,
pipeline-derived + full-universe persistence, stable/idempotent IDs, the
two-sided-single-ticker collision fix shared with
lib.edgelab.recommendations, PlacedBet linkage (lib.edgelab.bets), the
analytics-layer join calibration reads through, and the population
report.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import lib.pipeline_artifacts as pipeline_artifacts
from lib.edgelab import calibration as cal
from lib.edgelab import schema
from lib.edgelab.analytics import open_session
from lib.edgelab.bets import build_manual_bet_record, link_bets_to_recommendations
from lib.edgelab.model_evaluation import (
    DATA_QUALITY_BLOCK,
    EVALUATED,
    INVALID_PROBABILITY,
    MISSING_MARKET_PRICE,
    NO_MODEL_SUPPORT,
    NOT_EVALUATED,
    PARSER_UNRESOLVED,
    PARTIAL_EVALUATION,
    build_model_evaluations_from_pipeline,
    classify_evaluation_status,
    extend_full_universe_evaluations,
    population_by_canonical_family,
    population_by_model_version_and_source,
    population_report,
)
from lib.edgelab.recommendations import build_recommendations_from_pipeline

DATE = "2026-07-31"


def _row(**overrides):
    row = {
        "market": "ML_Away", "status": "Accepted", "ticker": None, "marketTicker": None,
        "modelProb": None, "kalshiVF": None, "marketProbVF": None, "executableMarketProb": None,
        "calibratedEdgeVsExecutable": None, "edge": None, "confidenceTier": None, "confidence": None,
        "line": None, "seriesTicker": None, "lineupStatus": None, "lineupConfirmedOfficial": None,
        "lineupDataQuality": None, "missingFields": [], "evaluationError": None, "rejectionReason": None,
    }
    row.update(overrides)
    return row


def _write_recommendations(monkeypatch, tmp_path, games, produced_by="scripts/build_market_ledger.py"):
    monkeypatch.setattr(pipeline_artifacts, "PIPELINE_ROOT", str(tmp_path))
    pipeline_artifacts.write_stage_artifact("recommendations", DATE, {"date": DATE, "games": games}, produced_by=produced_by)


def _game(ticker_rows, game_id="g1", away="PIT", home="CIN", away_extra=None, home_extra=None, f5=None, teamTotals=None, park=None):
    game = {
        "gameId": game_id,
        "away": {"abbr": away, **(away_extra or {})},
        "home": {"abbr": home, **(home_extra or {})},
        "status": "Scheduled", "marketLedger": ticker_rows,
    }
    if f5 is not None:
        game["f5"] = f5
    if teamTotals is not None:
        game["teamTotals"] = teamTotals
    if park is not None:
        game["park"] = park
    return game


# ── classify_evaluation_status: pure-function unit tests ────────────────

def test_evaluated_when_prob_ticker_market_prob_and_edge_all_present():
    row = _row(ticker="T1", modelProb=55.0, kalshiVF=50.0, edge=5.0)
    assert classify_evaluation_status(row) == EVALUATED


def test_partial_evaluation_when_edge_missing():
    row = _row(ticker="T1", modelProb=55.0, kalshiVF=50.0, calibratedEdgeVsExecutable=None, edge=None)
    assert classify_evaluation_status(row) == PARTIAL_EVALUATION


def test_missing_market_price_when_no_market_implied_probability():
    row = _row(ticker="T1", modelProb=55.0, kalshiVF=None, marketProbVF=None, executableMarketProb=None)
    assert classify_evaluation_status(row) == MISSING_MARKET_PRICE


def test_evaluated_when_prob_present_but_no_ticker():
    """
    Real-data finding: scripts/build_market_ledger.py's rejected_row()
    calls for ML_Away/ML_Home/F5_ML_Away/F5_ML_Home/NRFI/YRFI don't
    thread a ticker through even though the market WAS genuinely priced
    and evaluated (kalshiVF/edge both present) -- a missing ticker string
    alone must never downgrade an otherwise-complete evaluation to
    PARSER_UNRESOLVED, which previously mislabeled ~53% of real committed
    evaluated records as if the pipeline couldn't even parse the
    contract identity.
    """
    row = _row(ticker=None, marketTicker=None, modelProb=55.0, kalshiVF=50.0, edge=3.2)
    assert classify_evaluation_status(row) == EVALUATED


def test_partial_evaluation_when_prob_and_market_prob_present_but_no_ticker_and_no_edge():
    row = _row(ticker=None, marketTicker=None, modelProb=55.0, kalshiVF=50.0, edge=None, calibratedEdgeVsExecutable=None)
    assert classify_evaluation_status(row) == PARTIAL_EVALUATION


def test_invalid_probability_rejected_for_out_of_range_values():
    for bad in (0, 100, -5, 150):
        row = _row(ticker="T1", modelProb=bad, kalshiVF=50.0)
        assert classify_evaluation_status(row) == INVALID_PROBABILITY, f"expected INVALID_PROBABILITY for {bad}"


def test_no_model_support_when_no_prob_and_no_specific_reason():
    row = _row(modelProb=None, status="Accepted")
    assert classify_evaluation_status(row) == NO_MODEL_SUPPORT


def test_data_quality_block_for_generic_missing_data():
    row = _row(modelProb=None, status="Missing Data", missingFields=["lineup.confirmed"])
    assert classify_evaluation_status(row) == DATA_QUALITY_BLOCK


def test_missing_market_price_for_missing_data_mentioning_price():
    row = _row(modelProb=None, status="Missing Data", missingFields=["odds.kalshi.nrfi_yrfi.nrfi_american"])
    assert classify_evaluation_status(row) == MISSING_MARKET_PRICE


def test_parser_unresolved_for_evaluation_failed_mentioning_ticker():
    row = _row(modelProb=None, status="Evaluation Failed", evaluationError="could not resolve ticker for market")
    assert classify_evaluation_status(row) == PARSER_UNRESOLVED


def test_data_quality_block_for_evaluation_failed_other_reason():
    row = _row(modelProb=None, status="Evaluation Failed", evaluationError="stats provider timeout")
    assert classify_evaluation_status(row) == DATA_QUALITY_BLOCK


# ── build_model_evaluations_from_pipeline: persistence + IDs ────────────

def test_no_artifact_yields_empty_with_warning(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline_artifacts, "PIPELINE_ROOT", str(tmp_path))
    records, warnings = build_model_evaluations_from_pipeline(DATE, "run1", [])
    assert records == []
    assert warnings


# ── F5 Three-Way Pricing Correction milestone: modelVersion provenance ──

def test_f5_row_copies_f5_pricing_version_into_model_version(monkeypatch, tmp_path):
    games = [_game([_row(
        market="F5_ML_Away", ticker="KXMLBF5-T-AAA", modelProb=47.59, kalshiVF=45.47, edge=2.5,
        confidenceTier="MEDIUM", f5PricingVersion="f5_three_way_v1",
    )])]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_model_evaluations_from_pipeline(DATE, "run1", [])
    assert records[0]["modelVersion"] == "f5_three_way_v1"


def test_non_f5_row_model_version_stays_none(monkeypatch, tmp_path):
    """
    Only F5_ML_Away/F5_ML_Home rows carry f5PricingVersion at all -- every
    other market family has no versioning concept yet, and modelVersion
    must remain None for them exactly as it did before this milestone.
    """
    games = [_game([_row(market="ML_Away", ticker="KXMLBGAME-T-AAA", modelProb=55.0, kalshiVF=50.0, edge=5.0, confidenceTier="HIGH")])]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_model_evaluations_from_pipeline(DATE, "run1", [])
    assert records[0]["modelVersion"] is None


def test_f5_row_missing_f5_pricing_version_field_stays_none_not_fabricated(monkeypatch, tmp_path):
    """
    A hypothetical F5 row somehow missing f5PricingVersion (e.g. an old
    cached artifact from before this milestone) must never have a
    version fabricated for it -- None, not a guessed/default string.
    """
    games = [_game([_row(market="F5_ML_Away", ticker="KXMLBF5-T-AAA", modelProb=47.59, kalshiVF=45.47, edge=2.5, confidenceTier="MEDIUM")])]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_model_evaluations_from_pipeline(DATE, "run1", [])
    assert records[0]["modelVersion"] is None


def test_ev_per_dollar_read_from_f5_contract_pricing_when_present(monkeypatch, tmp_path):
    """
    scripts/build_market_ledger.py's contract_pricing() computes
    expectedValuePerDollar for F5_ML_Away/F5_ML_Home, nested under
    f5ContractPricing -- previously this was hardcoded null on every
    ModelEvaluation row unconditionally, dropping real, already-computed
    upstream data.
    """
    games = [_game([_row(
        market="F5_ML_Away", ticker="KXMLBF5-T-AAA", modelProb=47.59, kalshiVF=45.47, edge=2.5,
        confidenceTier="MEDIUM", f5PricingVersion="f5_three_way_v1",
        f5ContractPricing={"expectedValuePerDollar": 0.0821},
    )])]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_model_evaluations_from_pipeline(DATE, "run1", [])
    assert records[0]["evPerDollar"] == 0.0821
    assert schema.validate_record("model_evaluation", records[0]) == []


def test_ev_per_dollar_stays_none_for_non_f5_markets(monkeypatch, tmp_path):
    """No other market family has a per-dollar EV concept computed anywhere in the pipeline."""
    games = [_game([_row(market="ML_Away", ticker="KXMLBGAME-T-AAA", modelProb=55.0, kalshiVF=50.0, edge=5.0, confidenceTier="HIGH")])]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_model_evaluations_from_pipeline(DATE, "run1", [])
    assert records[0]["evPerDollar"] is None


def test_market_family_falls_back_to_market_name_when_ticker_missing(monkeypatch, tmp_path):
    """
    A Rejected NRFI row whose ticker wasn't threaded through (real-data
    finding -- see classify_evaluation_status's docstring) still gets a
    usable marketFamily from the model config's own market name, instead
    of null just because ticker.split(...) has nothing to split.
    """
    games = [_game([_row(market="NRFI", ticker=None, marketTicker=None, modelProb=44.0, kalshiVF=51.5,
                          edge=-1.82, status="Rejected", rejectionReason="edge below floor")])]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_model_evaluations_from_pipeline(DATE, "run1", [])
    assert records[0]["marketFamily"] == "NRFI"


def test_evaluated_row_persists_full_shape_and_validates(monkeypatch, tmp_path):
    games = [_game([_row(ticker="KXMLBGAME-T", modelProb=55.0, kalshiVF=50.0, edge=5.0, confidenceTier="HIGH", line=6.5)])]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, warnings = build_model_evaluations_from_pipeline(DATE, "run1", [])
    assert warnings == []
    assert len(records) == 1
    r = records[0]
    assert r["evaluationStatus"] == EVALUATED
    assert r["modelFairProbability"] == 55.0
    assert r["marketImpliedProbability"] == 50.0
    assert r["estimatedEdge"] == 5.0
    assert r["confidence"] == "HIGH"
    assert r["threshold"] == 6.5
    assert r["modelFairOdds"] is not None  # derivable from a valid probability
    assert r["modelSource"] == "scripts/build_market_ledger.py"  # from the artifact's own meta.producedBy
    assert schema.validate_record("model_evaluation", r) == []


def test_invalid_probability_row_stores_null_probability_not_the_bad_value(monkeypatch, tmp_path):
    games = [_game([_row(ticker="T1", modelProb=150.0, kalshiVF=50.0)])]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_model_evaluations_from_pipeline(DATE, "run1", [])
    assert records[0]["evaluationStatus"] == INVALID_PROBABILITY
    assert records[0]["modelFairProbability"] is None
    assert records[0]["modelFairOdds"] is None
    assert schema.validate_record("model_evaluation", records[0]) == []


def test_modelEvaluationId_stable_across_reruns_same_artifact(monkeypatch, tmp_path):
    games = [_game([_row(ticker="T1", modelProb=55.0, kalshiVF=50.0)])]
    _write_recommendations(monkeypatch, tmp_path, games)
    records1, _ = build_model_evaluations_from_pipeline(DATE, "run1", [])
    records2, _ = build_model_evaluations_from_pipeline(DATE, "run2", [])
    # Different script-invocation runId must not change identity when the
    # underlying artifact (same meta.createdAt) hasn't changed.
    assert records1[0]["modelEvaluationId"] == records2[0]["modelEvaluationId"]


def test_two_sided_single_ticker_markets_get_distinct_ids(monkeypatch, tmp_path):
    """
    A run-line/spread market is one Kalshi ticker shared by two opposite-
    side marketLedger rows (RL_Away/RL_Home) -- keying by ticker alone
    would collapse both into one modelEvaluationId, silently dropping one
    (found via testing against real 2026-07-31 data).
    """
    ticker = "KXMLBSPREAD-T"
    games = [_game([
        _row(market="RL_Away", ticker=ticker, modelProb=45.0, kalshiVF=48.0),
        _row(market="RL_Home", ticker=ticker, modelProb=55.0, kalshiVF=52.0),
    ])]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_model_evaluations_from_pipeline(DATE, "run1", [])
    assert len(records) == 2
    ids_seen = {r["modelEvaluationId"] for r in records}
    assert len(ids_seen) == 2
    probs = {r["selection"]: r["modelFairProbability"] for r in records}
    assert probs == {"RL_Away": 45.0, "RL_Home": 55.0}


def test_eventTicker_seriesTicker_derived_from_matching_observation(monkeypatch, tmp_path):
    ticker = "KXMLBGAME-T"
    games = [_game([_row(ticker=ticker, modelProb=55.0, kalshiVF=50.0)])]
    _write_recommendations(monkeypatch, tmp_path, games)
    observations = [{"marketTicker": ticker, "eventTicker": "KXMLBGAME-EVT", "seriesTicker": "KXMLBGAME"}]
    records, _ = build_model_evaluations_from_pipeline(DATE, "run1", observations)
    assert records[0]["eventTicker"] == "KXMLBGAME-EVT"
    assert records[0]["seriesTicker"] == "KXMLBGAME"


def test_eventTicker_null_when_no_matching_observation_never_fabricated(monkeypatch, tmp_path):
    games = [_game([_row(ticker="KXMLBGAME-T", modelProb=55.0, kalshiVF=50.0)])]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_model_evaluations_from_pipeline(DATE, "run1", [])
    assert records[0]["eventTicker"] is None


def test_lineup_confirmation_state_mapping(monkeypatch, tmp_path):
    """
    Milestone 4 extends the 3-value vocabulary (CONFIRMED/PROJECTED/null)
    this test originally asserted to the 5-value controlled vocabulary
    (CONFIRMED/PROJECTED/PARTIAL/UNCONFIRMED/UNKNOWN) -- CONFIRMED now
    additionally requires lineupDataQuality=='full', matching how
    scripts/fetch_lineups.py can mark an official lineup with
    incompletely-resolved batters.
    """
    games = [_game([
        _row(market="A", ticker="T1", modelProb=55.0, kalshiVF=50.0, lineupConfirmedOfficial=True, lineupDataQuality="full"),
        _row(market="B", ticker="T2", modelProb=55.0, kalshiVF=50.0, lineupConfirmedOfficial=True, lineupDataQuality="partial"),
        _row(market="C", ticker="T3", modelProb=55.0, kalshiVF=50.0, lineupStatus="projected"),
        _row(market="D", ticker="T4", modelProb=55.0, kalshiVF=50.0, lineupStatus=None),
        _row(market="E", ticker="T5", modelProb=55.0, kalshiVF=50.0, lineupPosted=False, lineupStatus="missing"),
    ])]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_model_evaluations_from_pipeline(DATE, "run1", [])
    by_market = {r["selection"]: r["lineupConfirmationState"] for r in records}
    assert by_market["A"] == "CONFIRMED"
    assert by_market["B"] == "PARTIAL"
    assert by_market["C"] == "PROJECTED"
    assert by_market["D"] == "UNKNOWN"  # no lineup evidence fields at all
    assert by_market["E"] == "UNCONFIRMED"  # actively checked, not yet available


def test_thesis_tags_empty_when_no_supporting_evidence_on_the_row(monkeypatch, tmp_path):
    """A row with no bullpen/f5/lineup/reasonCodes/teamTotals evidence at all gets no tags -- never fabricated."""
    games = [_game([_row(ticker="T1", modelProb=55.0, kalshiVF=50.0)])]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_model_evaluations_from_pipeline(DATE, "run1", [])
    assert records[0]["thesisTags"] == []
    assert records[0]["tagEvidence"] == {}


# ── Cross-module ID consistency (Recommendation <-> ModelEvaluation) ────

def test_recommendation_and_model_evaluation_share_the_same_ids(monkeypatch, tmp_path):
    ticker = "KXMLBGAME-T"
    games = [_game([_row(ticker=ticker, modelProb=55.0, kalshiVF=50.0)])]
    _write_recommendations(monkeypatch, tmp_path, games)
    rec_records, _ = build_recommendations_from_pipeline(DATE, "run1", {})
    eval_records, _ = build_model_evaluations_from_pipeline(DATE, "run1", [])
    assert rec_records[0]["recommendationId"] == eval_records[0]["recommendationId"]
    assert rec_records[0]["modelEvaluationId"] == eval_records[0]["modelEvaluationId"]


def test_extend_full_universe_evaluations_no_model_support(monkeypatch, tmp_path):
    observations = [
        {"marketTicker": "KXMLBHIT-T", "seriesTicker": "KXMLBHIT", "gameId": "g1", "eventTicker": "E1",
         "marketFamily": "hitter_hits", "runId": "obs-run",
         "provenance": {"sourceFile": "x", "sourceKey": "y", "capturedAt": "t", "sourceSystem": "s"}},
    ]
    extra = extend_full_universe_evaluations(covered_tickers=set(), observations=observations, date=DATE)
    assert len(extra) == 1
    assert extra[0]["evaluationStatus"] == NO_MODEL_SUPPORT
    assert extra[0]["modelFairProbability"] is None
    assert schema.validate_record("model_evaluation", extra[0]) == []


# ── Market integrity: alternate-rung visibility ──────────────────────────
#
# Objective: an archived alternate line/rung of a market family the model
# DOES otherwise support for this game (it evaluated a different
# ticker/line of the exact same family already) must be distinguishable
# from a family the model has no method for at all -- see
# extend_full_universe_evaluations' model_covered_series parameter. Ties
# to lib.edgelab.recommendations.extend_with_full_universe's identical,
# pre-existing NOT_EVALUATED/INSUFFICIENT_MODEL_SUPPORT split.

def test_extend_full_universe_evaluations_not_evaluated_for_alternate_rung_of_covered_family():
    """A Game_Total observation at a DIFFERENT line than the one the
    pipeline actually evaluated: the model demonstrably supports this
    family (KXMLBTOTAL is model-covered), it just never ran against this
    exact archived rung -- NOT_EVALUATED, not the blanket NO_MODEL_SUPPORT
    this used to collapse into."""
    observations = [
        {"marketTicker": "KXMLBTOTAL-T-9", "seriesTicker": "KXMLBTOTAL", "gameId": "g1", "eventTicker": "E1",
         "marketFamily": "game_total", "threshold": 9, "runId": "obs-run",
         "provenance": {"sourceFile": "x", "sourceKey": "y", "capturedAt": "t", "sourceSystem": "s"}},
    ]
    extra = extend_full_universe_evaluations(
        covered_tickers=set(), observations=observations, date=DATE,
        model_covered_series=frozenset({"KXMLBTOTAL"}),
    )
    assert len(extra) == 1
    assert extra[0]["evaluationStatus"] == NOT_EVALUATED
    assert extra[0]["threshold"] == 9  # the real archived line, never dropped to null
    assert extra[0]["modelFairProbability"] is None  # still never fabricated -- no new probability invented
    assert schema.validate_record("model_evaluation", extra[0]) == []


def test_extend_full_universe_evaluations_no_model_support_when_series_not_covered():
    """A player-prop series the model has no method for at all stays
    NO_MODEL_SUPPORT even when model_covered_series is passed."""
    observations = [
        {"marketTicker": "KXMLBHIT-T", "seriesTicker": "KXMLBHIT", "gameId": "g1", "eventTicker": "E1",
         "marketFamily": "hitter_hits", "threshold": 2, "runId": "obs-run",
         "provenance": {"sourceFile": "x", "sourceKey": "y", "capturedAt": "t", "sourceSystem": "s"}},
    ]
    extra = extend_full_universe_evaluations(
        covered_tickers=set(), observations=observations, date=DATE,
        model_covered_series=frozenset({"KXMLBTOTAL"}),
    )
    assert extra[0]["evaluationStatus"] == NO_MODEL_SUPPORT


def test_extend_full_universe_evaluations_omitting_model_covered_series_keeps_prior_behavior():
    """Backward compatible: a caller that never passes model_covered_series
    (e.g. any pre-existing test/caller) still gets exactly the old
    blanket NO_MODEL_SUPPORT -- no behavior change without opting in."""
    observations = [
        {"marketTicker": "KXMLBTOTAL-T-9", "seriesTicker": "KXMLBTOTAL", "gameId": "g1", "eventTicker": "E1",
         "marketFamily": "game_total", "threshold": 9, "runId": "obs-run",
         "provenance": {"sourceFile": "x", "sourceKey": "y", "capturedAt": "t", "sourceSystem": "s"}},
    ]
    extra = extend_full_universe_evaluations(covered_tickers=set(), observations=observations, date=DATE)
    assert extra[0]["evaluationStatus"] == NO_MODEL_SUPPORT


def test_extend_full_universe_evaluations_skips_already_covered():
    observations = [{"marketTicker": "T1", "seriesTicker": "S", "gameId": "g1", "eventTicker": None,
                      "marketFamily": "game_result", "runId": "r", "provenance": {"sourceFile": "x", "sourceKey": "y", "capturedAt": "t", "sourceSystem": "s"}}]
    extra = extend_full_universe_evaluations(covered_tickers={"T1"}, observations=observations, date=DATE)
    assert extra == []


def test_extend_full_universe_evaluations_idempotent_key():
    observations = [{"marketTicker": "T1", "seriesTicker": "S", "gameId": "g1", "eventTicker": None,
                      "marketFamily": "game_result", "runId": "r", "provenance": {"sourceFile": "x", "sourceKey": "y", "capturedAt": "t", "sourceSystem": "s"}}]
    first = extend_full_universe_evaluations(covered_tickers=set(), observations=observations, date=DATE)
    second = extend_full_universe_evaluations(covered_tickers=set(), observations=observations, date=DATE)
    assert first[0]["modelEvaluationId"] == second[0]["modelEvaluationId"]


# ── Universal ModelEvaluation Persistence mission: discovery_lookup ─────
#
# lib.edgelab.kalshi_discovery_bridge.load_discovery_lookup() reads
# scripts/discover_kalshi_mlb_markets.py's own already-computed,
# already-tested per-contract fair probabilities (data/kalshi/discovery/
# <date>.json) and extend_full_universe_evaluations() persists them for
# every family the 11-REQUIRED_MARKETS pipeline never runs against
# (F3/F5/F7 winner/totals, spread/winning_margin, pitcher K/outs). No
# new statistical methodology is added here -- only persistence of
# values another module already computed.

def _obs(ticker, family, threshold=None, gameId="g1"):
    return {
        "marketTicker": ticker, "seriesTicker": ticker.split("-", 1)[0], "gameId": gameId,
        "eventTicker": "E1", "marketFamily": family, "threshold": threshold, "runId": "obs-run",
        "provenance": {"sourceFile": "x", "sourceKey": "y", "capturedAt": "t", "sourceSystem": "s"},
    }


def _discovery_contract(ticker, family, fair_prob_pct, implied_pct=52.0, title="Some Market?", line=None,
                         status="SUPPORTED", reason=None, raw_edge_pct=None, ev_per_dollar=None):
    return {
        "ticker": ticker, "marketFamily": family, "marketTitle": title, "line": line,
        "modelSupportStatus": status, "fairProbabilityPct": fair_prob_pct,
        "impliedProbabilityPct": implied_pct, "unsupportedReason": reason,
        "rawEdgePct": raw_edge_pct, "expectedProfitPerDollar": ev_per_dollar,
    }


def test_discovery_supported_family_persists_real_probability_not_recommended_family():
    """A family the 11-REQUIRED_MARKETS pipeline never runs (e.g. winning_margin/spread) still gets its
    ALREADY-COMPUTED fair probability persisted -- proves 'a market failing recommendation thresholds
    (or never evaluated by the recommendation-eligible pipeline at all) must still retain its model
    probability' for a genuinely new family, not just an alternate rung of an existing one."""
    ticker = "KXMLBSPREAD-T-BOS1"
    observations = [_obs(ticker, "winning_margin", threshold=0.5)]
    discovery_lookup = {ticker: _discovery_contract(ticker, "winning_margin", fair_prob_pct=61.234, implied_pct=55.0,
                                                      title="BOS wins by 1+?", line=0.5, raw_edge_pct=6.234, ev_per_dollar=0.11)}
    extra = extend_full_universe_evaluations(covered_tickers=set(), observations=observations, date=DATE,
                                              discovery_lookup=discovery_lookup)
    assert len(extra) == 1
    row = extra[0]
    assert row["evaluationStatus"] == EVALUATED
    assert row["modelFairProbability"] == 61.234
    assert row["modelFairOdds"] is not None
    assert row["marketImpliedProbability"] == 55.0
    assert row["estimatedEdge"] == 6.234
    assert row["evPerDollar"] == 0.11
    assert row["qualityTier"] == "RESEARCH_ONLY"
    assert row["modelSource"] == "lib.kalshi_probability_adapters.adapt_contract"
    assert row["source"] == "kalshi_discovery_extension"
    assert row["selection"] == "BOS wins by 1+?"
    assert row["threshold"] == 0.5
    assert schema.validate_record("model_evaluation", row) == []


def test_discovery_partial_evaluation_when_no_market_price():
    """SUPPORTED with no executable price available -> PARTIAL_EVALUATION, marketImpliedProbability
    stays null -- never fabricated from a missing price."""
    ticker = "KXMLBF3-T-AWAY"
    observations = [_obs(ticker, "inning_result")]
    discovery_lookup = {ticker: _discovery_contract(ticker, "inning_result", fair_prob_pct=44.0, implied_pct=None)}
    extra = extend_full_universe_evaluations(covered_tickers=set(), observations=observations, date=DATE,
                                              discovery_lookup=discovery_lookup)
    assert extra[0]["evaluationStatus"] == PARTIAL_EVALUATION
    assert extra[0]["modelFairProbability"] == 44.0
    assert extra[0]["marketImpliedProbability"] is None
    assert extra[0]["probabilityAdapter"] is None
    assert schema.validate_record("model_evaluation", extra[0]) == []


def test_discovery_missing_data_never_fabricates_a_probability():
    """MISSING_DATA (method exists, this contract's inputs were insufficient) -> DATA_QUALITY_BLOCK,
    modelFairProbability stays None, the real reason is preserved verbatim."""
    ticker = "KXMLBKS-T-SMITH21-7"
    observations = [_obs(ticker, "pitcher_strikeouts", threshold=7)]
    discovery_lookup = {ticker: _discovery_contract(
        ticker, "pitcher_strikeouts", fair_prob_pct=None, status="MISSING_DATA",
        reason="pitcherAvgIPperStart/pitcherKPct missing from projection context",
    )}
    extra = extend_full_universe_evaluations(covered_tickers=set(), observations=observations, date=DATE,
                                              discovery_lookup=discovery_lookup)
    row = extra[0]
    assert row["evaluationStatus"] == DATA_QUALITY_BLOCK
    assert row["modelFairProbability"] is None
    assert row["qualityTier"] is None
    assert row["dataQualityReasons"] == ["pitcherAvgIPperStart/pitcherKPct missing from projection context"]
    assert schema.validate_record("model_evaluation", row) == []


def test_discovery_unsupported_hitter_family_falls_through_unchanged():
    """Hitter-prop status is explicitly required to remain unchanged: even when a discovery_lookup
    entry exists for a hitter-family ticker (marked UNSUPPORTED by lib.kalshi_probability_adapters'
    own _NEVER_MODELED_FAMILIES gate), the row falls back to the exact pre-existing NO_MODEL_SUPPORT
    path, never a fabricated probability."""
    ticker = "KXMLBHIT-T"
    observations = [_obs(ticker, "hitter_hits", threshold=2)]
    discovery_lookup = {ticker: _discovery_contract(
        ticker, "hitter_hits", fair_prob_pct=None, status="UNSUPPORTED",
        reason="no per-batter hit probability distribution exists in this codebase.",
    )}
    extra = extend_full_universe_evaluations(covered_tickers=set(), observations=observations, date=DATE,
                                              discovery_lookup=discovery_lookup)
    row = extra[0]
    assert row["evaluationStatus"] == NO_MODEL_SUPPORT
    assert row["modelFairProbability"] is None
    assert row["qualityTier"] == "UNSUPPORTED"
    assert row["source"] == "market_universe_extension"
    assert schema.validate_record("model_evaluation", row) == []


def test_ticker_absent_from_discovery_lookup_keeps_prior_behavior():
    """A date discovery hasn't run for (or a ticker discovery simply never saw) degrades to the exact
    pre-existing behavior -- no regression for any date/ticker this mission's new lookup doesn't cover."""
    ticker = "KXMLBTOTAL-T-9"
    observations = [_obs(ticker, "game_total", threshold=9)]
    extra_without = extend_full_universe_evaluations(covered_tickers=set(), observations=observations, date=DATE,
                                                       model_covered_series=frozenset({"KXMLBTOTAL"}))
    extra_with_empty_lookup = extend_full_universe_evaluations(covered_tickers=set(), observations=observations, date=DATE,
                                                                 model_covered_series=frozenset({"KXMLBTOTAL"}),
                                                                 discovery_lookup={})
    assert extra_without[0]["evaluationStatus"] == extra_with_empty_lookup[0]["evaluationStatus"] == NOT_EVALUATED
    assert extra_without[0]["modelFairProbability"] is extra_with_empty_lookup[0]["modelFairProbability"] is None


def test_alternate_thresholds_persist_independently():
    """Two different lines of the SAME newly-persisted family (e.g. two alternate winning_margin
    thresholds for the same team/game) each get their own distinct, independently-computed row --
    never collapsed into one, never approximated from the other's price."""
    t1, t2 = "KXMLBSPREAD-T-BOS1", "KXMLBSPREAD-T-BOS2"
    observations = [_obs(t1, "winning_margin", threshold=0.5), _obs(t2, "winning_margin", threshold=1.5)]
    discovery_lookup = {
        t1: _discovery_contract(t1, "winning_margin", fair_prob_pct=61.2, line=0.5, title="BOS wins by 1+?"),
        t2: _discovery_contract(t2, "winning_margin", fair_prob_pct=41.5, line=1.5, title="BOS wins by 2+?"),
    }
    extra = extend_full_universe_evaluations(covered_tickers=set(), observations=observations, date=DATE,
                                              discovery_lookup=discovery_lookup)
    by_ticker = {r["marketTicker"]: r for r in extra}
    assert len(extra) == 2
    assert by_ticker[t1]["modelFairProbability"] == 61.2
    assert by_ticker[t2]["modelFairProbability"] == 41.5
    assert by_ticker[t1]["threshold"] == 0.5
    assert by_ticker[t2]["threshold"] == 1.5
    assert by_ticker[t1]["modelEvaluationId"] != by_ticker[t2]["modelEvaluationId"]


def test_recommendation_id_unaffected_by_discovery_lookup():
    """Recommendation eligibility must be unchanged by this mission: recommendationId is computed the
    exact same way whether or not a discovery_lookup entry exists for this ticker -- this function
    never promotes a research-only row into a different recommendation-linkage outcome."""
    ticker = "KXMLBSPREAD-T-BOS1"
    observations = [_obs(ticker, "winning_margin", threshold=0.5)]
    without = extend_full_universe_evaluations(covered_tickers=set(), observations=observations, date=DATE)
    with_discovery = extend_full_universe_evaluations(
        covered_tickers=set(), observations=observations, date=DATE,
        discovery_lookup={ticker: _discovery_contract(ticker, "winning_margin", fair_prob_pct=61.2, line=0.5)},
    )
    assert without[0]["recommendationId"] == with_discovery[0]["recommendationId"]


def test_pitcher_strikeouts_identity_resolved_contract_persists_end_to_end():
    """Pitcher K/outs identity-resolution wiring result: a discovery contract whose subject was already
    resolved to a real probable starter (lib.kalshi_mlb_market_classifier._resolve_pitcher_prop_subject,
    exercised upstream by scripts/discover_kalshi_mlb_markets.py) flows all the way into a persisted,
    EVALUATED, RESEARCH_ONLY ModelEvaluation row -- the identity (preserved in the human-readable
    marketTitle/selection) is never dropped between discovery and persistence."""
    ticker = "KXMLBKS-26AUG202010LAAHOU-LAAGRODRIGUEZ21-7"
    observations = [_obs(ticker, "pitcher_strikeouts", threshold=7)]
    discovery_lookup = {ticker: _discovery_contract(
        ticker, "pitcher_strikeouts", fair_prob_pct=0.426, implied_pct=2.0,
        title="Will Grayson Rodriguez record 7+ strikeouts?", line=7,
    )}
    extra = extend_full_universe_evaluations(covered_tickers=set(), observations=observations, date=DATE,
                                              discovery_lookup=discovery_lookup)
    row = extra[0]
    assert row["evaluationStatus"] == EVALUATED
    assert row["modelFairProbability"] == 0.426
    assert row["qualityTier"] == "RESEARCH_ONLY"
    assert "Grayson Rodriguez" in row["selection"]
    assert row["threshold"] == 7
    assert schema.validate_record("model_evaluation", row) == []


def test_pipeline_derived_rows_tagged_trusted_production_only_when_probability_present():
    """build_model_evaluation_records_for_games (the 11-REQUIRED_MARKETS pipeline/prospective-snapshot
    path) tags every row that actually carries a probability TRUSTED_PRODUCTION -- regardless of
    Accepted/Rejected/Paper status, matching classify_evaluation_status()'s own 'rejection is a
    Recommendation-level decision, not evidence the model itself failed to evaluate' rule. A row with
    no probability gets no tier claim at all."""
    games = [_game([
        _row(ticker="T1", modelProb=55.0, kalshiVF=50.0, status="Accepted"),
        _row(market="ML_Home", status="Missing Data", missingFields=["odds.kalshi.ml.home"]),
    ])]
    from lib.edgelab.model_evaluation import build_model_evaluation_records_for_games
    records = build_model_evaluation_records_for_games(
        games, source_run_key="run1", run_id="run1", model_source="scripts/build_market_ledger.py",
        artifact_source="test", ticker_lookup={}, commit_sha=None, config_version=None,
        source_system="test", source_file=None,
    )
    by_selection = {r["selection"]: r for r in records}
    assert by_selection["ML_Away"]["qualityTier"] == "TRUSTED_PRODUCTION"
    assert by_selection["ML_Home"]["modelFairProbability"] is None
    assert by_selection["ML_Home"]["qualityTier"] is None
    for r in records:
        assert schema.validate_record("model_evaluation", r) == []


# ── Historical backfill provenance ───────────────────────────────────────

def test_provenance_preserves_original_artifact_timestamp_and_path(monkeypatch, tmp_path):
    games = [_game([_row(ticker="T1", modelProb=55.0, kalshiVF=50.0)])]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_model_evaluations_from_pipeline(DATE, "run1", [])
    prov = records[0]["provenance"]
    assert prov["sourceFile"] == os.path.join("data", "pipeline", DATE, "recommendations.json")
    assert prov["capturedAt"]  # the artifact's own meta.createdAt, not this test's wall-clock time
    assert prov["sourceSystem"] == "pipeline_recommendations"


# ── PlacedBet linkage (manual bets stay unlinked) ────────────────────────

def test_link_bets_to_recommendations_backfills_matching_ticker():
    bet = build_manual_bet_record("T1", "sel", 1.0, 0.5, "2026-07-31T22:00:00Z", source="MODEL")
    recs = [{"marketTicker": "T1", "recommendationId": "rec-1", "modelEvaluationId": "eval-1"}]
    updated = link_bets_to_recommendations([bet], recs)
    assert len(updated) == 1
    assert updated[0]["recommendationId"] == "rec-1"
    assert updated[0]["modelEvaluationId"] == "eval-1"
    assert updated[0]["updatedAt"] is not None


def test_link_bets_to_recommendations_never_overwrites_existing_link():
    bet = build_manual_bet_record("T1", "sel", 1.0, 0.5, "2026-07-31T22:00:00Z", recommendation_id="already-set")
    recs = [{"marketTicker": "T1", "recommendationId": "different-rec", "modelEvaluationId": "eval-1"}]
    updated = link_bets_to_recommendations([bet], recs)
    # recommendationId is preserved; modelEvaluationId (previously unset) is still backfilled.
    assert len(updated) == 1
    assert updated[0]["recommendationId"] == "already-set"
    assert updated[0]["modelEvaluationId"] == "eval-1"


def test_manual_bet_with_no_matching_recommendation_stays_unlinked():
    """A manual bet with no model evaluation must remain fully representable -- never fabricated a link."""
    bet = build_manual_bet_record("T-MANUAL", "sel", 1.0, 0.5, "2026-07-31T22:00:00Z", source="MANUAL")
    assert bet["recommendationId"] is None
    assert bet["modelEvaluationId"] is None
    updated = link_bets_to_recommendations([bet], [{"marketTicker": "OTHER_TICKER", "recommendationId": "r", "modelEvaluationId": "e"}])
    assert updated == []
    assert schema.validate_record("placed_bet", bet) == []


def test_link_bets_to_recommendations_no_bets_or_no_recommendations_is_empty():
    assert link_bets_to_recommendations([], []) == []
    bet = build_manual_bet_record("T1", "sel", 1.0, 0.5, "2026-07-31T22:00:00Z")
    assert link_bets_to_recommendations([bet], []) == []


# ── Backward compatibility ───────────────────────────────────────────────

def test_manual_bet_record_without_model_evaluation_id_kwarg_defaults_null_and_validates():
    """Existing callers that never pass model_evaluation_id must keep working unchanged."""
    bet = build_manual_bet_record("T1", "sel", 1.0, 0.5, "2026-07-31T22:00:00Z")
    assert bet["modelEvaluationId"] is None
    assert schema.validate_record("placed_bet", bet) == []


def test_bet_missing_model_evaluation_id_key_entirely_still_validates():
    """A record shaped exactly like every bet committed before this milestone (no modelEvaluationId key at all)."""
    old_bet = {
        "schemaVersion": "1", "betId": "b1", "marketTicker": "T", "selection": "x", "stake": 1.0,
        "entryPrice": 0.5, "entryTimestamp": "2026-07-31T22:00:00Z", "source": "MANUAL", "status": "pending",
        "createdAt": "2026-07-31T22:00:00Z", "validationStatus": "valid",
        "provenance": {"sourceSystem": None, "sourceFile": None, "sourceKey": None, "capturedAt": None, "ingestedAt": None},
    }
    assert "modelEvaluationId" not in old_bet
    assert schema.validate_record("placed_bet", old_bet) == []


# ── Missing optional columns (all-null-column DuckDB robustness) ────────

def _write_jsonl(path, records):
    import gzip
    import json
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wt") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _minimal_evaluation(model_evaluation_id, **overrides):
    rec = {
        "schemaVersion": "1", "modelEvaluationId": model_evaluation_id, "runId": "r1",
        "marketTicker": f"T-{model_evaluation_id}", "evaluationStatus": "NO_MODEL_SUPPORT",
        "createdAt": "2026-07-31T22:00:00Z", "source": "test", "validationStatus": "valid",
        "provenance": {"sourceSystem": None, "sourceFile": None, "sourceKey": None, "capturedAt": None, "ingestedAt": None},
    }
    rec.update(overrides)
    return rec


def test_model_evaluations_view_handles_all_null_optional_columns(tmp_path):
    """Every optional column (modelVersion, confidence, thesisTags, ...) genuinely absent/null in every row."""
    records = [_minimal_evaluation("e1"), _minimal_evaluation("e2")]
    _write_jsonl(os.path.join(str(tmp_path), "model_evaluations", f"{DATE}.jsonl"), records)
    with open_session(root=str(tmp_path)) as session:
        assert session.is_available("model_evaluations")
        rows = session.fetchall("SELECT modelVersion, confidence, thesisTags, side, eventTicker FROM v_model_evaluations")
        assert len(rows) == 2
        for modelVersion, confidence, thesisTags, side, eventTicker in rows:
            assert modelVersion is None
            assert confidence is None
            assert side is None
            assert eventTicker is None


def test_population_report_none_when_no_model_evaluations(tmp_path):
    with open_session(root=str(tmp_path)) as session:
        assert population_report(session) is None
        assert population_by_canonical_family(session) == []
        assert population_by_model_version_and_source(session) == []


def test_population_report_percentages(tmp_path):
    records = [
        _minimal_evaluation("e1", evaluationStatus="EVALUATED", modelFairProbability=55.0, estimatedEdge=5.0,
                            confidence="HIGH", recommendationId="r1", marketFamily="game_result"),
        _minimal_evaluation("e2", evaluationStatus="NO_MODEL_SUPPORT", marketFamily="game_result"),
    ]
    _write_jsonl(os.path.join(str(tmp_path), "model_evaluations", f"{DATE}.jsonl"), records)
    with open_session(root=str(tmp_path)) as session:
        report = population_report(session)
        assert report["total"] == 2
        assert report["modelFairProbability"]["count"] == 1
        assert report["modelFairProbability"]["pct"] == 50.0
        assert report["linkedToRecommendation"]["count"] == 1
        assert report["linkedToPlacedBet"] is None  # bets entity unavailable in this fixture -- not fabricated as 0%

        by_family = population_by_canonical_family(session)
        assert len(by_family) == 1
        assert by_family[0]["n"] == 2
        assert by_family[0]["pctModelFairProbability"] == 50.0


# ── Calibration integration: prefer linked ModelEvaluation ──────────────

def _bet_for_calibration(bet_id, model_evaluation_id=None, **overrides):
    rec = {
        "betId": bet_id, "marketTicker": f"T-{bet_id}", "marketFamily": "game_result",
        "selection": "x", "side": "YES", "stake": 10.0, "entryPrice": 0.5,
        "entryTimestamp": "2026-07-01T12:00:00Z", "status": "settled", "result": "WIN",
        "netProfitLoss": 5.0, "clv": None, "confidence": "LOW", "estimatedEdgeAtEntry": 1.0,
        "modelFairProbability": 40.0, "modelEvaluationId": model_evaluation_id, "thesisTags": [],
    }
    rec.update(overrides)
    return rec


def test_calibration_prefers_linked_model_evaluation_over_bet_own_copy(tmp_path):
    """
    The bet's OWN modelFairProbability/confidence (40.0/LOW, entry-time
    values) must be superseded by its linked ModelEvaluation's values
    (60.0/HIGH) once a real link exists -- per Milestone 3 scope item 10,
    "do not use duplicated fallback fields when a linked ModelEvaluation
    exists."

    v_placed_bets.modelFairProbability is always normalized to the 0-1
    scale (matching PlacedBet's own convention, see
    lib.edgelab.bets.resolve_recommendation_context), so the linked
    ModelEvaluation's native 0-100 value of 60.0 must read out as 0.6
    here, not pass through unconverted -- a research-milestone regression
    test for exactly this scale bug.
    """
    evaluation = _minimal_evaluation(
        "eval-1", marketTicker="T-b1", evaluationStatus="EVALUATED",
        modelFairProbability=60.0, estimatedEdge=9.0, confidence="HIGH",
    )
    bet = _bet_for_calibration("b1", model_evaluation_id="eval-1")
    _write_jsonl(os.path.join(str(tmp_path), "model_evaluations", f"{DATE}.jsonl"), [evaluation])
    _write_jsonl(os.path.join(str(tmp_path), "bets", "bets.jsonl"), [bet])

    with open_session(root=str(tmp_path)) as session:
        row = session.fetchall("SELECT modelFairProbability, estimatedEdgeAtEntry, confidence FROM v_placed_bets")[0]
        assert row == (0.6, 9.0, "HIGH")

        conf_rows = {r["confidence"]: r for r in cal.confidence_calibration(session)}
        assert "HIGH" in conf_rows  # bucketed under the linked evaluation's confidence, not the bet's own "LOW"
        assert "LOW" not in conf_rows


def test_calibration_falls_back_to_bet_own_copy_when_no_link(tmp_path):
    bet = _bet_for_calibration("b1", model_evaluation_id=None)
    _write_jsonl(os.path.join(str(tmp_path), "bets", "bets.jsonl"), [bet])
    with open_session(root=str(tmp_path)) as session:
        row = session.fetchall("SELECT modelFairProbability, estimatedEdgeAtEntry, confidence FROM v_placed_bets")[0]
        assert row == (40.0, 1.0, "LOW")


def test_calibration_falls_back_when_linked_id_does_not_resolve(tmp_path):
    """A bet's modelEvaluationId that doesn't (yet) match any real row -- e.g. a join miss -- must fall back, not go NULL."""
    bet = _bet_for_calibration("b1", model_evaluation_id="does-not-exist")
    _write_jsonl(os.path.join(str(tmp_path), "model_evaluations", f"{DATE}.jsonl"), [_minimal_evaluation("some-other-id")])
    _write_jsonl(os.path.join(str(tmp_path), "bets", "bets.jsonl"), [bet])
    with open_session(root=str(tmp_path)) as session:
        row = session.fetchall("SELECT modelFairProbability, estimatedEdgeAtEntry, confidence FROM v_placed_bets")[0]
        assert row == (40.0, 1.0, "LOW")


# ── Determinism ──────────────────────────────────────────────────────────

def test_repeated_builds_produce_identical_records(monkeypatch, tmp_path):
    games = [_game([
        _row(market="A", ticker="T1", modelProb=55.0, kalshiVF=50.0),
        _row(market="B", ticker=None, modelProb=None, status="Missing Data", missingFields=["x"]),
    ])]
    _write_recommendations(monkeypatch, tmp_path, games)
    first, _ = build_model_evaluations_from_pipeline(DATE, "run1", [])
    second, _ = build_model_evaluations_from_pipeline(DATE, "run1", [])
    # Same runId, same artifact -- every field, not just IDs, must match.
    assert first == second
