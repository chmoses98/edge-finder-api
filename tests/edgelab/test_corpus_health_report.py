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


def _required_component(component_type, missing=False, availability=None):
    availability = availability or (snap.MISSING if missing else snap.AVAILABLE)
    return {
        "componentType": component_type, "sourcePath": None, "snapshotPath": None,
        "storageMode": None, "contentHash": None, "byteSize": None, "rowCount": None,
        "capturedAt": None, "producer": None, "requiredStatus": snap.REQUIRED,
        "availabilityStatus": availability,
        "limitationReason": "OVERWRITTEN_SOURCE_NOT_YET_FROZEN" if availability == snap.MISSING else None,
    }


def _write_pregame_manifest(date, run_key, *, completeness_status, provenance_status="CAPTURED",
                             production_commit_sha="a" * 40, capture_mode=snap.CAPTURE_MODE_LIVE,
                             captured_at=None, skew_detected=False,
                             effective_config_availability=snap.PARTIAL, event_name="workflow_dispatch",
                             risk_gate_missing=False, market_universe_missing=False):
    """A minimal, fully synthetic but schema-shaped PRE_GAME_DECISION
    manifest -- every field scripts/corpus_health_report.py's forward
    gate rule table reads is set explicitly and deliberately, rather than
    derived, so each test exercises exactly one rule-table branch.

    event_name/risk_gate_missing/market_universe_missing exercise the
    run-type-aware completeness path (corpus-health audit, 2026-08-25
    follow-up): a real schedule-triggered manifest looks exactly like
    this -- event_name="schedule", RISK_GATE_OUTPUT MISSING, everything
    else AVAILABLE, stored completenessStatus=MISSING_REQUIRED_INPUT
    (computed under the OLD, not-yet-run-type-aware rules -- this helper
    lets a test set that stored value explicitly, mirroring an
    ALREADY-COMMITTED historical manifest, exactly like the real
    2026-08-21/22 manifests this fix targets)."""
    captured_at = captured_at or f"{date}T12:00:00Z"
    commit_sha = production_commit_sha if provenance_status == "CAPTURED" else None
    components = [
        _required_component("MARKET_UNIVERSE", missing=market_universe_missing),
        _required_component("RAW_PROJECTIONS"),
        _required_component("RISK_GATE_OUTPUT", missing=risk_gate_missing),
        {
            "componentType": "EFFECTIVE_CONFIG", "sourcePath": None, "snapshotPath": None,
            "storageMode": None, "contentHash": None, "byteSize": None, "rowCount": None,
            "capturedAt": None, "producer": None, "requiredStatus": snap.REQUIRED,
            "availabilityStatus": effective_config_availability, "limitationReason": None,
        },
    ]
    provenance = {
        "status": provenance_status, "commitSha": commit_sha,
        "gitHeadShaAtCapture": None, "workingTreeDirty": False if provenance_status == "CAPTURED" else None,
        "workflowRunId": None, "workflowRunAttempt": None, "ref": None, "refName": None,
        "repository": None, "capturedAt": captured_at,
        "reason": None if provenance_status == "CAPTURED" else "TEST_REASON",
        "eventName": event_name,
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


class TestRunTypeAwareCompleteness:
    """Corpus-health follow-up review (2026-08-25): RISK_GATE_OUTPUT is
    structurally impossible for a schedule-triggered run (fetch-slate.yml's
    BLOCK 7 never executes on `schedule` -- a deliberate, untouched safety
    boundary), so its absence alone must never hard-fail a research-only
    capture, while an authoritative/manual decision run missing the same
    component is a real, actionable gap and must still hard-fail."""

    def test_scheduled_research_only_run_missing_risk_gate_does_not_hard_fail(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-02-01")
        _write_recommendations("2026-02-21")
        _write_pregame_manifest(
            "2026-02-21", "2026-02-21T20:00:00Z",
            completeness_status=snap.MISSING_REQUIRED_INPUT, provenance_status="CAPTURED",
            event_name="schedule", risk_gate_missing=True,
        )
        # No forward_replay_status.json entry yet -- exactly the real
        # 2026-08-21 shape before run_forward_replay.py catches up.
        _write(os.path.join("data", "edgelab", "snapshots", "2026-02-21", "closing_line", "manifest.json"), {"stub": True})
        _write(os.path.join("data", "edgelab", "snapshots", "2026-02-21", "post_game_settlement", "manifest.json"), {"stub": True})

        report = chr_mod.build_report(today="2026-02-25")
        rec = next(r for r in report["perDate"] if r["date"] == "2026-02-21")
        assert rec["forwardGateStatus"] == chr_mod.STATUS_FORWARD_RESEARCH_ONLY_NO_DECISION
        assert rec["forwardGateStatus"] != chr_mod.STATUS_FORWARD_INCOMPLETE_CAPTURE
        assert rec["isResearchOnlyRun"] is True
        assert rec["completenessStatus"] == snap.MISSING_REQUIRED_INPUT  # stored record UNCHANGED
        assert rec["effectiveCompletenessStatus"] == snap.PARTIAL_REPLAY  # live re-interpretation
        assert "2026-02-21" not in report["forwardOperationalHealth"]["hardFailDates"]
        assert report["exitShouldFail"] is False

    def test_manual_decision_run_missing_risk_gate_still_hard_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-02-01")
        _write_recommendations("2026-02-21")
        _write_pregame_manifest(
            "2026-02-21", "2026-02-21T20:00:00Z",
            completeness_status=snap.MISSING_REQUIRED_INPUT, provenance_status="CAPTURED",
            event_name="workflow_dispatch", risk_gate_missing=True,
        )

        report = chr_mod.build_report(today="2026-02-25")
        rec = next(r for r in report["perDate"] if r["date"] == "2026-02-21")
        assert rec["forwardGateStatus"] == chr_mod.STATUS_FORWARD_INCOMPLETE_CAPTURE
        assert rec["isResearchOnlyRun"] is False
        assert rec["effectiveCompletenessStatus"] == snap.MISSING_REQUIRED_INPUT  # no reclassification -- a real gap
        assert "2026-02-21" in report["forwardOperationalHealth"]["hardFailDates"]
        assert report["exitShouldFail"] is True

    def test_scheduled_run_missing_a_research_component_still_hard_fails(self, tmp_path, monkeypatch):
        """The run-type exemption is narrow: it covers ONLY RISK_GATE_OUTPUT.
        A schedule-triggered run genuinely missing a real research input
        (here: MARKET_UNIVERSE -- the Kalshi market-universe archive,
        which fetch-slate.yml captures on EVERY trigger type, schedule
        included) is still a real, actionable capture gap."""
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-02-01")
        _write_recommendations("2026-02-21")
        _write_pregame_manifest(
            "2026-02-21", "2026-02-21T20:00:00Z",
            completeness_status=snap.MISSING_REQUIRED_INPUT, provenance_status="CAPTURED",
            event_name="schedule", risk_gate_missing=True, market_universe_missing=True,
        )

        report = chr_mod.build_report(today="2026-02-25")
        rec = next(r for r in report["perDate"] if r["date"] == "2026-02-21")
        assert rec["forwardGateStatus"] == chr_mod.STATUS_FORWARD_INCOMPLETE_CAPTURE
        assert rec["effectiveCompletenessStatus"] == snap.MISSING_REQUIRED_INPUT
        assert "2026-02-21" in report["forwardOperationalHealth"]["hardFailDates"]
        assert report["exitShouldFail"] is True

    def test_2026_08_21_style_fixture_classifies_research_only_no_decision(self, tmp_path, monkeypatch):
        """Reproduces the real 2026-08-21/22 shape exactly: a single
        schedule-triggered run, RISK_GATE_OUTPUT missing, everything else
        (including MARKET_UNIVERSE -- the Kalshi archive) present, replay
        never attempted (or attempted and correctly recorded as
        NOT_APPLICABLE_NO_DECISION)."""
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-02-01")
        _write_recommendations("2026-02-21")
        _write_pregame_manifest(
            "2026-02-21", "2026-02-21T20:29:07Z",
            completeness_status=snap.MISSING_REQUIRED_INPUT, provenance_status="CAPTURED",
            event_name="schedule", risk_gate_missing=True, market_universe_missing=False,
        )
        _write_forward_replay_status("2026-02-21", "NOT_APPLICABLE_NO_DECISION", outcome="not_applicable_no_decision")
        _write(os.path.join("data", "edgelab", "snapshots", "2026-02-21", "closing_line", "manifest.json"), {"stub": True})
        _write(os.path.join("data", "edgelab", "snapshots", "2026-02-21", "post_game_settlement", "manifest.json"), {"stub": True})

        report = chr_mod.build_report(today="2026-02-25")
        rec = next(r for r in report["perDate"] if r["date"] == "2026-02-21")
        assert rec["forwardGateStatus"] == chr_mod.STATUS_FORWARD_RESEARCH_ONLY_NO_DECISION
        assert report["exitShouldFail"] is False
        assert "2026-02-21" not in report["forwardOperationalHealth"]["hardFailDates"]

    def test_research_only_capture_not_misrepresented_as_completed_decision_replay(self, tmp_path, monkeypatch):
        """A research-only date's own forwardReplayStatus must never read
        as a completed BETTING-DECISION replay -- COMPLETED specifically
        means an authoritative decision was replayed successfully."""
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-02-01")
        _write_recommendations("2026-02-21")
        _write_pregame_manifest(
            "2026-02-21", "2026-02-21T20:00:00Z",
            completeness_status=snap.MISSING_REQUIRED_INPUT, provenance_status="CAPTURED",
            event_name="schedule", risk_gate_missing=True,
        )
        _write_forward_replay_status("2026-02-21", "NOT_APPLICABLE_NO_DECISION", outcome="not_applicable_no_decision")
        _write(os.path.join("data", "edgelab", "snapshots", "2026-02-21", "closing_line", "manifest.json"), {"stub": True})
        _write(os.path.join("data", "edgelab", "snapshots", "2026-02-21", "post_game_settlement", "manifest.json"), {"stub": True})

        report = chr_mod.build_report(today="2026-02-25")
        rec = next(r for r in report["perDate"] if r["date"] == "2026-02-21")
        assert rec["forwardReplayStatus"] != "COMPLETED"
        assert rec["forwardReplayStatus"] == "NOT_APPLICABLE_NO_DECISION"
        assert rec["forwardGateStatus"] != chr_mod.STATUS_FORWARD_HEALTHY  # HEALTHY means a real decision replay completed
        assert rec["forwardGateStatus"] == chr_mod.STATUS_FORWARD_RESEARCH_ONLY_NO_DECISION

    def test_manual_run_with_actually_failed_replay_still_hard_fails(self, tmp_path, monkeypatch):
        """A genuine authoritative-decision run whose replay failed must
        still hard-fail -- the run-type exemption must never swallow a
        real replay failure."""
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-02-01")
        _write_recommendations("2026-02-21")
        _write_pregame_manifest(
            "2026-02-21", "2026-02-21T20:00:00Z",
            completeness_status=snap.PARTIAL_REPLAY, provenance_status="CAPTURED",
            event_name="workflow_dispatch",
        )
        _write_forward_replay_status("2026-02-21", "REJECTED_INELIGIBLE", outcome="rejected_ineligible")

        report = chr_mod.build_report(today="2026-02-25")
        rec = next(r for r in report["perDate"] if r["date"] == "2026-02-21")
        assert rec["forwardGateStatus"] == chr_mod.STATUS_FORWARD_REPLAY_FAILURE
        assert "2026-02-21" in report["forwardOperationalHealth"]["hardFailDates"]
        assert report["exitShouldFail"] is True

    def test_market_universe_archive_required_regardless_of_run_type(self, tmp_path, monkeypatch):
        """Do not regress complete Kalshi archive coverage: MARKET_UNIVERSE
        stays REQUIRED and, when genuinely present, AVAILABLE for a
        schedule-triggered research-only run exactly as for a manual
        decision run -- the run-type exemption is narrow (RISK_GATE_OUTPUT
        only), never a blanket relaxation of research-capture completeness."""
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-02-01")
        _write_recommendations("2026-02-21")
        manifest = _write_pregame_manifest(
            "2026-02-21", "2026-02-21T20:00:00Z",
            completeness_status=snap.PARTIAL_REPLAY, provenance_status="CAPTURED",
            event_name="schedule", risk_gate_missing=True, market_universe_missing=False,
        )
        market_universe = next(c for c in manifest["components"] if c["componentType"] == "MARKET_UNIVERSE")
        assert market_universe["requiredStatus"] == snap.REQUIRED
        assert market_universe["availabilityStatus"] == snap.AVAILABLE

    def test_research_only_date_never_appears_in_incomplete_captures_metric(self, tmp_path, monkeypatch):
        """Regression: forwardOperationalHealth['incompleteCaptures'] must
        agree with the per-date forwardGateStatus -- a date correctly
        reclassified to FORWARD_RESEARCH_ONLY_NO_DECISION must not still
        show up in a metric named "incomplete captures" just because its
        immutable STORED completenessStatus (an honest historical record
        under the old, not-yet-run-type-aware rules) still says
        MISSING_REQUIRED_INPUT. Resurrecting exactly this kind of
        metric-vs-per-date-status disagreement is the original "17
        captured / 0 missing" bug this whole audit exists to close."""
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-02-01")
        _write_recommendations("2026-02-21")
        _write_pregame_manifest(
            "2026-02-21", "2026-02-21T20:00:00Z",
            completeness_status=snap.MISSING_REQUIRED_INPUT, provenance_status="CAPTURED",
            event_name="schedule", risk_gate_missing=True,
        )
        _write_forward_replay_status("2026-02-21", "NOT_APPLICABLE_NO_DECISION", outcome="not_applicable_no_decision")
        _write(os.path.join("data", "edgelab", "snapshots", "2026-02-21", "closing_line", "manifest.json"), {"stub": True})
        _write(os.path.join("data", "edgelab", "snapshots", "2026-02-21", "post_game_settlement", "manifest.json"), {"stub": True})

        report = chr_mod.build_report(today="2026-02-25")
        fwd = report["forwardOperationalHealth"]
        assert "2026-02-21" not in fwd["incompleteCaptures"]
        assert fwd["gateStatusCounts"].get(chr_mod.STATUS_FORWARD_RESEARCH_ONLY_NO_DECISION) == 1
        assert len(fwd["incompleteCaptures"]) == fwd["gateStatusCounts"].get(chr_mod.STATUS_FORWARD_INCOMPLETE_CAPTURE, 0)


# ── 2026-10-02 reliability cleanup: schedule-verified no-slate dates ─────────
#
# The real 2026-09-28 artifact (the MLB off-day between the regular season
# and the Wild Card round): fetch-slate.yml's scheduled run found
# `games: []`, failed pre-validation, and its `if: always()` snapshot step
# still wrote a PRE_GAME_DECISION manifest under run key "unkeyed" --
# captureMode LIVE_CAPTURE, productionProvenance MISSING
# (NEVER_CAPTURED_HISTORICALLY), every production pipeline component
# MISSING, and a frozen PRODUCTION_SLATE_INPUT of
# {"date": "2026-09-28", "games": [], "scheduleSource": "none", ...}.
# _write_empty_slate_manifest reproduces exactly that structure.

import gzip  # noqa: E402
import subprocess  # noqa: E402

OFF_DAY_SCHEDULE = {"totalGames": 0, "dates": []}


def _game_day_schedule(date, n=2):
    return {"totalGames": n, "dates": [{"date": date, "games": [
        {"gamePk": 900000 + i, "gameType": "F", "status": {"detailedState": "Scheduled"}} for i in range(n)
    ]}]}


class _Fetcher:
    """Deterministic schedule adapter: returns `responses` in order (the
    last one repeats); records every date asked for."""
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, date):
        self.calls.append(date)
        return self.responses[min(len(self.calls), len(self.responses)) - 1]


class _Sleep:
    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


def _frozen_slate_component(date, run_key, slate):
    path = os.path.join(snap.SNAPSHOTS_ROOT, date, "pre_game_decision", run_key, "frozen",
                        "production_slate_input.json.gz")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(slate, f)
    return {
        "componentType": "PRODUCTION_SLATE_INPUT", "sourcePath": "data/slate.json", "snapshotPath": path,
        "storageMode": snap.STORAGE_FROZEN_COPY, "contentHash": snap.sha256_file(path),
        "byteSize": os.path.getsize(path), "rowCount": None, "capturedAt": None,
        "producer": "scripts/protect_slate.py (legacy overwritten path)", "requiredStatus": snap.REQUIRED,
        "availabilityStatus": snap.AVAILABLE, "limitationReason": None,
    }


def _write_empty_slate_manifest(date, run_key="unkeyed", *, slate_date=None, games=None,
                                captured_at=None, provenance_status="MISSING",
                                recommendation_available=False):
    """The 2026-09-28 manifest shape (see block comment above)."""
    captured_at = captured_at or f"{date}T21:38:00Z"
    slate = {"date": slate_date or date, "kalshiDate": "26SEP28", "scheduleSource": "none",
             "games": games if games is not None else [], "kalshiMarketsFound": 0}
    components = [
        _frozen_slate_component(date, run_key, slate),
        _required_component("NORMALIZED_SLATE", missing=True),
        _required_component("RAW_PROJECTIONS", missing=True),
        _required_component("RECOMMENDATION_OUTPUT", missing=not recommendation_available),
        _required_component("MARKET_UNIVERSE"),
        _required_component("RISK_GATE_OUTPUT", missing=True),
        _required_component("PRODUCTION_PROVENANCE", missing=provenance_status != "CAPTURED"),
    ]
    captured = provenance_status == "CAPTURED"
    manifest = {
        "schemaVersion": snap.SCHEMA_VERSION,
        "snapshotId": f"test-{date}-{run_key}",
        "snapshotStage": snap.STAGE_PRE_GAME_DECISION,
        "snapshotDate": date,
        "captureMode": snap.CAPTURE_MODE_LIVE,
        "capturedAt": captured_at,
        "productionRunId": None,
        "workflowRunId": "36486867929",
        "productionCommitSha": "a" * 40 if captured else None,
        "productionProvenance": {
            "status": provenance_status, "commitSha": "a" * 40 if captured else None,
            "gitHeadShaAtCapture": None, "workingTreeDirty": None, "workflowRunId": None,
            "workflowRunAttempt": None, "ref": None, "refName": None, "repository": None,
            "capturedAt": None, "reason": None if captured else "NEVER_CAPTURED_HISTORICALLY",
            "eventName": "workflow_dispatch" if captured else None,
        },
        "snapshotWriterCommitSha": "b" * 40,
        "modelVersion": None,
        "pricingVersionsByFamily": {},
        "rulesConfigVersion": "1.0",
        "temporalConsistency": {"skewDetected": False, "maxSkewHours": 1.0, "referenceTimestamp": None, "detail": {}},
        "replayFidelityPotential": snap.LEVEL_1_APPROXIMATE,
        "completenessStatus": snap.MISSING_REQUIRED_INPUT,
        "validationStatus": "valid",
        "components": components,
        "missingComponents": [],
        "limitationReasons": [],
        "linkedSnapshotIds": [],
        "provenance": {"sourceSystem": "snapshot_foundation", "sourceFile": None, "sourceKey": None,
                       "capturedAt": captured_at, "ingestedAt": captured_at},
    }
    manifest["manifestHash"] = snap.compute_manifest_hash(manifest)
    path = snap.manifest_path(snap.STAGE_PRE_GAME_DECISION, date, run_key=run_key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(manifest, f)
    return manifest


def _rec(report, date):
    return next(r for r in report["perDate"] if r["date"] == date)


def _sep28_world(tmp_path, monkeypatch):
    """Boundary well before, healthy forward dates either side of the off-day
    -- the real 2026-09-27 / 09-28 / 09-29 neighborhood."""
    monkeypatch.chdir(tmp_path)
    _write_boundary("2026-08-03")
    _healthy_manifest_and_replay("2026-09-27")
    _write_empty_slate_manifest("2026-09-28")
    _healthy_manifest_and_replay("2026-09-29")


class TestScheduleVerifiedNoSlate:
    def test_sep28_off_day_is_a_successful_no_slate_terminal_state(self, tmp_path, monkeypatch):
        _sep28_world(tmp_path, monkeypatch)
        fetcher = _Fetcher(OFF_DAY_SCHEDULE)
        report = chr_mod.build_report(today="2026-10-02", schedule_fetcher=fetcher, sleep=_Sleep())

        rec = _rec(report, "2026-09-28")
        assert rec["forwardGateStatus"] == chr_mod.STATUS_FORWARD_NO_SLATE
        assert rec["gateStatus"] == chr_mod.STATUS_NOT_APPLICABLE_NO_SLATE
        assert rec["slateDay"]["verdict"] == "OFF_DAY"
        assert rec["slateDay"]["scheduleStatus"] == "NO_GAMES_SCHEDULED"
        # The evidence stays visible: the raw manifest facts are untouched.
        assert rec["productionProvenanceStatus"] == "MISSING"
        assert rec["completenessStatus"] == snap.MISSING_REQUIRED_INPUT
        assert fetcher.calls == ["2026-09-28"]  # only the empty-slate candidate is looked up

        fwd = report["forwardOperationalHealth"]
        assert fwd["noSlateDates"] == ["2026-09-28"]
        assert "2026-09-28" not in fwd["hardFailDates"]
        assert fwd["expectedRuns"] == 2  # 09-27 and 09-29 -- no run was expected on 09-28
        assert report["exitShouldFail"] is False
        assert report["operationalHealth"]["state"] == chr_mod.HEALTH_HEALTHY
        assert report["operationalHealth"]["noSlateDates"] == ["2026-09-28"]
        assert chr_mod.enforce(report)[0] == 0

    def test_conclusive_schedule_evidence_is_persisted_once_and_reused(self, tmp_path, monkeypatch):
        _sep28_world(tmp_path, monkeypatch)
        chr_mod.build_report(today="2026-10-02", schedule_fetcher=_Fetcher(OFF_DAY_SCHEDULE), sleep=_Sleep())
        path = os.path.join(chr_mod.SCHEDULE_EVIDENCE_DIR, "2026-09-28.json")
        with open(path) as f:
            persisted = json.load(f)
        assert persisted["status"] == "NO_GAMES_SCHEDULED" and persisted["date"] == "2026-09-28"

        # Later runs never re-ask (and never overwrite) a conclusive answer.
        later = _Fetcher(_game_day_schedule("2026-09-28"))
        report = chr_mod.build_report(today="2026-10-03", schedule_fetcher=later, sleep=_Sleep())
        assert later.calls == []
        assert _rec(report, "2026-09-28")["forwardGateStatus"] == chr_mod.STATUS_FORWARD_NO_SLATE
        with open(path) as f:
            assert json.load(f) == persisted

    def test_expected_production_that_never_happened_still_hard_fails(self, tmp_path, monkeypatch):
        # Same artifacts, but the schedule says games WERE scheduled: the
        # empty slate is a collection failure, not an off-day.
        _sep28_world(tmp_path, monkeypatch)
        report = chr_mod.build_report(today="2026-10-02",
                                      schedule_fetcher=_Fetcher(_game_day_schedule("2026-09-28")), sleep=_Sleep())
        rec = _rec(report, "2026-09-28")
        assert rec["forwardGateStatus"] == chr_mod.STATUS_FORWARD_SLATE_COLLECTION_FAILED
        assert rec["slateDay"]["verdict"] == "GAME_DAY_COLLECTION_FAILED"
        assert report["exitShouldFail"] is True
        assert report["operationalHealth"]["state"] == chr_mod.HEALTH_FAILED
        assert chr_mod.enforce(report)[0] == 1

    def test_unreadable_schedule_fails_closed_after_bounded_retries(self, tmp_path, monkeypatch):
        _sep28_world(tmp_path, monkeypatch)
        fetcher, sleep = _Fetcher(None), _Sleep()
        report = chr_mod.build_report(today="2026-10-02", schedule_fetcher=fetcher, sleep=sleep)
        rec = _rec(report, "2026-09-28")
        # Never read "no answer" as "no games": the ordinary rule table applies.
        assert rec["forwardGateStatus"] == chr_mod.STATUS_FORWARD_PROVENANCE_AMBIGUOUS
        assert rec["slateDay"]["verdict"] == "SCHEDULE_UNKNOWN"
        assert len(fetcher.calls) == chr_mod.SCHEDULE_FETCH_ATTEMPTS
        assert sleep.calls == list(chr_mod.SCHEDULE_FETCH_BACKOFF_SECONDS)
        assert not os.path.exists(os.path.join(chr_mod.SCHEDULE_EVIDENCE_DIR, "2026-09-28.json"))
        assert report["exitShouldFail"] is True

    def test_fetcher_exception_is_a_failed_lookup_not_an_off_day(self, tmp_path, monkeypatch):
        _sep28_world(tmp_path, monkeypatch)

        def boom(date):
            raise RuntimeError("network down")
        report = chr_mod.build_report(today="2026-10-02", schedule_fetcher=boom, sleep=_Sleep())
        assert _rec(report, "2026-09-28")["forwardGateStatus"] == chr_mod.STATUS_FORWARD_PROVENANCE_AMBIGUOUS
        assert report["exitShouldFail"] is True

    def test_transient_schedule_outage_recovers_within_the_same_run(self, tmp_path, monkeypatch):
        _sep28_world(tmp_path, monkeypatch)
        fetcher = _Fetcher(None, OFF_DAY_SCHEDULE)
        report = chr_mod.build_report(today="2026-10-02", schedule_fetcher=fetcher, sleep=_Sleep())
        rec = _rec(report, "2026-09-28")
        assert rec["forwardGateStatus"] == chr_mod.STATUS_FORWARD_NO_SLATE
        assert rec["slateDay"]["scheduleLookupAttempts"] == 2
        assert report["exitShouldFail"] is False

    def test_a_production_decision_on_disk_disqualifies_a_no_slate_claim(self, tmp_path, monkeypatch):
        _sep28_world(tmp_path, monkeypatch)
        _write_recommendations("2026-09-28")
        fetcher = _Fetcher(OFF_DAY_SCHEDULE)
        report = chr_mod.build_report(today="2026-10-02", schedule_fetcher=fetcher, sleep=_Sleep())
        assert fetcher.calls == []
        assert _rec(report, "2026-09-28")["forwardGateStatus"] == chr_mod.STATUS_FORWARD_PROVENANCE_AMBIGUOUS
        assert report["exitShouldFail"] is True

    def test_a_stale_slate_dated_for_another_day_is_never_a_no_slate_day(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-08-03")
        _write_empty_slate_manifest("2026-09-28", slate_date="2026-09-27")
        fetcher = _Fetcher(OFF_DAY_SCHEDULE)
        report = chr_mod.build_report(today="2026-10-02", schedule_fetcher=fetcher, sleep=_Sleep())
        assert fetcher.calls == []
        assert _rec(report, "2026-09-28")["forwardGateStatus"] == chr_mod.STATUS_FORWARD_PROVENANCE_AMBIGUOUS

    def test_any_run_with_games_disqualifies_the_date(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-08-03")
        _write_empty_slate_manifest("2026-09-28", "unkeyed", captured_at="2026-09-28T16:05:00Z")
        _write_empty_slate_manifest("2026-09-28", "2026-09-28T200500Z", captured_at="2026-09-28T20:05:00Z",
                                    games=[{"away": {"abbr": "NYY"}, "home": {"abbr": "BOS"}}])
        fetcher = _Fetcher(OFF_DAY_SCHEDULE)
        report = chr_mod.build_report(today="2026-10-02", schedule_fetcher=fetcher, sleep=_Sleep())
        assert fetcher.calls == []
        assert report["exitShouldFail"] is True

    def test_real_provenance_ambiguity_on_a_game_day_still_hard_fails(self, tmp_path, monkeypatch):
        # A slate WITH games whose production provenance is missing: the
        # off-day path never engages, the defect stays a hard fail.
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-08-03")
        _write_empty_slate_manifest("2026-09-24", games=[{"away": {"abbr": "NYY"}, "home": {"abbr": "BOS"}}])
        fetcher = _Fetcher(OFF_DAY_SCHEDULE)
        report = chr_mod.build_report(today="2026-10-02", schedule_fetcher=fetcher, sleep=_Sleep())
        assert fetcher.calls == []
        assert _rec(report, "2026-09-24")["forwardGateStatus"] == chr_mod.STATUS_FORWARD_PROVENANCE_AMBIGUOUS
        assert report["operationalHealth"]["state"] == chr_mod.HEALTH_FAILED

    def test_a_corrupted_off_day_artifact_is_still_an_integrity_failure(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-08-03")
        manifest = _write_empty_slate_manifest("2026-09-28")
        frozen = next(c for c in manifest["components"] if c["componentType"] == "PRODUCTION_SLATE_INPUT")
        with gzip.open(frozen["snapshotPath"], "wt", encoding="utf-8") as f:
            json.dump({"date": "2026-09-28", "games": [], "tampered": True}, f)
        report = chr_mod.build_report(today="2026-10-02", schedule_fetcher=_Fetcher(OFF_DAY_SCHEDULE), sleep=_Sleep())
        assert _rec(report, "2026-09-28")["forwardGateStatus"] == chr_mod.STATUS_INTEGRITY_FAILURE
        assert report["exitShouldFail"] is True

    def test_research_only_scheduled_run_is_unchanged(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-08-03")
        _write_recommendations("2026-09-26")
        _write_pregame_manifest("2026-09-26", "2026-09-26T16:00:00Z", completeness_status=snap.MISSING_REQUIRED_INPUT,
                                event_name="schedule", risk_gate_missing=True)
        _write_forward_replay_status("2026-09-26", "NOT_APPLICABLE_NO_DECISION", outcome="not_applicable_no_decision")
        _write(os.path.join("data", "edgelab", "snapshots", "2026-09-26", "closing_line", "manifest.json"), {"stub": True})
        _write(os.path.join("data", "edgelab", "snapshots", "2026-09-26", "post_game_settlement", "manifest.json"), {"stub": True})
        fetcher = _Fetcher(OFF_DAY_SCHEDULE)
        report = chr_mod.build_report(today="2026-10-02", schedule_fetcher=fetcher, sleep=_Sleep())
        assert fetcher.calls == []
        assert _rec(report, "2026-09-26")["forwardGateStatus"] == chr_mod.STATUS_FORWARD_RESEARCH_ONLY_NO_DECISION
        assert report["operationalHealth"]["state"] == chr_mod.HEALTH_HEALTHY

    def test_no_slate_day_neither_breaks_nor_extends_a_degraded_streak(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-08-03")
        for date in ("2026-09-26", "2026-09-27", "2026-09-29"):
            _write_recommendations(date)
            _write_pregame_manifest(date, f"{date}T12:00:00Z", completeness_status=snap.PARTIAL_REPLAY)
            _write_forward_replay_status(date, "COMPLETED", outcome="completed")
            # no closing-line manifest -> FORWARD_CLOSING_DATA_PENDING
        _write_empty_slate_manifest("2026-09-28")
        report = chr_mod.build_report(today="2026-10-02", schedule_fetcher=_Fetcher(OFF_DAY_SCHEDULE), sleep=_Sleep())
        # 09-26, 09-27, 09-29 are consecutive game days around the off-day.
        assert report["forwardOperationalHealth"]["consecutiveDegradedForwardRuns"] == 3
        assert report["exitShouldFail"] is True


class TestTodayIsNeverFinal:
    def test_an_early_empty_capture_today_is_pending_not_failed(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-08-03")
        _write_empty_slate_manifest("2026-10-02", captured_at="2026-10-02T06:00:00Z")
        fetcher = _Fetcher(_game_day_schedule("2026-10-02"))
        report = chr_mod.build_report(today="2026-10-02", schedule_fetcher=fetcher, sleep=_Sleep())
        rec = _rec(report, "2026-10-02")
        assert fetcher.calls == []  # no empty-slate verdict is ever drawn for today
        assert rec["forwardGateStatus"] == chr_mod.STATUS_FORWARD_PENDING_TODAY
        assert rec["forwardGateStatusIfFinal"] == chr_mod.STATUS_FORWARD_PROVENANCE_AMBIGUOUS
        assert report["exitShouldFail"] is False

        # The next day the same evidence is judged for real.
        tomorrow = chr_mod.build_report(today="2026-10-03",
                                        schedule_fetcher=_Fetcher(_game_day_schedule("2026-10-02")), sleep=_Sleep())
        assert _rec(tomorrow, "2026-10-02")["forwardGateStatus"] == chr_mod.STATUS_FORWARD_SLATE_COLLECTION_FAILED
        assert tomorrow["exitShouldFail"] is True

    def test_corruption_today_still_fails_today(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-08-03")
        manifest = _write_empty_slate_manifest("2026-10-02")
        frozen = next(c for c in manifest["components"] if c["componentType"] == "PRODUCTION_SLATE_INPUT")
        with gzip.open(frozen["snapshotPath"], "wt", encoding="utf-8") as f:
            f.write("{}")
        report = chr_mod.build_report(today="2026-10-02", schedule_fetcher=_Fetcher(None), sleep=_Sleep())
        assert _rec(report, "2026-10-02")["forwardGateStatus"] == chr_mod.STATUS_INTEGRITY_FAILURE
        assert report["exitShouldFail"] is True


class TestOperationalHealthAndEnforcement:
    def test_pending_postgame_data_is_degraded_not_failed(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-08-03")
        _healthy_manifest_and_replay("2026-09-29")
        _write_recommendations("2026-09-30")
        _write_pregame_manifest("2026-09-30", "2026-09-30T12:00:00Z", completeness_status=snap.PARTIAL_REPLAY)
        _write_forward_replay_status("2026-09-30", "COMPLETED", outcome="completed")
        report = chr_mod.build_report(today="2026-10-01", schedule_fetcher=_Fetcher(None), sleep=_Sleep())
        health = report["operationalHealth"]
        assert health["state"] == chr_mod.HEALTH_DEGRADED
        assert health["reasons"] == ["PENDING_POSTGAME_DATA:FORWARD_CLOSING_DATA_PENDING"]
        assert report["exitShouldFail"] is False
        assert chr_mod.enforce(report)[0] == 0
        assert "DEGRADED" in chr_mod.render_step_summary(report)

    def test_acknowledged_historical_gaps_do_not_make_current_health_degraded(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-08-03")
        _write_acknowledged_gap("2026-08-11")
        _write_recommendations("2026-08-11")
        _healthy_manifest_and_replay("2026-09-29")
        report = chr_mod.build_report(today="2026-10-01", schedule_fetcher=_Fetcher(None), sleep=_Sleep())
        assert report["operationalHealth"]["state"] == chr_mod.HEALTH_HEALTHY
        assert report["operationalHealth"]["historicalGapDates"] == ["2026-08-11"]

    def test_awaiting_first_forward_capture_is_not_applicable(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        report = chr_mod.build_report(today="2026-10-01", schedule_fetcher=_Fetcher(None), sleep=_Sleep())
        assert report["operationalHealth"]["state"] == chr_mod.HEALTH_NOT_APPLICABLE
        assert chr_mod.enforce(report)[0] == 0

    @pytest.mark.parametrize("bad", [None, {}, {"operationalHealth": {}}, {"operationalHealth": {"state": "GREEN"}}])
    def test_a_report_without_a_valid_state_fails_enforcement(self, bad):
        assert chr_mod.enforce(bad)[0] == 1

    def test_failed_state_always_matches_exit_should_fail(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_boundary("2026-02-01")
        _write_recommendations("2026-02-05")
        report = chr_mod.build_report(today="2026-02-10", schedule_fetcher=_Fetcher(None), sleep=_Sleep())
        assert report["exitShouldFail"] is True
        assert report["operationalHealth"]["state"] == chr_mod.HEALTH_FAILED
        assert report["operationalHealth"]["workflowShouldFail"] is True
        assert report["operationalHealth"]["hardFailDates"] == [
            {"date": "2026-02-05", "status": chr_mod.STATUS_FORWARD_MISSING_SNAPSHOT}]


class TestCliExitContract:
    """--report-only writes the report and exits 0 whatever the verdict;
    --enforce is the one place a FAILED verdict becomes exit 1; the bare
    invocation keeps its historical exit-1-on-failure behavior."""
    SCRIPT = os.path.join(ROOT, "scripts", "corpus_health_report.py")

    def _run(self, cwd, *args):
        env = dict(os.environ, EDGEFINDER_SCHEDULE_EVIDENCE="offline", PYTHONPATH=ROOT)
        env.pop("GITHUB_STEP_SUMMARY", None)
        return subprocess.run([sys.executable, self.SCRIPT, *args], cwd=cwd, env=env,
                              capture_output=True, text=True)

    def test_failing_verdict(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_boundary("2020-01-01")
        _write_recommendations("2020-01-05")  # forward production date with no snapshot -> hard fail
        assert self._run(tmp_path, "--report-only").returncode == 0
        with open(chr_mod.REPORT_JSON_PATH) as f:
            assert json.load(f)["operationalHealth"]["state"] == "FAILED"
        assert self._run(tmp_path, "--enforce").returncode == 1
        assert self._run(tmp_path).returncode == 1

    def test_passing_verdict(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert self._run(tmp_path, "--report-only").returncode == 0
        assert self._run(tmp_path, "--enforce").returncode == 0

    def test_enforce_without_a_report_fails(self, tmp_path):
        assert self._run(tmp_path, "--enforce").returncode == 1


class TestCorpusHealthWorkflowShape:
    """One pass/fail decision, no masking."""
    def _steps(self):
        import yaml
        with open(os.path.join(ROOT, ".github", "workflows", "corpus-health-check.yml")) as f:
            wf = yaml.safe_load(f)
        return wf["jobs"]["check"]["steps"]

    def test_single_enforcement_step_and_no_continue_on_error(self):
        steps = self._steps()
        assert not any(s.get("continue-on-error") for s in steps)
        report = next(s for s in steps if s.get("id") == "report")
        assert "--report-only" in report["run"]
        enforcing = [s for s in steps if "--enforce" in (s.get("run") or "")]
        assert len(enforcing) == 1
        assert steps[-1] is enforcing[0]
        assert enforcing[0].get("if") == "always()"
        # A crashed report step still turns the workflow red.
        assert "steps.report.outcome" in enforcing[0]["run"] and "exit 1" in enforcing[0]["run"]

    def test_schedule_evidence_is_published_with_the_report(self):
        commit = next(s for s in self._steps() if "git_data_commit.py" in (s.get("run") or ""))
        assert "data/edgelab/schedule_evidence/" in commit["run"]
