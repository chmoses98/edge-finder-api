#!/usr/bin/env python3
"""
tests/test_lineup_recheck_workflow.py
==========================================
Structural + safety-isolation tests for
.github/workflows/lineup-recheck.yml -- a data-refresh-only workflow
that safely merges a lineup/odds recheck into an already-published
data/slates/<date>/authoritative.json via
lib.slate_manager's LINEUP_RECHECK / IN_PLAY_RECHECK mechanism, without
ever running the edge-classification, execution-slip, risk-gate, or
bet-logging stages of the production pipeline.
"""
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_PATH = os.path.join(ROOT, ".github", "workflows", "lineup-recheck.yml")

FORBIDDEN_SCRIPTS = (
    "build_market_ledger.py",
    "validate_slate_final.py",
    "risk_gate.py",
    "write_pending_bets.py",
    "validate_bet_logging.py",
    "write_tracked_tickers.py",
    "capture_closing_lines.py",
    "generate_f5_audit.py",
    "regression_test.py",
)


def _read():
    with open(WORKFLOW_PATH) as f:
        return f.read()


def _run_bodies():
    with open(WORKFLOW_PATH) as f:
        doc = yaml.safe_load(f)
    bodies = []
    for job in doc.get("jobs", {}).values():
        for step in job.get("steps", []):
            if "run" in step:
                bodies.append(step["run"])
    return "\n".join(bodies)


class TestWorkflowStructure:

    def test_valid_yaml(self):
        with open(WORKFLOW_PATH) as f:
            doc = yaml.safe_load(f)
        assert doc is not None

    def test_workflow_dispatch_trigger_only(self):
        with open(WORKFLOW_PATH) as f:
            doc = yaml.safe_load(f)
        triggers = doc.get(True, doc.get("on"))
        assert "workflow_dispatch" in triggers
        assert "schedule" not in triggers
        assert "push" not in triggers
        assert "pull_request" not in triggers

    def test_date_input_present(self):
        with open(WORKFLOW_PATH) as f:
            doc = yaml.safe_load(f)
        triggers = doc.get(True, doc.get("on"))
        inputs = triggers["workflow_dispatch"]["inputs"]
        assert "date" in inputs
        assert inputs["date"]["default"] == ""

    def test_refuses_to_run_without_existing_authoritative_slate(self):
        src = _read()
        assert "authoritative.json does not exist" in src or "does not exist" in src
        assert "exit 1" in src

    def test_calls_protect_slate_for_safe_merge(self):
        run_bodies = _run_bodies()
        assert "scripts/protect_slate.py" in run_bodies

    def test_never_invokes_forbidden_stages(self):
        """This workflow must never call any script that applies edge
        classification (Rule 71/81), generates an execution slip, runs
        the risk gate, or logs/validates real-money bets."""
        run_bodies = _run_bodies()
        for forbidden in FORBIDDEN_SCRIPTS:
            assert forbidden not in run_bodies, (
                f"lineup-recheck.yml invokes forbidden script: {forbidden}"
            )

    def test_never_touches_bets_json(self):
        run_bodies = _run_bodies()
        assert "bets.json" not in run_bodies

    def test_never_grants_write_to_forbidden_files_via_git_add(self):
        """The commit step adds data/ broadly but must never explicitly
        stage bets.json."""
        src = _read()
        assert "git add bets.json" not in src

    def test_does_not_run_full_pipeline_pre_validation_gate(self):
        """The hard pre-validation gate (starters+pinnacle not-ready
        exit-2 path) is specific to a first pregame run and must not be
        present here -- a recheck runs unconditionally against an
        already-published slate."""
        src = _read()
        assert "validate_slate_pre.py" not in src

    def test_permissions_are_contents_write(self):
        """Needs write to commit the refreshed data, but nothing broader."""
        with open(WORKFLOW_PATH) as f:
            doc = yaml.safe_load(f)
        assert doc.get("permissions", {}).get("contents") == "write"

    def test_writes_lineup_recheck_status_not_pipeline_status(self):
        """Must not overwrite data/pipeline_status.json (the production
        execution-chain status artifact) with a partial/misleading
        record -- writes its own distinctly-named status file instead."""
        src = _read()
        assert "lineup_recheck_status.json" in src
        assert "pipeline_status.json" not in src
