#!/usr/bin/env python3
"""
tests/test_discover_kalshi_mlb_markets_workflow.py
======================================================
Structural tests for .github/workflows/discover-kalshi-mlb-markets.yml:
runs after "Fetch Slate Data", commit occurs before push, only the
intended paths are staged, empty diff exits cleanly, concurrency is
correct.
"""
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_PATH = os.path.join(ROOT, ".github", "workflows", "discover-kalshi-mlb-markets.yml")


def _doc():
    with open(WORKFLOW_PATH) as f:
        return yaml.safe_load(f)


def _read():
    with open(WORKFLOW_PATH) as f:
        return f.read()


def _commit_step_body():
    doc = _doc()
    for job in doc.get("jobs", {}).values():
        for step in job.get("steps", []):
            if "commit" in (step.get("name") or "").lower() and "run" in step:
                return step["run"]
    raise AssertionError("no commit step found")


class TestWorkflowStructure:

    def test_valid_yaml(self):
        assert _doc() is not None

    def test_triggered_by_fetch_slate_workflow_run(self):
        doc = _doc()
        triggers = doc.get(True, doc.get("on"))
        assert "workflow_run" in triggers
        assert "Fetch Slate Data" in triggers["workflow_run"]["workflows"]
        assert "completed" in triggers["workflow_run"]["types"]

    def test_supports_workflow_dispatch(self):
        doc = _doc()
        triggers = doc.get(True, doc.get("on"))
        assert "workflow_dispatch" in triggers
        assert "date" in triggers["workflow_dispatch"]["inputs"]

    def test_only_runs_on_dispatch_or_successful_fetch_slate(self):
        doc = _doc()
        job = doc["jobs"]["discover"]
        assert "if" in job
        assert "workflow_dispatch" in job["if"]
        assert "conclusion == 'success'" in job["if"]

    def test_permissions_contents_write(self):
        doc = _doc()
        assert doc.get("permissions", {}).get("contents") == "write"

    def test_concurrency_queues_not_cancels(self):
        doc = _doc()
        concurrency = doc.get("concurrency")
        assert concurrency is not None
        assert concurrency.get("cancel-in-progress") is False

    def test_checkout_pinned_to_main(self):
        doc = _doc()
        steps = doc["jobs"]["discover"]["steps"]
        checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout"))
        assert checkout["with"]["ref"] == "main"

    def test_runs_discovery_script(self):
        src = _read()
        assert "scripts/discover_kalshi_mlb_markets.py" in src

    def test_commit_occurs_before_push(self):
        body = _commit_step_body()
        commit_idx = body.index("git commit -m")
        push_idx = body.index("git push origin HEAD:main")
        assert commit_idx < push_idx

    def test_only_intended_paths_staged(self):
        """
        Spread-correction mission: this workflow also builds+commits
        data/research/paper_spread_ledger.jsonl (the spread paper-
        tracking ledger) alongside the discovery artifacts -- both are
        intended, sanctioned outputs of this SAME job/commit (avoiding
        a second workflow racing on the same files), so a staged path
        is allowed to be either the discovery directory OR the paper
        ledger, never a broad/unbounded git add.
        """
        body = _commit_step_body()
        add_lines = [l.strip() for l in body.splitlines() if "git add" in l]
        assert add_lines
        for line in add_lines:
            assert "data/kalshi/discovery/" in line or "data/research/paper_spread_ledger.jsonl" in line
        assert "git add data/\n" not in body
        assert "git add ." not in body
        assert "git add -A" not in body
        assert "bets.json" not in body

    def test_empty_diff_exits_cleanly_before_commit(self):
        body = _commit_step_body()
        diff_idx = body.index("git diff --cached --quiet")
        commit_idx = body.index("git commit -m")
        assert diff_idx < commit_idx
        assert "exit 0" in body[diff_idx:commit_idx]

    def test_persistent_push_failure_is_not_silent(self):
        body = _commit_step_body()
        push_idx = body.index("git push origin HEAD:main")
        assert "exit 1" in body[push_idx:]

    def test_retry_refetches_and_rebases(self):
        body = _commit_step_body()
        push_idx = body.index("git push origin HEAD:main")
        tail = body[push_idx:]
        assert "git fetch origin main" in tail
        assert "git rebase origin/main" in tail

    def test_never_touches_betting_logic_scripts(self):
        src = _read()
        for forbidden in ("build_market_ledger.py", "risk_gate.py", "write_pending_bets.py",
                          "validate_slate_final.py"):
            assert forbidden not in src
