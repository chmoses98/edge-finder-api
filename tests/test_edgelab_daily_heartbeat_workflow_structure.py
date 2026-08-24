#!/usr/bin/env python3
"""
tests/test_edgelab_daily_heartbeat_workflow_structure.py
============================================================
Structural regression test for .github/workflows/edgelab-daily-heartbeat.yml
-- Pipeline Health Incident guardrail (2026-08-24): this workflow exists
specifically to fail loudly when the daily EdgeLab pipeline is
unhealthy, so its two invariants must never regress silently:
  1. it has NO workflow_run trigger off any of the systems it watches
     (fetch-slate.yml, edgelab-postgame.yml, snapshot-capture-check.yml)
     -- the whole point is detecting their absence, so it must never
     depend on them firing;
  2. its final "fail the workflow" step is never continue-on-error and
     always actually propagates the health-check step's own outcome --
     an unhealthy daily state must never be silently turned green.
"""
import os

import pytest

yaml = pytest.importorskip("yaml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_PATH = os.path.join(ROOT, ".github", "workflows", "edgelab-daily-heartbeat.yml")
WATCHED_WORKFLOW_NAMES = {"Fetch Slate Data", "EdgeLab Postgame Settlement", "EdgeLab Snapshot Capture Check",
                          "Update CLV (Post-Slate Review)"}


@pytest.fixture(scope="module")
def workflow():
    with open(WORKFLOW_PATH) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def steps(workflow):
    return workflow["jobs"]["heartbeat"]["steps"]


def _index_by_name_substring(steps, substring):
    for i, s in enumerate(steps):
        if substring in (s.get("name") or ""):
            return i
    raise AssertionError(f"No step with name containing {substring!r} found in {WORKFLOW_PATH}")


def test_has_its_own_schedule_trigger(workflow):
    on = workflow.get(True) or workflow.get("on")  # PyYAML parses bare `on:` key as boolean True
    assert "schedule" in on
    assert on["schedule"], "must have at least one real cron entry"


def test_never_triggered_by_workflow_run_off_any_watched_system(workflow):
    on = workflow.get(True) or workflow.get("on")
    assert "workflow_run" not in on, (
        "this workflow's entire purpose is detecting the ABSENCE of fetch-slate.yml / "
        "edgelab-postgame.yml / snapshot-capture-check.yml firing -- it must never itself "
        "depend on one of them completing"
    )


def test_supports_manual_dispatch_with_optional_date_override(workflow):
    on = workflow.get(True) or workflow.get("on")
    assert "workflow_dispatch" in on
    assert "date" in (on["workflow_dispatch"].get("inputs") or {})


def test_health_check_step_has_no_continue_on_error(steps):
    idx = _index_by_name_substring(steps, "Run daily pipeline health check")
    assert "continue-on-error" not in steps[idx], (
        "the health-check step's own exit code must survive to the final fail-loud step -- "
        "continue-on-error here would let an unhealthy day pass through silently"
    )


def test_commit_step_runs_always_regardless_of_health_outcome(steps):
    idx = _index_by_name_substring(steps, "Commit health artifact")
    assert steps[idx].get("if") == "always()", (
        "the health artifact must be written and committed even on an UNHEALTHY day -- "
        "otherwise there is a red check with no record explaining why"
    )


def test_commit_step_only_touches_the_health_directory(steps):
    idx = _index_by_name_substring(steps, "Commit health artifact")
    run_text = steps[idx]["run"]
    assert "data/edgelab/health/" in run_text
    for forbidden in ("data/pipeline", "recommendations/", "model_evaluations/", "settlements/", "snapshots/", "bets.json"):
        assert forbidden not in run_text, f"heartbeat commit step must never touch {forbidden!r} -- it is observational only"


def test_final_fail_step_runs_always_and_is_not_continue_on_error(steps):
    idx = _index_by_name_substring(steps, "Fail the workflow if today's pipeline health is unhealthy")
    step = steps[idx]
    assert step.get("if") == "always()"
    assert "continue-on-error" not in step, (
        "this is the step whose own conclusion becomes the workflow's real conclusion -- "
        "continue-on-error here would defeat the entire fail-loudly requirement"
    )


def test_final_fail_step_actually_propagates_the_health_step_outcome(steps):
    idx = _index_by_name_substring(steps, "Fail the workflow if today's pipeline health is unhealthy")
    run_text = steps[idx]["run"]
    assert "steps.health.outcome" in run_text
    assert "exit 1" in run_text


def test_commit_step_precedes_the_final_fail_step(steps):
    commit_idx = _index_by_name_substring(steps, "Commit health artifact")
    fail_idx = _index_by_name_substring(steps, "Fail the workflow if today's pipeline health is unhealthy")
    assert commit_idx < fail_idx, "the health artifact must be committed before the workflow's own conclusion is decided"


def test_concurrency_group_is_independent_of_every_other_workflow(workflow):
    group = workflow["concurrency"]["group"]
    assert group == "edgelab-daily-heartbeat"
