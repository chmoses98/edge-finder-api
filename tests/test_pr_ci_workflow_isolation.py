#!/usr/bin/env python3
"""
tests/test_pr_ci_workflow_isolation.py
==========================================
Production Reliability and Settlement Recovery milestone: coverage for
.github/workflows/pr-ci.yml's isolation guarantees -- no secrets, no
writes to production files, no automated commits, read-only permissions.
"""
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PR_CI_PATH = os.path.join(ROOT, ".github", "workflows", "pr-ci.yml")


def _load():
    with open(PR_CI_PATH) as f:
        return yaml.safe_load(f)


def _all_run_scripts(doc):
    scripts = []
    for job in doc["jobs"].values():
        for step in job["steps"]:
            if "run" in step:
                scripts.append(step["run"])
    return scripts


class TestNoSecretsRequired:

    def test_no_step_references_secrets_context(self):
        for script in _all_run_scripts(_load()):
            assert "secrets." not in script, f"pr-ci.yml step references secrets: {script[:200]}"

    def test_no_env_block_references_secrets(self):
        doc = _load()
        for job in doc["jobs"].values():
            for step in job.get("steps", []):
                env = step.get("env", {})
                for value in env.values():
                    assert "secrets." not in str(value)


class TestReadOnlyPermissions:

    def test_top_level_permissions_are_contents_read(self):
        doc = _load()
        assert doc["permissions"] == {"contents": "read"}

    def test_no_job_overrides_permissions_to_write(self):
        doc = _load()
        for job in doc["jobs"].values():
            assert "permissions" not in job or job["permissions"].get("contents") != "write"


class TestNoAutomatedCommits:

    def test_no_step_configures_git_identity(self):
        for script in _all_run_scripts(_load()):
            assert "git config user" not in script

    def test_no_step_commits_or_pushes(self):
        for script in _all_run_scripts(_load()):
            assert "git commit" not in script
            assert "git push" not in script

    def test_no_step_writes_to_production_data_or_ledger_paths(self):
        production_paths = ("bets.json", "BET_LOG.md", "data/slate.json", "config/rules.json")
        for script in _all_run_scripts(_load()):
            for path in production_paths:
                assert f"> {path}" not in script and f">{path}" not in script, (
                    f"pr-ci.yml step appears to write to {path}"
                )


class TestTriggerAndConcurrency:

    def test_triggers_are_limited_to_pull_request_and_manual_dispatch(self):
        """
        pull_request is the automatic path; workflow_dispatch is the manual
        one, added after PR #230 received NO check run at all (run numbers go
        373 -> 375, no 374) while Actions was otherwise healthy. Without a
        dispatch entry point such a PR can only be merged unevaluated or have
        an empty commit pushed to bait the trigger.

        The trigger set stays CLOSED to these two. Nothing here may listen to
        `push`, `schedule`, `workflow_run` or `pull_request_target` -- the
        last especially, since it would run PR-authored code with repository
        credentials, which is exactly the isolation this file exists to pin.
        The other guarantees (no secrets, read-only permissions, no writes, no
        commits) are asserted by the tests above and are unaffected by
        dispatch: it runs the same job with the same permissions.
        """
        doc = _load()
        # PyYAML (1.1 spec) parses the bare `on:` key as the boolean True.
        trigger = doc.get("on", doc.get(True))
        if isinstance(trigger, str):
            trigger = [trigger]
        names = set(trigger)
        assert names <= {"pull_request", "workflow_dispatch"}, (
            "pr-ci.yml gained an unexpected trigger: %s" % sorted(names - {"pull_request", "workflow_dispatch"})
        )
        assert "pull_request" in names, "pr-ci.yml must still run automatically on PRs"

    def test_dispatch_cannot_be_used_to_run_pr_authored_code_with_credentials(self):
        """pull_request_target is the dangerous sibling -- it must never appear."""
        doc = _load()
        trigger = doc.get("on", doc.get(True))
        names = {trigger} if isinstance(trigger, str) else set(trigger)
        assert "pull_request_target" not in names
        assert doc["permissions"] == {"contents": "read"}

    def test_concurrency_group_is_scoped_per_pr_number(self):
        doc = _load()
        concurrency = doc["concurrency"]
        assert "github.event.pull_request.number" in concurrency["group"]
        # ...and must not collapse to one shared group when that is empty on a
        # manual dispatch, or each dispatch would cancel the previous one.
        assert "github.ref" in concurrency["group"]
        assert concurrency["cancel-in-progress"] is True

    def test_checkout_uses_full_history_for_changed_file_scope_tests(self):
        doc = _load()
        steps = doc["jobs"]["test"]["steps"]
        checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout"))
        assert checkout["with"]["fetch-depth"] == 0
