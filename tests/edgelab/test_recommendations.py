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
    TICKER_AMBIGUOUS,
    TICKER_NOT_APPLICABLE,
    TICKER_NOT_COMPUTED,
    TICKER_PARSER_UNRESOLVED,
    TICKER_RESOLVED,
    build_recommendations_from_pipeline,
    extend_with_full_universe,
    format_threshold_label,
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
    records, warnings = build_recommendations_from_pipeline(DATE, "run1", {})
    assert records == []
    assert warnings


def test_missing_data_maps_to_pass_data_quality(monkeypatch, tmp_path):
    games = [{"gameId": "g1", "away": {"abbr": "PIT"}, "home": {"abbr": "CIN"}, "status": "Scheduled",
              "marketLedger": [_game_row("Missing Data", missingFields=["odds.kalshi.nrfi_yrfi"])]}]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, warnings = build_recommendations_from_pipeline(DATE, "run1", {})
    assert warnings == []
    assert records[0]["status"] == "PASS_DATA_QUALITY"
    assert schema.validate_record("recommendation", records[0]) == []


def test_rejected_maps_to_specific_pass_reason(monkeypatch, tmp_path):
    games = [{"gameId": "g1", "away": {"abbr": "PIT"}, "home": {"abbr": "CIN"}, "status": "Scheduled",
              "marketLedger": [_game_row("Rejected", ticker="KXMLBF5-26JUL311810PITCIN-PIT",
                                          rejectionReason="Executable edge below threshold")]}]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_recommendations_from_pipeline(DATE, "run1", {})
    assert records[0]["status"] == "PASS_NO_EDGE"
    assert records[0]["passReason"] == "Executable edge below threshold"


def test_accepted_with_bet_is_bet_placed(monkeypatch, tmp_path):
    ticker = "KXMLBF5-26JUL311810PITCIN-PIT"
    games = [{"gameId": "g1", "away": {"abbr": "PIT"}, "home": {"abbr": "CIN"}, "status": "Scheduled",
              "marketLedger": [_game_row("Accepted", ticker=ticker, modelProb=63.0, kalshiVF=57.0)]}]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_recommendations_from_pipeline(DATE, "run1", {ticker: "bet-1"})
    assert records[0]["status"] == "BET_PLACED"
    assert records[0]["betPlaced"] is True
    assert records[0]["betId"] == "bet-1"  # must link back to the actual bet, not just flag betPlaced


def test_accepted_without_bet_final_game_is_recommended_not_bet(monkeypatch, tmp_path):
    ticker = "KXMLBF5-26JUL311810PITCIN-PIT"
    games = [{"gameId": "g1", "away": {"abbr": "PIT"}, "home": {"abbr": "CIN"}, "status": "Final",
              "marketLedger": [_game_row("Accepted", ticker=ticker)]}]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_recommendations_from_pipeline(DATE, "run1", {})
    assert records[0]["status"] == "RECOMMENDED_NOT_BET"


def test_accepted_without_bet_live_game_is_recommended(monkeypatch, tmp_path):
    ticker = "KXMLBF5-26JUL311810PITCIN-PIT"
    games = [{"gameId": "g1", "away": {"abbr": "PIT"}, "home": {"abbr": "CIN"}, "status": "Scheduled",
              "marketLedger": [_game_row("Accepted", ticker=ticker)]}]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_recommendations_from_pipeline(DATE, "run1", {})
    assert records[0]["status"] == "RECOMMENDED"
    assert records[0]["betId"] is None


def test_rerun_against_same_artifact_is_idempotent(monkeypatch, tmp_path):
    ticker = "KXMLBF5-26JUL311810PITCIN-PIT"
    games = [{"gameId": "g1", "away": {"abbr": "PIT"}, "home": {"abbr": "CIN"}, "status": "Scheduled",
              "marketLedger": [_game_row("Accepted", ticker=ticker)]}]
    _write_recommendations(monkeypatch, tmp_path, games)
    records1, _ = build_recommendations_from_pipeline(DATE, "run1", {})
    records2, _ = build_recommendations_from_pipeline(DATE, "run2", {})
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


def test_extend_with_full_universe_bet_placed_without_model_recommendation():
    """
    Section G explicitly requires researching "bets placed without a
    model recommendation" -- a market the model never evaluated at all
    (only ever reaches the full-universe extension path) that someone
    nonetheless bet on manually must show up as BET_PLACED with betId
    set, not NOT_EVALUATED/INSUFFICIENT_MODEL_SUPPORT with betPlaced
    silently left False.
    """
    ticker_supported_family = "KXMLBF5-26JUL311810PITCIN-CIN"
    ticker_unsupported_family = "KXMLBHIT-26JUL311810PITCIN-PLAYER1"
    observations = [
        {"marketTicker": ticker_supported_family, "seriesTicker": "KXMLBF5", "gameId": "g1",
         "marketFamily": "inning_result", "runId": "obs-run", "provenance": {"sourceFile": "x", "sourceKey": "y", "capturedAt": "t", "sourceSystem": "s"}},
        {"marketTicker": ticker_unsupported_family, "seriesTicker": "KXMLBHIT", "gameId": "g1",
         "marketFamily": "hitter_hits", "runId": "obs-run", "provenance": {"sourceFile": "x", "sourceKey": "y", "capturedAt": "t", "sourceSystem": "s"}},
    ]
    placed_bet_tickers = {ticker_supported_family: "bet-1", ticker_unsupported_family: "bet-2"}
    extra = extend_with_full_universe(
        covered_tickers=set(), observations=observations, model_covered_series=frozenset({"KXMLBF5"}),
        date=DATE, placed_bet_tickers=placed_bet_tickers,
    )
    by_ticker = {r["marketTicker"]: r for r in extra}
    for ticker in (ticker_supported_family, ticker_unsupported_family):
        assert by_ticker[ticker]["status"] == "BET_PLACED"
        assert by_ticker[ticker]["betPlaced"] is True
        assert by_ticker[ticker]["betId"] == placed_bet_tickers[ticker]
        assert by_ticker[ticker]["modelFairProbability"] is None  # the model still never evaluated it
        assert schema.validate_record("recommendation", by_ticker[ticker]) == []


def test_extend_without_placed_bet_tickers_arg_defaults_to_no_bets():
    """Backward-compatible default: omitting placed_bet_tickers must not crash or silently assume every ticker is bet."""
    observations = [
        {"marketTicker": "KXMLBF5-26JUL311810PITCIN-CIN", "seriesTicker": "KXMLBF5", "gameId": "g1",
         "marketFamily": "inning_result", "runId": "obs-run", "provenance": {"sourceFile": "x", "sourceKey": "y", "capturedAt": "t", "sourceSystem": "s"}},
    ]
    extra = extend_with_full_universe(covered_tickers=set(), observations=observations, model_covered_series=frozenset({"KXMLBF5"}), date=DATE)
    assert extra[0]["status"] == "NOT_EVALUATED"
    assert extra[0]["betPlaced"] is False
    assert extra[0]["betId"] is None


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


def test_load_model_covered_series_returns_empty_frozenset_when_file_missing(tmp_path):
    """Preserves the pre-existing lenient missing-file behavior -- this milestone's
    validation gate is about structurally INVALID content, not about requiring the
    file to exist."""
    missing_path = str(tmp_path / "does_not_exist.json")
    assert load_model_covered_series(missing_path) == frozenset()


# ── Market integrity: exact ticker mapping / ambiguity refusal ──────────
#
# Objective: a pipeline-claimed ticker is never trusted as "the exact
# archived Kalshi ticker" without being cross-checked against this
# date's real, already-archived MarketObservation corpus -- see
# lib.edgelab.recommendations.classify_ticker_resolution. None of these
# tests touch marketTicker's own value/behavior -- only the new,
# additional tickerResolutionStatus diagnostic.

def test_ticker_resolution_resolved_when_ticker_matches_archived_observation(monkeypatch, tmp_path):
    ticker = "KXMLBF5-26JUL311810PITCIN-PIT"
    games = [{"gameId": "g1", "away": {"abbr": "PIT"}, "home": {"abbr": "CIN"}, "status": "Scheduled",
              "marketLedger": [_game_row("Accepted", ticker=ticker)]}]
    _write_recommendations(monkeypatch, tmp_path, games)
    observations = [{"marketTicker": ticker, "gameId": "g1"}]
    records, _ = build_recommendations_from_pipeline(DATE, "run1", {}, observations)
    assert records[0]["tickerResolutionStatus"] == TICKER_RESOLVED
    assert records[0]["marketTicker"] == ticker  # unchanged by the new diagnostic
    assert schema.validate_record("recommendation", records[0]) == []


def test_ticker_resolution_ambiguous_when_archived_ticker_belongs_to_a_different_game(monkeypatch, tmp_path):
    """
    Two different things both claim to be the same real-world market:
    the pipeline's row says this ticker belongs to game g1, but the
    archive says it was actually observed under game g2. Refuse to
    trust either -- AMBIGUOUS, never a guess.
    """
    ticker = "KXMLBF5-26JUL311810PITCIN-PIT"
    games = [{"gameId": "g1", "away": {"abbr": "PIT"}, "home": {"abbr": "CIN"}, "status": "Scheduled",
              "marketLedger": [_game_row("Accepted", ticker=ticker)]}]
    _write_recommendations(monkeypatch, tmp_path, games)
    observations = [{"marketTicker": ticker, "gameId": "g2"}]
    records, _ = build_recommendations_from_pipeline(DATE, "run1", {}, observations)
    assert records[0]["tickerResolutionStatus"] == TICKER_AMBIGUOUS
    assert records[0]["marketTicker"] == ticker  # still not silently blanked -- just flagged
    assert schema.validate_record("recommendation", records[0]) == []


def test_ticker_resolution_parser_unresolved_when_claimed_ticker_never_archived(monkeypatch, tmp_path):
    """A ticker the model claims but that was never actually captured by
    ingest_market_observations.py -- never trusted as 'exact', never
    silently accepted."""
    ticker = "KXMLBF5-26JUL311810PITCIN-PIT"
    games = [{"gameId": "g1", "away": {"abbr": "PIT"}, "home": {"abbr": "CIN"}, "status": "Scheduled",
              "marketLedger": [_game_row("Accepted", ticker=ticker)]}]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_recommendations_from_pipeline(DATE, "run1", {}, observations=[])
    assert records[0]["tickerResolutionStatus"] == TICKER_PARSER_UNRESOLVED
    assert schema.validate_record("recommendation", records[0]) == []


def test_ticker_resolution_not_computed_for_missing_data_citing_ticker_field(monkeypatch, tmp_path):
    games = [{"gameId": "g1", "away": {"abbr": "PIT"}, "home": {"abbr": "CIN"}, "status": "Scheduled",
              "marketLedger": [_game_row("Missing Data", missingFields=["odds.kalshi.total.best_ticker"])]}]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_recommendations_from_pipeline(DATE, "run1", {})
    assert records[0]["tickerResolutionStatus"] == TICKER_NOT_COMPUTED
    assert schema.validate_record("recommendation", records[0]) == []


def test_ticker_resolution_not_applicable_for_missing_data_unrelated_to_ticker(monkeypatch, tmp_path):
    games = [{"gameId": "g1", "away": {"abbr": "PIT"}, "home": {"abbr": "CIN"}, "status": "Scheduled",
              "marketLedger": [_game_row("Missing Data", missingFields=["lineup.confirmed"])]}]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_recommendations_from_pipeline(DATE, "run1", {})
    assert records[0]["tickerResolutionStatus"] == TICKER_NOT_APPLICABLE
    assert schema.validate_record("recommendation", records[0]) == []


def test_extension_rows_are_always_resolved_since_ticker_is_the_archive_itself():
    """A full-universe extension row's marketTicker IS the literal
    already-archived MarketObservation ticker -- nothing to cross-check
    it against, so it's trivially RESOLVED, never a diagnostic gap."""
    observations = [
        {"marketTicker": "KXMLBHIT-T", "seriesTicker": "KXMLBHIT", "gameId": "g1",
         "marketFamily": "hitter_hits", "runId": "obs-run", "provenance": {"sourceFile": "x", "sourceKey": "y", "capturedAt": "t", "sourceSystem": "s"}},
    ]
    extra = extend_with_full_universe(covered_tickers=set(), observations=observations, model_covered_series=frozenset(), date=DATE)
    assert extra[0]["tickerResolutionStatus"] == TICKER_RESOLVED


# ── Threshold display: natural sportsbook-style labels ───────────────────

def test_format_threshold_label_game_total_adds_half_run_over():
    """Kalshi's strict integer line 8 ('total > 8') -> traditional 'Over 8.5'."""
    assert format_threshold_label("game_total", 8, "OVER") == "Over 8.5"


def test_format_threshold_label_game_total_under():
    assert format_threshold_label("inning_total", 7, "UNDER") == "Under 7.5"


def test_format_threshold_label_team_total_already_natural_no_double_adjustment():
    """team_total's threshold is already N-0.5 (suffix convention) -- used verbatim, never adjusted again."""
    assert format_threshold_label("team_total", 4.5, "OVER") == "Team Total Over 4.5"


def test_format_threshold_label_never_used_for_literal_prop_n_plus_markets():
    """AT_LEAST-direction (a literal Kalshi N+ count market) must never get an Over/Under-style label."""
    assert format_threshold_label("pitcher_strikeouts", 6, "AT_LEAST") is None
    assert format_threshold_label("hitter_stolen_bases", 2, "AT_LEAST") is None


def test_format_threshold_label_none_for_unrecognized_family_never_guessed():
    assert format_threshold_label("winning_margin", 1.5, "OVER") is None
    assert format_threshold_label("run_line", 1.5, "OVER") is None


def test_format_threshold_label_none_when_threshold_missing():
    assert format_threshold_label("game_total", None, "OVER") is None


def test_pipeline_row_thresholdDisplay_populated_for_game_total(monkeypatch, tmp_path):
    games = [{"gameId": "g1", "away": {"abbr": "PIT"}, "home": {"abbr": "CIN"}, "status": "Scheduled",
              "marketLedger": [_game_row("Rejected", market="Game_Total", ticker="KXMLBTOTAL-T-7", line=7, rejectionReason="paper only")]}]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_recommendations_from_pipeline(DATE, "run1", {})
    assert records[0]["thresholdDisplay"] == "Over 7.5"


def test_pipeline_row_thresholdDisplay_populated_for_team_total(monkeypatch, tmp_path):
    games = [{"gameId": "g1", "away": {"abbr": "PIT"}, "home": {"abbr": "CIN"}, "status": "Scheduled",
              "marketLedger": [_game_row("Accepted", market="TT_Away_Over", ticker="KXMLBTEAMTOTAL-T-PIT5", line=4.5)]}]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_recommendations_from_pipeline(DATE, "run1", {})
    assert records[0]["thresholdDisplay"] == "Team Total Over 4.5"


def test_pipeline_row_thresholdDisplay_null_for_moneyline(monkeypatch, tmp_path):
    games = [{"gameId": "g1", "away": {"abbr": "PIT"}, "home": {"abbr": "CIN"}, "status": "Scheduled",
              "marketLedger": [_game_row("Accepted", market="ML_Away", ticker="KXMLBGAME-T-PIT")]}]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_recommendations_from_pipeline(DATE, "run1", {})
    assert records[0]["thresholdDisplay"] is None


def test_extension_row_thresholdDisplay_from_observation_fields():
    observations = [
        {"marketTicker": "KXMLBTEAMTOTAL-T-TB7", "seriesTicker": "KXMLBTEAMTOTAL", "gameId": "g1",
         "marketFamily": "team_total", "threshold": 6.5, "comparisonOperator": "OVER", "runId": "obs-run",
         "provenance": {"sourceFile": "x", "sourceKey": "y", "capturedAt": "t", "sourceSystem": "s"}},
        {"marketTicker": "KXMLBKS-T-P1-6", "seriesTicker": "KXMLBKS", "gameId": "g1",
         "marketFamily": "pitcher_strikeouts", "threshold": 6, "comparisonOperator": "AT_LEAST", "runId": "obs-run",
         "provenance": {"sourceFile": "x", "sourceKey": "y", "capturedAt": "t", "sourceSystem": "s"}},
    ]
    extra = extend_with_full_universe(covered_tickers=set(), observations=observations, model_covered_series=frozenset(), date=DATE)
    by_ticker = {r["marketTicker"]: r for r in extra}
    assert by_ticker["KXMLBTEAMTOTAL-T-TB7"]["thresholdDisplay"] == "Team Total Over 6.5"
    assert by_ticker["KXMLBKS-T-P1-6"]["thresholdDisplay"] is None  # literal N+ prop -- never relabeled


def test_load_model_covered_series_raises_clearly_on_structurally_invalid_config(tmp_path):
    """
    Production Reliability and Settlement Recovery milestone: this is the
    one call site whose output actually gates production behavior (which
    markets the model is considered to cover at all), so a structurally
    broken config/rules.json must fail loudly here rather than silently
    returning an empty/partial series.
    """
    import json

    from lib.rules_config import RulesConfigError

    path = tmp_path / "rules.json"
    path.write_text(json.dumps({"market_list": [{"id": 1, "name": "ML_Away"}]}))  # missing 'series', missing other required sections
    try:
        load_model_covered_series(str(path))
        assert False, "expected RulesConfigError"
    except RulesConfigError as e:
        assert "market_list[0]" in str(e)
