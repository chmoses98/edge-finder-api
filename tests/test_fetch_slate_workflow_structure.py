#!/usr/bin/env python3
"""
tests/test_fetch_slate_workflow_structure.py
==============================================
Structural regression test for .github/workflows/fetch-slate.yml.

Guards the decoupling introduced to fix the 2026-07-25/07-26 incident:
optional execution/logging steps (risk gate, bet logging, CLV capture)
must never be able to block publication of the authoritative slate and
data/meta.json. This test does not run the workflow — it asserts the YAML
structure enforces the invariant:

  1. The step that commits data/meta.json ("publish_slate") runs BEFORE
     the execution/logging steps (risk_gate, write_pending_bets,
     validate_bet_logging, write_tracked_tickers, capture_closing_lines).
  2. Every execution/logging step has continue-on-error: true, so a
     failure there does not fail the job and does not skip later steps.
  3. The final stage-status step runs unconditionally (if: always()) so
     a stage-status artifact is always produced, and it runs AFTER the
     execution/logging chain so it can report on their outcomes.
"""

import os

import pytest

yaml = pytest.importorskip("yaml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_PATH = os.path.join(ROOT, ".github", "workflows", "fetch-slate.yml")

OPTIONAL_STEP_IDS = [
    "risk_gate",
    "write_pending_bets",
    "validate_bet_logging",
    "write_tracked_tickers",
    "capture_closing_lines",
]


@pytest.fixture(scope="module")
def steps():
    with open(WORKFLOW_PATH) as f:
        data = yaml.safe_load(f)
    return data["jobs"]["fetch"]["steps"]


def _index_by_id(steps, step_id):
    for i, s in enumerate(steps):
        if s.get("id") == step_id:
            return i
    raise AssertionError(f"No step with id={step_id!r} found in {WORKFLOW_PATH}")


def test_publish_slate_step_exists_and_commits_meta(steps):
    idx = _index_by_id(steps, "publish_slate")
    assert "meta.json" in steps[idx]["run"]


def test_publish_slate_precedes_every_optional_execution_step(steps):
    """
    The authoritative slate/meta commit must run before any step whose
    failure is allowed (continue-on-error) — otherwise a downstream
    execution/logging failure can still race the publish step or leave
    ordering unclear on future edits.
    """
    publish_idx = _index_by_id(steps, "publish_slate")
    for step_id in OPTIONAL_STEP_IDS:
        opt_idx = _index_by_id(steps, step_id)
        assert opt_idx > publish_idx, (
            f"step id={step_id!r} (index {opt_idx}) must come AFTER "
            f"publish_slate (index {publish_idx}) so its failure cannot "
            f"block authoritative slate publication"
        )


def test_every_optional_execution_step_has_continue_on_error(steps):
    for step_id in OPTIONAL_STEP_IDS:
        idx = _index_by_id(steps, step_id)
        assert steps[idx].get("continue-on-error") is True, (
            f"step id={step_id!r} must set continue-on-error: true so its "
            f"failure does not fail the job or block later steps"
        )


def test_publish_slate_step_itself_is_not_continue_on_error(steps):
    """
    publish_slate must remain a hard step (no continue-on-error) — if
    committing the authoritative slate itself fails, that IS a real
    publication failure and must be visible as a job failure, not silently
    swallowed.
    """
    idx = _index_by_id(steps, "publish_slate")
    assert steps[idx].get("continue-on-error") is not True


def test_final_stage_status_step_runs_always_and_last(steps):
    last = steps[-1]
    assert last.get("if") == "always()", (
        "the final stage-status step must run unconditionally (if: always()) "
        "so a stage-status artifact is produced even if execution/logging "
        "steps failed"
    )
    assert "pipeline_status.json" in last["run"]


def test_final_stage_status_step_is_last_and_after_optional_steps(steps):
    last_idx = len(steps) - 1
    for step_id in OPTIONAL_STEP_IDS:
        opt_idx = _index_by_id(steps, step_id)
        assert opt_idx < last_idx, (
            f"step id={step_id!r} must run before the final stage-status step"
        )


# ── Prerequisite-dependency conditions (pre-merge hardening pass) ────────────
#
# continue-on-error alone only stops a failure from failing the *job* — it
# does not stop GitHub Actions from still running the *next* step by default.
# Each optional step must therefore carry an explicit `if:` that checks the
# actual outcome of whatever it depends on, so a failed prerequisite is never
# silently followed by a step that assumes it succeeded.

EXPECTED_IF_CONDITIONS = {
    # risk_gate mutates data/slate.json written by publish_slate; no other
    # prerequisite in the optional chain.
    "risk_gate": "steps.publish_slate.outcome == 'success'",
    # write_pending_bets reads data/slate.json AFTER risk_gate's in-place
    # mutation (TT downgrades) — must not run against a slate risk_gate
    # failed to produce.
    "write_pending_bets": "steps.risk_gate.outcome == 'success'",
    # validate_bet_logging compares bets.json against the ledger; bets.json
    # is only trustworthy once write_pending_bets has finished.
    "validate_bet_logging": "steps.write_pending_bets.outcome == 'success'",
    # write_tracked_tickers registers CLV tracking for bets that were both
    # logged AND confirmed consistent with the ledger — requires both.
    "write_tracked_tickers": (
        "steps.write_pending_bets.outcome == 'success' && "
        "steps.validate_bet_logging.outcome == 'success'"
    ),
    # capture_closing_lines (snapshot mode) reads only
    # data/kalshi_market_registry.json, built earlier in the required
    # section of the job — independent of the bet-logging chain.
    "capture_closing_lines": "steps.publish_slate.outcome == 'success'",
}


def test_optional_steps_have_the_expected_prerequisite_conditions(steps):
    for step_id, expected_if in EXPECTED_IF_CONDITIONS.items():
        idx = _index_by_id(steps, step_id)
        actual_if = steps[idx].get("if")
        assert actual_if == expected_if, (
            f"step id={step_id!r} if-condition mismatch.\n"
            f"  expected: {expected_if!r}\n"
            f"  actual:   {actual_if!r}"
        )


def test_write_pending_bets_does_not_run_unconditionally(steps):
    """
    Guards specifically against the reviewed gap: continue-on-error alone
    would let write_pending_bets run even after risk_gate failed. It must
    have a condition at all (not None), and that condition must reference
    risk_gate's outcome.
    """
    idx = _index_by_id(steps, "write_pending_bets")
    cond = steps[idx].get("if")
    assert cond is not None, "write_pending_bets must not run unconditionally"
    assert "risk_gate" in cond and "success" in cond


def test_validate_bet_logging_step_name_clarifies_scope(steps):
    """
    The step name must make clear this is a hard gate for the execution
    chain only, not for authoritative slate publication (which already
    completed in publish_slate, earlier in the job).
    """
    idx = _index_by_id(steps, "validate_bet_logging")
    name = steps[idx]["name"].lower()
    assert "execution" in name or "does not affect" in name or "not affect" in name, (
        f"validate_bet_logging step name must clarify it is scoped to the "
        f"execution chain, not slate publication. Got: {steps[idx]['name']!r}"
    )
