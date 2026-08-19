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


class TestRuntimeCapacityFix:
    """Regression guard for the scheduler-capacity architecture fix
    (workflow run 32189380616 was cancelled by the then-configured
    25-minute timeout while legitimately still evaluating multiple due
    checkpoint groups on a busy slate; a follow-up review then found that
    merely raising the timeout to 45 minutes left a DEEPER capacity
    problem unaddressed -- see docs/HITTER_SCHEDULER_RUNTIME_HARDENING.md
    for the full incident audit, the concurrency-semantics audit, and the
    corrected 30-minute derivation). This is a SEPARATE concern from
    TestCoverageFixCadence above: that class guards checkpoint scheduling
    coverage; this one guards wall-clock job capacity."""

    def test_timeout_raised_to_the_derived_30_minutes(self):
        doc = _load_hitter_workflow()
        job = doc["jobs"]["hitter-snapshot"]
        assert job["timeout-minutes"] == 30, (
            "expected the derived 30-minute bound (docs/HITTER_SCHEDULER_RUNTIME_HARDENING.md) -- "
            "if this genuinely needs to change again, update that derivation, don't just bump the number"
        )

    def test_timeout_never_silently_regresses_to_the_too_tight_25_minutes(self):
        doc = _load_hitter_workflow()
        job = doc["jobs"]["hitter-snapshot"]
        assert job["timeout-minutes"] != 25

    def test_documentation_explains_the_timeout_is_derived_not_arbitrary(self):
        src = _read()
        assert "docs/HITTER_SCHEDULER_RUNTIME_HARDENING.md" in src
        assert "32189380616" in src

    def test_runtime_hardening_doc_exists_and_documents_the_incident(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        doc_path = os.path.join(root, "docs", "HITTER_SCHEDULER_RUNTIME_HARDENING.md")
        assert os.path.exists(doc_path)
        with open(doc_path) as f:
            content = f.read()
        assert "32189380616" in content
        assert "30" in content
        assert "n_sims" in content  # confirms the doc explicitly addresses "n_sims unchanged"

    def test_concurrency_group_uses_queue_max(self):
        """`queue: max` (GitHub Actions GA 2026-05-07) replaces the default single-pending-slot queue -- a run is never silently cancelled/replaced merely because a newer cron tick arrived while it waited. Compatible with this workflow's existing cancel-in-progress:false (the invalid combination is specifically queue:max + cancel-in-progress:true)."""
        doc = _load_hitter_workflow()
        assert doc["concurrency"].get("queue") == "max"
        assert doc["concurrency"]["cancel-in-progress"] is False

    def test_documentation_explains_consolidated_board_build_architecture(self):
        src = _read()
        assert "consolidat" in src.lower()
        assert "queue: max" in src or "queue:max" in src.lower().replace(" ", "")


class TestDailyOperatingWindowFix:
    """Regression guard for the SEPARATE daily-operating-window coverage bug
    (found after the minute-cadence fix above): the cron was originally
    16:00-23:45 UTC + 00:00-05:45 UTC, completely inactive before 16:00
    UTC -- an early MLB day game (e.g. a real 12:10 PM ET game, T-90 =
    14:40 UTC) had T-90/T-60/T-30 silently never captured. See
    docs/HITTER_CHECKPOINT_COVERAGE_FIX.md Sec.9 and
    scripts/research/simulate_hitter_checkpoint_coverage.py's
    --full-day mode for the full exhaustive proof; this test only pins
    the workflow's own cron window so it can never silently regress."""

    def test_operating_window_starts_at_13_utc_not_16(self):
        doc = _load_hitter_workflow()
        schedules = doc[True]["schedule"]
        crons = [s["cron"] for s in schedules]
        daytime_cron = next(c for c in crons if c.startswith("*/15 13,"))
        assert daytime_cron == "*/15 13,14,15,16,17,18,19,20,21,22,23 * * *"
        assert not any(c.startswith("*/15 16,") for c in crons), \
            "operating window must not regress to the pre-fix 16:00 UTC start"

    def test_overnight_window_unchanged(self):
        doc = _load_hitter_workflow()
        schedules = doc[True]["schedule"]
        crons = [s["cron"] for s in schedules]
        assert "*/15 0,1,2,3,4,5 * * *" in crons

    def test_documentation_distinguishes_cadence_from_operating_window_coverage(self):
        src = _read()
        assert "16:00" in src and "13:00" in src
        assert "operating-window" in src.lower() or "operating window" in src.lower()
        assert "1,440" in src or "1440" in src
