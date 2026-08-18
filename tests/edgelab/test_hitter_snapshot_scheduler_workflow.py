#!/usr/bin/env python3
"""
tests/edgelab/test_hitter_snapshot_scheduler_workflow.py
==============================================================
Structural content tests for .github/workflows/hitter-snapshot-scheduler.yml,
mirroring tests/edgelab/test_prospective_snapshot.py's own workflow-
structure test suite for model-snapshot-scheduler.yml exactly (this
milestone's own explicit isolation/reliability requirements are the
same, applied to a second, independent scheduler).
"""
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKFLOWS_DIR = os.path.join(ROOT, ".github", "workflows")
HITTER_WORKFLOW_PATH = os.path.join(WORKFLOWS_DIR, "hitter-snapshot-scheduler.yml")


def _load_hitter_workflow():
    with open(HITTER_WORKFLOW_PATH) as f:
        return yaml.safe_load(f)


def _read():
    with open(HITTER_WORKFLOW_PATH) as f:
        return f.read()


class TestWorkflowStructure:
    def test_valid_yaml(self):
        doc = _load_hitter_workflow()
        assert doc is not None

    def test_has_scheduled_and_dispatch_triggers(self):
        doc = _load_hitter_workflow()
        assert True in doc  # PyYAML parses bare `on:` key as boolean True
        assert "schedule" in doc[True]
        assert "workflow_dispatch" in doc[True]

    def test_permission_scoped_to_contents_write(self):
        doc = _load_hitter_workflow()
        job = doc["jobs"]["hitter-snapshot"]
        assert job["permissions"]["contents"] == "write"

    def test_has_job_timeout(self):
        """A materially more expensive evaluate step than the game-level scheduler's -- an explicit timeout bounds a runaway cycle instead of relying on GitHub's default (360 min)."""
        doc = _load_hitter_workflow()
        job = doc["jobs"]["hitter-snapshot"]
        assert isinstance(job.get("timeout-minutes"), int)
        assert job["timeout-minutes"] <= 60


class TestIsolationFromOtherWorkflows:
    """A failure here must never be able to block capture-snapshots-scheduled.yml, fetch-slate.yml, model-snapshot-scheduler.yml, or kalshi-price-check.yml -- no shared job, step, or concurrency group with any of them."""

    def _other_workflow_docs(self):
        others = {}
        for name in ("capture-snapshots-scheduled.yml", "fetch-slate.yml",
                     "model-snapshot-scheduler.yml", "kalshi-price-check.yml"):
            path = os.path.join(WORKFLOWS_DIR, name)
            if os.path.exists(path):
                with open(path) as f:
                    others[name] = yaml.safe_load(f)
        return others

    def test_no_shared_job_names(self):
        hitter_jobs = set(_load_hitter_workflow().get("jobs", {}).keys())
        for name, doc in self._other_workflow_docs().items():
            other_jobs = set(doc.get("jobs", {}).keys())
            assert hitter_jobs.isdisjoint(other_jobs), f"job name collision with {name}"

    def test_no_shared_concurrency_group(self):
        hitter_group = _load_hitter_workflow()["concurrency"]["group"]
        for name, doc in self._other_workflow_docs().items():
            other_group = (doc.get("concurrency") or {}).get("group")
            assert other_group != hitter_group, f"concurrency group collision with {name}"

    def test_not_in_shared_ledger_writer_group(self):
        """This workflow never writes data/slate.json/bets.json/BET_LOG.md, so per this repo's own existing concurrency convention it must NOT join the shared edge-finder-ledger-writer group."""
        doc = _load_hitter_workflow()
        assert doc["concurrency"]["group"] != "edge-finder-ledger-writer"

    def test_own_dedicated_concurrency_group(self):
        doc = _load_hitter_workflow()
        assert doc["concurrency"]["group"] == "edgelab-hitter-snapshot"


class TestScriptInvocationAndScope:
    def test_calls_the_hitter_prospective_snapshot_script(self):
        src = _read()
        assert "scripts/edgelab/run_hitter_prospective_snapshots.py" in src

    def test_never_calls_forbidden_production_scripts(self):
        doc = _load_hitter_workflow()
        run_bodies = []
        for job in doc.get("jobs", {}).values():
            for step in job.get("steps", []):
                if "run" in step:
                    run_bodies.append(step["run"])
        combined = "\n".join(run_bodies)
        for forbidden in ("write_pending_bets.py", "risk_gate.py", "protect_slate.py",
                           "validate_slate_final.py", "build_market_ledger.py"):
            assert forbidden not in combined, f"forbidden script invoked: {forbidden}"

    def test_commit_step_scoped_to_hitter_snapshot_and_research_run_paths(self):
        doc = _load_hitter_workflow()
        steps = doc["jobs"]["hitter-snapshot"]["steps"]
        commit_step = next(s for s in steps if s.get("id") == "commit")
        run_script = commit_step["run"]
        assert "data/edgelab/hitter_projection_snapshots/" in run_script
        assert "data/edgelab/research_runs/" in run_script
        for forbidden_path in ("data/slate.json", "data/edgelab/bets/", "data/edgelab/recommendations/",
                                "hitter_projection_board.json", "bets.json"):
            assert forbidden_path not in run_script, f"commit step touches forbidden path: {forbidden_path}"


class TestReliabilityAndFailureVisibility:
    """Mirrors tests/edgelab/test_prospective_snapshot.py's own reliability suite exactly -- same requirements, second scheduler."""

    def test_job_has_no_continue_on_error(self):
        doc = _load_hitter_workflow()
        job = doc["jobs"]["hitter-snapshot"]
        assert "continue-on-error" not in job

    def test_commit_step_never_swallows_failure_with_bare_or_echo(self):
        doc = _load_hitter_workflow()
        steps = doc["jobs"]["hitter-snapshot"]["steps"]
        commit_step = next(s for s in steps if s.get("id") == "commit")
        assert "||" not in commit_step.get("run", "")

    def test_commit_step_uses_canonical_git_commit_script(self):
        doc = _load_hitter_workflow()
        steps = doc["jobs"]["hitter-snapshot"]["steps"]
        commit_step = next(s for s in steps if s.get("id") == "commit")
        assert "scripts/ci/git_data_commit.py" in commit_step["run"]

    def test_backs_up_generated_files_before_attempting_commit(self):
        doc = _load_hitter_workflow()
        steps = doc["jobs"]["hitter-snapshot"]["steps"]
        step_names = [s.get("name", "") for s in steps]
        backup_idx = next(i for i, n in enumerate(step_names) if "back up" in n.lower() or "backup" in n.lower())
        commit_idx = next(i for i, s in enumerate(steps) if s.get("id") == "commit")
        assert backup_idx < commit_idx

    def test_uploads_artifact_on_persistence_failure(self):
        doc = _load_hitter_workflow()
        steps = doc["jobs"]["hitter-snapshot"]["steps"]
        upload_step = next(s for s in steps if s.get("uses", "").startswith("actions/upload-artifact"))
        assert "steps.commit.outcome == 'failure'" in upload_step["if"]

    def test_fails_visibly_when_persistence_fails(self):
        doc = _load_hitter_workflow()
        steps = doc["jobs"]["hitter-snapshot"]["steps"]
        fail_step = next(s for s in steps if "fail" in s.get("name", "").lower() and "visib" in s.get("name", "").lower())
        assert "steps.commit.outcome == 'failure'" in fail_step["if"]
        assert "exit 1" in fail_step["run"]


class TestCoverageFixCadence:
    """Regression guard for the scheduling-coverage fix (found and fixed
    before merge): a 30-minute cadence paired with the shared checkpoint
    classifier's default +/-7.5-minute tolerance covered only half of
    all possible game start-minute alignments. See
    docs/HITTER_CHECKPOINT_COVERAGE_FIX.md and
    tests/research/test_hitter_checkpoint_coverage_simulation.py for the
    full exhaustive proof; this test only pins the workflow's own cron
    cadence so it can never silently regress back to the buggy value."""

    def test_cron_cadence_is_15_minutes_not_30(self):
        doc = _load_hitter_workflow()
        schedules = doc[True]["schedule"]
        crons = [s["cron"] for s in schedules]
        for cron in crons:
            assert cron.startswith("*/15 "), f"expected a 15-minute cadence, found: {cron}"
            assert not cron.startswith("*/30 "), "cadence must never silently regress to the buggy 30-minute value"

    def test_documentation_no_longer_overclaims_reliable_coverage_at_30_minutes(self):
        src = _read()
        assert "*/30" not in src
        # The doc must still be honest about what IS and ISN'T guaranteed --
        # it should reference the actual verified guarantee, not a bare
        # unqualified "reliably".
        assert "exhaustive simulation" in src.lower() or "verified" in src.lower()
