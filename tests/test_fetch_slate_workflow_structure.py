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


def _index_by_name_substring(steps, substring):
    for i, s in enumerate(steps):
        if substring in (s.get("name") or ""):
            return i
    raise AssertionError(f"No step with name containing {substring!r} found in {WORKFLOW_PATH}")


def test_stage_status_step_runs_always_and_after_optional_steps(steps):
    """
    Historical Capture Completeness and Immutable Snapshot Foundation
    milestone: two new, purely-additive Snapshot-capture steps
    ("Create immutable PRE_GAME_DECISION snapshot", "Commit snapshot
    artifacts") now run AFTER the stage-status step, so it is no longer
    literally the LAST step in the job -- but it remains the last
    PRODUCTION-artifact step, and still runs unconditionally so a
    stage-status artifact is always produced even if execution/logging
    steps failed. The snapshot steps that follow it never touch
    data/pipeline_status.json, bets.json, or data/slate.json (see
    docs/SNAPSHOT_ARCHITECTURE.md) -- this test's actual invariant (stage
    status reflects the full production run, unconditionally) is
    unaffected by their addition.
    """
    idx = _index_by_name_substring(steps, "Write pipeline stage-status")
    stage_status_step = steps[idx]
    assert stage_status_step.get("if") == "always()", (
        "the stage-status step must run unconditionally (if: always()) "
        "so a stage-status artifact is produced even if execution/logging "
        "steps failed"
    )
    assert "pipeline_status.json" in stage_status_step["run"]
    for step_id in OPTIONAL_STEP_IDS:
        opt_idx = _index_by_id(steps, step_id)
        assert opt_idx < idx, (
            f"step id={step_id!r} must run before the stage-status step"
        )


def test_snapshot_capture_steps_run_after_stage_status_and_are_non_fatal(steps):
    """
    The new Snapshot-capture steps must run strictly after the
    stage-status step (every production artifact they could reference
    already exists or has definitively failed to by then), and must never
    be able to fail the overall workflow -- continue-on-error: true, per
    docs/SNAPSHOT_ARCHITECTURE.md's explicit "safest behavior" decision.
    """
    stage_status_idx = _index_by_name_substring(steps, "Write pipeline stage-status")
    snapshot_idx = _index_by_name_substring(steps, "Create immutable PRE_GAME_DECISION snapshot")
    assert snapshot_idx > stage_status_idx
    assert steps[snapshot_idx].get("continue-on-error") is True
    assert steps[snapshot_idx].get("if") == "always()"


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
