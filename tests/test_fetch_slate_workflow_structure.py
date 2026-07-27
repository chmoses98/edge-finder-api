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
