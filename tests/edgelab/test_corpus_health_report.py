#!/usr/bin/env python3
"""
tests/edgelab/test_corpus_health_report.py
==============================================
Corpus-health audit (2026-08-25): focused regression coverage for
scripts/corpus_health_report.py's forward-operational-health policy --
the enforcement-boundary immutability, the hard-fail rule table, the
same-day-pending / acknowledged-legacy-gap exceptions to it, and the
forwardOperationalHealth metric-population fix (the "17 captured / 0
missing" vs. "8 FORWARD_MISSING_SNAPSHOT dates" reporting inconsistency).

Every test runs inside an isolated tmp_path (monkeypatch.chdir), never
against the real repository's data/ tree, and builds fully synthetic
PRE_GAME_DECISION manifests directly (bypassing the full production
pipeline fixture -- lib/edgelab/snapshot.py's own capture logic already
has dedicated coverage in tests/edgelab/test_snapshot.py; this file is
about scripts/corpus_health_report.py's OWN policy layer on top of it).
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from lib.edgelab import snapshot as snap  # noqa: E402

import corpus_health_report as chr_mod  # noqa: E402
import importlib  # noqa: E402


@pytest.fixture(autouse=True)
def _reload_module():
    importlib.reload(chr_mod)
    yield


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f)


def _write_recommendations(date):
    _write(os.path.join("data", "pipeline", date, "recommendations.json"), {"games": []})


def _write_boundary(date, snapshot_id="boundary-snap"):
    _write(chr_mod.ENFORCEMENT_BOUNDARY_PATH, {
        "schemaVersion": "1", "enforcementBoundaryDate": date, "activatedAt": f"{date}T00:00:00Z",
        "activatingSnapshotId": snapshot_id, "note": "test boundary",
    })


def _write_forward_replay_status(date, run_status, outcome=None):
    path = os.path.join("data", "edgelab", "forward_replay_status.json")
    status = {}
    if os.path.exists(path):
        with open(path) as f:
            status = json.load(f)
    status[date] = {
        "date": date, "runStatus": run_status, "outcome": outcome or run_status.lower(),
        "replayRunId": f"test-{date}",
    }
    _write(path, status)


def _write_acknowledged_gap(date, reason="TERMINAL_UNRECOVERABLE_PRODUCTION_GAP", evidence="test evidence"):
    _write(chr_mod.ACKNOWLEDGED_GAPS_PATH, {
        "schemaVersion": "1", "entries": [
            {"date": date, "forwardGateStatus": "FORWARD_MISSING_SNAPSHOT", "reason": reason, "evidence": evidence},
        ],
    })


def _write_pregame_manifest(date, run_key, *, completeness_status, provenance_status="CAPTURED",
                             production_commit_sha="a" * 40, capture_mode=snap.CAPTURE_MODE_LIVE,
                             captured_at=None, skew_detected=False,
                             effective_config_availability=snap.PARTIAL):
    """A minimal, fully synthetic but schema-shaped PRE_GAME_DECISION
    manifest -- every field scripts/corpus_health_report.py's forward
    gate rule table reads is set explicitly and deliberately, rather than
    derived, so each test exercises exactly one rule-table branch."""
    captured_at = captured_at or f"{date}T12:00:00Z"
    commit_sha = production_commit_sha if provenance_status == "CAPTURED" else None
    components = [{
        "componentType": "EFFECTIVE_CONFIG", "sourcePath": None, "snapshotPath": None,
        "storageMode": None, "contentHash": None, "byteSize": None, "rowCount": None,
        "capturedAt": None, "producer": None, "requiredStatus": snap.REQUIRED,
        "availabilityStatus": effective_config_availability, "limitationReason": None,
    }]
    provenance = {
        "status": provenance_status, "commitSha": commit_sha,
        "gitHeadShaAtCapture": None, "workingTreeDirty": False if provenance_status == "CAPTURED" else None,
        "workflowRunId": None, "workflowRunAttempt": None, "ref": None, "refName": None,
        "repository": None, "capturedAt": captured_at,
        "reason": None if provenance_status == "CAPTURED" else "TEST_REASON",
    }
    manifest = {
        "schemaVersion": snap.SCHEMA_VERSION,
        "snapshotId": f"test-{date}-{run_key}",
        "snapshotStage": snap.STAGE_PRE_GAME_DECISION,
        "snapshotDate": date,
        "captureMode": capture_mode,
        "capturedAt": captured_at,
        "productionRunId": run_key,
        "workflowRunId": None,
        "productionCommitSha": commit_sha,
        "productionProvenance": provenance,
        "snapshotWriterCommitSha": "b" * 40,
        "modelVersion": None,
        "pricingVersionsByFamily": {},
        "rulesConfigVersion": "1.0",
        "temporalConsistency": {"skewDetected": skew_detected, "maxSkewHours": 1.0, "referenceTimestamp": run_key, "detail": {}},
        "replayFidelityPotential": snap.LEVEL_1_APPROXIMATE,
        "completenessStatus": completeness_status,
        "validationStatus": "valid",
        "components": components,
        "missingComponents": [],
        "limitationReasons": [],
        "linkedSnapshotIds": [],
        "provenance": {"sourceSystem": "test", "sourceFile": None, "sourceKey": None, "capturedAt": captured_at, "ingestedAt": captured_at},
    }
    manifest["manifestHash"] = snap.compute_manifest_hash(manifest)
    path = snap.manifest_path(snap.STAGE_PRE_GAME_DECISION, date, run_key=run_key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(manifest, f)
    return manifest


def _healthy_manifest_and_replay(date, run_key=None):
    run_key = run_key or f"{date}T12:00:00Z"
    _write_recommendations(date)
    _write_pregame_manifest(date, run_key, completeness_status=snap.PARTIAL_REPLAY)
    _write_forward_replay_status(date, "COMPLETED", outcome="completed")
    _write(os.path.join("data", "edgelab", "snapshots", date, "closing_line", "manifest.json"), {"stub": True})
    _write(os.path.join("data", "edgelab", "snapshots", date, "post_game_settlement", "manifest.json"), {"stub": True})


class TestEnforcementBoundaryImmutability:
    def test_persisted_boundary_is_never_recomputed_even_when_an_earlier_qualifying_date_appears(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-02-01")
        # An earlier-dated, ALSO-qualifying manifest shows up later (e.g. a
        # backfill or a data-repair commit) -- must NOT move the boundary.
        _write_recommendations("2026-01-15")
        _write_pregame_manifest("2026-01-15", "2026-01-15T12:00:00Z", completeness_status=snap.PARTIAL_REPLAY)

        report = chr_mod.build_report(today="2026-02-10")
        assert report["enforcement"]["boundaryDate"] == "2026-02-01"
        assert report["enforcement"]["status"] == chr_mod.ENFORCEMENT_ACTIVE


class TestHardFailRuleTable:
    def test_new_missing_forward_snapshot_hard_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-02-01")
        _write_recommendations("2026-02-05")  # production run happened; no snapshot ever captured

        report = chr_mod.build_report(today="2026-02-10")  # well after 02-05, not "today"
        rec = next(r for r in report["perDate"] if r["date"] == "2026-02-05")
        assert rec["forwardGateStatus"] == chr_mod.STATUS_FORWARD_MISSING_SNAPSHOT
        assert "2026-02-05" in report["forwardOperationalHealth"]["hardFailDates"]
        assert "2026-02-05" in report["forwardOperationalHealth"]["snapshotsMissing"]
        assert report["exitShouldFail"] is True

    def test_ambiguous_provenance_hard_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-02-01")
        _write_recommendations("2026-02-05")
        _write_pregame_manifest(
            "2026-02-05", "2026-02-05T12:00:00Z",
            completeness_status=snap.PARTIAL_REPLAY, provenance_status="AMBIGUOUS",
        )

        report = chr_mod.build_report(today="2026-02-10")
        rec = next(r for r in report["perDate"] if r["date"] == "2026-02-05")
        assert rec["forwardGateStatus"] == chr_mod.STATUS_FORWARD_PROVENANCE_AMBIGUOUS
        assert report["exitShouldFail"] is True
        assert "2026-02-05" in report["forwardOperationalHealth"]["hardFailDates"]

    def test_replay_failure_hard_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-02-01")
        _write_recommendations("2026-02-05")
        _write_pregame_manifest("2026-02-05", "2026-02-05T12:00:00Z", completeness_status=snap.PARTIAL_REPLAY)
        _write_forward_replay_status("2026-02-05", "REJECTED_INELIGIBLE", outcome="rejected_ineligible")

        report = chr_mod.build_report(today="2026-02-10")
        rec = next(r for r in report["perDate"] if r["date"] == "2026-02-05")
        assert rec["forwardGateStatus"] == chr_mod.STATUS_FORWARD_REPLAY_FAILURE
        assert report["exitShouldFail"] is True

    def test_missing_required_input_with_manifest_present_is_incomplete_capture_not_missing_snapshot(self, tmp_path, monkeypatch):
        """2026-08-21/22 regression: RISK_GATE_OUTPUT (or any other
        REQUIRED component besides provenance) missing while a manifest
        WITH known provenance genuinely exists must be reported as
        FORWARD_INCOMPLETE_CAPTURE -- a distinct, more specific fact than
        "no manifest exists at all" (FORWARD_MISSING_SNAPSHOT)."""
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-02-01")
        _write_recommendations("2026-02-05")
        _write_pregame_manifest(
            "2026-02-05", "2026-02-05T12:00:00Z",
            completeness_status=snap.MISSING_REQUIRED_INPUT, provenance_status="CAPTURED",
        )

        report = chr_mod.build_report(today="2026-02-10")
        rec = next(r for r in report["perDate"] if r["date"] == "2026-02-05")
        assert rec["forwardGateStatus"] == chr_mod.STATUS_FORWARD_INCOMPLETE_CAPTURE
        assert rec["forwardGateStatus"] != chr_mod.STATUS_FORWARD_MISSING_SNAPSHOT
        fwd = report["forwardOperationalHealth"]
        assert "2026-02-05" in fwd["incompleteCaptures"]
        assert "2026-02-05" not in fwd["snapshotsMissing"]
        assert "2026-02-05" in fwd["hardFailDates"]
        assert report["exitShouldFail"] is True


class TestSameDayPending:
    def test_same_day_pre_capture_date_does_not_false_fail(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-02-01")
        # No evidence at all for "today" yet -- the day's single daily
        # production opportunity hasn't happened.
        report = chr_mod.build_report(today="2026-02-10")

        rec = next(r for r in report["perDate"] if r["date"] == "2026-02-10")
        assert rec["forwardGateStatus"] == chr_mod.STATUS_FORWARD_PENDING_TODAY
        fwd = report["forwardOperationalHealth"]
        assert "2026-02-10" not in fwd["hardFailDates"]
        assert "2026-02-10" not in fwd["snapshotsMissing"]
        assert "2026-02-10" in fwd["pendingTodayDates"]
        assert report["exitShouldFail"] is False

    def test_a_genuine_miss_still_hard_fails_the_very_next_day(self, tmp_path, monkeypatch):
        """The exemption is strictly for TODAY -- once a pending date is
        no longer "today" (the next report run), silence is a real miss."""
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-02-01")
        _write_recommendations("2026-02-10")  # production ran; snapshot never captured

        today_report = chr_mod.build_report(today="2026-02-10")
        assert today_report["exitShouldFail"] is False  # still pending, not a miss yet

        tomorrow_report = chr_mod.build_report(today="2026-02-11")
        rec = next(r for r in tomorrow_report["perDate"] if r["date"] == "2026-02-10")
        assert rec["forwardGateStatus"] == chr_mod.STATUS_FORWARD_MISSING_SNAPSHOT
        assert tomorrow_report["exitShouldFail"] is True


class TestNoSlateDayExclusion:
    def test_date_with_zero_evidence_never_appears_as_a_failure(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-02-01")
        _write_recommendations("2026-02-05")  # only real evidence in the whole corpus
        _write_pregame_manifest("2026-02-05", "2026-02-05T12:00:00Z", completeness_status=snap.PARTIAL_REPLAY)
        _write_forward_replay_status("2026-02-05", "COMPLETED", outcome="completed")

        report = chr_mod.build_report(today="2026-02-06")
        dates = {r["date"] for r in report["perDate"]}
        # 2026-02-04 has NO recommendations.json and NO snapshot directory
        # anywhere -- a legitimate no-slate day -- and must never be
        # synthesized into the report at all.
        assert "2026-02-04" not in dates


class TestAcknowledgedLegacyGaps:
    def test_acknowledged_gap_stays_visible_but_does_not_drive_exit_code(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-02-01")
        _write_recommendations("2026-02-05")  # production ran; snapshot never captured, permanently
        _write_acknowledged_gap("2026-02-05")

        report = chr_mod.build_report(today="2026-02-10")
        rec = next(r for r in report["perDate"] if r["date"] == "2026-02-05")
        # Real status is untouched -- still visibly a missing-snapshot date.
        assert rec["forwardGateStatus"] == chr_mod.STATUS_FORWARD_MISSING_SNAPSHOT
        assert rec["acknowledgedLegacyGap"] is True
        assert rec["acknowledgedGapReason"] == "TERMINAL_UNRECOVERABLE_PRODUCTION_GAP"
        fwd = report["forwardOperationalHealth"]
        assert fwd["gateStatusCounts"].get(chr_mod.STATUS_FORWARD_MISSING_SNAPSHOT) == 1
        assert "2026-02-05" in fwd["acknowledgedLegacyGapDates"]
        # ...but it never drives the exit code.
        assert "2026-02-05" not in fwd["hardFailDates"]
        assert report["exitShouldFail"] is False

    def test_acknowledgment_does_not_generalize_to_a_new_unacknowledged_date(self, tmp_path, monkeypatch):
        """An acknowledged file with entries for OTHER dates must never
        blanket-excuse a brand new, unreviewed hard-fail date."""
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-02-01")
        _write_acknowledged_gap("2026-02-05")
        _write_recommendations("2026-02-06")  # a DIFFERENT, unacknowledged missing-snapshot date

        report = chr_mod.build_report(today="2026-02-10")
        rec = next(r for r in report["perDate"] if r["date"] == "2026-02-06")
        assert rec["acknowledgedLegacyGap"] is False
        assert "2026-02-06" in report["forwardOperationalHealth"]["hardFailDates"]
        assert report["exitShouldFail"] is True

    def test_script_never_writes_the_acknowledged_gaps_file_itself(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-02-01")
        _write_recommendations("2026-02-05")  # a real hard-fail date, unacknowledged
        assert not os.path.exists(chr_mod.ACKNOWLEDGED_GAPS_PATH)

        chr_mod.build_report(today="2026-02-10")
        assert not os.path.exists(chr_mod.ACKNOWLEDGED_GAPS_PATH), (
            "corpus_health_report.py must never self-acknowledge a hard-fail date"
        )


class TestReportPopulationConsistency:
    """The '17 captured / 0 missing' vs. '8 FORWARD_MISSING_SNAPSHOT
    dates' reporting-inconsistency regression: every forwardOperational
    Health counter must share one population and stay internally
    consistent with the per-date gate statuses."""

    def test_summary_metrics_agree_with_per_date_statuses(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-02-01")

        _healthy_manifest_and_replay("2026-02-02")
        _healthy_manifest_and_replay("2026-02-03")
        _write_recommendations("2026-02-04")  # truly missing: no manifest at all
        _write_recommendations("2026-02-05")
        _write_pregame_manifest(  # incomplete: manifest exists, MISSING_REQUIRED_INPUT
            "2026-02-05", "2026-02-05T12:00:00Z",
            completeness_status=snap.MISSING_REQUIRED_INPUT, provenance_status="CAPTURED",
        )

        report = chr_mod.build_report(today="2026-02-10")
        fwd = report["forwardOperationalHealth"]

        assert fwd["expectedRuns"] == fwd["snapshotsCaptured"] + len(fwd["snapshotsMissing"])
        assert set(fwd["snapshotsMissing"]) == {"2026-02-04"}
        assert set(fwd["incompleteCaptures"]) == {"2026-02-05"}
        # incompleteCaptures dates must be counted WITHIN snapshotsCaptured
        # (they have a manifest), never inside snapshotsMissing.
        assert "2026-02-05" not in fwd["snapshotsMissing"]

        gate_counts = fwd["gateStatusCounts"]
        assert gate_counts.get(chr_mod.STATUS_FORWARD_MISSING_SNAPSHOT, 0) == len(fwd["snapshotsMissing"])
        assert gate_counts.get(chr_mod.STATUS_FORWARD_INCOMPLETE_CAPTURE, 0) == len(fwd["incompleteCaptures"])
        assert gate_counts.get(chr_mod.STATUS_FORWARD_HEALTHY, 0) == 2

    def test_pending_today_is_excluded_from_expected_and_missing_counts(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-02-01")
        _healthy_manifest_and_replay("2026-02-02")
        # "today" has no evidence at all yet.

        report = chr_mod.build_report(today="2026-02-03")
        fwd = report["forwardOperationalHealth"]
        assert "2026-02-03" not in fwd["snapshotsMissing"]
        assert "2026-02-03" in fwd["pendingTodayDates"]
        assert fwd["expectedRuns"] == 1  # only 2026-02-02 -- 02-03 excluded as pending
