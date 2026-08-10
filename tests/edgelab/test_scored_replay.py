#!/usr/bin/env python3
"""
tests/edgelab/test_scored_replay.py
========================================
Scored Postgame Replay milestone: coverage for
lib/edgelab/scored_replay.py + data/edgelab/schema_v1/
scored_replay_run.schema.json + scored_replay_result.schema.json.

Every test runs inside an isolated tmp_path (monkeypatch.chdir), never
against the real repository's data/ tree -- same discipline as
tests/edgelab/test_replay.py.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lib.edgelab import ids  # noqa: E402
from lib.edgelab import replay  # noqa: E402
from lib.edgelab import schema as edgelab_schema  # noqa: E402
from lib.edgelab import scored_replay as sr  # noqa: E402
from lib.edgelab import storage  # noqa: E402


# ── Fixtures shared across this file ─────────────────────────────────────

def _replay_result(**overrides):
    base = {
        "schemaVersion": "1",
        "replayResultId": "rr-1",
        "replayRunId": "run-1",
        "gameId": "g1",
        "marketTicker": "KXMLBGAME-1-AAA",
        "marketFamily": "KXMLBGAME",
        "selection": "ML_Away",
        "side": None,
        "threshold": None,
        "originalModelProbability": 62.5,
        "replayedModelProbability": 65.0,
        "originalMarketPrice": 54.0,
        "replayedMarketPrice": 54.0,
        "originalExecutablePriceUsed": 55.0,
        "replayedExecutablePriceUsed": 55.0,
        "originalExecutableMarketProb": 55.0,
        "replayedExecutableMarketProb": 55.0,
        "originalEdge": 7.5,
        "replayedEdge": 10.0,
        "originalRecommendationStatus": "Accepted",
        "replayedRecommendationStatus": "Accepted",
        "originalTier": "HIGH",
        "replayedTier": "HIGH",
        "originalPassReason": None,
        "replayedPassReason": None,
        "originalPreferredExpression": None,
        "replayedPreferredExpression": None,
        "changedDecision": False,
        "changeReasons": [],
        "comparisonClassification": "UNCHANGED",
        "settlementLinkage": {"status": "RESOLVED", "result": "YES", "reason": None},
        "clvLinkage": {"status": "RESOLVED", "clvValue": 3.2, "reason": None},
        "comparisonMetadata": {"gameLabel": "AAA@HHH"},
        "validationStatus": "valid",
        "provenance": {"sourceSystem": "replay_engine", "sourceFile": None, "sourceKey": "k", "capturedAt": "t", "ingestedAt": "t"},
    }
    base.update(overrides)
    return base


def _replay_run(**overrides):
    base = {
        "schemaVersion": "1",
        "replayRunId": "run-1",
        "snapshotId": "snap-1",
        "snapshotManifestHash": "a" * 64,
        "snapshotDate": "2026-07-31",
        "productionRunId": "prod-run-1",
        "workflowRunId": None,
        "replayFrameworkVersion": replay.REPLAY_FRAMEWORK_VERSION,
        "replayMode": replay.MODE_CANDIDATE,
        "candidateModelCommitSha": "deadbeef",
        "candidateModelVersion": None,
        "productionModelCommitSha": None,
        "pricingVersions": {},
        "replayFidelity": "LEVEL_2_PRODUCTION_EQUIVALENT",
        "eligibilityStatus": replay.ELIGIBLE_LEVEL_2,
        "startedAt": "2026-08-01T00:00:00Z",
        "completedAt": "2026-08-01T00:00:05Z",
        "runStatus": replay.RUN_STATUS_COMPLETED,
        "limitationReasons": [],
        "summary": {"marketsEvaluated": 1, "marketsComparable": 1, "decisionsChanged": 0,
                     "settledResolved": 1, "settledUnresolved": 0, "clvResolved": 1},
        "performance": {"n": 1, "sampleSizeStatus": "INSUFFICIENT_SAMPLE", "winRate": 1.0,
                          "expectedWinRate": 0.65, "calibrationError": 0.35,
                          "avgBrierScore": 0.1225, "avgLogLoss": 0.43, "roi": None},
        "provenance": {"sourceSystem": "replay_engine", "sourceFile": None, "sourceKey": None,
                        "capturedAt": "2026-08-01T00:00:00Z", "ingestedAt": "2026-08-01T00:00:00Z"},
    }
    base.update(overrides)
    base["manifestHash"] = replay.compute_run_manifest_hash(base)
    return base


def _write_replay_run_and_results(run, results):
    return replay.write_replay_outputs(run, results)


def _write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


# ══════════════════════════════════════════════════════════════════════════
# Part 1: pure score_replay_result()
# ══════════════════════════════════════════════════════════════════════════

class TestScoreReplayResultPredictionAvailability:

    def test_prediction_available_copies_original_fields_verbatim(self):
        result = _replay_result()
        scored = sr.score_replay_result(result, scored_at="t")
        assert scored["predictionStatus"] == sr.PREDICTION_AVAILABLE
        assert scored["predictedFairProbability"] == 62.5
        assert scored["marketProbabilityPregame"] == 55.0
        assert scored["confidenceTier"] == "HIGH"
        assert scored["recommendationActionStatus"] == "Accepted"

    def test_never_uses_replayed_fields(self):
        """The replayed* fields on the ReplayResult (a candidate re-run of
        CURRENT code) must never leak into the scored output -- only
        original* (the immutable pregame prediction) is scored."""
        result = _replay_result(originalModelProbability=40.0, replayedModelProbability=99.0)
        scored = sr.score_replay_result(result, scored_at="t")
        assert scored["predictedFairProbability"] == 40.0

    def test_missing_prediction_is_unavailable_not_guessed(self):
        result = _replay_result(originalModelProbability=None)
        scored = sr.score_replay_result(result, scored_at="t")
        assert scored["predictionStatus"] == sr.PREDICTION_STATUS_UNAVAILABLE
        assert scored["predictedFairProbability"] is None
        assert scored["wager"]["evaluationStage"] == sr.PREDICTION_UNAVAILABLE

    def test_market_prob_falls_back_to_market_price_only_when_executable_absent(self):
        result = _replay_result(originalExecutableMarketProb=None, originalMarketPrice=48.0)
        scored = sr.score_replay_result(result, scored_at="t")
        assert scored["marketProbabilityPregame"] == 48.0


class TestScoreReplayResultObjectiveOutcomeAndClv:

    def test_resolved_settlement_maps_to_market_settled(self):
        result = _replay_result(settlementLinkage={"status": "RESOLVED", "result": "YES", "reason": None})
        scored = sr.score_replay_result(result, scored_at="t")
        assert scored["objectiveOutcome"] == {"settlementStatus": "MARKET_SETTLED", "result": "YES", "reason": None}

    def test_unresolved_settlement_carries_reason(self):
        result = _replay_result(settlementLinkage={"status": "UNRESOLVED", "result": None, "reason": "NO_SETTLEMENT_RECORD_FOR_THIS_MARKET"})
        scored = sr.score_replay_result(result, scored_at="t")
        assert scored["objectiveOutcome"]["settlementStatus"] == "UNRESOLVED_SETTLEMENT"
        assert scored["objectiveOutcome"]["reason"] == "NO_SETTLEMENT_RECORD_FOR_THIS_MARKET"

    def test_clv_available_only_from_resolved_closing_quote_linkage(self):
        result = _replay_result(clvLinkage={"status": "RESOLVED", "clvValue": -2.5, "reason": None})
        scored = sr.score_replay_result(result, scored_at="t")
        assert scored["clv"] == {"clvStatus": "CLV_AVAILABLE", "value": -2.5, "reason": None}

    def test_clv_unavailable_never_substitutes_a_value(self):
        result = _replay_result(clvLinkage={"status": "UNRESOLVED", "clvValue": None, "reason": "NO_CLV_QUOTE_FOR_THIS_MARKET"})
        scored = sr.score_replay_result(result, scored_at="t")
        assert scored["clv"]["clvStatus"] == "CLV_UNAVAILABLE"
        assert scored["clv"]["value"] is None


class TestScoreReplayResultWagerLifecycle:

    def test_confirmed_bet_when_active_bet_record_present(self):
        result = _replay_result()
        bet = {"betId": "bet-1", "result": "WIN", "stake": 10.0, "netProfitLoss": 8.0, "recordStatus": "ACTIVE"}
        scored = sr.score_replay_result(result, bet_record=bet, scored_at="t")
        assert scored["wager"]["evaluationStage"] == sr.CONFIRMED_BET
        assert scored["wager"]["betId"] == "bet-1"
        assert scored["wager"]["result"] == "WIN"
        assert scored["wager"]["netProfitLoss"] == 8.0
        assert scored["wager"]["grossReturn"] == 18.0

    def test_cancelled_bet_is_never_treated_as_confirmed(self):
        result = _replay_result()
        bet = {"betId": "bet-1", "result": "WIN", "stake": 10.0, "netProfitLoss": 8.0, "recordStatus": "CANCELLED"}
        scored = sr.score_replay_result(result, bet_record=bet, scored_at="t")
        assert scored["wager"]["evaluationStage"] != sr.CONFIRMED_BET
        assert scored["wager"]["result"] is None
        assert scored["wager"]["netProfitLoss"] is None

    def test_recommended_accepted_with_no_bet_is_recommended_no_confirmed_bet(self):
        result = _replay_result(originalRecommendationStatus="Accepted")
        scored = sr.score_replay_result(result, scored_at="t")
        assert scored["wager"]["evaluationStage"] == sr.RECOMMENDED_NO_CONFIRMED_BET

    def test_rejected_with_no_bet_is_evaluated_no_bet_placed(self):
        result = _replay_result(originalRecommendationStatus="Rejected")
        scored = sr.score_replay_result(result, scored_at="t")
        assert scored["wager"]["evaluationStage"] == sr.EVALUATED_NO_BET_PLACED

    def test_null_recommendation_realized_economics_uses_canonical_helper(self):
        """netProfitLoss/grossReturn must come from
        lib.edgelab.bets.realized_bet_economics -- a confirmed-receipt
        override must be honored exactly like it is everywhere else."""
        result = _replay_result()
        bet = {
            "betId": "bet-1", "result": "WIN", "stake": 10.0, "netProfitLoss": 8.0,
            "confirmedReceiptReturn": 19.5, "confirmedReceiptNetProfitLoss": 9.5,
            "recordStatus": "ACTIVE",
        }
        scored = sr.score_replay_result(result, bet_record=bet, scored_at="t")
        assert scored["wager"]["grossReturn"] == 19.5
        assert scored["wager"]["netProfitLoss"] == 9.5


class TestScoreReplayResultBrierScore:

    def test_brier_score_requires_both_prediction_and_resolved_binary_outcome(self):
        result = _replay_result(originalModelProbability=70.0,
                                  settlementLinkage={"status": "RESOLVED", "result": "YES", "reason": None})
        scored = sr.score_replay_result(result, scored_at="t")
        assert scored["binaryOutcome"] == 1
        assert scored["brierScore"] == pytest.approx((0.70 - 1) ** 2)

    def test_brier_score_null_when_prediction_unavailable(self):
        result = _replay_result(originalModelProbability=None,
                                  settlementLinkage={"status": "RESOLVED", "result": "YES", "reason": None})
        scored = sr.score_replay_result(result, scored_at="t")
        assert scored["brierScore"] is None
        assert scored["binaryOutcome"] is None

    def test_brier_score_null_when_settlement_unresolved(self):
        result = _replay_result(settlementLinkage={"status": "UNRESOLVED", "result": None, "reason": "x"})
        scored = sr.score_replay_result(result, scored_at="t")
        assert scored["brierScore"] is None


class TestScoreReplayResultIdempotency:

    def test_same_input_yields_same_scored_replay_result_id(self):
        result = _replay_result()
        a = sr.score_replay_result(result, scored_at="t1")
        b = sr.score_replay_result(result, scored_at="t2")
        assert a["scoredReplayResultId"] == b["scoredReplayResultId"]

    def test_does_not_mutate_input_result(self):
        import copy
        result = _replay_result()
        before = copy.deepcopy(result)
        sr.score_replay_result(result, scored_at="t")
        assert result == before


class TestScoreReplayResultSchema:

    def test_scored_result_validates_against_schema(self):
        result = _replay_result()
        bet = {"betId": "bet-1", "result": "WIN", "stake": 10.0, "netProfitLoss": 8.0, "recordStatus": "ACTIVE"}
        scored = sr.score_replay_result(result, model_evaluation_id="me-1", recommendation_id="rec-1", bet_record=bet, scored_at="t")
        errors = edgelab_schema.validate_record("scored_replay_result", scored)
        assert errors == []

    def test_scored_result_with_no_prediction_validates(self):
        result = _replay_result(originalModelProbability=None, originalRecommendationStatus="Missing Data",
                                  settlementLinkage={"status": "UNRESOLVED", "result": None, "reason": "x"},
                                  clvLinkage={"status": "UNRESOLVED", "clvValue": None, "reason": "y"})
        scored = sr.score_replay_result(result, scored_at="t")
        errors = edgelab_schema.validate_record("scored_replay_result", scored)
        assert errors == []


# ══════════════════════════════════════════════════════════════════════════
# Part 2: pure aggregate_scored_results()
# ══════════════════════════════════════════════════════════════════════════

class TestAggregateScoredResults:

    def test_empty_list_returns_none_not_fabricated_report(self):
        assert sr.aggregate_scored_results([]) is None

    def test_brier_and_calibration_over_resolved_predictions_only(self):
        scored = [
            sr.score_replay_result(_replay_result(replayResultId="r1", originalModelProbability=80.0,
                                                     settlementLinkage={"status": "RESOLVED", "result": "YES", "reason": None}), scored_at="t"),
            sr.score_replay_result(_replay_result(replayResultId="r2", originalModelProbability=20.0,
                                                     settlementLinkage={"status": "RESOLVED", "result": "NO", "reason": None}), scored_at="t"),
            sr.score_replay_result(_replay_result(replayResultId="r3", originalModelProbability=None,
                                                     settlementLinkage={"status": "UNRESOLVED", "result": None, "reason": "x"}), scored_at="t"),
        ]
        agg = sr.aggregate_scored_results(scored)
        assert agg["n"] == 3
        assert agg["predictionUnavailableCount"] == 1
        assert agg["brier"]["n"] == 2
        assert agg["brier"]["winRate"] == 0.5  # one YES outcome, one NO outcome, among the 2 resolved rows
        assert agg["brier"]["avgBrierScore"] == pytest.approx(((0.8 - 1) ** 2 + (0.2 - 0) ** 2) / 2)
        buckets = {b["bucket"]: b for b in agg["calibrationBuckets"]}
        assert buckets["80-90%"]["n"] == 1
        assert buckets["20-30%"]["n"] == 1

    def test_recommended_vs_passed_are_disjoint(self):
        scored = [
            sr.score_replay_result(_replay_result(replayResultId="r1", originalRecommendationStatus="Accepted"), scored_at="t"),
            sr.score_replay_result(_replay_result(replayResultId="r2", originalRecommendationStatus="Rejected"), scored_at="t"),
        ]
        agg = sr.aggregate_scored_results(scored)
        assert agg["recommendedVsPassed"]["recommended"]["n"] == 1
        assert agg["recommendedVsPassed"]["passed"]["n"] == 1

    def test_clv_coverage_and_average_over_available_only(self):
        scored = [
            sr.score_replay_result(_replay_result(replayResultId="r1", clvLinkage={"status": "RESOLVED", "clvValue": 4.0, "reason": None}), scored_at="t"),
            sr.score_replay_result(_replay_result(replayResultId="r2", clvLinkage={"status": "RESOLVED", "clvValue": 2.0, "reason": None}), scored_at="t"),
            sr.score_replay_result(_replay_result(replayResultId="r3", clvLinkage={"status": "UNRESOLVED", "clvValue": None, "reason": "x"}), scored_at="t"),
        ]
        agg = sr.aggregate_scored_results(scored)
        assert agg["clv"]["coverageCount"] == 2
        assert agg["clv"]["coverageRate"] == pytest.approx(2 / 3, abs=1e-4)
        assert agg["clv"]["avgClv"] == pytest.approx(3.0)

    def test_realized_pnl_only_from_confirmed_bets(self):
        bet1 = {"betId": "b1", "result": "WIN", "stake": 10.0, "netProfitLoss": 8.0, "recordStatus": "ACTIVE"}
        bet2 = {"betId": "b2", "result": "LOSS", "stake": 5.0, "netProfitLoss": -5.0, "recordStatus": "ACTIVE"}
        scored = [
            sr.score_replay_result(_replay_result(replayResultId="r1"), bet_record=bet1, scored_at="t"),
            sr.score_replay_result(_replay_result(replayResultId="r2", originalRecommendationStatus="Accepted"), bet_record=bet2, scored_at="t"),
            # A recommended-but-never-confirmed row must NOT contribute to realized P/L at all.
            sr.score_replay_result(_replay_result(replayResultId="r3", originalRecommendationStatus="Accepted"), scored_at="t"),
        ]
        agg = sr.aggregate_scored_results(scored)
        assert agg["realizedPnl"]["confirmedBetCount"] == 2
        assert agg["realizedPnl"]["totalStaked"] == pytest.approx(15.0)
        assert agg["realizedPnl"]["totalNetProfitLoss"] == pytest.approx(3.0)
        assert agg["realizedPnl"]["roi"] == pytest.approx(3.0 / 15.0)

    def test_realized_pnl_is_none_with_zero_confirmed_bets(self):
        scored = [sr.score_replay_result(_replay_result(), scored_at="t")]
        agg = sr.aggregate_scored_results(scored)
        assert agg["realizedPnl"]["confirmedBetCount"] == 0
        assert agg["realizedPnl"]["totalStaked"] is None
        assert agg["realizedPnl"]["totalNetProfitLoss"] is None
        assert agg["realizedPnl"]["roi"] is None

    def test_by_market_family_and_confidence_tier_group_correctly(self):
        scored = [
            sr.score_replay_result(_replay_result(replayResultId="r1", marketFamily="KXMLBGAME", originalTier="HIGH"), scored_at="t"),
            sr.score_replay_result(_replay_result(replayResultId="r2", marketFamily="KXMLBTOTAL", originalTier=None), scored_at="t"),
        ]
        agg = sr.aggregate_scored_results(scored)
        assert set(agg["byMarketFamily"]) == {"KXMLBGAME", "KXMLBTOTAL"}
        assert set(agg["byConfidenceTier"]) == {"HIGH", "NONE"}


# ══════════════════════════════════════════════════════════════════════════
# Part 3: score_replay_run() / write_scored_replay_outputs() orchestration
# ══════════════════════════════════════════════════════════════════════════

class TestScoreReplayRunOrchestration:

    def test_nonexistent_replay_run_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        scored_run, scored_results = sr.score_replay_run("no-such-run")
        assert scored_run is None
        assert scored_results == []

    def test_non_completed_replay_run_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run = _replay_run(runStatus=replay.RUN_STATUS_REJECTED_INELIGIBLE)
        _write_replay_run_and_results(run, [])
        scored_run, scored_results = sr.score_replay_run(run["replayRunId"])
        assert scored_run is None
        assert scored_results == []

    def test_never_modifies_original_replay_run_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run = _replay_run()
        results = [_replay_result()]
        _write_replay_run_and_results(run, results)

        run_path = os.path.join(replay.replay_run_dir(run["replayRunId"]), "replay_run.json")
        results_path = os.path.join(replay.replay_run_dir(run["replayRunId"]), "replay_results.jsonl")
        run_bytes_before = open(run_path, "rb").read()
        results_bytes_before = open(results_path, "rb").read()

        scored_run, scored_results = sr.score_replay_run(run["replayRunId"])
        write_result = sr.write_scored_replay_outputs(scored_run, scored_results)
        assert write_result["outcome"] == "created"

        assert open(run_path, "rb").read() == run_bytes_before
        assert open(results_path, "rb").read() == results_bytes_before

    def test_scored_run_and_results_written_to_separate_tree(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run = _replay_run()
        _write_replay_run_and_results(run, [_replay_result()])
        scored_run, scored_results = sr.score_replay_run(run["replayRunId"])
        result = sr.write_scored_replay_outputs(scored_run, scored_results)
        assert result["path"].startswith(sr.SCORED_REPLAY_RUNS_ROOT)
        assert not result["path"].startswith(replay.REPLAY_RUNS_ROOT)

    def test_reload_scored_run_and_results_matches_what_was_written(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run = _replay_run()
        _write_replay_run_and_results(run, [_replay_result()])
        scored_run, scored_results = sr.score_replay_run(run["replayRunId"])
        sr.write_scored_replay_outputs(scored_run, scored_results)

        loaded_run = sr.load_scored_replay_run(scored_run["scoredReplayRunId"])
        loaded_results = sr.load_scored_replay_results(scored_run["scoredReplayRunId"])
        assert loaded_run == scored_run
        assert loaded_results == scored_results

    def test_scored_run_id_deterministic_across_reruns(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run = _replay_run()
        _write_replay_run_and_results(run, [_replay_result()])
        first, _ = sr.score_replay_run(run["replayRunId"], scored_at="2026-08-01T00:00:00Z")
        second, _ = sr.score_replay_run(run["replayRunId"], scored_at="2026-08-02T00:00:00Z")
        assert first["scoredReplayRunId"] == second["scoredReplayRunId"]

    def test_rerun_with_unchanged_inputs_is_a_true_noop(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run = _replay_run()
        _write_replay_run_and_results(run, [_replay_result()])

        scored_run_1, scored_results_1 = sr.score_replay_run(run["replayRunId"], scored_at="2026-08-01T00:00:00Z")
        first_write = sr.write_scored_replay_outputs(scored_run_1, scored_results_1)
        assert first_write["outcome"] == "created"

        scored_run_2, scored_results_2 = sr.score_replay_run(run["replayRunId"], scored_at="2026-08-02T00:00:00Z")
        second_write = sr.write_scored_replay_outputs(scored_run_2, scored_results_2)
        assert second_write["outcome"] == "noop_unchanged"

        on_disk = sr.load_scored_replay_run(scored_run_1["scoredReplayRunId"])
        assert on_disk["scoredAt"] == "2026-08-01T00:00:00Z", "a true no-op must not overwrite the original scoring timestamp"

    def test_corrected_settlement_updates_scored_output_in_place(self, tmp_path, monkeypatch):
        """Requirement 8: a later corrected settlement may update the
        scored output but never the original replay."""
        monkeypatch.chdir(tmp_path)
        run = _replay_run()
        _write_replay_run_and_results(run, [_replay_result()])

        scored_run_1, scored_results_1 = sr.score_replay_run(run["replayRunId"], scored_at="2026-08-01T00:00:00Z")
        sr.write_scored_replay_outputs(scored_run_1, scored_results_1)

        # Simulate a corrected settlement flipping the outcome.
        corrected_results = [dict(scored_results_1[0])]
        corrected_results[0]["objectiveOutcome"] = {"settlementStatus": "MARKET_SETTLED", "result": "NO", "reason": None}
        corrected_run = dict(scored_run_1)
        corrected_run["summary"] = sr.aggregate_scored_results(corrected_results)
        corrected_run["contentHash"] = sr.compute_scored_run_content_hash(corrected_run, corrected_results)

        write_result = sr.write_scored_replay_outputs(corrected_run, corrected_results)
        assert write_result["outcome"] == "updated"
        assert corrected_run["scoredReplayRunId"] == scored_run_1["scoredReplayRunId"], (
            "identity must stay stable across a legitimate content correction"
        )

        on_disk = sr.load_scored_replay_run(scored_run_1["scoredReplayRunId"])
        assert on_disk["contentHash"] == corrected_run["contentHash"]

        # And the ORIGINAL replay run must still be untouched by any of this.
        run_path = os.path.join(replay.replay_run_dir(run["replayRunId"]), "replay_run.json")
        with open(run_path) as f:
            assert json.load(f) == run

    def test_model_evaluation_and_recommendation_ids_looked_up_when_present(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run = _replay_run(snapshotDate="2026-07-31")
        result = _replay_result(marketTicker="TICK-1", selection="ML_Away")
        _write_replay_run_and_results(run, [result])

        _write_jsonl(storage.partition_path("model_evaluations", "2026-07-31"), [
            {"modelEvaluationId": "me-real-1", "marketTicker": "TICK-1", "selection": "ML_Away"},
        ])
        _write_jsonl(storage.partition_path("recommendations", "2026-07-31"), [
            {"recommendationId": "rec-real-1", "marketTicker": "TICK-1", "marketName": "ML_Away"},
        ])

        scored_run, scored_results = sr.score_replay_run(run["replayRunId"])
        assert scored_results[0]["modelEvaluationId"] == "me-real-1"
        assert scored_results[0]["recommendationId"] == "rec-real-1"

    def test_ids_stay_null_when_no_ingested_record_exists(self, tmp_path, monkeypatch):
        """Requirement 6: never reconstruct a missing ModelEvaluation/
        Recommendation id from a hash -- only a genuine ingested record
        counts as 'available'."""
        monkeypatch.chdir(tmp_path)
        run = _replay_run(snapshotDate="2026-07-31")
        _write_replay_run_and_results(run, [_replay_result(marketTicker="TICK-1", selection="ML_Away")])

        scored_run, scored_results = sr.score_replay_run(run["replayRunId"])
        assert scored_results[0]["modelEvaluationId"] is None
        assert scored_results[0]["recommendationId"] is None

    def test_wager_linkage_unavailable_when_no_postgame_snapshot_exists(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run = _replay_run()
        _write_replay_run_and_results(run, [_replay_result(originalRecommendationStatus="Accepted")])

        scored_run, scored_results = sr.score_replay_run(run["replayRunId"])
        assert any("WAGER_LINKAGE_UNAVAILABLE" in reason for reason in scored_run["limitationReasons"])
        assert scored_run["wagerLinkageAvailable"] is False
        # Objective outcome/CLV are still scored from the ReplayResult's
        # own embedded linkage -- only the bet join degrades.
        assert scored_results[0]["objectiveOutcome"]["settlementStatus"] == "MARKET_SETTLED"
        assert scored_results[0]["wager"]["evaluationStage"] == sr.RECOMMENDED_NO_CONFIRMED_BET

    def test_wager_linkage_never_depends_on_the_pregame_manifest_file_existing_on_disk(self, tmp_path, monkeypatch):
        """
        Durability requirement: scoring must succeed using ONLY the
        ReplayRun's own already-durable snapshotId/snapshotDate fields --
        never by re-locating the full PRE_GAME_DECISION manifest on
        disk. No data/edgelab/snapshots/ tree exists anywhere in this
        sandbox at all; _linked_settlement_and_clv is monkeypatched to
        prove it was called with a minimal {snapshotId, snapshotDate}
        dict, not a full manifest object.
        """
        monkeypatch.chdir(tmp_path)
        run = _replay_run(snapshotId="snap-durable", snapshotDate="2026-07-31")
        result = _replay_result(marketTicker="TICK-1")
        _write_replay_run_and_results(run, [result])

        _write_jsonl(storage.singleton_path("bets", "bets.jsonl"), [
            {"betId": "bet-real-1", "result": "WIN", "stake": 20.0, "netProfitLoss": 15.0, "recordStatus": "ACTIVE"},
        ])

        seen_manifests = []

        def _fake_linked(manifest):
            seen_manifests.append(manifest)
            return (
                [{"marketTicker": "TICK-1", "settlementStatus": "SETTLED", "result": "YES", "betId": "bet-real-1"}],
                [],
                None,
            )
        monkeypatch.setattr(sr.replay_engine, "_linked_settlement_and_clv", _fake_linked)

        assert not os.path.isdir(os.path.join("data", "edgelab", "snapshots"))
        scored_run, scored_results = sr.score_replay_run(run["replayRunId"])

        assert seen_manifests == [{"snapshotId": "snap-durable", "snapshotDate": "2026-07-31"}]
        assert scored_results[0]["wager"]["evaluationStage"] == sr.CONFIRMED_BET
        assert scored_results[0]["wager"]["betId"] == "bet-real-1"
        assert scored_results[0]["wager"]["netProfitLoss"] == 15.0
        assert scored_run["wagerLinkageAvailable"] is True
        assert not any("WAGER_LINKAGE_UNAVAILABLE" in reason for reason in scored_run["limitationReasons"])

    def test_scored_run_validates_against_schema(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run = _replay_run()
        _write_replay_run_and_results(run, [_replay_result()])
        scored_run, scored_results = sr.score_replay_run(run["replayRunId"])
        assert edgelab_schema.validate_record("scored_replay_run", scored_run) == []
        for result in scored_results:
            assert edgelab_schema.validate_record("scored_replay_result", result) == []


# ══════════════════════════════════════════════════════════════════════════
# Part 4: assess_ingestion_readiness() and its effect on scoring
# ══════════════════════════════════════════════════════════════════════════

class TestIngestionReadiness:

    def test_nothing_ingested_reports_all_three_reasons(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        readiness = sr.assess_ingestion_readiness("2026-07-31")
        assert readiness == {
            "recommendationsIngested": False, "modelEvaluationsIngested": False,
            "settlementsIngested": False,
            "reasons": [
                "RECOMMENDATIONS_NOT_YET_INGESTED_FOR_DATE",
                "MODEL_EVALUATIONS_NOT_YET_INGESTED_FOR_DATE",
                "SETTLEMENTS_NOT_YET_INGESTED_FOR_DATE",
            ],
        }

    def test_fully_ingested_reports_no_reasons(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_jsonl(storage.partition_path("recommendations", "2026-07-31"), [])
        _write_jsonl(storage.partition_path("model_evaluations", "2026-07-31"), [])
        _write_jsonl(storage.partition_path("settlements", "2026-07-31"), [])
        readiness = sr.assess_ingestion_readiness("2026-07-31")
        assert readiness["recommendationsIngested"] is True
        assert readiness["modelEvaluationsIngested"] is True
        assert readiness["settlementsIngested"] is True
        assert readiness["reasons"] == []

    def test_partial_ingestion_reports_only_the_missing_ones(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_jsonl(storage.partition_path("recommendations", "2026-07-31"), [])
        readiness = sr.assess_ingestion_readiness("2026-07-31")
        assert readiness["recommendationsIngested"] is True
        assert readiness["settlementsIngested"] is False
        assert "SETTLEMENTS_NOT_YET_INGESTED_FOR_DATE" in readiness["reasons"]
        assert "RECOMMENDATIONS_NOT_YET_INGESTED_FOR_DATE" not in readiness["reasons"]

    def test_score_replay_run_never_refuses_when_ingestion_incomplete(self, tmp_path, monkeypatch):
        """Requirement 2: an incomplete-ingestion date must still be
        scoreable (idempotent rerun fills it in later) -- never a hard
        skip that produces nothing at all."""
        monkeypatch.chdir(tmp_path)
        run = _replay_run(snapshotDate="2026-07-31")
        _write_replay_run_and_results(run, [_replay_result()])

        scored_run, scored_results = sr.score_replay_run(run["replayRunId"])
        assert scored_run is not None
        assert len(scored_results) == 1
        assert scored_run["ingestionReadiness"]["recommendationsIngested"] is False
        assert "RECOMMENDATIONS_NOT_YET_INGESTED_FOR_DATE" in scored_run["limitationReasons"]

    def test_rerun_after_ingestion_completes_updates_scored_output(self, tmp_path, monkeypatch):
        """The idempotent-rerun path (write_scored_replay_outputs) picks
        up newly-ingested data automatically -- a later rerun against the
        SAME replay run, once recommendations/model_evaluations/
        settlements exist, produces different content and is written as
        an update, never silently discarded."""
        monkeypatch.chdir(tmp_path)
        run = _replay_run(snapshotDate="2026-07-31")
        result = _replay_result(marketTicker="TICK-1", selection="ML_Away")
        _write_replay_run_and_results(run, [result])

        first_scored_run, first_results = sr.score_replay_run(run["replayRunId"], scored_at="t1")
        first_write = sr.write_scored_replay_outputs(first_scored_run, first_results)
        assert first_write["outcome"] == "created"
        assert first_results[0]["modelEvaluationId"] is None

        _write_jsonl(storage.partition_path("model_evaluations", "2026-07-31"), [
            {"modelEvaluationId": "me-late-1", "marketTicker": "TICK-1", "selection": "ML_Away"},
        ])
        _write_jsonl(storage.partition_path("recommendations", "2026-07-31"), [])
        _write_jsonl(storage.partition_path("settlements", "2026-07-31"), [])

        second_scored_run, second_results = sr.score_replay_run(run["replayRunId"], scored_at="t2")
        second_write = sr.write_scored_replay_outputs(second_scored_run, second_results)
        assert second_write["outcome"] == "updated"
        assert second_results[0]["modelEvaluationId"] == "me-late-1"
        assert "MODEL_EVALUATIONS_NOT_YET_INGESTED_FOR_DATE" not in second_scored_run["limitationReasons"]

        on_disk = sr.load_scored_replay_run(first_scored_run["scoredReplayRunId"])
        assert on_disk["scoredReplayRunId"] == first_scored_run["scoredReplayRunId"]
        loaded_results = sr.load_scored_replay_results(first_scored_run["scoredReplayRunId"])
        assert loaded_results[0]["modelEvaluationId"] == "me-late-1"


# ══════════════════════════════════════════════════════════════════════════
# Part 5: linkageCoverage / missingCoverageReasons in aggregate_scored_results
# ══════════════════════════════════════════════════════════════════════════

class TestLinkageCoverageAggregate:

    def test_linkage_coverage_counts_and_rates(self):
        scored = [
            sr.score_replay_result(_replay_result(replayResultId="r1"), model_evaluation_id="me1", recommendation_id="rec1", scored_at="t"),
            sr.score_replay_result(_replay_result(replayResultId="r2"), scored_at="t"),
        ]
        agg = sr.aggregate_scored_results(scored)
        lc = agg["linkageCoverage"]
        assert lc["modelEvaluationLinkedCount"] == 1
        assert lc["modelEvaluationLinkedRate"] == 0.5
        assert lc["recommendationLinkedCount"] == 1
        assert lc["recommendationLinkedRate"] == 0.5

    def test_wager_linkage_rate_among_recommended(self):
        confirmed_bet = {"betId": "b1", "result": "WIN", "stake": 10.0, "netProfitLoss": 5.0, "recordStatus": "ACTIVE"}
        scored = [
            sr.score_replay_result(_replay_result(replayResultId="r1", originalRecommendationStatus="Accepted"), bet_record=confirmed_bet, scored_at="t"),
            sr.score_replay_result(_replay_result(replayResultId="r2", originalRecommendationStatus="Accepted"), scored_at="t"),
        ]
        agg = sr.aggregate_scored_results(scored)
        lc = agg["linkageCoverage"]
        assert lc["confirmedBetCount"] == 1
        assert lc["recommendedNoConfirmedBetCount"] == 1
        assert lc["wagerLinkageRateAmongRecommended"] == 0.5

    def test_wager_linkage_rate_is_none_when_nothing_recommended(self):
        scored = [sr.score_replay_result(_replay_result(replayResultId="r1", originalRecommendationStatus="Rejected"), scored_at="t")]
        agg = sr.aggregate_scored_results(scored)
        assert agg["linkageCoverage"]["wagerLinkageRateAmongRecommended"] is None

    def test_missing_coverage_reasons_tallied(self):
        scored = [
            sr.score_replay_result(_replay_result(replayResultId="r1",
                settlementLinkage={"status": "UNRESOLVED", "result": None, "reason": "NO_SETTLEMENT_RECORD_FOR_THIS_MARKET"}), scored_at="t"),
            sr.score_replay_result(_replay_result(replayResultId="r2",
                settlementLinkage={"status": "UNRESOLVED", "result": None, "reason": "NO_SETTLEMENT_RECORD_FOR_THIS_MARKET"}), scored_at="t"),
            sr.score_replay_result(_replay_result(replayResultId="r3",
                clvLinkage={"status": "UNRESOLVED", "clvValue": None, "reason": "NO_CLV_QUOTE_FOR_THIS_MARKET"}), scored_at="t"),
        ]
        agg = sr.aggregate_scored_results(scored)
        assert agg["missingCoverageReasons"]["settlementUnresolvedReasons"] == {"NO_SETTLEMENT_RECORD_FOR_THIS_MARKET": 2}
        assert agg["missingCoverageReasons"]["clvUnavailableReasons"]["NO_CLV_QUOTE_FOR_THIS_MARKET"] == 1


# ══════════════════════════════════════════════════════════════════════════
# Part 6: date-level report (requirement 4)
# ══════════════════════════════════════════════════════════════════════════

class TestDateReport:

    def test_build_date_report_none_for_none_run(self):
        assert sr.build_date_report(None, []) is None

    def test_build_date_report_none_for_empty_results(self):
        scored_run = {"summary": None, "snapshotDate": "2026-07-31"}
        assert sr.build_date_report(scored_run, []) is None

    def test_build_date_report_surfaces_every_requirement_4_field(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run = _replay_run(snapshotDate="2026-07-31")
        result = _replay_result(marketTicker="TICK-1", selection="ML_Away")
        _write_replay_run_and_results(run, [result])
        scored_run, scored_results = sr.score_replay_run(run["replayRunId"])

        report = sr.build_date_report(scored_run, scored_results)
        assert report["date"] == "2026-07-31"
        assert report["predictions"]["total"] == 1
        assert report["predictions"]["scoreable"] == 1
        assert "settlementCoverage" in report
        assert "recommendationLinkageCoverage" in report
        assert "modelEvaluationLinkageCoverage" in report
        assert "wagerLinkageCoverage" in report
        assert "clvCoverage" in report
        assert "brier" in report
        assert "calibrationBuckets" in report
        assert "missingCoverageReasons" in report
        assert "limitationReasons" in report
        assert "ingestionReadiness" in report

    def test_write_scored_replay_date_report_writes_expected_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run = _replay_run(snapshotDate="2026-07-31")
        _write_replay_run_and_results(run, [_replay_result()])
        scored_run, scored_results = sr.score_replay_run(run["replayRunId"])
        report = sr.build_date_report(scored_run, scored_results)

        path = sr.write_scored_replay_date_report(report)
        assert path == sr.date_report_path("2026-07-31")
        assert os.path.exists(path)
        with open(path) as f:
            on_disk = json.load(f)
        assert on_disk == report

    def test_write_scored_replay_date_report_noop_for_none(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert sr.write_scored_replay_date_report(None) is None

    def test_date_report_regeneration_is_deterministic(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run = _replay_run(snapshotDate="2026-07-31")
        _write_replay_run_and_results(run, [_replay_result()])
        scored_run, scored_results = sr.score_replay_run(run["replayRunId"], scored_at="fixed-t")
        report1 = sr.build_date_report(scored_run, scored_results)
        report2 = sr.build_date_report(scored_run, scored_results)
        assert report1 == report2
