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
        src = _read()
        assert "git diff --cached --quiet" in src

    def test_safe_commit_pattern_fetch_rebase_push(self):
        run_bodies = _run_bodies()
        assert "git fetch origin main" in run_bodies
        assert "git rebase --autostash origin/main" in run_bodies
        assert "git push origin HEAD:main" in run_bodies

    def test_exits_cleanly_with_nothing_to_commit(self):
        src = _read()
        assert "nothing to commit" in src.lower() or "no game inside the capture window" in src.lower()

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
    `git commit`, so the push had nothing new to send. These tests assert
    the fixed step actually commits, in the right order, and only stages
    the two intended paths.
    """

    def test_git_commit_step_is_present(self):
        body = _commit_step_body()
        assert "git commit -m" in body

    def test_commit_occurs_before_push(self):
        body = _commit_step_body()
        commit_idx = body.index("git commit -m")
        push_idx = body.index("git push origin HEAD:main")
        assert commit_idx < push_idx, "git commit must run before git push"

    def test_commit_message_includes_date_and_timestamp(self):
        body = _commit_step_body()
        assert 'git commit -m "closing lines: ${{ env.DATE }}' in body
        # Must embed a real UTC timestamp, not just the date, so distinct
        # captures for the same date produce distinct commit messages.
        assert "date -u +" in body

    def test_only_two_paths_ever_staged_in_commit_step(self):
        body = _commit_step_body()
        add_lines = [line.strip() for line in body.splitlines() if "git add" in line]
        assert add_lines, "expected at least one git add line"
        allowed_fragments = ("data/kalshi_market_registry.json", "closing_capture_log.json")
        for line in add_lines:
            assert any(frag in line for frag in allowed_fragments), (
                f"unexpected staged path in commit step: {line!r}"
            )
        # And never a blanket add.
        assert "git add data/\n" not in body
        assert "git add ." not in body
        assert "git add -A" not in body

    def test_empty_diff_exits_cleanly_before_commit(self):
        body = _commit_step_body()
        # The empty-diff check must come before the commit call, and must
        # exit 0 (a clean, successful no-op) rather than falling through
        # into a commit/push attempt with nothing staged.
        diff_check_idx = body.index("git diff --cached --quiet")
        commit_idx = body.index("git commit -m")
        assert diff_check_idx < commit_idx
        # Structural check: the exit 0 for the empty-diff branch appears
        # between the diff check and the commit call.
        between = body[diff_check_idx:commit_idx]
        assert "exit 0" in between

    def test_checkout_pinned_to_main_explicitly(self):
        doc = _doc()
        steps = doc["jobs"]["capture"]["steps"]
        checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout"))
        assert checkout.get("with", {}).get("ref") == "main"

    def test_push_failure_after_retries_is_not_silent_success(self):
        body = _commit_step_body()
        # After the retry loop, a persistent push failure must exit
        # non-zero, not silently report success.
        push_idx = body.index("git push origin HEAD:main")
        tail = body[push_idx:]
        assert "exit 1" in tail
        # Must not swallow a real failure behind a bare `exit 0` after the
        # retry loop (that was the pre-fix silent-success bug pattern).
        assert "will retry on next schedule run\"\n          exit 0" not in body

    def test_retry_protection_against_concurrent_main_updates(self):
        body = _commit_step_body()
        # Must re-fetch and rebase before each retried push, not just
        # blindly retry the same push against a now-stale base.
        push_idx = body.index("git push origin HEAD:main")
        tail = body[push_idx:]
        assert "git fetch origin main" in tail
        assert "git rebase origin/main" in tail
        assert "for attempt" in body
