#!/usr/bin/env python3
"""
tests/test_research_multiseason_bullpen_backtest_workflow_guard.py
========================================================================
Structural + behavioral regression test for the protected-branch guard
in .github/workflows/research-multiseason-bullpen-backtest.yml.

That workflow's final step pushes a commit (scripts/ci/git_data_commit.py)
to whatever branch `inputs.branch` names. Without an enforced guard, a
mistaken or malicious dispatch with branch=main/master/<default branch>
would land a research-cache commit directly on the branch this repo
ships from. This test proves the guard is (1) the very first step in
the job -- runs before checkout, before anything else -- and (2)
actually refuses/allows the right branches when its own shell script is
executed for real, not merely present in the YAML.
"""
import os
import subprocess

import pytest

yaml = pytest.importorskip("yaml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_PATH = os.path.join(ROOT, ".github", "workflows", "research-multiseason-bullpen-backtest.yml")


@pytest.fixture(scope="module")
def workflow():
    with open(WORKFLOW_PATH) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def steps(workflow):
    return workflow["jobs"]["backtest"]["steps"]


@pytest.fixture(scope="module")
def guard_step(steps):
    return steps[0]


def test_guard_is_the_very_first_step(guard_step):
    assert "protected branch" in (guard_step.get("name") or "").lower()


def test_guard_runs_before_checkout(steps):
    names = [s.get("name", "") for s in steps]
    guard_index = next(i for i, n in enumerate(names) if "protected branch" in n.lower())
    checkout_index = next(i for i, n in enumerate(names) if "checkout" in n.lower())
    assert guard_index < checkout_index, "the protected-branch guard must run before checkout, not after"


def test_guard_step_is_never_continue_on_error(guard_step):
    assert guard_step.get("continue-on-error") is not True


def test_guard_references_target_branch_and_default_branch_inputs(guard_step):
    env = guard_step.get("env") or {}
    assert env.get("TARGET_BRANCH") == "${{ inputs.branch }}"
    assert env.get("DEFAULT_BRANCH") == "${{ github.event.repository.default_branch }}"


# ── Behavioral: actually execute the guard's own shell script ──────────

def _run_guard_script(guard_step, target_branch, default_branch):
    script = guard_step["run"]
    env = dict(os.environ)
    env["TARGET_BRANCH"] = target_branch
    env["DEFAULT_BRANCH"] = default_branch
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)


class TestGuardBehavior:
    def test_refuses_main(self, guard_step):
        result = _run_guard_script(guard_step, "main", "main")
        assert result.returncode != 0
        assert "Refusing" in result.stderr or "Refusing" in result.stdout

    def test_refuses_master(self, guard_step):
        result = _run_guard_script(guard_step, "master", "main")
        assert result.returncode != 0

    def test_refuses_the_actual_repository_default_branch_even_if_not_named_main(self, guard_step):
        """The repo's default branch might not literally be named "main" --
        the guard must still catch it via DEFAULT_BRANCH, not just the
        literal strings "main"/"master"."""
        result = _run_guard_script(guard_step, "trunk", "trunk")
        assert result.returncode != 0

    def test_allows_a_research_branch(self, guard_step):
        result = _run_guard_script(guard_step, "claude/mlb-multiseason-bullpen-backtest-milestone4", "main")
        assert result.returncode == 0

    def test_allows_a_research_branch_when_default_branch_is_not_main(self, guard_step):
        result = _run_guard_script(guard_step, "claude/some-other-research-branch", "trunk")
        assert result.returncode == 0
