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

Extended by the Heartbeat False-Failure Incident (2026-08-27) with the
third invariant that fix depends on:
  3. the target production date is resolved ONCE, before the health
     check, from the run's own scheduled-event metadata, and handed to
     scripts/edgelab/daily_health_check.py explicitly -- the health
     check must never be left to infer a scheduled run's date from the
     wall clock at process start, which is what turned a cron delayed to
     05:06 UTC into a full sheet of false MISSING_* failures for a
     production date whose slate cycle had not begun.
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


class TestTargetDateResolution:
    """Heartbeat False-Failure Incident (2026-08-27) -- one authoritative date path."""

    def test_target_date_is_resolved_in_its_own_step_before_the_health_check(self, steps):
        resolve_idx = _index_by_name_substring(steps, "Resolve heartbeat target date")
        health_idx = _index_by_name_substring(steps, "Run daily pipeline health check")
        assert resolve_idx < health_idx
        assert "scripts/edgelab/resolve_heartbeat_target.py" in steps[resolve_idx]["run"]

    def test_the_resolver_step_is_never_continue_on_error(self, steps):
        step = steps[_index_by_name_substring(steps, "Resolve heartbeat target date")]
        assert "continue-on-error" not in step, (
            "a heartbeat that cannot determine WHICH production date it is validating must "
            "go red, never fall back to the wall clock and validate the wrong day"
        )

    def test_the_resolver_receives_the_runs_own_schedule_metadata(self, steps):
        env = steps[_index_by_name_substring(steps, "Resolve heartbeat target date")]["env"]
        assert "github.event.schedule" in env["HEARTBEAT_SCHEDULE_EXPRESSION"], (
            "the cron literal must reach the resolver from the workflow's own schedule block -- "
            "never be re-declared in Python, where it could drift from this file"
        )
        assert "github.event.inputs.date" in env["HEARTBEAT_DISPATCH_DATE"]
        assert "github.run_started_at" in env["HEARTBEAT_RUN_STARTED_AT"]
        assert "github.token" in env["GITHUB_TOKEN"], (
            "the durable re-run-safe anchor is the run's REST created_at, which needs the token"
        )

    def test_the_health_check_is_given_the_resolved_date_explicitly(self, steps):
        run_text = steps[_index_by_name_substring(steps, "Run daily pipeline health check")]["run"]
        assert "steps.target.outputs.target_date" in run_text, (
            "the health check must be told which production date to validate -- inferring it "
            "from the process wall clock is the 2026-08-27 incident itself"
        )
        assert "--resolution-file" in run_text, "the artifact must carry its own date-resolution audit trail"
        assert "github.event.inputs.date" not in run_text, (
            "manual dispatch dates now flow through the single resolver, not a second inline expression"
        )

    def test_the_schedule_block_remains_the_only_place_a_cron_time_is_declared(self, workflow):
        on = workflow.get(True) or workflow.get("on")
        crons = [entry["cron"] for entry in on["schedule"]]
        assert crons == ["45 23 * * *"]
        steps_text = yaml.safe_dump(workflow["jobs"]["heartbeat"]["steps"])
        for cron in crons:
            assert cron not in steps_text, "no step may hardcode the schedule it is supposed to inherit"

    def test_manual_dispatch_date_input_is_still_offered_and_documented(self, workflow):
        on = workflow.get(True) or workflow.get("on")
        date_input = on["workflow_dispatch"]["inputs"]["date"]
        assert date_input.get("required") is False
        assert "YYYY-MM-DD" in date_input["description"]
