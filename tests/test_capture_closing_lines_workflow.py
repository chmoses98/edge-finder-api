#!/usr/bin/env python3
"""
tests/test_capture_closing_lines_workflow.py
================================================
Structural + safety-isolation tests for
.github/workflows/capture-closing-lines.yml — the scheduled, every-5-minute
pregame closing-line collector.
"""
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_PATH = os.path.join(ROOT, ".github", "workflows", "capture-closing-lines.yml")

FORBIDDEN_SCRIPTS = (
    "build_market_ledger.py",
    "validate_slate_final.py",
    "risk_gate.py",
    "write_pending_bets.py",
    "validate_bet_logging.py",
    "generate_f5_audit.py",
    "regression_test.py",
)


def _read():
    with open(WORKFLOW_PATH) as f:
        return f.read()


def _doc():
    with open(WORKFLOW_PATH) as f:
        return yaml.safe_load(f)


def _run_bodies():
    doc = _doc()
    bodies = []
    for job in doc.get("jobs", {}).values():
        for step in job.get("steps", []):
            if "run" in step:
                bodies.append(step["run"])
    return "\n".join(bodies)


def _commit_step_body():
    """The specific step that stages/commits/pushes the registry + audit
    log — isolated so structural assertions (commit-before-push, staged
    paths, etc.) can't accidentally match unrelated steps."""
    doc = _doc()
    for job in doc.get("jobs", {}).values():
        for step in job.get("steps", []):
            if "commit" in (step.get("name") or "").lower() and "run" in step:
                return step["run"]
    raise AssertionError("no commit/push step found in workflow")


class TestWorkflowStructure:

    def test_valid_yaml(self):
        assert _doc() is not None

    def test_scheduled_every_5_minutes(self):
        doc = _doc()
        triggers = doc.get(True, doc.get("on"))
        schedules = triggers.get("schedule", [])
        assert any(s.get("cron") == "*/5 * * * *" for s in schedules)

    def test_workflow_dispatch_with_optional_date(self):
        doc = _doc()
        triggers = doc.get(True, doc.get("on"))
        assert "workflow_dispatch" in triggers
        inputs = triggers["workflow_dispatch"]["inputs"]
        assert "date" in inputs
        assert inputs["date"]["required"] is False
        assert inputs["date"]["default"] == ""

    def test_permissions_contents_write(self):
        doc = _doc()
        assert doc.get("permissions", {}).get("contents") == "write"

    def test_concurrency_prevents_overlap(self):
        doc = _doc()
        concurrency = doc.get("concurrency")
        assert concurrency is not None
        assert concurrency.get("group")
        # Must not cancel an in-progress run mid-write (that could corrupt
        # a partially-written commit) — it should queue, not cancel.
        assert concurrency.get("cancel-in-progress") is False

    def test_checks_out_main(self):
        doc = _doc()
        steps = doc["jobs"]["capture"]["steps"]
        checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout"))
        assert checkout["with"]["ref"] == "main"

    def test_resolves_et_date_when_not_provided(self):
        src = _read()
        assert "America/New_York" in src
        assert "github.event.inputs.date" in src

    def test_runs_capture_script_with_date_arg(self):
        run_bodies = _run_bodies()
        assert "scripts/capture_pregame_closing_lines.py" in run_bodies
        assert 'capture_pregame_closing_lines.py "${{ env.DATE }}"' in run_bodies

    def test_commits_only_registry_and_clv_log(self):
        src = _read()
        assert "data/kalshi_market_registry.json" in src
        assert "data/clv/" in src
        assert "closing_capture_log.json" in src
        # Must not stage the whole data/ directory or bets.json.
        assert "git add data/\n" not in src
        assert "git add data\n" not in src
        assert "git add bets.json" not in src
        assert "git add ." not in src
        assert "git add -A" not in src

    def test_commit_only_when_changed(self):
        """The empty-diff-is-a-no-op guard now lives inside
        scripts/ci/git_data_commit.py's commit_and_push() (see
        tests/test_git_data_commit.py::TestCleanPathStillWorks::test_no_op_when_nothing_changed)
        rather than being reimplemented inline here."""
        src = _read()
        assert "scripts/ci/git_data_commit.py" in src

    def test_safe_commit_pattern_fetch_rebase_push(self):
        """fetch/rebase(--autostash)/push are now performed by
        scripts/ci/git_data_commit.py itself, never trusting `git
        rebase`'s own exit code for a conflicted autostash pop (see that
        script's module docstring and tests/test_git_data_commit.py) --
        this workflow's job is simply to delegate to it."""
        run_bodies = _run_bodies()
        assert "python3 scripts/ci/git_data_commit.py" in run_bodies
        assert "git rebase --autostash" not in run_bodies

    def test_exits_cleanly_with_nothing_to_commit(self):
        """scripts/ci/git_data_commit.py itself exits 0 for both the
        no-matching-path case ("Nothing to commit") and the unchanged-diff
        case ("No changes to commit") -- see its commit_and_push()."""
        src = _read()
        assert "scripts/ci/git_data_commit.py" in src

    def test_never_invokes_forbidden_stages(self):
        run_bodies = _run_bodies()
        for forbidden in FORBIDDEN_SCRIPTS:
            assert forbidden not in run_bodies, f"capture-closing-lines.yml invokes forbidden script: {forbidden}"

    def test_never_touches_bets_json(self):
        run_bodies = _run_bodies()
        assert "bets.json" not in run_bodies


class TestCommitBeforePush:
    """
    Regression coverage for the specific bug found in review: the step
    staged files and ran `git push origin HEAD:main` without ever calling
    `git commit`, so the push had nothing new to send. Commit-before-push
    ordering, staged-path scoping, the empty-diff no-op, and non-silent
    persistent push failure are now all guaranteed by
    scripts/ci/git_data_commit.py itself (see tests/test_git_data_commit.py
    for the dedicated regression coverage of each property) -- these tests
    now confirm the workflow actually delegates to it, with the same two
    intended paths and commit-message shape, rather than reimplementing
    that logic inline (which is what let the original bug happen).
    """

    def test_git_commit_step_is_present(self):
        body = _commit_step_body()
        assert "python3 scripts/ci/git_data_commit.py" in body

    def test_commit_occurs_before_push(self):
        """No longer applicable as an inline-ordering check -- commit-
        before-push ordering is enforced inside git_data_commit.py's own
        commit_and_push() (see
        tests/test_git_data_commit.py::TestCleanPathStillWorks::test_new_file_commits_and_pushes)."""
        body = _commit_step_body()
        assert "python3 scripts/ci/git_data_commit.py" in body
        assert "git commit -m" not in body

    def test_commit_message_includes_date_and_timestamp(self):
        body = _commit_step_body()
        assert '--message "closing lines: ${{ env.DATE }}' in body
        # Must embed a real UTC timestamp, not just the date, so distinct
        # captures for the same date produce distinct commit messages.
        assert "date -u +" in body

    def test_only_two_paths_ever_staged_in_commit_step(self):
        body = _commit_step_body()
        assert "python3 scripts/ci/git_data_commit.py" in body
        assert "data/kalshi_market_registry.json" in body
        assert "closing_capture_log.json" in body
        # And never a blanket add -- the script is only ever handed these
        # two explicit paths, never a bare `data/` or `.`.
        assert "git add data/\n" not in body
        assert "git add ." not in body
        assert "git add -A" not in body

    def test_empty_diff_exits_cleanly_before_commit(self):
        """The empty-diff no-op is now handled inside
        scripts/ci/git_data_commit.py's commit_and_push() (see
        tests/test_git_data_commit.py::TestCleanPathStillWorks::test_no_op_when_nothing_changed)."""
        body = _commit_step_body()
        assert "python3 scripts/ci/git_data_commit.py" in body

    def test_checkout_pinned_to_main_explicitly(self):
        doc = _doc()
        steps = doc["jobs"]["capture"]["steps"]
        checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout"))
        assert checkout.get("with", {}).get("ref") == "main"

    def test_push_failure_after_retries_is_not_silent_success(self):
        """A persistent push failure is surfaced via
        scripts/ci/git_data_commit.py's own non-zero exit code (see its
        commit_and_push(), which returns 1 after exhausting its retries) --
        this step has no `|| true`/`|| echo` swallowing that failure."""
        body = _commit_step_body()
        assert "python3 scripts/ci/git_data_commit.py" in body
        assert "|| true" not in body
        assert "|| echo" not in body

    def test_retry_protection_against_concurrent_main_updates(self):
        """Re-fetching and rebasing before each retried push is now
        implemented once, centrally, in
        scripts/ci/git_data_commit.py::commit_and_push()'s own retry loop
        (see tests/test_git_data_commit.py) rather than duplicated inline
        in every workflow."""
        body = _commit_step_body()
        assert "python3 scripts/ci/git_data_commit.py" in body
