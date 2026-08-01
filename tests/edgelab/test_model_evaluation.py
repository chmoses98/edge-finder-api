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


def _game(ticker_rows, game_id="g1", away="PIT", home="CIN"):
    return {"gameId": game_id, "away": {"abbr": away}, "home": {"abbr": home}, "status": "Scheduled", "marketLedger": ticker_rows}


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


def test_parser_unresolved_when_prob_present_but_no_ticker():
    row = _row(ticker=None, marketTicker=None, modelProb=55.0, kalshiVF=50.0)
    assert classify_evaluation_status(row) == PARSER_UNRESOLVED


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
    games = [_game([
        _row(market="A", ticker="T1", modelProb=55.0, kalshiVF=50.0, lineupConfirmedOfficial=True),
        _row(market="B", ticker="T2", modelProb=55.0, kalshiVF=50.0, lineupStatus="projected"),
        _row(market="C", ticker="T3", modelProb=55.0, kalshiVF=50.0, lineupStatus=None),
    ])]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_model_evaluations_from_pipeline(DATE, "run1", [])
    by_market = {r["selection"]: r["lineupConfirmationState"] for r in records}
    assert by_market["A"] == "CONFIRMED"
    assert by_market["B"] == "PROJECTED"
    assert by_market["C"] is None  # no assessment at all -- null, never fabricated "UNKNOWN"


def test_thesis_tags_always_empty_at_evaluation_time(monkeypatch, tmp_path):
    """Matches the documented gap: the production pipeline never attaches thesis tags at evaluation time."""
    games = [_game([_row(ticker="T1", modelProb=55.0, kalshiVF=50.0)])]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_model_evaluations_from_pipeline(DATE, "run1", [])
    assert records[0]["thesisTags"] == []


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
        assert row == (60.0, 9.0, "HIGH")

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
