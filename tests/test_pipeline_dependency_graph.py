#!/usr/bin/env python3
"""
tests/test_pipeline_dependency_graph.py
=========================================
Behavioral regression tests for the execution/logging dependency graph in
.github/workflows/fetch-slate.yml (pre-merge hardening pass).

continue-on-error: true on the optional execution/logging steps (risk_gate,
write_pending_bets, validate_bet_logging, write_tracked_tickers,
capture_closing_lines) stops a failure there from failing the whole job —
which is what lets the final stage-status step always run. But
continue-on-error alone does NOT stop GitHub Actions from still running the
*next* step after a masked failure: a step only auto-skips when a prior step
actually failed the job, and continue-on-error prevents exactly that failing
state. So without explicit `if:` conditions, write_pending_bets.py would
still run on a data/slate.json that risk_gate.py failed to mutate, etc.

These tests do not spin up a real GitHub Actions runner. Instead they:

  1. Simulate GitHub Actions' own `if:` evaluation semantics (a step's
     condition is checked against the *actual outcome* of steps in this
     job so far; a step whose condition evaluates false gets outcome
     "skipped") against the real `if:` strings parsed out of
     fetch-slate.yml — not a hand-copied duplicate of them.

  2. Execute the literal jq filter embedded in fetch-slate.yml's final
     "Write pipeline stage-status..." step against synthetic step
     outcomes, so the "partial" vs "failed" vs "success" status logic is
     tested as actually written, not reimplemented in Python.
"""

import json
import os
import re
import subprocess

import pytest

yaml = pytest.importorskip("yaml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_PATH = os.path.join(ROOT, ".github", "workflows", "fetch-slate.yml")

ALL_STAGE_IDS = [
    "final_validate", "protect_slate", "publish_slate",
    "risk_gate", "write_pending_bets", "validate_bet_logging",
    "write_tracked_tickers", "capture_closing_lines",
]


@pytest.fixture(scope="module")
def workflow_steps():
    with open(WORKFLOW_PATH) as f:
        data = yaml.safe_load(f)
    return data["jobs"]["fetch"]["steps"]


@pytest.fixture(scope="module")
def jq_status_filter(workflow_steps):
    """
    Pull the literal jq invocation out of the final step's `run:` block —
    the exact text that ships in the workflow, not a reimplementation.
    """
    final_step = workflow_steps[-1]
    assert final_step.get("if") == "always()"
    run_text = final_step["run"]
    m = re.search(r"(jq -n.*?)\s*> data/pipeline_status\.json", run_text, re.S)
    assert m, "could not locate the jq invocation in the final step's run: block"
    return m.group(1)


# ── 1. Simulating GitHub Actions' `if:` evaluation ───────────────────────────

_COND_RE = re.compile(r"steps\.([\w]+)\.outcome == '(\w+)'")


def _condition_holds(condition, outcomes):
    """
    Evaluate the small subset of GitHub Actions `if:` expression syntax
    actually used in fetch-slate.yml: `None` (no condition => always runs),
    `always()`, or one/more `steps.<id>.outcome == '<value>'` clauses
    joined with `&&`.
    """
    if condition is None:
        return True
    if condition.strip() == "always()":
        return True
    clauses = [c.strip() for c in condition.split("&&")]
    for clause in clauses:
        m = _COND_RE.fullmatch(clause)
        assert m, f"unrecognized if: clause (extend the test evaluator): {clause!r}"
        step_id, expected = m.group(1), m.group(2)
        if outcomes.get(step_id) != expected:
            return False
    return True


def simulate_job(steps, intended_outcomes):
    """
    Walk the real step list in order. For each step with an id: if its real
    `if:` condition (evaluated against outcomes computed so far) is false,
    record outcome "skipped". Otherwise the step "runs" and takes whatever
    outcome the scenario says it would produce (default "success").

    This mirrors actual GitHub Actions behavior for the linear, no-matrix
    job in fetch-slate.yml: a step's `if:` is evaluated against the
    already-recorded outcomes of earlier steps in the same job.
    """
    outcomes = {}
    for step in steps:
        step_id = step.get("id")
        if not step_id:
            continue
        if _condition_holds(step.get("if"), outcomes):
            outcomes[step_id] = intended_outcomes.get(step_id, "success")
        else:
            outcomes[step_id] = "skipped"
    return outcomes


# ── 2. Running the real jq status filter against synthetic outcomes ─────────

def compute_pipeline_status(jq_filter, outcomes, run_id="12345", slate_date="2026-07-26"):
    """
    Substitute the same ${{ ... }} tokens GitHub Actions would substitute
    (textually, before the shell ever runs) and execute the literal jq
    filter from the workflow file against a synthetic outcome set.
    """
    substituted = jq_filter
    substituted = substituted.replace("${{ github.run_id }}", run_id)
    substituted = substituted.replace("${{ env.DATE }}", slate_date)
    for step_id in ALL_STAGE_IDS:
        token = "${{ steps.%s.outcome }}" % step_id
        substituted = substituted.replace(token, outcomes.get(step_id, "skipped"))
    # completedAt uses a literal $(date ...) subshell — harmless to actually run.
    substituted = substituted.replace(
        '--arg completedAt "$(date -u +%Y-%m-%dT%H:%M:%SZ)"',
        '--arg completedAt "2026-07-26T18:00:00Z"',
    )
    assert "${{" not in substituted, f"unsubstituted token remains: {substituted}"

    result = subprocess.run(
        ["bash", "-c", substituted], capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


# ── Scenarios ────────────────────────────────────────────────────────────────

REQUIRED_SUCCESS = {"final_validate": "success", "protect_slate": "success", "publish_slate": "success"}


class TestDependencySkipping:

    def test_risk_gate_fails_slate_remains_published(self, workflow_steps):
        outcomes = simulate_job(
            workflow_steps,
            {**REQUIRED_SUCCESS, "risk_gate": "failure"},
        )
        assert outcomes["publish_slate"] == "success", (
            "publish_slate must already have completed before risk_gate runs "
            "at all — its outcome cannot be affected by a later step failing"
        )

    def test_risk_gate_fails_pending_bet_logging_is_skipped(self, workflow_steps):
        outcomes = simulate_job(
            workflow_steps,
            {**REQUIRED_SUCCESS, "risk_gate": "failure"},
        )
        assert outcomes["write_pending_bets"] == "skipped", (
            "write_pending_bets must not run against a slate risk_gate failed "
            "to mutate"
        )
        # And everything downstream of it cascades to skipped too.
        assert outcomes["validate_bet_logging"] == "skipped"
        assert outcomes["write_tracked_tickers"] == "skipped"

    def test_write_pending_bets_fails_validation_is_skipped(self, workflow_steps):
        outcomes = simulate_job(
            workflow_steps,
            {**REQUIRED_SUCCESS, "risk_gate": "success", "write_pending_bets": "failure"},
        )
        assert outcomes["validate_bet_logging"] == "skipped", (
            "validate_bet_logging must not run against a bets.json "
            "write_pending_bets failed to finish writing"
        )
        assert outcomes["write_tracked_tickers"] == "skipped"

    def test_validate_bet_logging_fails_ticker_writing_is_skipped(self, workflow_steps):
        outcomes = simulate_job(
            workflow_steps,
            {
                **REQUIRED_SUCCESS,
                "risk_gate": "success",
                "write_pending_bets": "success",
                "validate_bet_logging": "failure",
            },
        )
        assert outcomes["write_tracked_tickers"] == "skipped"

    def test_capture_closing_lines_independent_of_bet_logging_chain(self, workflow_steps):
        """
        capture_closing_lines reads only the Kalshi market registry (built in
        the required section of the job) — it must still run even when the
        entire bet-logging chain fails, since it has no dependency on it.
        """
        outcomes = simulate_job(
            workflow_steps,
            {**REQUIRED_SUCCESS, "risk_gate": "failure", "capture_closing_lines": "success"},
        )
        assert outcomes["capture_closing_lines"] == "success", (
            "capture_closing_lines must run independently of the "
            "risk_gate/write_pending_bets/validate_bet_logging/"
            "write_tracked_tickers chain"
        )

    def test_all_execution_stages_run_normally_when_prerequisites_succeed(self, workflow_steps):
        outcomes = simulate_job(
            workflow_steps,
            {
                **REQUIRED_SUCCESS,
                "risk_gate": "success",
                "write_pending_bets": "success",
                "validate_bet_logging": "success",
                "write_tracked_tickers": "success",
                "capture_closing_lines": "success",
            },
        )
        for step_id in [
            "risk_gate", "write_pending_bets", "validate_bet_logging",
            "write_tracked_tickers", "capture_closing_lines",
        ]:
            assert outcomes[step_id] == "success", f"{step_id} should have run and succeeded"

    def test_publish_fails_entire_optional_chain_is_skipped(self, workflow_steps):
        outcomes = simulate_job(
            workflow_steps,
            {"final_validate": "success", "protect_slate": "success", "publish_slate": "failure"},
        )
        for step_id in [
            "risk_gate", "write_pending_bets", "validate_bet_logging",
            "write_tracked_tickers", "capture_closing_lines",
        ]:
            assert outcomes[step_id] == "skipped"


class TestPipelineStatusComputation:
    """Exercises the literal jq filter from fetch-slate.yml, not a reimplementation."""

    def test_partial_when_publish_succeeds_but_risk_gate_fails(self, workflow_steps, jq_status_filter):
        outcomes = simulate_job(
            workflow_steps,
            {**REQUIRED_SUCCESS, "risk_gate": "failure"},
        )
        status = compute_pipeline_status(jq_status_filter, outcomes)
        assert status["status"] == "partial"
        assert status["stages"]["publish"]["status"] == "success"
        assert status["stages"]["risk_gate"]["status"] == "failure"

    def test_partial_when_downstream_stage_is_skipped_not_failed(self, workflow_steps, jq_status_filter):
        """A skipped stage (not just a failed one) must also yield 'partial', never 'success'."""
        outcomes = simulate_job(
            workflow_steps,
            {**REQUIRED_SUCCESS, "risk_gate": "failure"},
        )
        assert outcomes["write_pending_bets"] == "skipped"
        status = compute_pipeline_status(jq_status_filter, outcomes)
        assert status["stages"]["write_pending_bets"]["status"] == "skipped"
        assert status["status"] == "partial"

    def test_failed_when_publish_fails(self, workflow_steps, jq_status_filter):
        outcomes = simulate_job(
            workflow_steps,
            {"final_validate": "success", "protect_slate": "success", "publish_slate": "failure"},
        )
        status = compute_pipeline_status(jq_status_filter, outcomes)
        assert status["status"] == "failed"
        assert status["stages"]["publish"]["status"] == "failure"

    def test_success_when_everything_succeeds(self, workflow_steps, jq_status_filter):
        outcomes = simulate_job(
            workflow_steps,
            {
                **REQUIRED_SUCCESS,
                "risk_gate": "success",
                "write_pending_bets": "success",
                "validate_bet_logging": "success",
                "write_tracked_tickers": "success",
                "capture_closing_lines": "success",
            },
        )
        status = compute_pipeline_status(jq_status_filter, outcomes)
        assert status["status"] == "success"

    def test_failed_takes_priority_over_partial(self, workflow_steps, jq_status_filter):
        """Even if some optional stage looks fine, a failed publish is always 'failed', never 'partial'."""
        outcomes = {
            "final_validate": "success", "protect_slate": "success", "publish_slate": "failure",
            "risk_gate": "skipped", "write_pending_bets": "skipped",
            "validate_bet_logging": "skipped", "write_tracked_tickers": "skipped",
            "capture_closing_lines": "skipped",
        }
        status = compute_pipeline_status(jq_status_filter, outcomes)
        assert status["status"] == "failed"
