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
        # Phase 4: build_market_ledger.py's main() also writes projections.json
        # (see TestBuildMarketLedgerProjectionsSnapshot below) — a rerun must
        # still overwrite each artifact file in place, not duplicate either one.
        assert sorted(os.listdir(str(date_dir))) == ["projections.json", "recommendations.json"], (
            "a rerun must overwrite each existing artifact file, not create a second one"
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


class TestBuildMarketLedgerProjectionsSnapshot:
    """
    Phase 4: data/pipeline/<date>/projections.json — see
    docs/IMMUTABLE_PIPELINE.md's Projection Layer section for why this
    boundary (right before evaluate_game()'s per-game loop in main(), the
    same point compute_projections() is otherwise called from internally)
    was chosen, and why this artifact is a narrowed/canonical schema
    rather than a transitional full-slate snapshot like recommendations.json.
    """

    def _write_fixtures_with_computable_projections(self, tmp_path, date="2026-07-27"):
        (tmp_path / "scripts").mkdir()
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        slate = {
            "date": date,
            "games": [{
                "away": {"abbr": "NYY", "pitcherSavant": {"xFIP": 4.0}, "bullpen": {}},
                "home": {"abbr": "PHI", "pitcherSavant": {"xFIP": 4.0}, "bullpen": {}},
                "awayTeamStats": {"offenseBaselineAdj": 4.5},
                "homeTeamStats": {"offenseBaselineAdj": 4.5},
                "park": {"parkFactor": 100},
                "odds": {"kalshi": {}},
            }],
        }
        (data_dir / "slate.json").write_text(json.dumps(slate))
        return data_dir

    @pytest.mark.parametrize("status", ["Scheduled", "In Progress", "Final", "Postponed"])
    def test_projection_computed_regardless_of_game_status(self, tmp_path, monkeypatch, status):
        """
        Pre-merge hardening addition (PR #5 review, Section D). Neither
        compute_projections() nor the new artifact-writing block checks
        g.get('status') at all -- this mirrors evaluate_game()'s own
        existing, unconditional call to compute_projections() exactly
        (not a new behavior introduced by this artifact). Confirmed here
        explicitly for all four observed game-status values so this
        isn't left as an unverified inference from reading the code.
        """
        import build_market_ledger as bml

        data_dir = self._write_fixtures_with_computable_projections(tmp_path)
        slate = json.loads((data_dir / "slate.json").read_text())
        slate["games"][0]["status"] = status
        (data_dir / "slate.json").write_text(json.dumps(slate))

        monkeypatch.setattr(bml, "__file__", str(tmp_path / "scripts" / "build_market_ledger.py"))
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "data" / "pipeline"))

        bml.main()

        proj_game = pa.read_stage_artifact("projections", "2026-07-27")["data"]["games"][0]
        assert proj_game["missingFields"] == []
        assert proj_game["awayProjRuns"] is not None, (
            f"projection must be computed for status={status!r} exactly as "
            f"evaluate_game() itself would compute it, regardless of game status"
        )

    def test_projections_artifact_matches_compute_projections_output(self, tmp_path, monkeypatch):
        import build_market_ledger as bml

        self._write_fixtures_with_computable_projections(tmp_path)
        monkeypatch.setattr(bml, "__file__", str(tmp_path / "scripts" / "build_market_ledger.py"))
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "data" / "pipeline"))

        bml.main()

        with open(tmp_path / "data" / "slate.json") as f:
            legacy_slate = json.load(f)
        expected_away, expected_home, expected_f5a, expected_f5h, expected_missing = (
            bml.compute_projections(legacy_slate["games"][0])
        )
        assert expected_missing == [], "fixture must produce a fully computable projection"

        artifact = pa.read_stage_artifact("projections", "2026-07-27")
        proj_game = artifact["data"]["games"][0]
        assert proj_game["away"] == "NYY"
        assert proj_game["home"] == "PHI"
        assert proj_game["awayProjRuns"] == expected_away
        assert proj_game["homeProjRuns"] == expected_home
        assert proj_game["f5AwayProj"] == expected_f5a
        assert proj_game["f5HomeProj"] == expected_f5h
        assert proj_game["missingFields"] == []

    def test_projections_artifact_is_canonical_not_transitional(self, tmp_path, monkeypatch):
        import build_market_ledger as bml

        self._write_fixtures_with_computable_projections(tmp_path)
        monkeypatch.setattr(bml, "__file__", str(tmp_path / "scripts" / "build_market_ledger.py"))
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "data" / "pipeline"))

        bml.main()

        meta = pa.read_stage_artifact("projections", "2026-07-27")["meta"]
        assert meta["status"] == "canonical"
        assert meta["sourceStage"] == "normalized_slate"
        assert meta["stage"] == "projections"
        assert meta["producedBy"] == "scripts/build_market_ledger.py"

    def test_projections_computed_even_for_excluded_game(self, tmp_path, monkeypatch):
        """
        compute_projections() has no awareness of excludedFromSlate — the
        Projection Layer artifact snapshot should not silently skip a
        quarantined game's projection just because its markets are
        excluded from real-money recommendations. missingFields will be
        non-empty here since this fixture (borrowed from the
        recommendations test class) has no team-stats data at all, but
        the game must still be represented with excludedFromSlate=True.
        """
        import build_market_ledger as bml

        (tmp_path / "scripts").mkdir()
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        slate = {
            "date": "2026-07-27",
            "games": [{
                "away": {"abbr": "NYY"}, "home": {"abbr": "PHI"},
                "excludedFromSlate": True, "exclusionReason": "test fixture",
            }],
        }
        (data_dir / "slate.json").write_text(json.dumps(slate))

        monkeypatch.setattr(bml, "__file__", str(tmp_path / "scripts" / "build_market_ledger.py"))
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "data" / "pipeline"))

        bml.main()

        proj_game = pa.read_stage_artifact("projections", "2026-07-27")["data"]["games"][0]
        assert proj_game["excludedFromSlate"] is True
        assert proj_game["awayProjRuns"] is None
        assert proj_game["missingFields"] != []

    def test_projections_write_failure_does_not_break_legacy_write_or_recommendations(self, tmp_path, monkeypatch):
        import build_market_ledger as bml

        self._write_fixtures_with_computable_projections(tmp_path)
        monkeypatch.setattr(bml, "__file__", str(tmp_path / "scripts" / "build_market_ledger.py"))
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "data" / "pipeline"))

        real_write = pa.write_stage_artifact

        def _fail_projections_only(stage, *args, **kwargs):
            if stage == "projections":
                raise RuntimeError("simulated projections artifact backend failure")
            return real_write(stage, *args, **kwargs)

        monkeypatch.setattr(pa, "write_stage_artifact", _fail_projections_only)

        bml.main()  # must not raise

        with open(tmp_path / "data" / "slate.json") as f:
            legacy_slate = json.load(f)
        assert legacy_slate["games"][0]["marketLedger"], (
            "the legacy slate.json write must succeed even when the projections "
            "artifact write fails"
        )
        assert not pa.stage_artifact_exists("projections", "2026-07-27")
        assert pa.stage_artifact_exists("recommendations", "2026-07-27"), (
            "a projections-artifact failure must not prevent the independent "
            "recommendations artifact from being written"
        )

    def test_rerun_overwrites_projections_artifact(self, tmp_path, monkeypatch):
        import build_market_ledger as bml

        self._write_fixtures_with_computable_projections(tmp_path)
        monkeypatch.setattr(bml, "__file__", str(tmp_path / "scripts" / "build_market_ledger.py"))
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "data" / "pipeline"))

        bml.main()
        first = pa.read_stage_artifact("projections", "2026-07-27")

        bml.main()
        second = pa.read_stage_artifact("projections", "2026-07-27")

        assert second["meta"]["createdAt"] >= first["meta"]["createdAt"]
        assert second["data"] == first["data"], (
            "the artifact must be reproducible: an unchanged input slate must "
            "produce byte-for-byte identical projection data on every run"
        )
        date_dir = tmp_path / "data" / "pipeline" / "2026-07-27"
        assert sorted(os.listdir(str(date_dir))) == ["projections.json", "recommendations.json"]

    def test_schema_version_present_and_current(self, tmp_path, monkeypatch):
        import build_market_ledger as bml

        self._write_fixtures_with_computable_projections(tmp_path)
        monkeypatch.setattr(bml, "__file__", str(tmp_path / "scripts" / "build_market_ledger.py"))
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "data" / "pipeline"))

        bml.main()

        assert pa.read_stage_artifact("projections", "2026-07-27")["meta"]["schemaVersion"] == pa.SCHEMA_VERSION

    def test_multi_game_ordering_matches_slate_order(self, tmp_path, monkeypatch):
        import build_market_ledger as bml

        (tmp_path / "scripts").mkdir()
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        def _g(away, home):
            return {
                "away": {"abbr": away, "pitcherSavant": {"xFIP": 4.0}, "bullpen": {}},
                "home": {"abbr": home, "pitcherSavant": {"xFIP": 4.0}, "bullpen": {}},
                "awayTeamStats": {"offenseBaselineAdj": 4.5},
                "homeTeamStats": {"offenseBaselineAdj": 4.5},
                "park": {"parkFactor": 100},
                "odds": {"kalshi": {}},
            }

        slate = {"date": "2026-07-27", "games": [_g("NYY", "PHI"), _g("BOS", "TB"), _g("LAD", "SD")]}
        (data_dir / "slate.json").write_text(json.dumps(slate))

        monkeypatch.setattr(bml, "__file__", str(tmp_path / "scripts" / "build_market_ledger.py"))
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "data" / "pipeline"))

        bml.main()

        proj_games = pa.read_stage_artifact("projections", "2026-07-27")["data"]["games"]
        matchups = [f"{g['away']}@{g['home']}" for g in proj_games]
        assert matchups == ["NYY@PHI", "BOS@TB", "LAD@SD"], (
            "projections.json's game order must match data/slate.json's game order exactly"
        )
        assert all(g["awayProjRuns"] is not None for g in proj_games)

    def test_invalid_slate_date_does_not_break_legacy_write_or_other_files(self, tmp_path, monkeypatch):
        """
        An empty/invalid slate date makes write_stage_artifact() raise
        ValueError for BOTH the projections and recommendations writes
        (each independently caught). main() must still complete, still
        populate marketLedger, and must not touch data/bets.json or the
        authoritative slate — build_market_ledger.py never reads or
        writes either file, so this also documents that boundary
        explicitly rather than leaving it as an implicit code-reading fact.
        """
        import build_market_ledger as bml

        data_dir = self._write_fixtures_with_computable_projections(tmp_path, date="")
        bets_path = data_dir / "bets.json"
        bets_path.write_text(json.dumps({"bets": ["untouched"]}))
        auth_dir = data_dir / "slates" / "2026-07-27"
        auth_dir.mkdir(parents=True)
        auth_path = auth_dir / "authoritative.json"
        auth_path.write_text(json.dumps({"authoritative": "untouched"}))

        monkeypatch.setattr(bml, "__file__", str(tmp_path / "scripts" / "build_market_ledger.py"))
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "data" / "pipeline"))

        bml.main()  # must not raise despite date="" making both artifact writes fail

        with open(tmp_path / "data" / "slate.json") as f:
            legacy_slate = json.load(f)
        assert legacy_slate["games"][0]["marketLedger"], (
            "an invalid slate date must not prevent the legacy slate.json write"
        )
        assert not (tmp_path / "data" / "pipeline").exists(), (
            "no pipeline/ directory should be created when the date is invalid"
        )
        assert json.loads(bets_path.read_text()) == {"bets": ["untouched"]}
        assert json.loads(auth_path.read_text()) == {"authoritative": "untouched"}

    def test_bets_and_authoritative_files_untouched_on_a_normal_run(self, tmp_path, monkeypatch):
        """build_market_ledger.py has no code path that reads or writes either file."""
        import build_market_ledger as bml

        data_dir = self._write_fixtures_with_computable_projections(tmp_path)
        bets_path = data_dir / "bets.json"
        bets_path.write_text(json.dumps({"bets": ["untouched"]}))
        auth_dir = data_dir / "slates" / "2026-07-27"
        auth_dir.mkdir(parents=True)
        auth_path = auth_dir / "authoritative.json"
        auth_path.write_text(json.dumps({"authoritative": "untouched"}))

        monkeypatch.setattr(bml, "__file__", str(tmp_path / "scripts" / "build_market_ledger.py"))
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "data" / "pipeline"))

        bml.main()

        assert json.loads(bets_path.read_text()) == {"bets": ["untouched"]}
        assert json.loads(auth_path.read_text()) == {"authoritative": "untouched"}

    def test_malformed_prior_projections_artifact_is_cleanly_overwritten(self, tmp_path, monkeypatch):
        """
        A corrupt projections.json already on disk (e.g. from a crash
        before atomic writes existed, or external tampering) must not
        confuse a subsequent run — write_stage_artifact()'s atomic
        os.replace() cleanly replaces it, proven here at the
        build_market_ledger.py integration level (the primitive itself is
        already covered by tests/test_pipeline_artifacts.py).
        """
        import build_market_ledger as bml

        self._write_fixtures_with_computable_projections(tmp_path)
        monkeypatch.setattr(bml, "__file__", str(tmp_path / "scripts" / "build_market_ledger.py"))
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "data" / "pipeline"))

        date_dir = tmp_path / "data" / "pipeline" / "2026-07-27"
        date_dir.mkdir(parents=True)
        (date_dir / "projections.json").write_text("{not valid json at all")

        bml.main()  # must not raise

        artifact = pa.read_stage_artifact("projections", "2026-07-27")
        assert artifact["data"]["games"][0]["away"] == "NYY"

    def test_interrupted_projections_write_does_not_block_recommendations_or_legacy_write(self, tmp_path, monkeypatch):
        """
        Simulate a crash mid-serialization of ONLY the projections write
        (os.fdopen raises inside write_stage_artifact, the same technique
        tests/test_pipeline_artifacts.py uses for the primitive itself).
        No partial projections.json may be left behind, and the
        recommendations artifact plus the legacy slate.json write —
        which happen afterward in main() — must be entirely unaffected.
        """
        import build_market_ledger as bml

        self._write_fixtures_with_computable_projections(tmp_path)
        monkeypatch.setattr(bml, "__file__", str(tmp_path / "scripts" / "build_market_ledger.py"))
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "data" / "pipeline"))

        real_fdopen = pa.os.fdopen
        call_count = {"n": 0}

        def _boom_first_call(*a, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError("simulated interrupted write")
            return real_fdopen(*a, **kw)

        monkeypatch.setattr(pa.os, "fdopen", _boom_first_call)

        bml.main()  # must not raise — the failure is caught inside main()'s try/except

        date_dir = tmp_path / "data" / "pipeline" / "2026-07-27"
        assert not (date_dir / "projections.json").exists(), (
            "an interrupted projections write must leave no partial file behind"
        )
        assert pa.stage_artifact_exists("recommendations", "2026-07-27"), (
            "an interrupted projections write must not prevent the later "
            "recommendations write from completing"
        )
        with open(tmp_path / "data" / "slate.json") as f:
            legacy_slate = json.load(f)
        assert legacy_slate["games"][0]["marketLedger"]


class TestProjectionsMatchEvaluateGameUsage:
    """
    Pre-merge hardening addition (PR #5 review, Section E). The PR
    publishes projections.json via an EXTRA call to compute_projections()
    in main(), ahead of the per-game loop that calls evaluate_game() --
    which internally calls compute_projections() again, unchanged, on the
    same game object. This proves those two calls always produce
    identical output, for every marketLedger row of every non-excluded
    game across three differently-shaped fixtures (fully computable,
    partially missing data, and a multi-game mix of both).

    Why this is structurally guaranteed rather than merely usually true:
      1. compute_projections(g) is a pure function -- no randomness, no
         wall-clock dependency, no module-level cache, no global mutable
         state; verified by reading its full body (scripts/build_market_ledger.py).
      2. Both calls receive the exact same `g` object reference (main()'s
         `games = slate.get('games', [])` list is iterated by both the
         projections-artifact loop and the marketLedger loop -- no copy).
      3. Nothing mutates `g`'s projection-input fields (awayTeamStats,
         pitcherSavant, bullpen, park) between the two calls --
         evaluate_game() only reads `g` before its own internal
         compute_projections(g) call; grepped for any `g[...] =`
         assignment before that call and found none.
    Excluded (`excludedFromSlate`) games are out of scope for this
    comparison by design: main() skips evaluate_game() for them entirely
    (their marketLedger rows are EXCLUDED-reason rejected rows built
    without proj_context at all), so there is no second computation for
    their projection to possibly diverge from in the first place --
    projections.json still gets a real, non-excluded-aware projection
    for them (see test_projections_computed_even_for_excluded_game),
    which is a documented, intentional difference in scope, not drift.
    """

    def _fully_computable_game(self, away="NYY", home="PHI"):
        return {
            "away": {"abbr": away, "pitcherSavant": {"xFIP": 3.5}, "bullpen": {}},
            "home": {"abbr": home, "pitcherSavant": {"xFIP": 4.2}, "bullpen": {}},
            "awayTeamStats": {"offenseBaselineAdj": 4.8},
            "homeTeamStats": {"offenseBaselineAdj": 4.1},
            "park": {"parkFactor": 105},
            "odds": {"kalshi": {}},
        }

    def _partially_missing_game(self, away="BOS", home="TB"):
        return {
            "away": {"abbr": away},  # no pitcherSavant at all -> missing xFIP
            "home": {"abbr": home, "pitcherSavant": {"xFIP": 4.0}, "bullpen": {}},
            "awayTeamStats": {},
            "homeTeamStats": {"offenseBaselineAdj": 4.1},
            "odds": {"kalshi": {}},
        }

    def _run_and_compare(self, tmp_path, monkeypatch, games):
        import build_market_ledger as bml

        (tmp_path / "scripts").mkdir()
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        slate = {"date": "2026-07-27", "games": games}
        (data_dir / "slate.json").write_text(json.dumps(slate))
        monkeypatch.setattr(bml, "__file__", str(tmp_path / "scripts" / "build_market_ledger.py"))
        monkeypatch.setattr(pa, "PIPELINE_ROOT", str(tmp_path / "data" / "pipeline"))

        bml.main()

        proj_games = pa.read_stage_artifact("projections", "2026-07-27")["data"]["games"]
        with open(data_dir / "slate.json") as f:
            final_slate = json.load(f)
        ledger_games = final_slate["games"]

        assert len(proj_games) == len(ledger_games)
        checked_rows = 0
        for proj, g in zip(proj_games, ledger_games):
            ledger = g.get("marketLedger", [])
            assert ledger, "every game must have marketLedger rows to compare against"
            for row in ledger:
                # make_row()'s call sites are asymmetric (pre-existing,
                # unmodified by this PR): missing_row()/failed_row() never
                # receive **proj_context, so a Missing Data/Evaluation
                # Failed row's own awayProjRuns/etc. fields are always None
                # by construction, REGARDLESS of what compute_projections()
                # actually returned -- that is a fact about evaluate_game()'s
                # existing row-building code, not something projections.json
                # could "diverge" from. Only Accepted/Rejected rows actually
                # thread the computed projection into their own fields, so
                # only those are meaningful to compare here.
                if row["status"] not in ("Accepted", "Rejected"):
                    continue
                checked_rows += 1
                assert row["awayProjRuns"] == proj["awayProjRuns"], (proj["away"], row["market"])
                assert row["homeProjRuns"] == proj["homeProjRuns"], (proj["away"], row["market"])
                assert row["f5AwayProj"] == proj["f5AwayProj"], (proj["away"], row["market"])
                assert row["f5HomeProj"] == proj["f5HomeProj"], (proj["away"], row["market"])
        assert checked_rows > 0, "fixture must produce at least one Accepted/Rejected row to compare"
        return proj_games, ledger_games

    def test_fully_computable_projection_matches_every_marketledger_row(self, tmp_path, monkeypatch):
        proj_games, _ = self._run_and_compare(tmp_path, monkeypatch, [self._fully_computable_game()])
        assert proj_games[0]["awayProjRuns"] is not None, "fixture must actually exercise the computable path"

    def test_partial_missing_data_projection_matches_every_marketledger_row(self, tmp_path, monkeypatch):
        proj_games, _ = self._run_and_compare(tmp_path, monkeypatch, [self._partially_missing_game()])
        assert proj_games[0]["awayProjRuns"] is None, "fixture must actually exercise the missing-data (None) path"
        assert proj_games[0]["missingFields"] != []

    def test_multi_game_mixed_projection_matches_every_marketledger_row(self, tmp_path, monkeypatch):
        games = [
            self._fully_computable_game("NYY", "PHI"),
            self._partially_missing_game("BOS", "TB"),
            self._fully_computable_game("LAD", "SD"),
        ]
        proj_games, _ = self._run_and_compare(tmp_path, monkeypatch, games)
        assert proj_games[0]["awayProjRuns"] is not None
        assert proj_games[1]["awayProjRuns"] is None
        assert proj_games[2]["awayProjRuns"] is not None
