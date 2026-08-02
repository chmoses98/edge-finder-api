#!/usr/bin/env python3
"""
tests/edgelab/test_snapshot.py
=================================
Historical Capture Completeness and Immutable Snapshot Foundation
milestone: coverage for lib/edgelab/snapshot.py + scripts/create_snapshot.py
+ scripts/backfill_snapshots.py.

Every test runs inside an isolated tmp_path (monkeypatch.chdir), never
against the real repository's data/ tree -- no test here reads or writes
the actual production data/pipeline, data/slates, data/weather.json, etc.
"""
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


def _wire_full_pregame_fixture(tmp_path, monkeypatch):
    """A minimal but complete set of real-shaped inputs for PRE_GAME_DECISION."""
    monkeypatch.chdir(tmp_path)

    pipeline_artifacts.write_stage_artifact(
        "recommendations", DATE, {"games": [{"gameId": "1", "marketLedger": []}]},
        produced_by="scripts/build_market_ledger.py",
    )
    pipeline_artifacts.write_stage_artifact("projections", DATE, {"games": []}, produced_by="scripts/build_market_ledger.py")
    pipeline_artifacts.write_stage_artifact("normalized_slate", DATE, {"games": []}, produced_by="scripts/enrich_data.py")
    pipeline_artifacts.write_stage_artifact("execution", DATE, {"rulesVersion": "1.0", "candidates": []}, produced_by="scripts/risk_gate.py")
    pipeline_artifacts.write_stage_artifact("validation", DATE, {"errors": []}, produced_by="scripts/validate_slate_final.py")
    pipeline_artifacts.write_stage_artifact("protection", DATE, {"runType": "OFFICIAL_PREGAME"}, produced_by="scripts/protect_slate.py")

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


class TestWriteOnceImmutability:
    def test_rerun_with_unchanged_inputs_is_noop_verified_not_rewritten(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        first = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        assert first["outcome"] == "created"
        path = snap.manifest_path(snap.STAGE_PRE_GAME_DECISION, DATE)
        mtime_before = os.path.getmtime(path)

        second = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        assert second["outcome"] == "noop_verified"
        assert os.path.getmtime(path) == mtime_before
        assert second["manifest"]["manifestHash"] == first["manifest"]["manifestHash"]

    def test_conflicting_rerun_preserves_existing_and_writes_diagnostics(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        first = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        assert first["outcome"] == "created"

        # Simulate a genuine change to a REFERENCED_IMMUTABLE source between captures.
        _write(os.path.join("data", "kalshi_registry_snapshots", f"kalshi_search_{DATE}.json"), {"markets": ["changed"]})

        second = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        assert second["outcome"] == "conflict"
        assert second["manifest"]["manifestHash"] == first["manifest"]["manifestHash"]  # existing preserved untouched
        assert os.path.isdir(second["conflictEvidencePath"])
        assert os.path.exists(os.path.join(second["conflictEvidencePath"], "candidate_manifest.json"))

        # The existing manifest.json on disk is byte-for-byte what it was.
        reloaded = snap.load_manifest(snap.STAGE_PRE_GAME_DECISION, DATE)
        assert reloaded["manifestHash"] == first["manifest"]["manifestHash"]

    def test_atomic_write_leaves_no_temp_files_behind(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        snapshot_dir = snap.snapshot_dir(snap.STAGE_PRE_GAME_DECISION, DATE)
        for root, _, files in os.walk(snapshot_dir):
            for fn in files:
                assert not fn.endswith(".tmp"), f"leftover temp file: {os.path.join(root, fn)}"


class TestReferencedImmutableComponent:
    def test_referenced_component_hashes_source_without_copying(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        manifest = result["manifest"]
        rec_output = next(c for c in manifest["components"] if c["componentType"] == "RECOMMENDATION_OUTPUT")
        assert rec_output["storageMode"] == snap.STORAGE_REFERENCED_IMMUTABLE
        assert rec_output["snapshotPath"] is None
        assert rec_output["contentHash"] == snap.sha256_file(rec_output["sourcePath"])
        # No bulky duplicate exists under frozen/ for this component.
        frozen_dir = snap.frozen_dir(snap.STAGE_PRE_GAME_DECISION, DATE)
        if os.path.isdir(frozen_dir):
            assert "recommendations.json" not in os.listdir(frozen_dir)

    def test_referenced_immutable_violation_detected_at_verify_time(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        manifest = result["manifest"]
        rec_path = next(c for c in manifest["components"] if c["componentType"] == "RECOMMENDATION_OUTPUT")["sourcePath"]
        # Mutate the "believed immutable" source after capture.
        _write(rec_path, {"data": {"games": [{"tampered": True}]}})
        verification = snap.verify_snapshot(manifest)
        assert verification["overallStatus"] == "INTEGRITY_FAILURE"


class TestFrozenMutableComponent:
    def test_weather_and_bullpen_are_frozen_copies(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        manifest = result["manifest"]
        for component_type in ("WEATHER", "BULLPEN_STATE", "EFFECTIVE_CONFIG"):
            component = next(c for c in manifest["components"] if c["componentType"] == component_type)
            assert component["storageMode"] == snap.STORAGE_FROZEN_COPY
            assert os.path.exists(component["snapshotPath"])
            assert snap.sha256_file(component["snapshotPath"]) == component["contentHash"]

    def test_overwritten_source_preserved_after_live_file_changes(self, tmp_path, monkeypatch):
        """The exact defining property of FROZEN_COPY: after the snapshot is
        taken, the live source can be overwritten (as production does every
        run) and the frozen copy must still reflect what was true at capture
        time."""
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        manifest = result["manifest"]
        weather_component = next(c for c in manifest["components"] if c["componentType"] == "WEATHER")
        with open(weather_component["snapshotPath"]) as f:
            frozen_content = json.load(f)
        assert frozen_content["parks"][0]["temp"] == 72

        # Production overwrites data/weather.json the next run.
        _write(os.path.join("data", "weather.json"), {"parks": [{"team": "SD", "temp": 95}]})

        # The frozen copy is untouched.
        with open(weather_component["snapshotPath"]) as f:
            still_frozen = json.load(f)
        assert still_frozen["parks"][0]["temp"] == 72


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

        # The pregame manifest file on disk is unchanged.
        reloaded_pregame = snap.load_manifest(snap.STAGE_PRE_GAME_DECISION, DATE)
        assert reloaded_pregame["manifestHash"] == pregame_hash_before
        assert reloaded_pregame["linkedSnapshotIds"] == []


class TestEffectiveConfigCapture:
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
        # Explicitly does NOT claim to be the complete production rule set.
        assert "NOT the complete production rule set" in record["note"]

    def test_effective_config_is_deterministic_across_rebuilds(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        record1 = snap.capture_effective_config(DATE, "abc123")
        record2 = snap.capture_effective_config(DATE, "abc123")
        assert snap.canonical_json_bytes(record1) == snap.canonical_json_bytes(record2)


class TestContentAndManifestHashVerification:
    def test_verify_snapshot_reports_verified_for_untouched_snapshot(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        verification = snap.verify_snapshot(result["manifest"])
        assert verification["manifestHashValid"] is True
        assert verification["overallStatus"] == "VERIFIED"

    def test_verify_snapshot_detects_frozen_copy_tampering(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        manifest = result["manifest"]
        weather_component = next(c for c in manifest["components"] if c["componentType"] == "WEATHER")
        with open(weather_component["snapshotPath"], "w") as f:
            json.dump({"tampered": True}, f)
        verification = snap.verify_snapshot(manifest)
        assert verification["overallStatus"] == "INTEGRITY_FAILURE"

    def test_manifest_hash_changes_if_manifest_json_is_hand_edited(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        path = snap.manifest_path(snap.STAGE_PRE_GAME_DECISION, DATE)
        with open(path) as f:
            manifest = json.load(f)
        manifest["completenessStatus"] = "COMPLETE_FOR_PRODUCTION_REPLAY"  # tamper, hash now stale
        with open(path, "w") as f:
            json.dump(manifest, f)
        reloaded = snap.load_manifest(snap.STAGE_PRE_GAME_DECISION, DATE)
        assert snap.compute_manifest_hash(reloaded) != reloaded["manifestHash"]


class TestMissingRequiredComponentAndLimitationReasons:
    def test_missing_required_pipeline_artifacts_yields_missing_required_input(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        manifest = result["manifest"]
        assert manifest["completenessStatus"] == snap.MISSING_REQUIRED_INPUT
        assert manifest["replayFidelityPotential"] == snap.LEVEL_1_APPROXIMATE
        assert set(manifest["missingComponents"]) >= {"RAW_PROJECTIONS", "RECOMMENDATION_OUTPUT", "MARKET_UNIVERSE"}

    def test_never_null_limitation_reason_for_a_missing_component(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        for component in result["manifest"]["components"]:
            if component["availabilityStatus"] in (snap.MISSING, snap.NOT_APPLICABLE_FOR_STAGE):
                assert component["limitationReason"] is not None

    def test_quarantined_slate_gets_specific_limitation_reason(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs(os.path.join("data", "slates", DATE), exist_ok=True)
        _write(os.path.join("data", "slates", DATE, "rejected_contaminated_20260731T223807Z.json"), {"contaminated": True})
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        slate_component = next(c for c in result["manifest"]["components"] if c["componentType"] == "PRODUCTION_SLATE_INPUT")
        assert slate_component["limitationReason"] == snap.REASON_SOURCE_QUARANTINED


class TestHistoricalPartialSnapshotClassification:
    def test_classify_date_is_a_dry_run_that_writes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        classification = snap.classify_date(snap.STAGE_PRE_GAME_DECISION, DATE)
        assert classification["completenessStatus"] == snap.MISSING_REQUIRED_INPUT
        assert not os.path.isdir(snap.SNAPSHOTS_ROOT)

    def test_partial_dates_are_labeled_approximate_only_not_fully_capable(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        # Remove one NICE_TO_HAVE source so the date is APPROXIMATE_ONLY, not COMPLETE.
        os.remove(os.path.join("data", "weather.json"))
        classification = snap.classify_date(snap.STAGE_PRE_GAME_DECISION, DATE)
        assert classification["completenessStatus"] == snap.APPROXIMATE_ONLY
        assert snap.CLASSIFICATION_LABELS[classification["completenessStatus"]] == "APPROXIMATE_ONLY"


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


class TestWorkflowIntegration:
    def test_create_snapshot_cli_exits_zero_on_created(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "create_snapshot.py"), "PRE_GAME_DECISION", DATE],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        summary = json.loads(result.stdout)
        assert summary["outcome"] == "created"

    def test_create_snapshot_cli_exits_nonzero_on_conflict(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch)
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


class TestNoProductionRecommendationChanges:
    """
    This milestone is capture/reproducibility only -- it must not change
    model probabilities, recommendation logic, thresholds, staking,
    settlement outcomes, market selection, or production handicapping
    behavior. Belt-and-suspenders: confirm zero working-tree changes to
    every core handicapping file.
    """

    def test_core_handicapping_files_have_zero_working_tree_changes(self):
        core_files = [
            "scripts/build_market_ledger.py",
            "scripts/executable_price.py",
            "scripts/reason_codes.py",
            "scripts/risk_gate.py",
            "lib/f5_settlement.py",
            "config/rules.json",
            "RULES.md",
            "api/slate.js",
            "clv_update.py",
        ]
        result = subprocess.run(
            ["git", "status", "--short", "--"] + core_files,
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        assert result.stdout.strip() == "", f"Unexpected handicapping-logic changes: {result.stdout}"
