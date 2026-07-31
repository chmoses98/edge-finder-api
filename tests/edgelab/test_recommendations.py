#!/usr/bin/env python3
"""
tests/edgelab/test_recommendations.py
=========================================
Coverage for lib/edgelab/recommendations.py: full status vocabulary,
pipeline ingestion, full-universe extension (NOT_EVALUATED vs
INSUFFICIENT_MODEL_SUPPORT), and idempotent reruns.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import lib.pipeline_artifacts as pipeline_artifacts
from lib.edgelab import schema
from lib.edgelab.recommendations import (
    build_recommendations_from_pipeline,
    extend_with_full_universe,
    load_model_covered_series,
)

DATE = "2026-07-31"


def _game_row(status, **overrides):
    row = {
        "market": "F5_ML_Away", "status": status, "ticker": None,
        "marketTicker": None, "modelProb": None, "kalshiVF": None,
        "calibratedEdgeVsExecutable": None, "confidenceTier": None,
        "rejectionReason": None, "missingFields": [], "evaluationError": None,
    }
    row.update(overrides)
    return row


def _write_recommendations(monkeypatch, tmp_path, games):
    monkeypatch.setattr(pipeline_artifacts, "PIPELINE_ROOT", str(tmp_path))
    pipeline_artifacts.write_stage_artifact("recommendations", DATE, {"date": DATE, "games": games})


def test_no_pipeline_artifact_yields_empty_with_warning(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline_artifacts, "PIPELINE_ROOT", str(tmp_path))
    records, warnings = build_recommendations_from_pipeline(DATE, "run1", set())
    assert records == []
    assert warnings


def test_missing_data_maps_to_pass_data_quality(monkeypatch, tmp_path):
    games = [{"gameId": "g1", "away": {"abbr": "PIT"}, "home": {"abbr": "CIN"}, "status": "Scheduled",
              "marketLedger": [_game_row("Missing Data", missingFields=["odds.kalshi.nrfi_yrfi"])]}]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, warnings = build_recommendations_from_pipeline(DATE, "run1", set())
    assert warnings == []
    assert records[0]["status"] == "PASS_DATA_QUALITY"
    assert schema.validate_record("recommendation", records[0]) == []


def test_rejected_maps_to_specific_pass_reason(monkeypatch, tmp_path):
    games = [{"gameId": "g1", "away": {"abbr": "PIT"}, "home": {"abbr": "CIN"}, "status": "Scheduled",
              "marketLedger": [_game_row("Rejected", ticker="KXMLBF5-26JUL311810PITCIN-PIT",
                                          rejectionReason="Executable edge below threshold")]}]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_recommendations_from_pipeline(DATE, "run1", set())
    assert records[0]["status"] == "PASS_NO_EDGE"
    assert records[0]["passReason"] == "Executable edge below threshold"


def test_accepted_with_bet_is_bet_placed(monkeypatch, tmp_path):
    ticker = "KXMLBF5-26JUL311810PITCIN-PIT"
    games = [{"gameId": "g1", "away": {"abbr": "PIT"}, "home": {"abbr": "CIN"}, "status": "Scheduled",
              "marketLedger": [_game_row("Accepted", ticker=ticker, modelProb=63.0, kalshiVF=57.0)]}]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_recommendations_from_pipeline(DATE, "run1", {ticker})
    assert records[0]["status"] == "BET_PLACED"
    assert records[0]["betPlaced"] is True


def test_accepted_without_bet_final_game_is_recommended_not_bet(monkeypatch, tmp_path):
    ticker = "KXMLBF5-26JUL311810PITCIN-PIT"
    games = [{"gameId": "g1", "away": {"abbr": "PIT"}, "home": {"abbr": "CIN"}, "status": "Final",
              "marketLedger": [_game_row("Accepted", ticker=ticker)]}]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_recommendations_from_pipeline(DATE, "run1", set())
    assert records[0]["status"] == "RECOMMENDED_NOT_BET"


def test_accepted_without_bet_live_game_is_recommended(monkeypatch, tmp_path):
    ticker = "KXMLBF5-26JUL311810PITCIN-PIT"
    games = [{"gameId": "g1", "away": {"abbr": "PIT"}, "home": {"abbr": "CIN"}, "status": "Scheduled",
              "marketLedger": [_game_row("Accepted", ticker=ticker)]}]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_recommendations_from_pipeline(DATE, "run1", set())
    assert records[0]["status"] == "RECOMMENDED"


def test_rerun_against_same_artifact_is_idempotent(monkeypatch, tmp_path):
    ticker = "KXMLBF5-26JUL311810PITCIN-PIT"
    games = [{"gameId": "g1", "away": {"abbr": "PIT"}, "home": {"abbr": "CIN"}, "status": "Scheduled",
              "marketLedger": [_game_row("Accepted", ticker=ticker)]}]
    _write_recommendations(monkeypatch, tmp_path, games)
    records1, _ = build_recommendations_from_pipeline(DATE, "run1", set())
    records2, _ = build_recommendations_from_pipeline(DATE, "run2", set())
    # Different runId (script invocation) must not change identity when the
    # underlying artifact (same meta.createdAt) hasn't changed.
    assert records1[0]["recommendationId"] == records2[0]["recommendationId"]


def test_extend_with_full_universe_distinguishes_not_evaluated_vs_unsupported():
    model_covered = frozenset({"KXMLBF5"})
    observations = [
        {"marketTicker": "KXMLBF5-26JUL311810PITCIN-CIN", "seriesTicker": "KXMLBF5", "gameId": "g1",
         "marketFamily": "inning_result", "runId": "obs-run", "provenance": {"sourceFile": "x", "sourceKey": "y", "capturedAt": "t", "sourceSystem": "s"}},
        {"marketTicker": "KXMLBHIT-26JUL311810PITCIN-PLAYER1", "seriesTicker": "KXMLBHIT", "gameId": "g1",
         "marketFamily": "hitter_hits", "runId": "obs-run", "provenance": {"sourceFile": "x", "sourceKey": "y", "capturedAt": "t", "sourceSystem": "s"}},
    ]
    extra = extend_with_full_universe(covered_tickers=set(), observations=observations, model_covered_series=model_covered, date=DATE)
    by_ticker = {r["marketTicker"]: r for r in extra}
    assert by_ticker["KXMLBF5-26JUL311810PITCIN-CIN"]["status"] == "NOT_EVALUATED"
    assert by_ticker["KXMLBHIT-26JUL311810PITCIN-PLAYER1"]["status"] == "INSUFFICIENT_MODEL_SUPPORT"
    for r in extra:
        assert schema.validate_record("recommendation", r) == []


def test_extend_skips_already_covered_tickers():
    observations = [
        {"marketTicker": "KXMLBF5-26JUL311810PITCIN-PIT", "seriesTicker": "KXMLBF5", "gameId": "g1",
         "marketFamily": "inning_result", "runId": "obs-run", "provenance": {"sourceFile": "x", "sourceKey": "y", "capturedAt": "t", "sourceSystem": "s"}},
    ]
    extra = extend_with_full_universe(
        covered_tickers={"KXMLBF5-26JUL311810PITCIN-PIT"}, observations=observations,
        model_covered_series=frozenset({"KXMLBF5"}), date=DATE,
    )
    assert extra == []


def test_load_model_covered_series_reads_real_config():
    series = load_model_covered_series()
    assert "KXMLBGAME" in series
    assert "KXMLBRFI" in series
