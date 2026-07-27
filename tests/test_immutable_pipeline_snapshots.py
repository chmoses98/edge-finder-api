#!/usr/bin/env python3
"""
tests/test_immutable_pipeline_snapshots.py
=============================================
Integration tests for the two Phase 3 additive immutable-artifact
snapshot points (see docs/IMMUTABLE_PIPELINE.md):

  - scripts/enrich_data.py      -> data/pipeline/<date>/normalized_slate.json
  - scripts/build_market_ledger.py -> data/pipeline/<date>/recommendations.json

Both tests prove two things:
  1. The new artifact is written with content matching what the script
     also wrote (unchanged) to the legacy data/slate.json path.
  2. If the new artifact-writing code fails for any reason, the primary
     data/slate.json write is completely unaffected — the try/except
     wrapper around the new code must never be able to break the
     existing pipeline stage that adopts it.

Both tests run against isolated temporary directories/paths — neither
ever reads or writes the real repository's data/ directory.
"""

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import pipeline_artifacts as pa  # noqa: E402


class TestEnrichDataNormalizedSlateSnapshot:
    """
    enrich_data.py is a top-level script (executes at import/run time, no
    main()), so it is exercised as a real subprocess with cwd redirected
    to an isolated tmp_path — this never touches the real repo's data/.
    """

    def _write_fixtures(self, tmp_path, date="2026-07-27", games=None):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "teamstats.json").write_text(json.dumps({"teams": {}}))
        (data_dir / "slate.json").write_text(json.dumps({"date": date, "games": games or []}))
        (data_dir / "bullpen.json").write_text(json.dumps({"bullpens": {}}))
        return data_dir

    def test_normalized_slate_artifact_matches_legacy_slate_json(self, tmp_path):
        data_dir = self._write_fixtures(tmp_path)

        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "enrich_data.py")],
            cwd=str(tmp_path), capture_output=True, text=True,
        )
        assert result.returncode == 0, f"enrich_data.py failed: {result.stderr}"

        with open(data_dir / "slate.json") as f:
            legacy_slate = json.load(f)

        artifact_path = tmp_path / "data" / "pipeline" / "2026-07-27" / "normalized_slate.json"
        assert artifact_path.exists(), "normalized_slate.json artifact was not written"
        with open(artifact_path) as f:
            artifact = json.load(f)

        assert artifact == legacy_slate, (
            "normalized_slate.json must match data/slate.json exactly — "
            "the snapshot must not diverge from the legacy write"
        )

    def test_missing_date_does_not_break_legacy_write(self, tmp_path):
        """
        Safety property: write_stage_artifact() raises ValueError when
        date is falsy. enrich_data.py must catch this and still complete
        its primary data/slate.json write successfully.
        """
        data_dir = self._write_fixtures(tmp_path, date="")  # no date -> artifact write will raise

        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "enrich_data.py")],
            cwd=str(tmp_path), capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"enrich_data.py must complete successfully even if the new "
            f"pipeline-artifact write fails. stderr: {result.stderr}"
        )
        assert "WARNING: could not write normalized_slate pipeline artifact" in result.stdout

        with open(data_dir / "slate.json") as f:
            legacy_slate = json.load(f)
        assert legacy_slate["date"] == ""
        assert not (tmp_path / "data" / "pipeline").exists(), (
            "no pipeline/ directory should be created when the artifact write fails"
        )


class TestBuildMarketLedgerRecommendationsSnapshot:
    """
    build_market_ledger.py has a real main(), so it's imported in-process
    and exercised directly, with __file__ monkeypatched to an isolated
    tmp_path so its data/slate.json read/write never touches the real
    repo, and pipeline_artifacts.PIPELINE_ROOT monkeypatched the same way
    test_pipeline_artifacts.py does.
    """

    def _write_fixtures(self, tmp_path, date="2026-07-27"):
        # The monkeypatched __file__ below points at tmp_path/scripts/... —
        # os.path.join(os.path.dirname(__file__), '..', 'data', 'slate.json')
        # requires the OS to actually traverse tmp_path/scripts/.. , so that
        # directory must exist even though no file is placed in it.
        (tmp_path / "scripts").mkdir()
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        # A single quarantined (excludedFromSlate) game is enough to exercise
        # main()'s full write path without needing a realistic pricing
        # fixture for evaluate_game() — that logic is untouched by this
        # change and is already covered by other test files.
        slate = {
            "date": date,
            "games": [{
                "away": {"abbr": "NYY"}, "home": {"abbr": "PHI"},
                "excludedFromSlate": True, "exclusionReason": "test fixture",
            }],
        }
        (data_dir / "slate.json").write_text(json.dumps(slate))
        return data_dir

    def test_recommendations_artifact_matches_legacy_slate_json(self, tmp_path, monkeypatch):
        import build_market_ledger as bml

        self._write_fixtures(tmp_path)
        monkeypatch.setattr(bml, "__file__", str(tmp_path / "scripts" / "build_market_ledger.py"))
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "data" / "pipeline"))

        bml.main()

        with open(tmp_path / "data" / "slate.json") as f:
            legacy_slate = json.load(f)
        assert legacy_slate["games"][0]["marketLedger"], "marketLedger must be populated as before"

        artifact = pa.read_stage_artifact("recommendations", "2026-07-27")
        assert artifact == legacy_slate, (
            "recommendations.json must match data/slate.json exactly after build_market_ledger.py runs"
        )

    def test_artifact_write_failure_does_not_break_legacy_write(self, tmp_path, monkeypatch):
        """
        Safety property: if write_stage_artifact() itself raises for any
        reason, build_market_ledger.py's primary data/slate.json write
        (which already completed earlier in main()) must be unaffected,
        and main() must not raise.
        """
        import build_market_ledger as bml

        self._write_fixtures(tmp_path)
        monkeypatch.setattr(bml, "__file__", str(tmp_path / "scripts" / "build_market_ledger.py"))

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated artifact backend failure")

        monkeypatch.setattr(pa, "write_stage_artifact", _boom)

        bml.main()  # must not raise

        with open(tmp_path / "data" / "slate.json") as f:
            legacy_slate = json.load(f)
        assert legacy_slate["games"][0]["marketLedger"], (
            "the legacy slate.json write must succeed even when the new "
            "pipeline-artifact write fails"
        )
