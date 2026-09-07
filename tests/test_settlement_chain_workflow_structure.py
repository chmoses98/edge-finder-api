#!/usr/bin/env python3
"""
tests/test_settlement_chain_workflow_structure.py
=================================================
REMEDIATION WAVE 0, section F. Structural guards on the settlement workflow
chain -- persistence gating, exception suppression, and downstream condition
semantics.

These assert against the REAL YAML, parsed, never against a hand-copied
duplicate of it. The 2026-09 outage had three structural contributors and each
one gets a guard here:

  1. clv-update.yml's commit step ran only on implicit success(), so a failure
     in a downstream REPORT discarded a night's already-computed settlement.
  2. The snapshot-coverage step carried BOTH `continue-on-error: true` and a
     trailing `|| true`, so a hard crash reported `success`.
  3. Every settlement-producing step in edgelab-postgame.yml is
     `continue-on-error: true`, so a settlement crash left the job green.
"""

import os

import pytest

yaml = pytest.importorskip("yaml")

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")


def _load(name):
    with open(os.path.join(WORKFLOWS, name)) as handle:
        return yaml.safe_load(handle)


def _steps(workflow):
    (job,) = workflow["jobs"].values()
    return job["steps"]


def _step_by_id(steps, step_id):
    for step in steps:
        if step.get("id") == step_id:
            return step
    raise AssertionError("no step with id %r (ids present: %r)"
                         % (step_id, [s.get("id") for s in steps if s.get("id")]))


# ── 1. Persistence must not depend on downstream reporting ───────────────────

def test_clv_update_commit_step_persists_even_when_a_later_step_fails():
    """
    THE durability regression. On 2026-09-02..09-07 clv_update.py succeeded
    every night, a later step failed, and the commit step never ran -- six
    nights of real settlement computed and thrown away with the runner.
    """
    commit = _step_by_id(_steps(_load("clv-update.yml")), "commit")
    assert commit.get("if") == "always()", (
        "clv-update.yml's commit step must run with `if: always()`; without it "
        "any downstream failure silently discards the settlement already written "
        "to bets.json")


def test_clv_update_commit_step_still_runs_before_the_reporting_steps_can_block_it():
    """
    Ordering is part of the contract: the commit must not be reachable only
    after the identity/rule71 reports, or `always()` alone would still leave a
    window where a crash loses data.
    """
    steps = _steps(_load("clv-update.yml"))
    ids = [s.get("id") for s in steps]
    assert "commit" in ids
    commit_index = ids.index("commit")
    # Everything after the commit must be reporting-only (no data producers).
    for step in steps[commit_index + 1:]:
        run = step.get("run") or ""
        assert "git_data_commit" not in run, (
            "a second commit path after the main commit step would reintroduce "
            "ambiguity about what actually persisted")


# ── 2. No double suppression on the settlement chain ─────────────────────────

def test_no_step_in_the_clv_chain_swallows_its_exit_code_with_or_true():
    """
    `|| true` makes a step unable to fail AT ALL, so the summary table printed
    "success" for a step that had produced a traceback every day for six days.
    `continue-on-error` alone is a legitimate "advisory" marker; `|| true` on
    top of it is a lie.
    """
    offenders = []
    for name in ("clv-update.yml", "edgelab-postgame.yml"):
        for step in _steps(_load(name)):
            run = step.get("run") or ""
            for line in run.splitlines():
                stripped = line.strip()
                if stripped.endswith("|| true") and "python3" in stripped:
                    offenders.append("%s :: %s :: %s"
                                     % (name, step.get("name"), stripped))
    assert offenders == [], (
        "a production step swallows its own exit code, making failure invisible:\n  "
        + "\n  ".join(offenders))


# ── 3. A settlement crash must turn the job red ──────────────────────────────

def test_postgame_fails_the_job_when_a_core_settlement_step_fails():
    """
    Every settlement-producing step in edgelab-postgame.yml is deliberately
    `continue-on-error: true` so that partial settlement still gets committed.
    That is the right trade ONLY if something afterwards still fails the job --
    otherwise a total settlement failure reports success.
    """
    steps = _steps(_load("edgelab-postgame.yml"))
    for step_id in ("sync_ledger", "settle_markets", "reingest_bets"):
        _step_by_id(steps, step_id)  # raises if the gate lost its anchors

    gate = steps[-1]
    condition = (gate.get("if") or "").replace("\n", " ")
    assert "always()" in condition
    for step_id in ("sync_ledger", "settle_markets", "reingest_bets"):
        assert "steps.%s.outcome == 'failure'" % step_id in condition, (
            "the settlement failure gate must observe %s" % step_id)
    assert "exit 1" in (gate.get("run") or ""), (
        "the gate must actually fail the job, not merely annotate it")


def test_postgame_settlement_gate_runs_after_the_commit_step():
    """
    Persist first, then fail. Reversing these would skip the commit and
    re-create the discard bug this Wave is repairing.
    """
    steps = _steps(_load("edgelab-postgame.yml"))
    names = [s.get("name") or "" for s in steps]
    commit_index = next(i for i, n in enumerate(names) if n.startswith("Commit EdgeLab postgame"))
    gate_index = next(i for i, n in enumerate(names) if n.startswith("Fail the job if core settlement"))
    assert gate_index > commit_index


# ── 4. Downstream condition semantics ────────────────────────────────────────

def test_postgame_still_refuses_to_run_on_a_failed_upstream_workflow():
    """
    The `workflow_run.conclusion == 'success'` gate is CORRECT and is
    deliberately left in place by Wave 0: settling from a run whose settlement
    inputs failed to materialize would be worse than not settling.

    What Wave 0 changes is that the resulting skip is no longer invisible --
    scripts/ci/production_health_gate.py's PROD-4 detects the cascade from
    durable partition divergence, without needing the Actions API. This test
    pins the gate so a future change cannot quietly loosen it.
    """
    (job,) = _load("edgelab-postgame.yml")["jobs"].values()
    condition = job.get("if") or ""
    assert "workflow_run" in condition
    assert "success" in condition


# ── 5. The health gate itself must be unable to hide a failure ───────────────

def test_health_gate_workflow_can_actually_fail():
    workflow = _load("production-health-gate.yml")
    steps = _steps(workflow)
    final = steps[-1]
    assert "exit 1" in (final.get("run") or "")
    assert final.get("continue-on-error") is not True, (
        "the final gate step must not be able to swallow its own failure")
    condition = final.get("if") or ""
    assert "steps.gate.outcome != 'success'" in condition


def test_health_gate_publishes_its_artifact_even_on_a_red_run():
    """
    The history of WHEN the loop broke is exactly what was missing during the
    2026-09 investigation, so the artifact must survive a red run.
    """
    steps = _steps(_load("production-health-gate.yml"))
    publish = next(s for s in steps if (s.get("name") or "").startswith("Publish health artifact"))
    assert publish.get("if") == "always()"


def test_health_gate_checks_out_full_history():
    """
    PROD-5 asks when bets.json was last COMMITTED. A shallow clone would answer
    from a truncated log and silently under-report a persistence failure.
    """
    steps = _steps(_load("production-health-gate.yml"))
    checkout = next(s for s in steps if "checkout" in str(s.get("uses", "")))
    assert checkout.get("with", {}).get("fetch-depth") == 0
