#!/usr/bin/env python3
"""
tests/edgelab/test_evaluation_metadata.py
==============================================
Coverage for EdgeLab Phase 2 Milestone 4 (docs/EDGELAB_EVALUATION_METADATA.md):
model identity/provenance, lineup-state mapping, thesis-tag assignment +
evidence, confidence/data-quality provenance, deterministic correlation
groups, historical backfill, and calibration slicing by the new
metadata. Builds on the same fixture conventions
tests/edgelab/test_model_evaluation.py established.
"""
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import lib.pipeline_artifacts as pipeline_artifacts
from lib.edgelab import calibration as cal
from lib.edgelab import schema
from lib.edgelab.analytics import open_session
from lib.edgelab.model_evaluation import (
    _confidence_with_source,
    _data_quality_reasons,
    _git_commit_sha,
    _lineup_confirmation_state,
    _model_config_version,
    _market_implied_probability_with_adapter,
    build_model_evaluations_from_pipeline,
    correlation_groups_for_row,
    population_by_date,
    population_by_recommendation_status,
    thesis_tags_and_evidence_for_row,
    unresolved_metadata_report,
)

DATE = "2026-07-31"


def _row(**overrides):
    row = {
        "market": "ML_Away", "status": "Accepted", "ticker": None, "marketTicker": None,
        "modelProb": None, "kalshiVF": None, "marketProbVF": None, "executableMarketProb": None,
        "calibratedEdgeVsExecutable": None, "edge": None, "confidenceTier": None, "confidence": None,
        "line": None, "seriesTicker": None, "lineupStatus": None, "lineupConfirmedOfficial": None,
        "lineupDataQuality": None, "lineupPosted": None, "lineupSource": None, "lineupAdjApplied": None,
        "missingFields": [], "evaluationError": None, "rejectionReason": None, "reasonCodes": [],
    }
    row.update(overrides)
    return row


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


def _write_recommendations(monkeypatch, tmp_path, games, produced_by="scripts/build_market_ledger.py"):
    monkeypatch.setattr(pipeline_artifacts, "PIPELINE_ROOT", str(tmp_path))
    pipeline_artifacts.write_stage_artifact("recommendations", DATE, {"date": DATE, "games": games}, produced_by=produced_by)


def _write_jsonl(path, records):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wt") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


# ── Model identity / provenance ──────────────────────────────────────────

def test_git_commit_sha_prefers_github_sha_env_var(monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "deadbeef1234")
    assert _git_commit_sha() == "deadbeef1234"


def test_git_commit_sha_falls_back_to_git_rev_parse(monkeypatch):
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    sha = _git_commit_sha()
    # This repo IS a git checkout, so the fallback must return a real 40-char hex SHA.
    assert sha is not None
    assert len(sha) == 40
    assert all(c in "0123456789abcdef" for c in sha)


def test_model_config_version_reads_real_rules_json():
    assert _model_config_version() == "1.0"


def test_model_config_version_none_when_file_missing(tmp_path):
    assert _model_config_version(rules_path=str(tmp_path / "nope.json")) is None


def test_multiple_model_configurations_produce_different_versions(tmp_path):
    """Two differently-versioned config files must never be labeled the same modelConfigVersion."""
    path_a = tmp_path / "rules_a.json"
    path_b = tmp_path / "rules_b.json"
    path_a.write_text(json.dumps({"_version": "1.0"}))
    path_b.write_text(json.dumps({"_version": "2.0"}))
    assert _model_config_version(str(path_a)) != _model_config_version(str(path_b))
    assert _model_config_version(str(path_a)) == "1.0"
    assert _model_config_version(str(path_b)) == "2.0"


def test_probability_adapter_reports_which_field_supplied_the_value():
    assert _market_implied_probability_with_adapter(_row(kalshiVF=55.0)) == (55.0, "kalshiVF")
    assert _market_implied_probability_with_adapter(_row(marketProbVF=55.0)) == (55.0, "marketProbVF")
    assert _market_implied_probability_with_adapter(_row(executableMarketProb=55.0)) == (55.0, "executableMarketProb")
    # priority order: kalshiVF wins even when others are also present
    assert _market_implied_probability_with_adapter(_row(kalshiVF=55.0, marketProbVF=60.0)) == (55.0, "kalshiVF")
    assert _market_implied_probability_with_adapter(_row()) == (None, None)


def test_model_identity_fields_populate_on_real_build(monkeypatch, tmp_path):
    games = [_game([_row(ticker="T1", modelProb=55.0, kalshiVF=50.0)])]
    _write_recommendations(monkeypatch, tmp_path, games)
    records, _ = build_model_evaluations_from_pipeline(DATE, "run1", [])
    r = records[0]
    assert r["modelCommitSha"] is not None
    assert r["modelConfigVersion"] == "1.0"
    assert r["probabilityAdapter"] == "kalshiVF"
    assert r["pipelineRunId"]  # the artifact's own meta.createdAt
    assert r["artifactSource"] == "recommendations"
    assert schema.validate_record("model_evaluation", r) == []


# ── Confidence / data quality provenance ────────────────────────────────

def test_confidence_source_prefers_confidence_tier():
    assert _confidence_with_source(_row(confidenceTier="HIGH", confidence="MEDIUM")) == ("HIGH", "confidenceTier")


def test_confidence_source_falls_back_to_bare_confidence_field():
    assert _confidence_with_source(_row(confidenceTier=None, confidence="PAPER")) == ("PAPER", "confidence")


def test_confidence_source_none_when_neither_present():
    assert _confidence_with_source(_row()) == (None, None)


def test_data_quality_reasons_copied_verbatim_never_reworded():
    row = _row(missingFields=["odds.kalshi.nrfi_yrfi"], lineupStatusReason="Official lineup confirmed, 6/9 batters resolved")
    reasons = _data_quality_reasons(row)
    assert "odds.kalshi.nrfi_yrfi" in reasons
    assert "Official lineup confirmed, 6/9 batters resolved" in reasons


def test_data_quality_reasons_empty_when_nothing_to_report():
    assert _data_quality_reasons(_row()) == []


# ── Lineup-state mapping (5-value vocabulary) ────────────────────────────

def test_lineup_state_confirmed_requires_full_quality():
    assert _lineup_confirmation_state(_row(lineupConfirmedOfficial=True, lineupDataQuality="full")) == "CONFIRMED"


def test_lineup_state_partial_when_official_but_not_full_quality():
    assert _lineup_confirmation_state(_row(lineupConfirmedOfficial=True, lineupDataQuality="partial")) == "PARTIAL"
    assert _lineup_confirmation_state(_row(lineupConfirmedOfficial=True, lineupDataQuality="insufficient")) == "PARTIAL"


def test_lineup_state_partial_when_posted_but_not_official_and_incomplete():
    assert _lineup_confirmation_state(_row(lineupPosted=True, lineupDataQuality="partial")) == "PARTIAL"


def test_lineup_state_projected_when_posted_but_not_official_full_quality():
    assert _lineup_confirmation_state(_row(lineupPosted=True, lineupDataQuality="full")) == "PROJECTED"


def test_lineup_state_projected_when_nonmissing_status_without_official_flag():
    assert _lineup_confirmation_state(_row(lineupStatus="projected")) == "PROJECTED"


def test_lineup_state_unconfirmed_when_actively_checked_and_missing():
    assert _lineup_confirmation_state(_row(lineupPosted=False, lineupStatus="missing")) == "UNCONFIRMED"
    assert _lineup_confirmation_state(_row(lineupPosted=False)) == "UNCONFIRMED"


def test_lineup_state_unknown_when_no_evidence_at_all():
    assert _lineup_confirmation_state(_row()) == "UNKNOWN"


# ── Thesis-tag assignment + provenance ───────────────────────────────────

def test_starter_edge_tag_with_evidence(monkeypatch, tmp_path):
    game = _game([_row(market="F5_ML_Away", ticker="T1", modelProb=55.0, kalshiVF=50.0)], f5={"f5Amplified": True, "xERAGap": 1.8, "favoredSide": "AWAY"})
    tags, evidence = thesis_tags_and_evidence_for_row(game["marketLedger"][0], game)
    assert "STARTER_EDGE" in tags
    assert "1.8" in evidence["STARTER_EDGE"]
    assert "STARTER_FADE" not in tags


def test_starter_fade_tag_when_favored_side_opposes():
    game = _game([_row(market="F5_ML_Home", ticker="T1", modelProb=55.0, kalshiVF=50.0)], f5={"f5Amplified": True, "xERAGap": 1.8, "favoredSide": "AWAY"})
    tags, evidence = thesis_tags_and_evidence_for_row(game["marketLedger"][0], game)
    assert "STARTER_FADE" in tags
    assert "STARTER_EDGE" not in tags


def test_bullpen_edge_and_disadvantage_are_independent():
    game = _game(
        [_row(market="ML_Away", ticker="T1", modelProb=55.0, kalshiVF=50.0)],
        away_extra={"bullpen": {"vulnerable": True, "xFIP": 5.0}},
        home_extra={"bullpen": {"vulnerable": False, "xFIP": 3.0}},
    )
    tags, evidence = thesis_tags_and_evidence_for_row(game["marketLedger"][0], game)
    # ML_Away bets ON the away team -- the HOME bullpen (opponent) is what matters for BULLPEN_EDGE,
    # and the AWAY bullpen (own) is what matters for BULLPEN_DISADVANTAGE.
    assert "BULLPEN_EDGE" not in tags  # home bullpen (opponent) is not vulnerable
    assert "BULLPEN_DISADVANTAGE" in tags  # away bullpen (own) IS vulnerable

    game2 = _game(
        [_row(market="ML_Away", ticker="T1", modelProb=55.0, kalshiVF=50.0)],
        away_extra={"bullpen": {"vulnerable": True, "xFIP": 5.0}},
        home_extra={"bullpen": {"vulnerable": True, "xFIP": 5.5}},
    )
    tags2, _ = thesis_tags_and_evidence_for_row(game2["marketLedger"][0], game2)
    assert "BULLPEN_EDGE" in tags2  # opponent (home) bullpen vulnerable -> edge for away bettor
    assert "BULLPEN_DISADVANTAGE" in tags2  # own (away) bullpen also vulnerable -> disadvantage too


def test_lineup_edge_and_downgrade():
    row_edge = _row(market="ML_Away", ticker="T1", modelProb=55.0, kalshiVF=50.0,
                     lineupConfirmedOfficial=True, lineupAdjApplied=True, lineupDataQuality="full")
    tags, evidence = thesis_tags_and_evidence_for_row(row_edge, _game([row_edge]))
    assert "LINEUP_EDGE" in tags
    assert "full" in evidence["LINEUP_EDGE"]

    row_downgrade = _row(market="ML_Away", ticker="T2", modelProb=55.0, kalshiVF=50.0,
                          lineupDataQuality="partial", lineupPosted=True)
    tags2, _ = thesis_tags_and_evidence_for_row(row_downgrade, _game([row_downgrade]))
    assert "LINEUP_DOWNGRADE" in tags2
    assert "LINEUP_EDGE" not in tags2


def test_price_dislocation_from_raw_edge_strong_reason_code():
    row = _row(market="ML_Away", ticker="T1", modelProb=55.0, kalshiVF=50.0, reasonCodes=["RAW_EDGE_STRONG", "LINEUP_CONFIRMED_OFFICIAL"])
    tags, evidence = thesis_tags_and_evidence_for_row(row, _game([row]))
    assert "PRICE_DISLOCATION" in tags
    assert "RAW_EDGE_STRONG" in evidence["PRICE_DISLOCATION"]


def test_market_expression_from_team_total_reason():
    game = _game([_row(market="TT_Home_Over", ticker="T1", modelProb=55.0, kalshiVF=50.0)], teamTotals={"homeTTReason": "Opp starter xERA 5.21", "awayTTReason": None})
    tags, evidence = thesis_tags_and_evidence_for_row(game["marketLedger"][0], game)
    assert "MARKET_EXPRESSION" in tags
    assert "Opp starter xERA 5.21" in evidence["MARKET_EXPRESSION"]


def test_market_expression_absent_when_reason_is_null():
    game = _game([_row(market="TT_Away_Over", ticker="T1", modelProb=55.0, kalshiVF=50.0)], teamTotals={"homeTTReason": "something", "awayTTReason": None})
    tags, _ = thesis_tags_and_evidence_for_row(game["marketLedger"][0], game)
    assert "MARKET_EXPRESSION" not in tags


def test_f5_over_full_game_tag():
    game = _game([_row(market="F5_ML_Away", ticker="T1", modelProb=55.0, kalshiVF=50.0)], f5={"f5Amplified": True, "xERAGap": 2.0, "favoredSide": None})
    tags, _ = thesis_tags_and_evidence_for_row(game["marketLedger"][0], game)
    assert "F5_OVER_FULL_GAME" in tags


def test_park_factor_tag_only_when_deviating_from_neutral():
    game_neutral = _game([_row(market="Game_Total", ticker="T1", modelProb=55.0, kalshiVF=50.0)], park={"parkFactor": 100, "name": "Neutral Park"})
    tags_neutral, _ = thesis_tags_and_evidence_for_row(game_neutral["marketLedger"][0], game_neutral)
    assert "PARK_FACTOR" not in tags_neutral

    game_hitter = _game([_row(market="Game_Total", ticker="T1", modelProb=55.0, kalshiVF=50.0)], park={"parkFactor": 103, "name": "Coors Field"})
    tags_hitter, evidence = thesis_tags_and_evidence_for_row(game_hitter["marketLedger"][0], game_hitter)
    assert "PARK_FACTOR" in tags_hitter
    assert "103" in evidence["PARK_FACTOR"]


def test_unsupported_tags_never_assigned_even_with_lots_of_context(monkeypatch, tmp_path):
    """
    PLATOON_EDGE, WEATHER_OVER/UNDER, UMPIRE_FACTOR, WORKLOAD_OVER/UNDER,
    STRIKEOUT_MATCHUP, CONTACT_MATCHUP, CORRELATED_POSITION have no real
    producer anywhere in the pipeline (docs/EDGELAB_EVALUATION_METADATA.md)
    -- must never appear regardless of how much other evidence is present.
    """
    game = _game(
        [_row(market="F5_ML_Away", ticker="T1", modelProb=55.0, kalshiVF=50.0,
              lineupConfirmedOfficial=True, lineupAdjApplied=True, lineupDataQuality="full",
              reasonCodes=["RAW_EDGE_STRONG"])],
        away_extra={"bullpen": {"vulnerable": True, "xFIP": 5.0}},
        home_extra={"bullpen": {"vulnerable": True, "xFIP": 5.5}},
        f5={"f5Amplified": True, "xERAGap": 2.0, "favoredSide": "AWAY"},
        park={"parkFactor": 103},
    )
    tags, _ = thesis_tags_and_evidence_for_row(game["marketLedger"][0], game)
    unsupported = {"PLATOON_EDGE", "WEATHER_OVER", "WEATHER_UNDER", "UMPIRE_FACTOR",
                   "WORKLOAD_OVER", "WORKLOAD_UNDER", "STRIKEOUT_MATCHUP", "CONTACT_MATCHUP",
                   "CORRELATED_POSITION"}
    assert unsupported.isdisjoint(set(tags))


def test_no_tags_or_evidence_when_no_supporting_context():
    row = _row(market="ML_Away", ticker="T1", modelProb=55.0, kalshiVF=50.0)
    tags, evidence = thesis_tags_and_evidence_for_row(row, _game([row]))
    assert tags == []
    assert evidence == {}


# ── Correlation groups (deterministic, name-based) ───────────────────────

def test_game_side_and_starter_groups_for_ml_markets():
    game = _game([], away="PIT", home="CIN", away_extra={"pitcher": {"name": "Paul Skenes"}}, home_extra={"pitcher": {"name": "Hunter Greene"}})
    groups_away = correlation_groups_for_row(_row(market="ML_Away"), game)
    assert "GAME_SIDE_PIT" in groups_away
    assert "STARTER_SUCCESS_Paul Skenes" in groups_away
    assert "STARTER_FAILURE_Hunter Greene" in groups_away

    groups_home = correlation_groups_for_row(_row(market="ML_Home"), game)
    assert "GAME_SIDE_CIN" in groups_home
    assert "STARTER_SUCCESS_Hunter Greene" in groups_home
    assert "STARTER_FAILURE_Paul Skenes" in groups_home


def test_two_sided_single_ticker_run_line_markets_map_to_their_own_team():
    """RL_Away/RL_Home share one Kalshi ticker but must map to DIFFERENT (their own team's) correlation groups."""
    game = _game([], away="PIT", home="CIN")
    groups_away = correlation_groups_for_row(_row(market="RL_Away"), game)
    groups_home = correlation_groups_for_row(_row(market="RL_Home"), game)
    assert groups_away == ["GAME_SIDE_PIT"]
    assert groups_home == ["GAME_SIDE_CIN"]
    # Consistent with ML's own grouping -- this is exactly Rule 76's "ML + RL on the same team are the same bet".
    ml_away_groups = correlation_groups_for_row(_row(market="ML_Away"), game)
    assert "GAME_SIDE_PIT" in ml_away_groups


def test_alternate_line_team_totals_collapse_into_the_same_group():
    """Two TT_Away_Over rows at different thresholds (alternate lines) must land in the SAME correlation group -- same thesis, different price."""
    game = _game([], away="PIT", home="CIN")
    groups_line_a = correlation_groups_for_row(_row(market="TT_Away_Over", threshold=4.5), game)
    groups_line_b = correlation_groups_for_row(_row(market="TT_Away_Over", threshold=5.5), game)
    assert groups_line_a == groups_line_b == ["TEAM_RUNS_OVER_PIT"]


def test_game_total_yrfi_nrfi_groups():
    game = _game([])
    assert correlation_groups_for_row(_row(market="Game_Total"), game) == ["GAME_OVER"]
    assert correlation_groups_for_row(_row(market="YRFI"), game) == ["YRFI"]
    assert correlation_groups_for_row(_row(market="NRFI"), game) == ["NRFI"]


def test_correlation_groups_empty_for_unmapped_market():
    assert correlation_groups_for_row(_row(market="SOMETHING_ELSE"), _game([])) == []


def test_correlation_groups_missing_pitcher_name_omits_starter_groups_not_fabricated():
    game = _game([], away="PIT", home="CIN", away_extra={"pitcher": {}}, home_extra={"pitcher": {"name": "Hunter Greene"}})
    groups = correlation_groups_for_row(_row(market="ML_Away"), game)
    assert "GAME_SIDE_PIT" in groups
    assert not any(g.startswith("STARTER_SUCCESS_") for g in groups)  # away pitcher name missing
    assert "STARTER_FAILURE_Hunter Greene" in groups


# ── Historical backfill (population rates, no data invention) ──────────

def test_backfill_script_updates_records_and_reports_before_after_rates(monkeypatch, tmp_path):
    from scripts.edgelab import backfill_evaluation_metadata as backfill

    monkeypatch.chdir(tmp_path)
    # tmp_path isn't a git checkout, so the `git rev-parse HEAD` fallback
    # would legitimately return None here -- GITHUB_SHA stands in for it,
    # matching how the real Actions environment always has it set.
    monkeypatch.setenv("GITHUB_SHA", "test-sha-for-backfill")
    games = [_game([_row(market="ML_Away", ticker="T1", modelProb=55.0, kalshiVF=50.0, edge=5.0)])]
    monkeypatch.setattr(pipeline_artifacts, "PIPELINE_ROOT", os.path.join(str(tmp_path), "data", "pipeline"))
    pipeline_artifacts.write_stage_artifact("recommendations", DATE, {"date": DATE, "games": games}, produced_by="scripts/build_market_ledger.py")

    # Simulate an "old" (pre-Milestone-4) committed record: real required
    # fields present, none of the new Milestone 4 fields at all.
    old_record = {
        "schemaVersion": "1", "modelEvaluationId": None, "runId": "old-run", "marketTicker": "T1",
        "evaluationStatus": "EVALUATED", "modelFairProbability": 55.0, "estimatedEdge": 5.0,
        "createdAt": "2026-07-31T00:00:00Z", "source": "pipeline_recommendations", "validationStatus": "valid",
        "provenance": {"sourceSystem": "pipeline_recommendations", "sourceFile": "x", "sourceKey": "y", "capturedAt": "2026-07-31T00:00:00Z", "ingestedAt": "2026-07-31T00:00:00Z"},
    }
    # Compute the real deterministic ID so the backfill recognizes this as the same row.
    fresh_preview, _ = build_model_evaluations_from_pipeline(DATE, "preview-run", [])
    old_record["modelEvaluationId"] = fresh_preview[0]["modelEvaluationId"]

    path = os.path.join("data", "edgelab", "model_evaluations", f"{DATE}.jsonl")
    _write_jsonl(path, [old_record])

    result = backfill.backfill_date(DATE)
    assert result["recordsInspected"] == 1
    assert result["recordsUpdated"] == 1
    assert result["conflicts"] == []
    assert result["beforeRates"]["modelCommitSha"] == 0.0
    assert result["afterRates"]["modelCommitSha"] == 100.0

    refreshed = list(__import__("lib.edgelab.storage", fromlist=["read_records"]).read_records(path))
    assert refreshed[0]["modelCommitSha"] is not None
    # Immutable core facts must be preserved exactly.
    assert refreshed[0]["modelFairProbability"] == 55.0
    assert refreshed[0]["evaluationStatus"] == "EVALUATED"


def test_backfill_flags_conflict_instead_of_silently_overwriting_changed_core_fact(monkeypatch, tmp_path):
    from scripts.edgelab import backfill_evaluation_metadata as backfill

    monkeypatch.chdir(tmp_path)
    # tmp_path isn't a git checkout, so the `git rev-parse HEAD` fallback
    # would legitimately return None here -- GITHUB_SHA stands in for it,
    # matching how the real Actions environment always has it set.
    monkeypatch.setenv("GITHUB_SHA", "test-sha-for-backfill")
    games = [_game([_row(market="ML_Away", ticker="T1", modelProb=55.0, kalshiVF=50.0, edge=5.0)])]
    monkeypatch.setattr(pipeline_artifacts, "PIPELINE_ROOT", os.path.join(str(tmp_path), "data", "pipeline"))
    pipeline_artifacts.write_stage_artifact("recommendations", DATE, {"date": DATE, "games": games}, produced_by="scripts/build_market_ledger.py")

    fresh_preview, _ = build_model_evaluations_from_pipeline(DATE, "preview-run", [])
    old_record = dict(fresh_preview[0])
    old_record["modelFairProbability"] = 999.0  # deliberately wrong/stale vs. what recomputation will produce

    path = os.path.join("data", "edgelab", "model_evaluations", f"{DATE}.jsonl")
    _write_jsonl(path, [old_record])

    result = backfill.backfill_date(DATE)
    assert result["recordsUpdated"] == 0
    assert len(result["conflicts"]) == 1
    assert "modelFairProbability" in result["conflicts"][0]


def test_backfill_no_existing_file_reports_zero_inspected(tmp_path, monkeypatch):
    from scripts.edgelab import backfill_evaluation_metadata as backfill
    monkeypatch.chdir(tmp_path)
    result = backfill.backfill_date("2099-01-01")
    assert result["recordsInspected"] == 0
    assert result["recordsUpdated"] == 0


# ── Population report additions (by date, by recommendation status, unresolved) ──

def _minimal_evaluation(model_evaluation_id, **overrides):
    rec = {
        "schemaVersion": "1", "modelEvaluationId": model_evaluation_id, "runId": "r1",
        "marketTicker": f"T-{model_evaluation_id}", "evaluationStatus": "NO_MODEL_SUPPORT",
        "createdAt": "2026-07-31T22:00:00Z", "source": "test", "validationStatus": "valid",
        "provenance": {"sourceSystem": None, "sourceFile": None, "sourceKey": None, "capturedAt": None, "ingestedAt": None},
    }
    rec.update(overrides)
    return rec


def test_population_by_date_breakdown(tmp_path):
    _write_jsonl(os.path.join(str(tmp_path), "model_evaluations", "2026-07-30.jsonl"), [_minimal_evaluation("e1", evaluationStatus="EVALUATED", modelFairProbability=55.0)])
    _write_jsonl(os.path.join(str(tmp_path), "model_evaluations", "2026-07-31.jsonl"), [_minimal_evaluation("e2"), _minimal_evaluation("e3")])
    with open_session(root=str(tmp_path)) as session:
        rows = {r["date"]: r for r in population_by_date(session)}
        assert rows["2026-07-30"]["n"] == 1
        assert rows["2026-07-30"]["pctModelFairProbability"] == 100.0
        assert rows["2026-07-31"]["n"] == 2
        assert rows["2026-07-31"]["pctModelFairProbability"] == 0.0


def test_population_by_recommendation_status_requires_both_entities(tmp_path):
    with open_session(root=str(tmp_path)) as session:
        assert population_by_recommendation_status(session) == []


def test_unresolved_metadata_report_flags_evaluated_rows_missing_confidence(tmp_path):
    records = [
        _minimal_evaluation("e1", evaluationStatus="EVALUATED", confidence="HIGH", lineupConfirmationState="CONFIRMED"),
        _minimal_evaluation("e2", evaluationStatus="EVALUATED", confidence=None, lineupConfirmationState="UNKNOWN"),
    ]
    _write_jsonl(os.path.join(str(tmp_path), "model_evaluations", f"{DATE}.jsonl"), records)
    with open_session(root=str(tmp_path)) as session:
        report = unresolved_metadata_report(session)
        assert report["totalEvaluated"] == 2
        assert report["evaluatedMissingConfidence"] == 1
        assert report["evaluatedMissingLineupEvidence"] == 1


def test_unresolved_metadata_report_none_when_unavailable(tmp_path):
    with open_session(root=str(tmp_path)) as session:
        assert unresolved_metadata_report(session) is None


# ── Calibration slicing by new metadata ─────────────────────────────────

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


def test_calibration_slices_by_model_source_and_data_quality(tmp_path):
    evaluation = _minimal_evaluation(
        "eval-1", marketTicker="T-b1", evaluationStatus="EVALUATED",
        modelSource="scripts/build_market_ledger.py", dataQuality="full",
        correlationGroups=["GAME_SIDE_PIT"],
    )
    bet = _bet_for_calibration("b1", model_evaluation_id="eval-1")
    _write_jsonl(os.path.join(str(tmp_path), "model_evaluations", f"{DATE}.jsonl"), [evaluation])
    _write_jsonl(os.path.join(str(tmp_path), "bets", "bets.jsonl"), [bet])

    with open_session(root=str(tmp_path)) as session:
        version_rows = cal.model_version_source_calibration(session)
        assert len(version_rows) == 1
        assert version_rows[0]["modelSource"] == "scripts/build_market_ledger.py"
        assert version_rows[0]["n"] == 1

        quality_rows = {r["dataQuality"]: r for r in cal.data_quality_calibration(session)}
        assert quality_rows["full"]["n"] == 1

        corr_rows = {r["correlationGroup"]: r for r in cal.correlation_group_calibration(session)}
        assert corr_rows["GAME_SIDE_PIT"]["n"] == 1


def test_calibration_correlation_group_multi_membership(tmp_path):
    """A bet linked to an evaluation with multiple correlation groups contributes to every one of them."""
    evaluation = _minimal_evaluation(
        "eval-1", marketTicker="T-b1", evaluationStatus="EVALUATED",
        correlationGroups=["F5_SIDE_PIT", "STARTER_SUCCESS_Paul Skenes"],
    )
    bet = _bet_for_calibration("b1", model_evaluation_id="eval-1")
    _write_jsonl(os.path.join(str(tmp_path), "model_evaluations", f"{DATE}.jsonl"), [evaluation])
    _write_jsonl(os.path.join(str(tmp_path), "bets", "bets.jsonl"), [bet])
    with open_session(root=str(tmp_path)) as session:
        corr_rows = {r["correlationGroup"] for r in cal.correlation_group_calibration(session)}
        assert "F5_SIDE_PIT" in corr_rows
        assert "STARTER_SUCCESS_Paul Skenes" in corr_rows


def test_calibration_sample_size_gate_still_applies_to_new_dimensions(tmp_path):
    """Milestone 4 dimensions must still be gated by the same INSUFFICIENT_SAMPLE/DESCRIPTIVE_ONLY/CALIBRATED thresholds -- not a new, looser rule."""
    records, bets = [], []
    for i in range(5):
        eval_id = f"eval-{i}"
        records.append(_minimal_evaluation(eval_id, marketTicker=f"T-b{i}", evaluationStatus="EVALUATED", dataQuality="full"))
        bets.append(_bet_for_calibration(f"b{i}", model_evaluation_id=eval_id))
    _write_jsonl(os.path.join(str(tmp_path), "model_evaluations", f"{DATE}.jsonl"), records)
    _write_jsonl(os.path.join(str(tmp_path), "bets", "bets.jsonl"), bets)
    with open_session(root=str(tmp_path)) as session:
        rows = cal.data_quality_calibration(session)
        assert rows[0]["n"] == 5
        assert rows[0]["status"] == "INSUFFICIENT_SAMPLE"


# ── Backward compatibility ───────────────────────────────────────────────

def test_old_shaped_model_evaluation_without_milestone4_fields_still_validates():
    """A record shaped exactly like every one of the 275 real records committed before this milestone's code existed."""
    old = {
        "schemaVersion": "1", "modelEvaluationId": "x", "runId": "r", "marketTicker": "T",
        "evaluationStatus": "EVALUATED", "modelFairProbability": 55.0,
        "createdAt": "2026-07-31T22:00:00Z", "source": "pipeline_recommendations", "validationStatus": "valid",
        "provenance": {"sourceSystem": None, "sourceFile": None, "sourceKey": None, "capturedAt": None, "ingestedAt": None},
    }
    for field in ("modelCommitSha", "modelConfigVersion", "probabilityAdapter", "confidenceSource",
                  "pipelineRunId", "artifactSource", "dataQualityReasons", "tagEvidence", "correlationGroups"):
        assert field not in old
    assert schema.validate_record("model_evaluation", old) == []


def test_view_handles_missing_milestone4_columns_entirely(tmp_path):
    """A model_evaluations file with none of the new columns at all (every real file before this migration) must still be queryable."""
    old = {
        "schemaVersion": "1", "modelEvaluationId": "x", "runId": "r", "marketTicker": "T",
        "evaluationStatus": "EVALUATED", "modelFairProbability": 55.0,
        "createdAt": "2026-07-31T22:00:00Z", "source": "pipeline_recommendations", "validationStatus": "valid",
        "provenance": {"sourceSystem": None, "sourceFile": None, "sourceKey": None, "capturedAt": None, "ingestedAt": None},
    }
    _write_jsonl(os.path.join(str(tmp_path), "model_evaluations", f"{DATE}.jsonl"), [old])
    with open_session(root=str(tmp_path)) as session:
        # tagEvidence (a dict-shaped object) is deliberately not exposed as
        # a SQL column on v_model_evaluations -- it's a per-record audit
        # trail meant to be read directly off the JSONL, not aggregated in
        # SQL; only the array/scalar Milestone 4 columns are queryable.
        rows = session.fetchall("SELECT modelCommitSha, correlationGroups FROM v_model_evaluations")
        assert rows[0][0] is None
        assert rows[0][1] is None


# ── Determinism ──────────────────────────────────────────────────────────

def test_repeated_builds_produce_identical_metadata(monkeypatch, tmp_path):
    game = _game(
        [_row(market="F5_ML_Away", ticker="T1", modelProb=55.0, kalshiVF=50.0, edge=5.0,
              lineupConfirmedOfficial=True, lineupAdjApplied=True, lineupDataQuality="full",
              reasonCodes=["RAW_EDGE_STRONG"])],
        away_extra={"bullpen": {"vulnerable": False}}, home_extra={"bullpen": {"vulnerable": True, "xFIP": 5.0}},
        f5={"f5Amplified": True, "xERAGap": 1.7, "favoredSide": "AWAY"},
    )
    _write_recommendations(monkeypatch, tmp_path, [game])
    monkeypatch.setenv("GITHUB_SHA", "fixed-sha-for-determinism-test")
    first, _ = build_model_evaluations_from_pipeline(DATE, "run1", [])
    second, _ = build_model_evaluations_from_pipeline(DATE, "run1", [])
    assert first == second
    # F5_OVER_FULL_GAME also fires: market starts with "F5_" and f5Amplified=True.
    assert sorted(first[0]["thesisTags"]) == sorted(["STARTER_EDGE", "BULLPEN_EDGE", "PRICE_DISLOCATION", "LINEUP_EDGE", "F5_OVER_FULL_GAME"])
