#!/usr/bin/env python3
"""
tests/test_research_sharp_market_probe_workflow_guard.py
================================================================================
Structural + behavioral regression test for
.github/workflows/research-sharp-market-probe.yml -- same shape and
rationale as tests/test_research_multiseason_bullpen_backtest_workflow_guard.py
and tests/test_research_multiseason_starter_workload_backtest_workflow_guard.py.
"""
import os
import subprocess

import pytest

yaml = pytest.importorskip("yaml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_PATH = os.path.join(ROOT, ".github", "workflows", "research-sharp-market-probe.yml")


@pytest.fixture(scope="module")
def workflow():
    with open(WORKFLOW_PATH) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def steps(workflow):
    return workflow["jobs"]["probe"]["steps"]


@pytest.fixture(scope="module")
def guard_step(steps):
    return steps[0]


def test_guard_is_the_very_first_step(guard_step):
    assert "protected branch" in (guard_step.get("name") or "").lower()


def test_guard_runs_before_checkout(steps):
    names = [s.get("name", "") for s in steps]
    guard_index = next(i for i, n in enumerate(names) if "protected branch" in n.lower())
    checkout_index = next(i for i, n in enumerate(names) if "checkout" in n.lower())
    assert guard_index < checkout_index


def test_guard_step_is_never_continue_on_error(guard_step):
    assert guard_step.get("continue-on-error") is not True


def test_guard_references_target_branch_and_default_branch_inputs(guard_step):
    env = guard_step.get("env") or {}
    assert env.get("TARGET_BRANCH") == "${{ inputs.branch }}"
    assert env.get("DEFAULT_BRANCH") == "${{ github.event.repository.default_branch }}"


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

    def test_refuses_master(self, guard_step):
        result = _run_guard_script(guard_step, "master", "main")
        assert result.returncode != 0

    def test_refuses_the_actual_repository_default_branch_even_if_not_named_main(self, guard_step):
        result = _run_guard_script(guard_step, "trunk", "trunk")
        assert result.returncode != 0

    def test_allows_a_research_branch(self, guard_step):
        result = _run_guard_script(guard_step, "claude/historical-sharp-market-audit", "main")
        assert result.returncode == 0


def test_dependency_install_step_exists_before_probe_step(steps):
    names = [s.get("name", "") for s in steps]
    probe_step_index = next(i for i, s in enumerate(steps) if "inputs.script" in (s.get("run") or ""))
    install_step_index = next((i for i, n in enumerate(names) if "install test dependencies" in n.lower()), None)
    assert install_step_index is not None
    assert install_step_index < probe_step_index


def test_dependency_install_step_actually_installs_pytest_and_requirements_ci(steps):
    install_step = next(s for s in steps if "install test dependencies" in (s.get("name") or "").lower())
    run_script = install_step.get("run") or ""
    assert "pip install pytest" in run_script
    assert "requirements-ci.txt" in run_script


def test_probe_step_reads_odds_api_key_from_secrets_not_a_literal(steps):
    probe_step = next(s for s in steps if "inputs.script" in (s.get("run") or ""))
    env = probe_step.get("env") or {}
    assert env.get("ODDS_API_KEY") == "${{ secrets.ODDS_API_KEY }}"


def test_script_input_defaults_to_the_original_probe_script(workflow):
    # YAML 1.1 parses the bare `on:` key as the boolean True -- pyyaml
    # follows that spec, so the parsed dict key is True, not "on".
    on_section = workflow.get("on", workflow.get(True))
    script_input = on_section["workflow_dispatch"]["inputs"]["script"]
    assert script_input["default"] == "scripts/edgelab/backtest/probe_odds_api_historical_pinnacle.py"


def test_commit_paths_input_defaults_to_the_original_probe_cache_path(workflow):
    on_section = workflow.get("on", workflow.get(True))
    commit_paths_input = on_section["workflow_dispatch"]["inputs"]["commit_paths"]
    assert commit_paths_input["default"] == "data/research_cache/sharp_market_probe/"


def test_probe_step_uses_the_script_input_not_a_hardcoded_filename(steps):
    probe_step = next(s for s in steps if "inputs.script" in (s.get("run") or ""))
    run_script = probe_step.get("run") or ""
    assert "${{ inputs.script }}" in run_script


def test_commit_step_uses_the_commit_paths_input_never_a_hardcoded_production_path(steps):
    commit_step = next(s for s in steps if "commit probe result" in (s.get("name") or "").lower())
    run_script = commit_step.get("run") or ""
    assert "${{ inputs.commit_paths }}" in run_script
    assert "config/rules.json" not in run_script
    assert "bets.json" not in run_script
