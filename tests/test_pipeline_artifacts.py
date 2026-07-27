#!/usr/bin/env python3
"""
tests/test_pipeline_artifacts.py
==================================
Unit tests for lib/pipeline_artifacts.py — the Phase 3 immutable-pipeline
artifact primitive. See docs/IMMUTABLE_PIPELINE.md.
"""

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import pipeline_artifacts as pa  # noqa: E402


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


class TestWriteReadRoundTrip:

    def test_write_then_read_round_trips(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        data = {"date": "2026-07-27", "games": [{"away": {"abbr": "NYY"}}]}
        path = pa.write_stage_artifact("normalized_slate", "2026-07-27", data)
        assert os.path.exists(path)
        result = pa.read_stage_artifact("normalized_slate", "2026-07-27")
        assert result == data

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
        assert pa.read_stage_artifact("normalized_slate", "2026-07-27") == {"stage": "normalized"}
        assert pa.read_stage_artifact("recommendations", "2026-07-27") == {"stage": "recommendations"}

    def test_rerun_overwrites_same_stage_and_date(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        pa.write_stage_artifact("normalized_slate", "2026-07-27", {"run": 1})
        pa.write_stage_artifact("normalized_slate", "2026-07-27", {"run": 2})
        assert pa.read_stage_artifact("normalized_slate", "2026-07-27") == {"run": 2}

    def test_written_json_is_valid_and_indented(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "pipeline"))
        path = pa.write_stage_artifact("normalized_slate", "2026-07-27", {"a": 1})
        with open(path) as f:
            content = f.read()
        assert json.loads(content) == {"a": 1}
        assert "\n" in content  # indent=2 implies multi-line output
