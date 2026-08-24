#!/usr/bin/env python3
"""
tests/edgelab/test_daily_health_check_script.py
================================================================
Integration coverage for scripts/edgelab/daily_health_check.py's real
fact-gathering (gather_inputs) against an actual on-disk fixture tree --
complements tests/edgelab/test_daily_health.py's pure-logic coverage.
Specifically proves the two mistakes already made once each elsewhere in
this repository's history never recur here:
  - the .jsonl/.jsonl.gz compaction-path mistake (Pipeline Health
    Incident, 2026-08-24: a workflow step passed a hardcoded plain
    .jsonl path to git_data_commit.py and silently dropped every write
    to an already-compacted date)
  - PARTIAL_REPLAY/late-recovery snapshots masquerading as real
    same-day prospective coverage.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import daily_health, ids, mlb_schedule, storage
from scripts.edgelab.daily_health_check import gather_inputs, main


def _write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(doc, f)


def _write_pregame_manifest(date, run_key, *, captured_at, completeness_status="PARTIAL_REPLAY"):
    path = os.path.join("data", "edgelab", "snapshots", date, "pre_game_decision", run_key, "manifest.json")
    _write_json(path, {
        "snapshotStage": "PRE_GAME_DECISION",
        "snapshotDate": date,
        "capturedAt": captured_at,
        "captureMode": "LIVE_CAPTURE",
        "completenessStatus": completeness_status,
    })


def _write_observations(date, tickers, *, compressed=False):
    rows = [{"marketTicker": t, "runId": "r1"} for t in tickers]
    path = storage.partition_path("observations", date, compressed=compressed)
    storage.write_all_records(path, rows)


def _write_recommendations(date, *, slate_date, game_count, workflow_run_id="12345"):
    _write_json(
        os.path.join("data", "pipeline", date, "recommendations.json"),
        {"meta": {"slateDate": slate_date}, "data": {"games": [{"gameId": i} for i in range(game_count)]}},
    )
    _write_json(
        os.path.join("data", "pipeline", date, "provenance.json"),
        {"meta": {"slateDate": slate_date}, "data": {"capturedAt": f"{date}T20:00:00Z", "workflowRunId": workflow_run_id}},
    )


class TestSettlementGzipDetection:
    """The exact compaction-path mistake this repo already made once (Pipeline Health Incident,
    2026-08-24) must never recur in the watchdog itself."""

    def test_plain_jsonl_settlement_is_detected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        storage.write_all_records(storage.partition_path("settlements", "2026-08-20"), [{"settlementId": "s1"}, {"settlementId": "s2"}])
        inputs = gather_inputs("2026-08-21", "2026-08-20")
        assert inputs["settlementsFileExists"] is True
        assert inputs["settlementsRowCount"] == 2

    def test_gzip_compacted_settlement_is_detected_identically(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        storage.write_all_records(storage.partition_path("settlements", "2026-08-20", compressed=True), [{"settlementId": "s1"}, {"settlementId": "s2"}, {"settlementId": "s3"}])
        inputs = gather_inputs("2026-08-21", "2026-08-20")
        assert inputs["settlementsFileExists"] is True
        assert inputs["settlementsRowCount"] == 3

    def test_missing_settlement_partition_is_absent_not_a_crash(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        inputs = gather_inputs("2026-08-21", "2026-08-20")
        assert inputs["settlementsFileExists"] is False
        assert inputs["settlementsRowCount"] == 0


class TestFullUniverseExtensionDetectionAcrossCompaction:
    def test_full_universe_rows_detected_in_gzip_compacted_model_evaluations(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rows = [
            {"modelEvaluationId": "m1", "source": "prospective_snapshot"},
            {"modelEvaluationId": "m2", "source": "market_universe_extension"},
            {"modelEvaluationId": "m3", "source": "kalshi_discovery_extension"},
        ]
        storage.write_all_records(storage.partition_path("model_evaluations", "2026-08-19", compressed=True), rows)
        inputs = gather_inputs("2026-08-20", "2026-08-19")
        assert inputs["fullUniverseExtensionRowCount"] == 2  # prospective_snapshot excluded


class TestPreGameDecisionSnapshotSameDayVsLateRecovery:
    def test_same_day_capture_is_detected_as_present(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_pregame_manifest("2026-08-20", "2026-08-20T160000Z", captured_at="2026-08-20T16:00:05Z")
        inputs = gather_inputs("2026-08-20", "2026-08-19")
        assert inputs["preGameDecisionSnapshotFileExists"] is True
        assert inputs["preGameDecisionSnapshotIsSameDayCapture"] is True

    def test_late_recovery_capture_is_flagged_as_not_same_day(self, tmp_path, monkeypatch):
        """A manifest physically stored under 2026-08-11/pre_game_decision/ but captured
        2026-08-16 is exactly scripts/check_snapshot_capture.py's late recovery pattern."""
        monkeypatch.chdir(tmp_path)
        _write_pregame_manifest("2026-08-11", "2026-08-16T170000Z", captured_at="2026-08-16T17:00:00Z")
        inputs = gather_inputs("2026-08-11", "2026-08-10")
        assert inputs["preGameDecisionSnapshotFileExists"] is True
        assert inputs["preGameDecisionSnapshotIsSameDayCapture"] is False

    def test_no_manifest_at_all_is_absent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        inputs = gather_inputs("2026-08-11", "2026-08-10")
        assert inputs["preGameDecisionSnapshotFileExists"] is False


class TestRecommendationsAndProvenance:
    def test_current_date_recommendations_with_valid_provenance(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_recommendations("2026-08-20", slate_date="2026-08-20", game_count=10)
        inputs = gather_inputs("2026-08-20", "2026-08-19")
        assert inputs["recommendationsFileExists"] is True
        assert inputs["recommendationsIsCurrentDate"] is True
        assert inputs["recommendationsProvenanceValid"] is True
        assert inputs["recommendationsRowCount"] == 10

    def test_stale_recommendations_wrong_slate_date_is_detected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_recommendations("2026-08-20", slate_date="2026-08-19", game_count=10)  # yesterday's content, wrong meta date
        inputs = gather_inputs("2026-08-20", "2026-08-19")
        assert inputs["recommendationsFileExists"] is True
        assert inputs["recommendationsIsCurrentDate"] is False

    def test_missing_recommendations_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        inputs = gather_inputs("2026-08-20", "2026-08-19")
        assert inputs["recommendationsFileExists"] is False
        assert inputs["recommendationsRowCount"] == 0


class TestZeroRowsDespiteEligibleMarkets:
    def test_market_observations_counted_as_unique_tickers(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_observations("2026-08-20", ["T1", "T2", "T1"])  # duplicate ticker across snapshots
        inputs = gather_inputs("2026-08-20", "2026-08-19")
        assert inputs["marketsObservedCount"] == 2

    def test_zero_market_observations_is_reported_as_zero_not_missing_partition_crash(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        inputs = gather_inputs("2026-08-20", "2026-08-19")
        assert inputs["marketsObservedCount"] == 0


class TestEndToEndMainExitCode:
    def test_main_writes_health_artifact_and_returns_nonzero_when_unhealthy(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(mlb_schedule, "fetch_schedule", lambda date, timeout=15: {"dates": [{"games": [{"gamePk": 1}, {"gamePk": 2}]}]})
        exit_code = main(["--date", "2026-08-20"])
        assert exit_code == 1
        out_path = os.path.join("data", "edgelab", "health", "2026-08-20.json")
        assert os.path.exists(out_path)
        with open(out_path) as f:
            record = json.load(f)
        assert record["healthStatus"] == daily_health.HEALTH_STATUS_UNHEALTHY
        assert record["date"] == "2026-08-20"
        assert len(record["reasons"]) > 0

    def test_main_returns_zero_on_a_legitimate_no_game_day(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(mlb_schedule, "fetch_schedule", lambda date, timeout=15: {"dates": []})
        exit_code = main(["--date", "2026-08-20"])
        assert exit_code == 0
        with open(os.path.join("data", "edgelab", "health", "2026-08-20.json")) as f:
            record = json.load(f)
        assert record["healthStatus"] == daily_health.HEALTH_STATUS_NO_MLB_GAMES

    def test_main_writes_artifact_even_when_unhealthy_never_swallowed_by_a_crash(self, tmp_path, monkeypatch):
        """Fail-loudly requirement: the health artifact must exist BEFORE the process exits
        non-zero, regardless of what's missing -- never merely a red check with no record."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(mlb_schedule, "fetch_schedule", lambda date, timeout=15: None)  # live check unavailable
        exit_code = main(["--date", "2026-08-20"])
        assert exit_code == 1
        assert os.path.exists(os.path.join("data", "edgelab", "health", "2026-08-20.json"))
