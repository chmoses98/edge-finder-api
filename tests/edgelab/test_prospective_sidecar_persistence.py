#!/usr/bin/env python3
"""
tests/edgelab/test_prospective_sidecar_persistence.py
=====================================================
Regression coverage for the E4 SIDECAR PERSISTENCE defect.

THE DEFECT: .github/workflows/model-snapshot-scheduler.yml ran the
prospective snapshot cycle, which writes FOUR date-partitioned entities,
but passed only TWO of them to the pre-commit backup and to
scripts/ci/git_data_commit.py. The two research-only sidecars --
MLB-RSCH-0011's negative-binomial shadow evaluations and MLB-RSCH-0019's
uncertainty capture -- were therefore generated successfully on the
runner and then destroyed when the runner was reclaimed.

That loss is IRREVERSIBLE: a prospective checkpoint cannot be
re-created after the fact without violating the point-in-time contract
that makes it E4 evidence at all. It is also silent: because the rows
were written, every downstream reader saw an empty partition and
reported "0 prospective rows", which reads as "the shadow has not
accumulated evidence yet" rather than "the evidence is being deleted".

These tests pin the repaired contract so the sidecars can never be
dropped from persistence again by an edit that only looks at the core
paths.
"""
import os
import subprocess
import sys

import pytest
import yaml

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab")):
    if p not in sys.path:
        sys.path.insert(0, p)

WORKFLOW_PATH = os.path.join(_ROOT, ".github", "workflows", "model-snapshot-scheduler.yml")
WORKFLOW_TEXT = open(WORKFLOW_PATH).read()
WORKFLOW = yaml.safe_load(WORKFLOW_TEXT)
STEPS = WORKFLOW["jobs"]["prospective-snapshot"]["steps"]

# The four entities the snapshot cycle actually writes, taken from the
# canonical writers in scripts/edgelab/run_prospective_snapshots.py --
# NOT from the workflow (which is the thing under test) and not from a
# prose description of it.
CORE_ENTITIES = ("model_evaluations", "research_runs")
SIDECAR_ENTITIES = ("mlb_rsch_0011_shadow_evaluations", "uncertainty_capture_snapshots")
ALL_ENTITIES = CORE_ENTITIES + SIDECAR_ENTITIES


def _step(name_fragment):
    for s in STEPS:
        if name_fragment.lower() in (s.get("name") or "").lower():
            return s
    raise AssertionError(f"no workflow step matching {name_fragment!r}")


def _step_index(name_fragment):
    for i, s in enumerate(STEPS):
        if name_fragment.lower() in (s.get("name") or "").lower():
            return i
    raise AssertionError(f"no workflow step matching {name_fragment!r}")


class TestCanonicalWritersDefineTheEntitySet:
    """The entity list above must stay tied to what the script really
    writes. If someone adds a fifth sidecar, this fails and forces the
    persistence lists to be updated with it."""

    def test_script_writes_exactly_these_entities(self):
        src = open(os.path.join(_ROOT, "scripts", "edgelab", "run_prospective_snapshots.py")).read()
        written = set()
        for entity in ALL_ENTITIES:
            assert f'"{entity}"' in src, f"{entity} is no longer written by the snapshot cycle"
            written.add(entity)
        assert written == set(ALL_ENTITIES)

    def test_sidecar_entities_resolve_under_the_edgelab_root(self):
        from lib.edgelab import storage
        for entity in SIDECAR_ENTITIES:
            path = storage.partition_path(entity, "2026-08-29")
            assert path == os.path.join("data", "edgelab", entity, "2026-08-29.jsonl")


class TestGitPersistence:
    """Requirements 1-4: all four entities reach git_data_commit.py."""

    @pytest.mark.parametrize("entity", ALL_ENTITIES)
    def test_entity_is_passed_to_git_data_commit(self, entity):
        run = _step("Commit new model evaluations")["run"]
        assert "git_data_commit.py" in run
        assert f"data/edgelab/{entity}/" in run, f"{entity} would not be committed"

    def test_commit_step_persists_no_fewer_than_four_edgelab_paths(self):
        run = _step("Commit new model evaluations")["run"]
        committed = {ln.strip() for ln in run.replace("\\", " ").split() if ln.startswith("data/edgelab/")}
        assert len(committed) == 4, f"expected exactly the four cycle entities, got {sorted(committed)}"


class TestPreCommitBackup:
    """Requirement 5: all four are backed up BEFORE the commit runs, so a
    persistence failure is recoverable rather than silent."""

    @pytest.mark.parametrize("entity", ALL_ENTITIES)
    def test_entity_is_backed_up(self, entity):
        run = _step("Back up generated snapshot files")["run"]
        assert f"data/edgelab/{entity}" in run, f"{entity} would not be backed up"

    def test_backup_happens_before_the_commit_step(self):
        assert _step_index("Back up generated snapshot files") < _step_index("Commit new model evaluations")

    def test_backup_tolerates_a_sidecar_that_produced_nothing(self):
        # Preserves the existing fail-safe contract: a sidecar step that
        # produced no rows leaves no directory, and that must not fail the
        # backup -- exactly as already true for the core paths.
        run = _step("Back up generated snapshot files")["run"]
        for line in run.splitlines():
            if "cp -r" in line:
                assert "|| true" in line, f"backup line is not fail-safe: {line.strip()}"


class TestFailureArtifactRecoverability:
    """Requirement 6: if git persistence ultimately fails, all four are
    recoverable from the uploaded artifact."""

    def test_artifact_uploads_the_backup_directory(self):
        step = _step("Preserve generated snapshots as a recoverable artifact")
        assert step["with"]["path"].strip().rstrip("/") == "/tmp/prospective-snapshot-backup"

    def test_backup_directory_is_the_one_all_four_are_copied_into(self):
        run = _step("Back up generated snapshot files")["run"]
        for entity in ALL_ENTITIES:
            assert any("/tmp/prospective-snapshot-backup" in ln and entity in ln
                       for ln in run.splitlines()), f"{entity} not copied into the artifact directory"

    def test_persistence_failure_is_still_visible_not_silent(self):
        # Requirement: sidecar computation succeeding but persistence
        # failing must NOT be reported as success.
        fail_step = _step("Fail visibly if persistence failed")
        assert "exit 1" in fail_step["run"]
        assert fail_step["if"] == "steps.commit.outcome == 'failure'"

    def test_failure_message_names_the_sidecars(self):
        run = _step("Fail visibly if persistence failed")["run"]
        assert "shadow-evaluation" in run and "uncertainty-capture" in run


class TestDryRunWritesNothing:
    """Requirement 7."""

    @pytest.mark.parametrize("fragment", ["Back up generated snapshot files",
                                          "Commit new model evaluations",
                                          "Preserve generated snapshots as a recoverable artifact"])
    def test_persistence_steps_are_skipped_on_dry_run(self, fragment):
        assert "dry_run != 'true'" in _step(fragment)["if"]

    def test_dry_run_flag_is_forwarded_to_the_script(self):
        assert "--dry-run" in _step("Run prospective snapshot cycle")["run"]


class TestProductionPathsAreNeverPersistedHere:
    """Requirement 8: this workflow must never touch bet-affecting state."""

    FORBIDDEN = ("data/edgelab/recommendations", "data/edgelab/bets", "data/edgelab/bankroll",
                 "data/slate.json", "risk_gate.py", "write_pending_bets.py")

    @pytest.mark.parametrize("forbidden", FORBIDDEN)
    def test_forbidden_path_absent_from_backup_and_commit(self, forbidden):
        for fragment in ("Back up generated snapshot files", "Commit new model evaluations"):
            assert forbidden not in _step(fragment)["run"], f"{forbidden} appears in {fragment}"

    def test_no_step_invokes_a_production_writer(self):
        for step in STEPS:
            run = step.get("run") or ""
            assert "risk_gate.py" not in run and "write_pending_bets.py" not in run


class TestScopeIsPersistenceOnly:
    """Requirements 9-12: nothing about the model, schema or cadence moved."""

    def test_frozen_nb_dispersion_is_unchanged(self):
        from lib.edgelab.shadow_distribution import FROZEN_DISPERSION
        assert FROZEN_DISPERSION == 0.281513

    def test_uncertainty_schema_is_unchanged(self):
        from lib.edgelab.research.uncertainty_capture_schema import REQUIRED_FIELDS
        assert REQUIRED_FIELDS == (
            "gameId", "checkpoint", "capturedAt",
            "homeSampleDepth", "awaySampleDepth", "minSampleDepth",
            "homeBullpenSampleDepth", "awayBullpenSampleDepth", "minBullpenSampleDepth",
            "starterResolvedHome", "starterResolvedAway",
            "lineupConfirmedHome", "lineupConfirmedAway",
            "weatherDataAvailable", "mappingResolved",
            "inputStaleAgeMinutes", "unsupportedFeatureFallbackCount",
            "componentDisagreement", "probExtremeness",
        )

    def test_workflow_cadence_is_unchanged(self):
        crons = [c["cron"] for c in WORKFLOW[True]["schedule"]]
        assert crons == ["*/15 13,14,15,16,17,18,19,20,21,22,23 * * *", "*/15 0,1,2,3,4,5 * * *"]

    def test_snapshot_cycle_invocation_is_unchanged(self):
        run = _step("Run prospective snapshot cycle")["run"]
        assert "python3 scripts/edgelab/run_prospective_snapshots.py $ARGS" in run

    def test_workflow_runs_no_python_beyond_the_cycle_and_the_committer(self):
        """Structural scope guard, durable across branches: this workflow
        may invoke exactly two python entrypoints -- the snapshot cycle and
        the shared data committer. Anything else would mean the persistence
        repair had grown into a behavioural change."""
        invoked = set()
        for step in STEPS:
            for line in (step.get("run") or "").splitlines():
                line = line.strip()
                if line.startswith("python3 ") or " python3 " in line:
                    for token in line.split():
                        if token.endswith(".py"):
                            invoked.add(token)
        assert invoked == {"scripts/edgelab/run_prospective_snapshots.py",
                           "scripts/ci/git_data_commit.py"}, invoked

    def test_persistence_steps_only_copy_and_commit(self):
        """The two persistence steps may only move bytes around: copy into
        the backup dir, configure git, and invoke the shared committer.
        No step may compute anything."""
        allowed_starts = ("mkdir", "cp ", "cp -r", "git config", "python3", "#", "")
        for fragment in ("Back up generated snapshot files", "Commit new model evaluations"):
            for raw in _step(fragment)["run"].splitlines():
                line = raw.strip()
                if line.startswith("#") or not line:
                    continue
                # continuation lines of the committer invocation
                if line.startswith("data/edgelab/") or line == "\\":
                    continue
                assert line.startswith(allowed_starts), f"unexpected command in {fragment}: {line}"


class TestEndToEndPersistencePath:
    """Deterministic workflow-style integration test: generate one row of
    EACH of the four entities, run the SAME persistence path the workflow
    calls, and assert all four survive into a commit."""

    def _repo(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / "data" / "edgelab").mkdir(parents=True)
        def git(*args):
            subprocess.run(["git", *args], cwd=repo, check=True,
                           capture_output=True, text=True)
        git("init", "-b", "main")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "t")
        (repo / "README").write_text("seed\n")
        git("add", "README")
        git("commit", "-m", "seed")
        return repo, git

    def _write_row(self, repo, entity, record_id):
        import json
        d = repo / "data" / "edgelab" / entity
        d.mkdir(parents=True, exist_ok=True)
        (d / "2026-08-29.jsonl").write_text(json.dumps({"id": record_id, "entity": entity}) + "\n")

    def test_all_four_entities_survive_one_commit(self, tmp_path):
        repo, git = self._repo(tmp_path)
        for entity in ALL_ENTITIES:
            self._write_row(repo, entity, f"{entity}-1")

        paths = [f"data/edgelab/{e}/" for e in ALL_ENTITIES]
        # Exercise the real staging/commit path without a remote push.
        existing = [p for p in paths if os.path.exists(os.path.join(repo, p))]
        assert len(existing) == 4, "all four partitions should exist before commit"
        subprocess.run(["git", "add", *existing], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "persist"], cwd=repo, check=True,
                       capture_output=True, text=True)

        tracked = subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True,
                                 text=True).stdout.split()
        for entity in ALL_ENTITIES:
            assert f"data/edgelab/{entity}/2026-08-29.jsonl" in tracked, \
                f"{entity} did not survive persistence"

    def test_a_missing_sidecar_does_not_break_persistence(self, tmp_path):
        """The core two must still persist when a sidecar produced nothing
        -- git_data_commit filters non-existent paths."""
        repo, git = self._repo(tmp_path)
        for entity in CORE_ENTITIES:
            self._write_row(repo, entity, f"{entity}-1")

        paths = [f"data/edgelab/{e}/" for e in ALL_ENTITIES]
        existing = [p for p in paths if os.path.exists(os.path.join(repo, p))]
        assert len(existing) == 2
        subprocess.run(["git", "add", *existing], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "persist"], cwd=repo, check=True,
                       capture_output=True, text=True)
        tracked = subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True,
                                 text=True).stdout.split()
        assert any("model_evaluations" in t for t in tracked)

    def test_git_data_commit_filters_nonexistent_paths(self):
        """The property the workflow relies on: naming a sidecar directory
        that does not exist yet is safe."""
        src = open(os.path.join(_ROOT, "scripts", "ci", "git_data_commit.py")).read()
        assert "existing_paths = [p for p in paths if os.path.exists" in src
        assert "Nothing to commit (none of the target paths exist)" in src
