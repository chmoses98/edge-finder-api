#!/usr/bin/env python3
"""
tests/test_pipeline_artifacts.py
==================================
Unit tests for lib/pipeline_artifacts.py — the Phase 3 immutable-pipeline
artifact primitive. See docs/IMMUTABLE_PIPELINE.md.

Covers path construction and path-traversal rejection, envelope shape
and metadata, atomic-write behavior (temp file + os.replace, no stray
temp files, no partial writes observable at the real path), and the
full failure-isolation matrix requested in the Phase 3 pre-merge review:
directory-creation failure, write failure, serialization failure,
invalid dates, malformed pre-existing artifacts, interrupted/partial
write simulation, repeated writes to the same stage/date, and writes to
different dates/stages.
"""

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import pipeline_artifacts as pa  # noqa: E402


# ── Path construction and traversal rejection ────────────────────────────────

class TestArtifactPath:

    def test_path_includes_stage_and_date(self):
        path = pa.artifact_path("normalized_slate", "2026-07-27")
        assert path == os.path.join("data", "pipeline", "2026-07-27", "normalized_slate.json")

    def test_missing_stage_raises(self):
        with pytest.raises(ValueError):
            pa.artifact_path("", "2026-07-27")

    def test_missing_date_raises(self):
        with pytest.raises(ValueError):
            pa.artifact_path("normalized_slate", "")

    def test_different_stages_same_date_do_not_collide(self):
        p1 = pa.artifact_path("normalized_slate", "2026-07-27")
        p2 = pa.artifact_path("recommendations", "2026-07-27")
        assert p1 != p2

    def test_same_stage_different_dates_do_not_collide(self):
        p1 = pa.artifact_path("normalized_slate", "2026-07-27")
        p2 = pa.artifact_path("normalized_slate", "2026-07-28")
        assert p1 != p2

    @pytest.mark.parametrize("bad_stage", [
        "../../etc/passwd", "..", "a/b", "a\\b", "a/../b", "./x", "a.b",
    ])
    def test_path_traversal_in_stage_is_rejected(self, bad_stage):
        with pytest.raises(ValueError):
            pa.artifact_path(bad_stage, "2026-07-27")

    @pytest.mark.parametrize("bad_date", [
        "../../2026-07-27", "..", "2026-07-27/../../x", "2026/07/27", "2026-07-27\\x",
    ])
    def test_path_traversal_in_date_is_rejected(self, bad_date):
        with pytest.raises(ValueError):
            pa.artifact_path("normalized_slate", bad_date)

    def test_ordinary_date_with_hyphens_still_works(self):
        """Regression guard: date validation must not mangle a normal YYYY-MM-DD date."""
        path = pa.artifact_path("normalized_slate", "2026-07-27")
        assert "2026-07-27" in path
        assert "20260727" not in path


# ── Write/read round trip, envelope shape, metadata ──────────────────────────

class TestWriteReadRoundTrip:

    def test_write_then_read_round_trips(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        data = {"date": "2026-07-27", "games": [{"away": {"abbr": "NYY"}}]}
        path = pa.write_stage_artifact("normalized_slate", "2026-07-27", data)
        assert os.path.exists(path)
        result = pa.read_stage_artifact("normalized_slate", "2026-07-27")
        assert result["data"] == data

    def test_envelope_has_required_metadata(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        pa.write_stage_artifact("normalized_slate", "2026-07-27", {"x": 1}, produced_by="scripts/enrich_data.py")
        result = pa.read_stage_artifact("normalized_slate", "2026-07-27")
        meta = result["meta"]
        assert meta["stage"] == "normalized_slate"
        assert meta["slateDate"] == "2026-07-27"
        assert meta["schemaVersion"] == "1.0"
        assert meta["producedBy"] == "scripts/enrich_data.py"
        assert "createdAt" in meta and meta["createdAt"].endswith("Z")

    def test_produced_by_defaults_to_stage_name_if_omitted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        pa.write_stage_artifact("normalized_slate", "2026-07-27", {"x": 1})
        result = pa.read_stage_artifact("normalized_slate", "2026-07-27")
        assert result["meta"]["producedBy"] == "normalized_slate"

    def test_write_creates_date_directory(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        pa.write_stage_artifact("recommendations", "2026-07-27", {"x": 1})
        assert os.path.isdir(str(tmp_path / "pipeline" / "2026-07-27"))

    def test_stage_artifact_exists_false_before_write(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        assert pa.stage_artifact_exists("normalized_slate", "2026-07-27") is False

    def test_stage_artifact_exists_true_after_write(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        pa.write_stage_artifact("normalized_slate", "2026-07-27", {"x": 1})
        assert pa.stage_artifact_exists("normalized_slate", "2026-07-27") is True

    def test_read_missing_artifact_raises_file_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        with pytest.raises(FileNotFoundError):
            pa.read_stage_artifact("normalized_slate", "2026-07-27")

    def test_different_stages_do_not_overwrite_each_other(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        pa.write_stage_artifact("normalized_slate", "2026-07-27", {"stage": "normalized"})
        pa.write_stage_artifact("recommendations", "2026-07-27", {"stage": "recommendations"})
        assert pa.read_stage_artifact("normalized_slate", "2026-07-27")["data"] == {"stage": "normalized"}
        assert pa.read_stage_artifact("recommendations", "2026-07-27")["data"] == {"stage": "recommendations"}

    def test_different_dates_do_not_overwrite_each_other(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        pa.write_stage_artifact("normalized_slate", "2026-07-27", {"run": "day1"})
        pa.write_stage_artifact("normalized_slate", "2026-07-28", {"run": "day2"})
        assert pa.read_stage_artifact("normalized_slate", "2026-07-27")["data"] == {"run": "day1"}
        assert pa.read_stage_artifact("normalized_slate", "2026-07-28")["data"] == {"run": "day2"}

    def test_rerun_overwrites_same_stage_and_date(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        pa.write_stage_artifact("normalized_slate", "2026-07-27", {"run": 1})
        pa.write_stage_artifact("normalized_slate", "2026-07-27", {"run": 2})
        assert pa.read_stage_artifact("normalized_slate", "2026-07-27")["data"] == {"run": 2}

    def test_written_json_is_valid_deterministic_and_indented(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        path1 = pa.write_stage_artifact("normalized_slate", "2026-07-27", {"b": 2, "a": 1})
        with open(path1) as f:
            content1 = f.read()
        assert json.loads(content1)["data"] == {"b": 2, "a": 1}
        assert "\n" in content1  # indent=2 implies multi-line output

        # sort_keys=True: rebuilding from a dict with different insertion
        # order but equivalent content produces byte-identical output
        # (modulo the createdAt timestamp, which is expected to differ).
        path2 = pa.write_stage_artifact("recommendations", "2026-07-27", {"a": 1, "b": 2})
        with open(path2) as f:
            content2 = f.read()
        # Both "data" blocks serialize identically regardless of input order.
        assert '"a": 1' in content1 and '"b": 2' in content1
        assert content1.index('"a"') < content1.index('"b"')  # sorted alphabetically
        assert content2.index('"a"') < content2.index('"b"')

    def test_data_argument_is_not_mutated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        original = {"games": [{"a": 1}]}
        snapshot = json.loads(json.dumps(original))
        pa.write_stage_artifact("normalized_slate", "2026-07-27", original)
        assert original == snapshot, "write_stage_artifact must never mutate its data argument"

    def test_status_defaults_to_canonical(self, tmp_path, monkeypatch):
        """
        Phase 4 addition. Existing callers that never pass `status` are
        implicitly asserting their payload IS the intended schema for
        that stage — this must keep being true without any caller change.
        """
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        pa.write_stage_artifact("normalized_slate", "2026-07-27", {"x": 1})
        assert pa.read_stage_artifact("normalized_slate", "2026-07-27")["meta"]["status"] == "canonical"

    def test_status_can_be_marked_transitional(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        pa.write_stage_artifact("recommendations", "2026-07-27", {"x": 1}, status="transitional")
        assert pa.read_stage_artifact("recommendations", "2026-07-27")["meta"]["status"] == "transitional"

    def test_source_stage_defaults_to_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        pa.write_stage_artifact("normalized_slate", "2026-07-27", {"x": 1})
        assert pa.read_stage_artifact("normalized_slate", "2026-07-27")["meta"]["sourceStage"] is None

    def test_source_stage_can_be_set(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        pa.write_stage_artifact("projections", "2026-07-27", {"x": 1}, source_stage="normalized_slate")
        assert pa.read_stage_artifact("projections", "2026-07-27")["meta"]["sourceStage"] == "normalized_slate"

    def test_invalid_status_value_raises_before_any_filesystem_write(self, tmp_path, monkeypatch):
        """
        Pre-merge hardening addition (PR #5 review, Section F). status is
        documented as a closed two-value enum ("canonical"/"transitional")
        -- a typo must fail loudly rather than silently writing metadata
        no reader recognizes, and (like an invalid stage/date) must raise
        before touching the filesystem at all.
        """
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        with pytest.raises(ValueError):
            pa.write_stage_artifact("normalized_slate", "2026-07-27", {"x": 1}, status="cannonical")
        assert not os.path.exists(str(tmp_path / "pipeline"))

    def test_valid_status_values_are_accepted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        pa.write_stage_artifact("normalized_slate", "2026-07-27", {"x": 1}, status="canonical")
        pa.write_stage_artifact("recommendations", "2026-07-27", {"x": 1}, status="transitional")
        assert pa.read_stage_artifact("normalized_slate", "2026-07-27")["meta"]["status"] == "canonical"
        assert pa.read_stage_artifact("recommendations", "2026-07-27")["meta"]["status"] == "transitional"


# ── Atomicity ─────────────────────────────────────────────────────────────────

class TestAtomicWrite:

    def test_no_stray_temp_files_after_successful_write(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        path = pa.write_stage_artifact("normalized_slate", "2026-07-27", {"x": 1})
        siblings = os.listdir(os.path.dirname(path))
        assert siblings == ["normalized_slate.json"], (
            f"no .tmp files should remain after a successful write, found: {siblings}"
        )

    def test_interrupted_write_leaves_no_file_at_real_path(self, tmp_path, monkeypatch):
        """
        Simulate a crash mid-serialization (e.g. an unserializable object
        deep in the payload) on the FIRST write ever for this stage/date.
        The real artifact path must not exist afterward — readers must
        never see a partially-written file.
        """
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))

        class Unserializable:
            pass

        with pytest.raises(TypeError):
            pa.write_stage_artifact("normalized_slate", "2026-07-27", {"bad": Unserializable()})

        real_path = pa.artifact_path("normalized_slate", "2026-07-27")
        assert not os.path.exists(real_path), (
            "an interrupted write must never leave a partial file at the real artifact path"
        )
        # and no leftover temp file either
        date_dir = os.path.dirname(real_path)
        assert not os.path.isdir(date_dir) or os.listdir(date_dir) == [], (
            "a failed write must clean up its temp file"
        )

    def test_interrupted_rerun_preserves_previous_valid_artifact(self, tmp_path, monkeypatch):
        """
        Same as above, but this time a valid artifact already exists from
        a prior successful run. The interrupted rerun's failure must leave
        the PREVIOUS valid content in place, untouched — never a partial
        overwrite.
        """
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        pa.write_stage_artifact("normalized_slate", "2026-07-27", {"run": "good"})

        class Unserializable:
            pass

        with pytest.raises(TypeError):
            pa.write_stage_artifact("normalized_slate", "2026-07-27", {"run": "bad", "x": Unserializable()})

        result = pa.read_stage_artifact("normalized_slate", "2026-07-27")
        assert result["data"] == {"run": "good"}, (
            "a failed rerun must not corrupt or replace the previously valid artifact"
        )

    def test_write_uses_os_replace_not_direct_write(self, tmp_path, monkeypatch):
        """
        Structural check that the implementation actually goes through a
        temp-file + os.replace sequence, not a direct open(path, 'w') —
        the real safety property is proven by the two tests above, this
        just documents the mechanism is what it claims to be.
        """
        import inspect
        src = inspect.getsource(pa.write_stage_artifact)
        assert "os.replace" in src
        assert "tempfile" in src

    def test_rename_failure_cleans_up_temp_file_and_leaves_no_real_artifact(self, tmp_path, monkeypatch):
        """
        Pre-merge hardening addition (PR #5 review, Section F/G item 4:
        "rename failure"). Distinct from test_write_failure_cleans_up_temp_file
        (which simulates os.fdopen failing, i.e. never reaching a
        successful temp-file write at all) -- this simulates the temp
        file being written successfully and then os.replace() itself
        raising (e.g. cross-device rename, permission denied). The
        except BaseException handler must still clean up the temp file
        and re-raise; no real artifact may be created.
        """
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        os.makedirs(str(tmp_path / "pipeline" / "2026-07-27"))

        def _boom_replace(*a, **kw):
            raise OSError("simulated rename failure (e.g. cross-device link)")

        monkeypatch.setattr(pa.os, "replace", _boom_replace)
        with pytest.raises(OSError):
            pa.write_stage_artifact("normalized_slate", "2026-07-27", {"x": 1})

        remaining = os.listdir(str(tmp_path / "pipeline" / "2026-07-27"))
        assert remaining == [], f"temp file must be cleaned up on rename failure, found: {remaining}"
        assert not pa.stage_artifact_exists("normalized_slate", "2026-07-27")

    def test_rename_failure_preserves_previous_valid_artifact(self, tmp_path, monkeypatch):
        """Same as above, but a valid artifact already exists -- the failed rename must not touch it."""
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        pa.write_stage_artifact("normalized_slate", "2026-07-27", {"run": "good"})

        def _boom_replace(*a, **kw):
            raise OSError("simulated rename failure")

        monkeypatch.setattr(pa.os, "replace", _boom_replace)
        with pytest.raises(OSError):
            pa.write_stage_artifact("normalized_slate", "2026-07-27", {"run": "bad"})

        result = pa.read_stage_artifact("normalized_slate", "2026-07-27")
        assert result["data"] == {"run": "good"}


# ── Failure isolation matrix ─────────────────────────────────────────────────

class TestFailureIsolation:

    def test_directory_creation_failure_raises_and_leaves_no_partial_state(self, tmp_path, monkeypatch):
        """
        If os.makedirs fails (e.g. permissions, disk full), the exception
        must propagate (callers wrap this in try/except themselves — see
        tests/test_immutable_pipeline_snapshots.py) and no file should be
        left behind.
        """
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))

        def _boom(*a, **kw):
            raise OSError("simulated permission denied")

        monkeypatch.setattr(pa.os, "makedirs", _boom)
        with pytest.raises(OSError):
            pa.write_stage_artifact("normalized_slate", "2026-07-27", {"x": 1})
        assert not os.path.exists(str(tmp_path / "pipeline"))

    def test_write_failure_cleans_up_temp_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        os.makedirs(str(tmp_path / "pipeline" / "2026-07-27"))

        real_fdopen = pa.os.fdopen

        def _boom_fdopen(*a, **kw):
            raise OSError("simulated disk write error")

        monkeypatch.setattr(pa.os, "fdopen", _boom_fdopen)
        with pytest.raises(OSError):
            pa.write_stage_artifact("normalized_slate", "2026-07-27", {"x": 1})

        remaining = os.listdir(str(tmp_path / "pipeline" / "2026-07-27"))
        assert remaining == [], f"temp file must be cleaned up on write failure, found: {remaining}"

    def test_serialization_failure_does_not_create_real_artifact(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        with pytest.raises(TypeError):
            pa.write_stage_artifact("normalized_slate", "2026-07-27", {"bad": object()})
        assert not pa.stage_artifact_exists("normalized_slate", "2026-07-27")

    def test_invalid_slate_date_raises_before_any_filesystem_write(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        with pytest.raises(ValueError):
            pa.write_stage_artifact("normalized_slate", "", {"x": 1})
        assert not os.path.exists(str(tmp_path / "pipeline"))

    def test_malformed_existing_artifact_is_cleanly_overwritten(self, tmp_path, monkeypatch):
        """
        A garbage/corrupt file already sitting at the target path (e.g.
        from a previous crash before atomic writes existed, or external
        tampering) must not confuse a subsequent write — the atomic
        rename replaces it cleanly.
        """
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        real_path = pa.artifact_path("normalized_slate", "2026-07-27")
        os.makedirs(os.path.dirname(real_path))
        with open(real_path, "w") as f:
            f.write("{not valid json at all")

        pa.write_stage_artifact("normalized_slate", "2026-07-27", {"good": True})

        result = pa.read_stage_artifact("normalized_slate", "2026-07-27")
        assert result["data"] == {"good": True}

    def test_read_distinguishes_missing_from_malformed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))

        with pytest.raises(FileNotFoundError):
            pa.read_stage_artifact("normalized_slate", "2026-07-27")

        real_path = pa.artifact_path("normalized_slate", "2026-07-27")
        os.makedirs(os.path.dirname(real_path))
        with open(real_path, "w") as f:
            f.write("{not valid json")

        with pytest.raises(json.JSONDecodeError):
            pa.read_stage_artifact("normalized_slate", "2026-07-27")

    def test_repeated_write_same_stage_and_date(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        pa.write_stage_artifact("normalized_slate", "2026-07-27", {"n": 1})
        pa.write_stage_artifact("normalized_slate", "2026-07-27", {"n": 2})
        pa.write_stage_artifact("normalized_slate", "2026-07-27", {"n": 3})
        assert pa.read_stage_artifact("normalized_slate", "2026-07-27")["data"] == {"n": 3}

    def test_writes_to_different_dates_are_isolated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        pa.write_stage_artifact("normalized_slate", "2026-07-27", {"d": "27"})
        pa.write_stage_artifact("normalized_slate", "2026-07-28", {"d": "28"})
        assert pa.read_stage_artifact("normalized_slate", "2026-07-27")["data"] == {"d": "27"}
        assert pa.read_stage_artifact("normalized_slate", "2026-07-28")["data"] == {"d": "28"}

    def test_writes_to_different_stages_same_date_are_isolated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        pa.write_stage_artifact("normalized_slate", "2026-07-27", {"s": "normalized"})
        pa.write_stage_artifact("recommendations", "2026-07-27", {"s": "recommendations"})
        assert pa.read_stage_artifact("normalized_slate", "2026-07-27")["data"] == {"s": "normalized"}
        assert pa.read_stage_artifact("recommendations", "2026-07-27")["data"] == {"s": "recommendations"}
