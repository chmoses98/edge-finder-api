#!/usr/bin/env python3
"""
tests/edgelab/test_daily_health_check_target_date.py
================================================================
End-to-end coverage for scripts/edgelab/daily_health_check.py's TARGET
DATE handling (Heartbeat False-Failure Incident, 2026-08-27) -- the
half of the fix that lives in the health check itself:

  - the target date arrives explicitly (--date, cross-checked against
    the resolver's own --resolution-file) and is validated strictly;
  - the health artifact is filed under the TARGET production date while
    `checkedAt` keeps the real execution timestamp, so a delayed run can
    never overwrite a later date's artifact;
  - `capturedAt` fields inside artifacts are UTC instants and are
    compared against the Eastern production date they belong to, not by
    string prefix (a 2026-08-27T02:18Z capture belongs to production
    date 2026-08-26 -- see lib/edgelab/production_date.py);
  - and, above all, that NONE of this made the watchdog softer: every
    genuine missing artifact for the target date still hard-fails.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import daily_health, mlb_schedule, production_date, storage
from scripts.edgelab.daily_health_check import gather_inputs, main

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The incident's own numbers (GitHub run 33041444748).
TARGET_DATE = "2026-08-26"
SETTLEMENT_DATE = "2026-08-25"
DELAYED_START = "2026-08-27T05:06:16Z"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(mlb_schedule, "fetch_schedule", lambda date, timeout=15: {"dates": [{"games": [{"gamePk": i} for i in range(15)]}]})


def _resolution_file(tmp_path, target_date=TARGET_DATE, anchor=DELAYED_START):
    record = production_date.resolve_target_date(
        event_name="schedule", schedule_expression="45 23 * * *", anchor=anchor,
        anchor_source="github_rest_run_created_at",
    )
    assert record["targetDate"] == target_date
    path = tmp_path / "heartbeat_target_date.json"
    path.write_text(json.dumps(record))
    return str(path)


def _write_healthy_target_day(date=TARGET_DATE, settlement_date=SETTLEMENT_DATE, *,
                              provenance_captured_at=None, snapshot_captured_at=None):
    """The minimum real artifact tree that makes `date` genuinely healthy."""
    provenance_captured_at = provenance_captured_at or f"{date}T20:00:00Z"
    snapshot_captured_at = snapshot_captured_at or f"{date}T20:00:05Z"

    storage.write_all_records(storage.partition_path("observations", date),
                              [{"marketTicker": f"T{i}"} for i in range(50)])
    storage.write_all_records(storage.partition_path("model_evaluations", date),
                              [{"modelEvaluationId": f"m{i}", "source": "prospective_snapshot"} for i in range(20)])
    os.makedirs(os.path.join("data", "pipeline", date), exist_ok=True)
    with open(os.path.join("data", "pipeline", date, "recommendations.json"), "w") as f:
        json.dump({"meta": {"slateDate": date}, "data": {"games": [{"gameId": i} for i in range(15)]}}, f)
    with open(os.path.join("data", "pipeline", date, "provenance.json"), "w") as f:
        json.dump({"meta": {"slateDate": date},
                   "data": {"capturedAt": provenance_captured_at, "workflowRunId": "33032789348"}}, f)

    manifest_dir = os.path.join("data", "edgelab", "snapshots", date, "pre_game_decision",
                                snapshot_captured_at.replace("-", "").replace(":", ""))
    os.makedirs(manifest_dir, exist_ok=True)
    with open(os.path.join(manifest_dir, "manifest.json"), "w") as f:
        json.dump({"snapshotStage": "PRE_GAME_DECISION", "snapshotDate": date,
                   "capturedAt": snapshot_captured_at, "captureMode": "LIVE_CAPTURE",
                   "completenessStatus": "PARTIAL_REPLAY"}, f)

    # Settlement-date side (edgelab-postgame.yml's own output).
    storage.write_all_records(storage.partition_path("observations", settlement_date),
                              [{"marketTicker": f"S{i}"} for i in range(40)])
    storage.write_all_records(storage.partition_path("settlements", settlement_date),
                              [{"settlementId": f"s{i}"} for i in range(40)])
    storage.write_all_records(storage.partition_path("model_evaluations", settlement_date),
                              [{"modelEvaluationId": f"f{i}", "source": "market_universe_extension"} for i in range(30)])


class TestDelayedRunChecksTheIntendedDate:
    """The incident, replayed end to end."""

    def test_delayed_run_writes_the_target_dates_artifact_not_the_start_dates(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_healthy_target_day()
        exit_code = main(["--date", TARGET_DATE, "--resolution-file", _resolution_file(tmp_path)])
        assert exit_code == 0
        assert os.path.exists(os.path.join("data", "edgelab", "health", f"{TARGET_DATE}.json"))
        assert not os.path.exists(os.path.join("data", "edgelab", "health", "2026-08-27.json")), (
            "a run delayed into 2026-08-27 must never file its verdict under 2026-08-27 -- "
            "that artifact belongs to a production date whose own cycle has not run yet"
        )

    def test_the_record_reports_the_target_date_and_target_minus_one_settlement_date(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_healthy_target_day()
        main(["--date", TARGET_DATE, "--resolution-file", _resolution_file(tmp_path)])
        with open(os.path.join("data", "edgelab", "health", f"{TARGET_DATE}.json")) as f:
            record = json.load(f)
        assert record["date"] == TARGET_DATE
        assert record["settlementDateChecked"] == SETTLEMENT_DATE
        assert record["healthStatus"] == daily_health.HEALTH_STATUS_HEALTHY

    def test_checked_at_stays_the_real_execution_timestamp_distinct_from_the_target_date(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_healthy_target_day()
        main(["--date", TARGET_DATE, "--resolution-file", _resolution_file(tmp_path)])
        with open(os.path.join("data", "edgelab", "health", f"{TARGET_DATE}.json")) as f:
            record = json.load(f)
        # Two distinct concepts, both preserved: WHAT was validated (`date`)
        # and WHEN the validation actually ran (`checkedAt`).
        assert production_date.et_date_for_timestamp(record["checkedAt"]) is not None
        assert record["date"] == TARGET_DATE
        assert record["dateResolution"]["scheduledCheckpointUtc"] == "2026-08-26T23:45:00Z"
        assert record["dateResolution"]["anchorTimestamp"] == DELAYED_START
        assert record["dateResolution"]["delayedRun"] is True

    def test_a_delayed_run_does_not_overwrite_an_unrelated_later_date_artifact(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_healthy_target_day()
        os.makedirs(os.path.join("data", "edgelab", "health"), exist_ok=True)
        later = os.path.join("data", "edgelab", "health", "2026-08-27.json")
        with open(later, "w") as f:
            json.dump({"date": "2026-08-27", "healthStatus": "HEALTHY", "sentinel": True}, f)
        main(["--date", TARGET_DATE, "--resolution-file", _resolution_file(tmp_path)])
        with open(later) as f:
            assert json.load(f)["sentinel"] is True


class TestTargetDateIsValidatedStrictly:
    """Requirement 15."""

    @pytest.mark.parametrize("bad", ["2026-8-1", "08/26/2026", "2026-08-26T00:00:00Z", "2026-02-30", "not-a-date"])
    def test_a_malformed_date_argument_exits_non_zero_and_writes_nothing(self, tmp_path, monkeypatch, bad):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["--date", bad])
        assert exc.value.code != 0
        assert not os.path.isdir(os.path.join("data", "edgelab", "health"))

    def test_a_date_contradicting_the_resolvers_own_target_is_refused(self, tmp_path, monkeypatch):
        """The artifact must never be filed under a date the resolver did not choose."""
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["--date", "2026-08-27", "--resolution-file", _resolution_file(tmp_path)])
        assert exc.value.code != 0

    def test_the_resolution_record_is_embedded_verbatim_for_audit(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_healthy_target_day()
        path = _resolution_file(tmp_path)
        main(["--date", TARGET_DATE, "--resolution-file", path])
        with open(os.path.join("data", "edgelab", "health", f"{TARGET_DATE}.json")) as f:
            record = json.load(f)
        with open(path) as f:
            assert record["dateResolution"] == json.load(f)


class TestCapturedAtIsAnInstantNotADateString:
    """The second, distinct half of the same UTC-vs-Eastern confusion.

    data/edgelab/snapshots/<date>/ and data/pipeline/<date>/ are keyed by
    the EASTERN production date; the capturedAt values inside them are
    UTC instants. 2026-08-26's own slate was captured at
    2026-08-27T02:18:17Z (22:18 ET) -- genuinely same-day, but reported
    as INVALID_PROVENANCE / a late snapshot recovery by a string-prefix
    comparison.
    """

    def test_a_2218_et_capture_written_after_utc_midnight_is_valid_provenance(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_healthy_target_day(provenance_captured_at="2026-08-27T02:18:17Z",
                                  snapshot_captured_at="2026-08-27T02:18:47Z")
        inputs = gather_inputs(TARGET_DATE, SETTLEMENT_DATE)
        assert inputs["recommendationsProvenanceValid"] is True
        assert inputs["preGameDecisionSnapshotIsSameDayCapture"] is True

    def test_a_late_recovery_days_afterwards_is_still_flagged(self, tmp_path, monkeypatch):
        """Strictness preserved: check_snapshot_capture.py's own late recovery pattern
        (a 2026-08-11 snapshot rebuilt on 2026-08-16) must still not count as same-day."""
        monkeypatch.chdir(tmp_path)
        _write_healthy_target_day(snapshot_captured_at="2026-08-31T17:00:00Z")
        inputs = gather_inputs(TARGET_DATE, SETTLEMENT_DATE)
        assert inputs["preGameDecisionSnapshotIsSameDayCapture"] is False
        record = daily_health.compute_daily_health(inputs, "2026-08-27T05:06:37Z")
        assert record["healthStatus"] == daily_health.HEALTH_STATUS_UNHEALTHY

    def test_provenance_from_a_different_production_day_is_still_invalid(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_healthy_target_day(provenance_captured_at="2026-08-24T20:00:00Z")
        assert gather_inputs(TARGET_DATE, SETTLEMENT_DATE)["recommendationsProvenanceValid"] is False

    def test_an_unparseable_captured_at_is_never_silently_accepted(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_healthy_target_day(provenance_captured_at="whenever", snapshot_captured_at="2026-08-27T02:18:47Z")
        assert gather_inputs(TARGET_DATE, SETTLEMENT_DATE)["recommendationsProvenanceValid"] is False


class TestStrictnessIsUnchanged:
    """Requirements 10/11/12: a genuinely missed production day still fails loudly.

    Each of these starts from the fully healthy target-day tree above and
    removes exactly one real artifact.
    """

    def _run(self, tmp_path):
        return main(["--date", TARGET_DATE, "--resolution-file", _resolution_file(tmp_path)])

    def _reasons(self):
        with open(os.path.join("data", "edgelab", "health", f"{TARGET_DATE}.json")) as f:
            return json.load(f)["reasons"]

    def test_missing_recommendations_for_the_target_date_still_hard_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_healthy_target_day()
        os.remove(os.path.join("data", "pipeline", TARGET_DATE, "recommendations.json"))
        assert self._run(tmp_path) == 1
        assert any(r.startswith(daily_health.REASON_MISSING_RECOMMENDATIONS) for r in self._reasons())

    def test_stale_wrong_date_recommendations_still_hard_fail(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_healthy_target_day()
        path = os.path.join("data", "pipeline", TARGET_DATE, "recommendations.json")
        with open(path, "w") as f:
            json.dump({"meta": {"slateDate": "2026-08-25"}, "data": {"games": [{"gameId": 1}]}}, f)
        assert self._run(tmp_path) == 1
        assert any(r.startswith(daily_health.REASON_STALE_ARTIFACT) for r in self._reasons())

    def test_missing_pre_game_decision_snapshot_still_hard_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_healthy_target_day()
        import shutil
        shutil.rmtree(os.path.join("data", "edgelab", "snapshots", TARGET_DATE))
        assert self._run(tmp_path) == 1
        assert any(r.startswith(daily_health.REASON_MISSING_PRE_GAME_DECISION_SNAPSHOT) for r in self._reasons())

    def test_missing_settlements_for_target_minus_one_still_hard_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_healthy_target_day()
        os.remove(storage.partition_path("settlements", SETTLEMENT_DATE))
        assert self._run(tmp_path) == 1
        assert any(r.startswith(daily_health.REASON_MISSING_SETTLEMENTS) for r in self._reasons())

    def test_missing_recommendation_sync_extension_for_target_minus_one_still_hard_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_healthy_target_day()
        storage.write_all_records(storage.partition_path("model_evaluations", SETTLEMENT_DATE),
                                  [{"modelEvaluationId": "p1", "source": "prospective_snapshot"}])
        assert self._run(tmp_path) == 1
        assert any("RECOMMENDATION_SYNC" in r for r in self._reasons())

    def test_zero_observations_with_scheduled_games_still_hard_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_healthy_target_day()
        os.remove(storage.partition_path("observations", TARGET_DATE))
        assert self._run(tmp_path) == 1
        assert any(r.startswith(daily_health.REASON_ZERO_MARKET_OBSERVATIONS) for r in self._reasons())

    def test_missing_model_evaluations_for_the_target_date_still_hard_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_healthy_target_day()
        os.remove(storage.partition_path("model_evaluations", TARGET_DATE))
        assert self._run(tmp_path) == 1
        assert any(r.startswith(daily_health.REASON_MISSING_MODEL_EVALUATIONS) for r in self._reasons())

    def test_a_completely_missed_production_day_is_unhealthy_with_every_reason(self, tmp_path, monkeypatch):
        """The genuine outage this watchdog exists for -- unchanged by the date fix."""
        monkeypatch.chdir(tmp_path)
        assert self._run(tmp_path) == 1
        reasons = self._reasons()
        assert len(reasons) >= 4


class TestLegitimateNoSlateDay:
    """Requirement 13: a real off-day is not a false failure."""

    def test_no_scheduled_games_is_never_unhealthy_for_missing_mlb_artifacts(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(mlb_schedule, "fetch_schedule", lambda date, timeout=15: {"dates": []})
        exit_code = main(["--date", TARGET_DATE, "--resolution-file", _resolution_file(tmp_path)])
        assert exit_code == 0
        with open(os.path.join("data", "edgelab", "health", f"{TARGET_DATE}.json")) as f:
            assert json.load(f)["healthStatus"] == daily_health.HEALTH_STATUS_NO_MLB_GAMES

    def test_an_unavailable_live_schedule_check_still_fails_toward_checking_too_much(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(mlb_schedule, "fetch_schedule", lambda date, timeout=15: None)
        assert main(["--date", TARGET_DATE, "--resolution-file", _resolution_file(tmp_path)]) == 1


class TestLocalFallbackAndBackwardCompatibility:
    def test_no_date_and_no_resolution_falls_back_to_the_eastern_production_date(self, tmp_path, monkeypatch):
        """Documented local/no-context convention -- and genuinely ET now, not UTC
        wearing an America/New_York docstring."""
        monkeypatch.chdir(tmp_path)
        expected = production_date.et_today()
        main([])
        assert os.path.exists(os.path.join("data", "edgelab", "health", f"{expected}.json"))

    def test_existing_committed_health_artifacts_remain_readable(self):
        """Requirement 16: schemaVersion 1 artifacts (written before dateResolution
        existed) must still parse and keep every field a consumer relied on."""
        health_dir = os.path.join(ROOT, "data", "edgelab", "health")
        artifacts = sorted(f for f in os.listdir(health_dir) if f.endswith(".json"))
        assert artifacts, "the historical heartbeat artifacts are evidence -- they must not be deleted"
        for name in artifacts:
            with open(os.path.join(health_dir, name)) as f:
                record = json.load(f)
            assert record["date"] == name[: -len(".json")]
            for field in ("schemaVersion", "checkedAt", "healthStatus", "reasons",
                          "settlementDateChecked", "artifactFreshnessStatus"):
                assert field in record, f"{name} lost the pre-existing field {field!r}"
            assert record["healthStatus"] in daily_health.VALID_HEALTH_STATUSES

    def test_date_resolution_is_optional_and_never_changes_a_verdict(self):
        inputs = {
            "date": TARGET_DATE, "gamesScheduledToday": 15, "marketsObservedCount": 5000,
            "recommendationsFileExists": True, "recommendationsIsCurrentDate": True,
            "recommendationsProvenanceValid": True, "recommendationsRowCount": 15,
            "modelEvaluationsFileExists": True, "modelEvaluationsIsCurrentDate": True,
            "modelEvaluationsRowCount": 400, "preGameDecisionSnapshotFileExists": True,
            "preGameDecisionSnapshotIsSameDayCapture": True,
            "preGameDecisionSnapshotCompletenessStatus": "PARTIAL_REPLAY",
            "settlementDateChecked": SETTLEMENT_DATE, "settlementsExpected": True,
            "settlementsFileExists": True, "settlementsRowCount": 5100,
            "fullUniverseExtensionRowCount": 5200,
        }
        without = daily_health.compute_daily_health(inputs, "2026-08-27T05:06:37Z")
        with_resolution = daily_health.compute_daily_health(
            inputs, "2026-08-27T05:06:37Z",
            date_resolution={"targetDate": TARGET_DATE, "triggerType": "schedule"},
        )
        assert without["dateResolution"] is None
        assert with_resolution["dateResolution"]["triggerType"] == "schedule"
        assert {k: v for k, v in without.items() if k != "dateResolution"} == \
               {k: v for k, v in with_resolution.items() if k != "dateResolution"}
