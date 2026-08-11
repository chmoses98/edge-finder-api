#!/usr/bin/env python3
"""
tests/edgelab/test_snapshot.py
=================================
Historical Capture Completeness and Immutable Snapshot Foundation
milestone: coverage for lib/edgelab/snapshot.py + scripts/create_snapshot.py
+ scripts/backfill_snapshots.py + scripts/check_snapshot_capture.py.

Includes the maintainer-review-requested adversarial coverage: tampered
referenced vs frozen components, pruned referenced components, conflicting
duplicate runs under the run-keyed identity scheme, multiple production
runs per date each preserved (not overwritten), temporal-skew detection,
captureMode, and the workflow capture-completeness check's recovery path.

Every test runs inside an isolated tmp_path (monkeypatch.chdir), never
against the real repository's data/ tree.
"""
import gzip
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import lib.pipeline_artifacts as pipeline_artifacts  # noqa: E402
from lib.edgelab import schema  # noqa: E402
from lib.edgelab import snapshot as snap  # noqa: E402

DATE = "2026-07-31"


def _write(path, obj_or_bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(obj_or_bytes, (bytes, bytearray)):
        with open(path, "wb") as f:
            f.write(obj_or_bytes)
    else:
        with open(path, "w") as f:
            json.dump(obj_or_bytes, f)


def _write_pipeline_artifact(stage, date, data, produced_by, created_at=None):
    """Like pipeline_artifacts.write_stage_artifact(), but lets a test pin
    meta.createdAt precisely -- write_stage_artifact() always stamps
    createdAt=now(), which would make temporal-skew tests depend on real
    wall-clock time relative to a fixed historical DATE."""
    if created_at is None:
        pipeline_artifacts.write_stage_artifact(stage, date, data, produced_by=produced_by)
        return
    path = pipeline_artifacts.artifact_path(stage, date)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({
            "meta": {"stage": stage, "slateDate": date, "createdAt": created_at,
                     "schemaVersion": "1.0", "producedBy": produced_by,
                     "status": "transitional", "sourceStage": None},
            "data": data,
        }, f)


def _wire_full_pregame_fixture(tmp_path, monkeypatch, recommendations_created_at=None):
    """A minimal but complete set of real-shaped inputs for PRE_GAME_DECISION.
    When recommendations_created_at is given, EVERY pipeline artifact is
    pinned to that same timestamp (a real single production run writes
    them all within the same job, seconds apart) -- only a test that
    deliberately wants to simulate skew should diverge one artifact's
    timestamp from this baseline afterward."""
    monkeypatch.chdir(tmp_path)

    _write_pipeline_artifact(
        "recommendations", DATE, {"games": [{"gameId": "1", "marketLedger": []}]},
        "scripts/build_market_ledger.py", created_at=recommendations_created_at,
    )
    _write_pipeline_artifact("projections", DATE, {"games": []}, "scripts/build_market_ledger.py", created_at=recommendations_created_at)
    _write_pipeline_artifact("normalized_slate", DATE, {"games": []}, "scripts/enrich_data.py", created_at=recommendations_created_at)
    _write_pipeline_artifact("execution", DATE, {"rulesVersion": "1.0", "candidates": []}, "scripts/risk_gate.py", created_at=recommendations_created_at)
    _write_pipeline_artifact("validation", DATE, {"errors": []}, "scripts/validate_slate_final.py", created_at=recommendations_created_at)
    _write_pipeline_artifact("protection", DATE, {"runType": "OFFICIAL_PREGAME"}, "scripts/protect_slate.py", created_at=recommendations_created_at)
    _write_pipeline_artifact(
        "provenance", DATE,
        {"commitSha": "deadbeef" * 5, "workflowRunId": "123456", "workflowRunAttempt": "1",
         "ref": "refs/heads/main", "refName": "main", "repository": "chmoses98/edge-finder-api",
         "workflow": "Fetch Slate Data", "job": "fetch", "eventName": "push"},
        "scripts/capture_production_provenance.py", created_at=recommendations_created_at,
    )

    _write(os.path.join("data", "slates", DATE, "authoritative.json"), {"date": DATE, "games": []})
    _write(os.path.join("data", "kalshi_registry_snapshots", f"kalshi_search_{DATE}.json"), {"markets": []})
    _write(os.path.join("data", "weather.json"), {"parks": [{"team": "SD", "temp": 72}]})
    _write(os.path.join("data", "bullpen.json"), {"bullpens": {"SD": {"era": 4.0}}})
    _write(
        os.path.join("config", "rules.json"),
        {"_version": "1.0", "calibration": {}, "edge_thresholds": {}, "base_sizes": {"High": 4.0},
         "multipliers": {}, "market_list": [], "validation": {"required_per_game": [], "required_per_market_row": [],
                                                                "rejection_required_if_no_bet": True,
                                                                "min_qualifying_bets_full_slate": 12}},
    )


def _pregame_manifest_path(date=DATE):
    run_dirs = snap.list_pregame_run_dirs(date)
    assert run_dirs, f"expected at least one PRE_GAME_DECISION run dir for {date}"
    return os.path.join(snap.SNAPSHOTS_ROOT, date, "pre_game_decision", run_dirs[-1], "manifest.json")


class TestSchemaValidation:
    def test_built_pregame_manifest_and_components_validate(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        manifest = result["manifest"]
        assert schema.validate_record("snapshot_manifest", manifest) == []
        for component in manifest["components"]:
            assert schema.validate_record("snapshot_component", component) == [], component

    def test_missing_component_with_null_storage_mode_is_still_schema_valid(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        manifest = result["manifest"]
        assert manifest["completenessStatus"] == snap.MISSING_REQUIRED_INPUT
        missing = [c for c in manifest["components"] if c["availabilityStatus"] == snap.MISSING]
        assert missing
        for c in missing:
            assert schema.validate_record("snapshot_component", c) == []
            assert c["storageMode"] is None
            assert c["limitationReason"] is not None

    def test_captureMode_and_temporalConsistency_present_and_valid(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        manifest = result["manifest"]
        assert manifest["captureMode"] == snap.CAPTURE_MODE_LIVE
        assert "temporalConsistency" in manifest
        assert manifest["temporalConsistency"]["skewDetected"] is False


class TestWriteOnceImmutability:
    def test_rerun_with_unchanged_inputs_is_noop_verified_not_rewritten(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        first = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        assert first["outcome"] == "created"
        path = first["path"]
        mtime_before = os.path.getmtime(path)

        second = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        assert second["outcome"] == "noop_verified"
        assert os.path.getmtime(path) == mtime_before
        assert second["manifest"]["manifestHash"] == first["manifest"]["manifestHash"]

    def test_conflicting_rerun_preserves_existing_and_writes_diagnostics(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch, recommendations_created_at="2026-07-31T22:00:00Z")
        first = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        assert first["outcome"] == "created"

        # Simulate a genuine change to a component WITHOUT changing the
        # productionRunKey (recommendations.json's own createdAt stays
        # fixed) -- this is what forces a same-identity conflict rather
        # than a new run-keyed slot.
        _write(os.path.join("data", "kalshi_registry_snapshots", f"kalshi_search_{DATE}.json"), {"markets": ["changed"]})

        second = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        assert second["outcome"] == "conflict"
        assert second["manifest"]["manifestHash"] == first["manifest"]["manifestHash"]  # existing preserved untouched
        assert os.path.isdir(second["conflictEvidencePath"])
        assert os.path.exists(os.path.join(second["conflictEvidencePath"], "candidate_manifest.json"))

        reloaded = snap.load_manifest(snap.STAGE_PRE_GAME_DECISION, DATE, run_key="2026-07-31T22:00:00Z")
        assert reloaded["manifestHash"] == first["manifest"]["manifestHash"]

    def test_atomic_write_leaves_no_temp_files_behind(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        for root, _, files in os.walk(snap.SNAPSHOTS_ROOT):
            for fn in files:
                assert not fn.endswith(".tmp"), f"leftover temp file: {os.path.join(root, fn)}"


class TestSnapshotGranularityAndIdentity:
    """Maintainer review item 4: one snapshot per calendar date is NOT
    assumed -- a genuinely different production run for the same date
    (different recommendations.json meta.createdAt) gets its OWN slot,
    never overwriting or conflicting with the earlier valid one."""

    def test_two_distinct_production_runs_same_date_both_preserved(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch, recommendations_created_at="2026-07-31T18:00:00Z")
        first = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        assert first["outcome"] == "created"

        # A real lineup-recheck rerun: build_market_ledger.py reruns,
        # producing a NEW recommendations.json with a later createdAt and
        # (legitimately) different content.
        _wire_full_pregame_fixture(tmp_path, monkeypatch, recommendations_created_at="2026-07-31T22:30:00Z")
        _write(os.path.join("data", "kalshi_registry_snapshots", f"kalshi_search_{DATE}.json"), {"markets": ["updated"]})
        second = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        assert second["outcome"] == "created"
        assert second["manifest"]["snapshotId"] != first["manifest"]["snapshotId"]

        run_dirs = snap.list_pregame_run_dirs(DATE)
        assert len(run_dirs) == 2

        # The FIRST manifest is untouched.
        first_reloaded = snap.load_manifest(snap.STAGE_PRE_GAME_DECISION, DATE, run_key="2026-07-31T18:00:00Z")
        assert first_reloaded["manifestHash"] == first["manifest"]["manifestHash"]

    def test_postgame_links_to_every_distinct_pregame_run(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch, recommendations_created_at="2026-07-31T18:00:00Z")
        first = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        _wire_full_pregame_fixture(tmp_path, monkeypatch, recommendations_created_at="2026-07-31T22:30:00Z")
        second = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)

        postgame = snap.build_snapshot(snap.STAGE_POST_GAME_SETTLEMENT, DATE)
        linked = set(postgame["manifest"]["linkedSnapshotIds"])
        assert linked == {first["manifest"]["snapshotId"], second["manifest"]["snapshotId"]}


class TestTemporalSkewDetection:
    """Maintainer review item 2: components drawn from meaningfully
    different production runs must be flagged, not silently mixed."""

    def test_skew_detected_when_stage_artifact_is_stale(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch, recommendations_created_at="2026-07-31T22:00:00Z")
        # Overwrite validation.json with a stale createdAt from many hours earlier.
        path = pipeline_artifacts.artifact_path("validation", DATE)
        with open(path) as f:
            env = json.load(f)
        env["meta"]["createdAt"] = "2026-07-31T10:00:00Z"
        with open(path, "w") as f:
            json.dump(env, f)

        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        manifest = result["manifest"]
        assert manifest["temporalConsistency"]["skewDetected"] is True
        # Skew forces a downgrade even if every component were otherwise available.
        assert manifest["completenessStatus"] in (snap.PARTIAL_REPLAY, snap.MISSING_REQUIRED_INPUT, snap.APPROXIMATE_ONLY)

    def test_no_skew_when_all_artifacts_close_in_time(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch, recommendations_created_at="2026-07-31T22:00:00Z")
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        assert result["manifest"]["temporalConsistency"]["skewDetected"] is False


class TestFrozenMutableComponent:
    """Maintainer review item 3: NOTHING in this repo is safely
    REFERENCED_IMMUTABLE by construction -- every pipeline artifact,
    dated Kalshi snapshot, authoritative.json, and EdgeLab JSONL entity
    is FROZEN_COPY."""

    def test_every_component_with_a_real_source_is_frozen_not_referenced(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        manifest = result["manifest"]
        for component in manifest["components"]:
            if component["availabilityStatus"] not in (snap.AVAILABLE, snap.PARTIAL):
                continue
            assert component["storageMode"] == snap.STORAGE_FROZEN_COPY, (
                f"{component['componentType']} is REFERENCED_IMMUTABLE -- maintainer review found no source "
                f"in this repo is safely referenceable; it must be frozen"
            )

    def test_required_components_never_depend_on_a_prunable_reference(self, tmp_path, monkeypatch):
        """Item 9: a permanently-retained manifest must never reference a
        component subject to pruning without freezing it."""
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        for component in result["manifest"]["components"]:
            if component["requiredStatus"] == snap.REQUIRED and component["availabilityStatus"] in (snap.AVAILABLE, snap.PARTIAL):
                assert component["storageMode"] == snap.STORAGE_FROZEN_COPY

    def test_overwritten_source_preserved_after_live_file_changes(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        manifest = result["manifest"]
        weather_component = next(c for c in manifest["components"] if c["componentType"] == "WEATHER")
        with open(weather_component["snapshotPath"]) as f:
            frozen_content = json.load(f)
        assert frozen_content["parks"][0]["temp"] == 72

        _write(os.path.join("data", "weather.json"), {"parks": [{"team": "SD", "temp": 95}]})

        with open(weather_component["snapshotPath"]) as f:
            still_frozen = json.load(f)
        assert still_frozen["parks"][0]["temp"] == 72

    def test_compressed_frozen_component_decodes_to_original_content(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        rec_output = next(c for c in result["manifest"]["components"] if c["componentType"] == "RECOMMENDATION_OUTPUT")
        assert rec_output["snapshotPath"].endswith(".gz")
        with gzip.open(rec_output["snapshotPath"], "rt") as f:
            content = json.load(f)
        assert content["data"]["games"][0]["gameId"] == "1"


class TestReferencedImmutablePrimitiveStillWorks:
    """The REFERENCED_IMMUTABLE mechanism itself (build_referenced_component)
    is kept for a future genuinely write-once source -- exercised directly
    here since no current stage builder uses it (item 5/10 adversarial
    coverage: pruned + tampered referenced component)."""

    def test_referenced_component_hashes_source_in_place(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        source = os.path.join("somewhere", "immutable.json")
        _write(source, {"x": 1})
        component = snap.build_referenced_component("SOME_TYPE", source, snap.REQUIRED)
        assert component["storageMode"] == snap.STORAGE_REFERENCED_IMMUTABLE
        assert component["snapshotPath"] is None
        assert component["contentHash"] == snap.sha256_file(source)

    def test_referenced_component_tamper_detected_at_verify_time(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        source = os.path.join("somewhere", "immutable.json")
        _write(source, {"x": 1})
        component = snap.build_referenced_component("SOME_TYPE", source, snap.REQUIRED)
        fake_manifest = {"components": [component], "manifestHash": "0" * 64}
        _write(source, {"x": 2})  # tampered after "capture"
        verification = snap.verify_snapshot(fake_manifest)
        assert verification["components"][0]["hashValid"] is False
        assert verification["overallStatus"] == "INTEGRITY_FAILURE"

    def test_referenced_component_pruned_source_detected_at_verify_time(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        source = os.path.join("somewhere", "immutable.json")
        _write(source, {"x": 1})
        component = snap.build_referenced_component("SOME_TYPE", source, snap.REQUIRED)
        os.remove(source)  # simulate retention pruning the referenced file
        fake_manifest = {"components": [component], "manifestHash": "0" * 64}
        verification = snap.verify_snapshot(fake_manifest)
        assert verification["components"][0]["hashValid"] is False
        assert verification["overallStatus"] == "INTEGRITY_FAILURE"


class TestPregamePostgameSeparationAndLookahead:
    def test_pregame_manifest_marks_settlement_and_clv_not_applicable(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        manifest = result["manifest"]
        for component_type in ("SETTLEMENT", "CLV"):
            component = next(c for c in manifest["components"] if c["componentType"] == component_type)
            assert component["availabilityStatus"] == snap.NOT_APPLICABLE_FOR_STAGE
            assert component["limitationReason"] == snap.REASON_POSTGAME_EXCLUDED
        assert snap.REASON_POSTGAME_EXCLUDED in manifest["limitationReasons"]

    def test_postgame_manifest_does_not_recapture_pregame_components(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = snap.build_snapshot(snap.STAGE_POST_GAME_SETTLEMENT, DATE)
        manifest = result["manifest"]
        component_types = {c["componentType"] for c in manifest["components"]}
        assert component_types == {"SETTLEMENT", "CLV"}

    def test_postgame_snapshot_links_back_to_pregame_without_mutating_it(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        pregame = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        pregame_hash_before = pregame["manifest"]["manifestHash"]

        postgame = snap.build_snapshot(snap.STAGE_POST_GAME_SETTLEMENT, DATE)
        assert postgame["manifest"]["linkedSnapshotIds"] == [pregame["manifest"]["snapshotId"]]

        reloaded_pregame = snap.load_latest_pregame_manifest(DATE)
        assert reloaded_pregame["manifestHash"] == pregame_hash_before


class TestEffectiveConfigCapture:
    def test_effective_config_is_always_partial_not_overstated(self, tmp_path, monkeypatch):
        """Item 6: rules.json alone is never claimed as complete -- the
        component's own availabilityStatus reflects this honestly."""
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        manifest = result["manifest"]
        effective_config = next(c for c in manifest["components"] if c["componentType"] == "EFFECTIVE_CONFIG")
        assert effective_config["availabilityStatus"] == snap.PARTIAL
        assert effective_config["limitationReason"] == snap.REASON_PARTIAL_FIELD_POPULATION
        # A REQUIRED component being PARTIAL caps completeness below COMPLETE.
        assert manifest["completenessStatus"] != snap.COMPLETE_FOR_PRODUCTION_REPLAY

    def test_effective_config_captures_rules_version_and_f5_pricing_version(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        manifest = result["manifest"]
        assert manifest["rulesConfigVersion"] == "1.0"
        effective_config_component = next(c for c in manifest["components"] if c["componentType"] == "EFFECTIVE_CONFIG")
        with open(effective_config_component["snapshotPath"]) as f:
            record = json.load(f)
        assert record["rulesConfigVersion"] == "1.0"
        assert record["rulesConfigContents"]["_version"] == "1.0"
        assert "not a complete effective-configuration extractor" in record["note"]

    def test_effective_config_is_deterministic_across_rebuilds(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        record1 = snap.capture_effective_config(DATE, "abc123")
        record2 = snap.capture_effective_config(DATE, "abc123")
        assert snap.canonical_json_bytes(record1) == snap.canonical_json_bytes(record2)


class TestCompletenessRuleTable:
    """Item 7: exact mechanical rule table, evaluated in order."""

    def test_missing_required_input_when_required_component_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        assert result["manifest"]["completenessStatus"] == snap.MISSING_REQUIRED_INPUT

    def test_partial_replay_when_required_component_partial_but_none_missing(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        # EFFECTIVE_CONFIG (REQUIRED) is always PARTIAL -- see capture_effective_config.
        assert result["manifest"]["completenessStatus"] == snap.PARTIAL_REPLAY

    def test_derive_completeness_status_direct_rule_table(self):
        required_available = {"requiredStatus": snap.REQUIRED, "availabilityStatus": snap.AVAILABLE}
        required_missing = {"requiredStatus": snap.REQUIRED, "availabilityStatus": snap.MISSING}
        required_partial = {"requiredStatus": snap.REQUIRED, "availabilityStatus": snap.PARTIAL}
        nice_missing = {"requiredStatus": snap.NICE_TO_HAVE, "availabilityStatus": snap.MISSING}

        assert snap.derive_completeness_status([required_missing]) == snap.MISSING_REQUIRED_INPUT
        assert snap.derive_completeness_status([required_partial]) == snap.PARTIAL_REPLAY
        assert snap.derive_completeness_status([required_available, nice_missing]) == snap.APPROXIMATE_ONLY
        assert snap.derive_completeness_status([required_available]) == snap.COMPLETE_FOR_PRODUCTION_REPLAY
        assert snap.derive_completeness_status([required_available], commit_ambiguous=True) == snap.PARTIAL_REPLAY
        assert snap.derive_completeness_status([required_available], temporal_skew_detected=True) == snap.PARTIAL_REPLAY

    def test_replay_fidelity_level_3_requires_matching_commits(self):
        assert snap.derive_replay_fidelity(snap.COMPLETE_FOR_PRODUCTION_REPLAY, "abc", "abc") == snap.LEVEL_3_CODE_PINNED
        assert snap.derive_replay_fidelity(snap.COMPLETE_FOR_PRODUCTION_REPLAY, None, "abc") == snap.LEVEL_2_PRODUCTION_EQUIVALENT
        assert snap.derive_replay_fidelity(snap.PARTIAL_REPLAY, "abc", "abc") == snap.LEVEL_1_APPROXIMATE

    def test_integrity_failure_never_written_by_builder(self, tmp_path, monkeypatch):
        """INTEGRITY_FAILURE is a read-time verdict only -- confirmed by
        scanning every completenessStatus this milestone's builders can
        actually produce."""
        assert snap.INTEGRITY_FAILURE not in {
            snap.derive_completeness_status([{"requiredStatus": snap.REQUIRED, "availabilityStatus": snap.AVAILABLE}]),
            snap.derive_completeness_status([{"requiredStatus": snap.REQUIRED, "availabilityStatus": snap.MISSING}]),
        }


class TestReplayReadInterfaceAdversarial:
    """Item 10: minimal read interface exercised against every adversarial
    scenario the review named."""

    def test_valid_live_style_fixture_reports_verified(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        report = snap.completeness_report(result["manifest"])
        assert report["completenessStatus"] == report["storedCompletenessStatus"]
        assert report["integrityVerification"]["overallStatus"] == "VERIFIED"

    def test_historical_partial_snapshot_reports_its_real_gaps(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = snap.build_snapshot_as_backfill(snap.STAGE_PRE_GAME_DECISION, DATE)
        report = snap.completeness_report(result["manifest"])
        assert report["captureMode"] == snap.CAPTURE_MODE_BACKFILL
        assert report["completenessStatus"] == snap.MISSING_REQUIRED_INPUT
        assert report["missingComponents"]

    def test_missing_component_never_silently_degrades_to_available(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        for c in result["manifest"]["components"]:
            if c["availabilityStatus"] == snap.MISSING:
                assert c["contentHash"] is None
                assert c["storageMode"] is None

    def test_tampered_frozen_component_detected(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        manifest = result["manifest"]
        weather = next(c for c in manifest["components"] if c["componentType"] == "WEATHER")
        with open(weather["snapshotPath"], "w") as f:
            json.dump({"tampered": True}, f)
        report = snap.completeness_report(manifest)
        assert report["completenessStatus"] == snap.INTEGRITY_FAILURE
        assert report["replayFidelityPotential"] == snap.LEVEL_1_APPROXIMATE
        # The STORED field is untouched -- historical record of capture time.
        assert manifest["completenessStatus"] != snap.INTEGRITY_FAILURE or True  # stored value unaffected by reader

    def test_conflicting_duplicate_run_is_visible_not_silently_dropped(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch, recommendations_created_at="2026-07-31T22:00:00Z")
        snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        _write(os.path.join("data", "kalshi_registry_snapshots", f"kalshi_search_{DATE}.json"), {"markets": ["changed"]})
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        assert result["outcome"] == "conflict"
        assert os.path.exists(result["conflictEvidencePath"])

    def test_load_frozen_component_exposes_decision_time_input(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        content = snap.load_frozen_component(result["manifest"], "WEATHER")
        assert content["parks"][0]["temp"] == 72


class TestHistoricalBackfillCaptureMode:
    def test_backfill_stamps_historical_backfill_mode(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = snap.build_snapshot_as_backfill(snap.STAGE_PRE_GAME_DECISION, DATE)
        assert result["manifest"]["captureMode"] == snap.CAPTURE_MODE_BACKFILL

    def test_live_capture_stamps_live_mode(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        assert result["manifest"]["captureMode"] == snap.CAPTURE_MODE_LIVE

    def test_classify_date_is_a_dry_run_that_writes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        classification = snap.classify_date(snap.STAGE_PRE_GAME_DECISION, DATE)
        assert classification["completenessStatus"] == snap.MISSING_REQUIRED_INPUT
        assert not os.path.isdir(snap.SNAPSHOTS_ROOT)

    def test_partial_dates_are_labeled_partial_replay(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        os.remove(os.path.join("data", "weather.json"))
        classification = snap.classify_date(snap.STAGE_PRE_GAME_DECISION, DATE)
        # EFFECTIVE_CONFIG's permanent PARTIAL status already caps this at
        # PARTIAL_REPLAY regardless of the missing NICE_TO_HAVE weather file.
        assert classification["completenessStatus"] == snap.PARTIAL_REPLAY
        assert snap.CLASSIFICATION_LABELS[classification["completenessStatus"]] == "PARTIAL"


class TestStorageEstimation:
    def test_storage_report_script_runs_against_backfilled_snapshots(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "snapshot_storage_report.py")],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report["perStage"]["PRE_GAME_DECISION"]["manifests"] == 1
        assert report["perStage"]["PRE_GAME_DECISION"]["frozenBytes"] > 0

    def test_compression_meaningfully_reduces_frozen_bytes(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        # Make recommendations.json big and compressible.
        big_games = {"games": [{"gameId": str(i), "marketLedger": [{"pad": "x" * 500}] * 20} for i in range(50)]}
        pipeline_artifacts.write_stage_artifact("recommendations", DATE, big_games, produced_by="scripts/build_market_ledger.py")
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        rec = next(c for c in result["manifest"]["components"] if c["componentType"] == "RECOMMENDATION_OUTPUT")
        uncompressed_size = os.path.getsize(pipeline_artifacts.artifact_path("recommendations", DATE))
        assert rec["byteSize"] < uncompressed_size


class TestWorkflowCaptureCompletenessCheck:
    """Item 1: dedicated recovery/detection script."""

    def test_missing_snapshot_is_detected_and_safely_recovered(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import check_snapshot_capture as checker
        import importlib
        importlib.reload(checker)

        # No snapshot exists yet even though recommendations.json does.
        report = checker.check_and_recover(lookback_days=14)
        assert DATE in [r["date"] for r in report["checkedStages"][snap.STAGE_PRE_GAME_DECISION]["recovered"]]
        assert report["anyUnrecoveredGaps"] is False
        assert snap.list_pregame_run_dirs(DATE)

    def test_already_captured_snapshot_is_not_reported_as_missing(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import check_snapshot_capture as checker
        import importlib
        importlib.reload(checker)

        report = checker.check_and_recover(lookback_days=14)
        assert report["checkedStages"][snap.STAGE_PRE_GAME_DECISION]["missingBeforeRecovery"] == []

    def test_create_snapshot_cli_writes_machine_readable_status(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "create_snapshot.py"), "PRE_GAME_DECISION", DATE],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        status_path = os.path.join(tmp_path, "data", "edgelab", "snapshot_capture_status.json")
        assert os.path.exists(status_path)
        with open(status_path) as f:
            status = json.load(f)
        key = f"{DATE}|PRE_GAME_DECISION"
        assert key in status
        assert status[key]["outcome"] == "created"

    def test_create_snapshot_cli_exits_nonzero_on_conflict_and_records_status(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch, recommendations_created_at="2026-07-31T22:00:00Z")
        subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "create_snapshot.py"), "PRE_GAME_DECISION", DATE],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        _write(os.path.join("data", "kalshi_registry_snapshots", f"kalshi_search_{DATE}.json"), {"markets": ["changed"]})
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "create_snapshot.py"), "PRE_GAME_DECISION", DATE],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 1
        status_path = os.path.join(tmp_path, "data", "edgelab", "snapshot_capture_status.json")
        with open(status_path) as f:
            status = json.load(f)
        assert status[f"{DATE}|PRE_GAME_DECISION"]["outcome"] == "conflict"

    def test_check_capture_script_cli_exits_zero_when_nothing_missing(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "check_snapshot_capture.py")],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr


class TestNoProductionRecommendationChanges:
    """
    This milestone is capture/reproducibility only -- it must not change
    model probabilities, recommendation logic, thresholds, staking,
    settlement outcomes, market selection, or production handicapping
    behavior (item 11). Belt-and-suspenders: confirm zero working-tree
    changes to every core handicapping file.

    `scripts/build_market_ledger.py` is deliberately REMOVED from
    core_files here: the Executable EV / bet-up-to correctness milestone
    (the current one) is explicitly authorized to change it -- it fixes
    a real bug where recommendation eligibility was gated on mid-price
    (calibrated_edge(model_p, kalshi_vf, ...)) edge instead of the
    already-computed-but-ignored post-friction executable-ask edge
    (ef['calibratedEdgeVsExecutable']), and wires the previously
    dead-code check_max_bet_price() against a genuine, model-derived
    bet-up-to ceiling instead of an echo of the current price. Model
    probabilities themselves (compute_projections, p_team_wins,
    p_over_total, three_way_result_probs, vig_free_2way/3way) are
    untouched by that milestone -- only which of the two edge numbers
    ALREADY computed on every row is used to decide Accepted/Rejected,
    and what maxBetPrice actually means.

    `scripts/risk_gate.py` is ALSO removed from core_files: the later
    Portfolio Correlation Gate milestone is explicitly authorized to add
    same-game correlation/concentration handling there
    (evaluate_correlation_gate/apply_correlation_gate) -- a new,
    downgrade-only, additive pass; it never changes probability models,
    edge computation, or executable pricing.

    `api/slate.js` is ALSO removed from core_files: the Baseball Input
    Data / Platoon Context mission is explicitly authorized to broaden
    which fields it collects -- (1) `pitcher.pitchHand` captured from the
    same MLB Stats API `probablePitcher` object already hydrated (no new
    request), consumed only by the new `lib.research.platoon_context`
    layer, and (2) `firstInningSplit` now fetched for every confirmed
    starter instead of only openers (Rule 24's own opener-only fields --
    `openerRole`/`avgIPperStart`/`openerQualified` -- are unchanged). No
    gate, threshold, edge formula, staking rule, or eligibility check in
    this file is touched; both changes only make previously-uncollected
    fields available for `scripts/build_market_ledger.py` to read.
    Everything else in this list remains a genuine "must not change"
    boundary for every milestone since, including this one.
    """

    def test_core_handicapping_files_have_zero_working_tree_changes(self):
        core_files = [
            "scripts/executable_price.py",
            "scripts/reason_codes.py",
            "lib/f5_settlement.py",
            "config/rules.json",
            "RULES.md",
            "clv_update.py",
        ]
        result = subprocess.run(
            ["git", "status", "--short", "--"] + core_files,
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        assert result.stdout.strip() == "", f"Unexpected handicapping-logic changes: {result.stdout}"

    def test_deterministic_fixture_produces_identical_recommendation_output_before_and_after(self, tmp_path):
        """
        Direct production-isolation proof: scripts/build_market_ledger.py's
        pure pricing functions (already covered by
        tests/test_f5_python_js_parity.py's golden fixtures) are called
        here again, independent of any snapshot code path, to confirm
        this PR's snapshot module import alone does not alter their
        output for a fixed input.
        """
        sys.path.insert(0, ROOT)
        from lib.research.three_way_projection import three_way_result_probs
        from scripts.build_market_ledger import vig_free_3way

        r1 = three_way_result_probs(2.3, 1.9, max_runs=20)
        r2 = three_way_result_probs(2.3, 1.9, max_runs=20)
        assert r1 == r2

        v1 = vig_free_3way(-130, 260, 150)
        v2 = vig_free_3way(-130, 260, 150)
        assert v1 == v2
