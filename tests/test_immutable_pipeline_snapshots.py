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

        assert artifact["data"] == legacy_slate, (
            "normalized_slate.json's data payload must match data/slate.json exactly — "
            "the snapshot must not diverge from the legacy write"
        )
        assert artifact["meta"]["stage"] == "normalized_slate"
        assert artifact["meta"]["slateDate"] == "2026-07-27"
        assert artifact["meta"]["producedBy"] == "scripts/enrich_data.py"
        assert artifact["meta"]["schemaVersion"] == "1.0"

    def test_artifact_date_follows_slate_date_not_wall_clock(self, tmp_path):
        """
        Date/time safety: the artifact directory must be keyed by the
        slate's own 'date' field (already validated upstream by
        post_fetch_gate.py/validate_current_slate_date.py against the
        requested ET slate date long before enrich_data.py runs), never
        by the runner's current UTC (or any other) calendar date. This
        guards against a late-night run whose wall clock has already
        rolled over to the next day writing into the wrong date
        directory. enrich_data.py never calls datetime.now() anywhere in
        its artifact-writing code path — this test proves that by using
        a slate date that deliberately does NOT match the real system
        date this test happens to run on, and confirming the artifact
        still lands under the slate's date.
        """
        import datetime
        real_today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        slate_date = "2019-03-28"  # deliberately not "today" under any timezone
        assert slate_date != real_today

        self._write_fixtures(tmp_path, date=slate_date)
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "enrich_data.py")],
            cwd=str(tmp_path), capture_output=True, text=True,
        )
        assert result.returncode == 0, f"enrich_data.py failed: {result.stderr}"

        assert (tmp_path / "data" / "pipeline" / slate_date / "normalized_slate.json").exists()
        assert not (tmp_path / "data" / "pipeline" / real_today).exists(), (
            "the artifact must never be written under the wall-clock date "
            "when the slate's own date differs"
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
        assert artifact["data"] == legacy_slate, (
            "recommendations.json's data payload must match data/slate.json exactly "
            "after build_market_ledger.py runs"
        )
        assert artifact["meta"]["stage"] == "recommendations"
        assert artifact["meta"]["slateDate"] == "2026-07-27"
        assert artifact["meta"]["producedBy"] == "scripts/build_market_ledger.py"
        assert artifact["meta"]["schemaVersion"] == "1.0"

    def test_recommendations_artifact_still_contains_full_slate_not_just_recommendations(self, tmp_path, monkeypatch):
        """
        Documents (and locks in) that this is a TRANSITIONAL snapshot, not
        the final canonical Recommendation schema: it currently contains
        the entire slate object (projections, team stats, etc.), not just
        the marketLedger rows the name "recommendations" might suggest.
        docs/IMMUTABLE_PIPELINE.md must say so explicitly so a future
        reader doesn't mistake this for the narrower schema
        docs/CANONICAL_SCHEMAS.md designs.
        """
        import build_market_ledger as bml

        self._write_fixtures(tmp_path)
        monkeypatch.setattr(bml, "__file__", str(tmp_path / "scripts" / "build_market_ledger.py"))
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "data" / "pipeline"))

        bml.main()

        artifact = pa.read_stage_artifact("recommendations", "2026-07-27")
        # Full slate fields (not just marketLedger) are present, confirming
        # this is a whole-slate snapshot, not a narrowed recommendation schema.
        assert "date" in artifact["data"]
        assert "games" in artifact["data"]

        with open(os.path.join(ROOT, "docs", "IMMUTABLE_PIPELINE.md")) as f:
            doc = f.read()
        assert "transitional snapshot" in doc.lower(), (
            "docs/IMMUTABLE_PIPELINE.md must explicitly label these artifacts "
            "as transitional snapshots, not the final canonical schema"
        )

    def test_rerun_same_date_overwrites_recommendations_artifact(self, tmp_path, monkeypatch):
        """A second real run for the same slate date must replace, not duplicate, the artifact."""
        import build_market_ledger as bml

        self._write_fixtures(tmp_path)
        monkeypatch.setattr(bml, "__file__", str(tmp_path / "scripts" / "build_market_ledger.py"))
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "data" / "pipeline"))

        bml.main()
        first_created_at = pa.read_stage_artifact("recommendations", "2026-07-27")["meta"]["createdAt"]

        bml.main()  # rerun on the same date
        second = pa.read_stage_artifact("recommendations", "2026-07-27")

        assert second["meta"]["createdAt"] >= first_created_at
        date_dir = tmp_path / "data" / "pipeline" / "2026-07-27"
        assert os.listdir(str(date_dir)) == ["recommendations.json"], (
            "a rerun must overwrite the same artifact file, not create a second one"
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
