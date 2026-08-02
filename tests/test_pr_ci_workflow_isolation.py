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

    def test_only_triggers_on_pull_request(self):
        doc = _load()
        # PyYAML (1.1 spec) parses the bare `on:` key as the boolean True.
        trigger = doc.get("on", doc.get(True))
        assert trigger == "pull_request" or trigger == ["pull_request"] or (
            isinstance(trigger, dict) and set(trigger) == {"pull_request"}
        )

    def test_concurrency_group_is_scoped_per_pr_number(self):
        doc = _load()
        concurrency = doc["concurrency"]
        assert "github.event.pull_request.number" in concurrency["group"]
        assert concurrency["cancel-in-progress"] is True

    def test_checkout_uses_full_history_for_changed_file_scope_tests(self):
        doc = _load()
        steps = doc["jobs"]["test"]["steps"]
        checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout"))
        assert checkout["with"]["fetch-depth"] == 0
