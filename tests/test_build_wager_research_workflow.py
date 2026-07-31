#!/usr/bin/env python3
"""
tests/test_build_wager_research_workflow.py
================================================
Structural tests for .github/workflows/build-wager-research.yml: runs
after CLV update, nightly fallback schedule, commit before push, only
intended artifacts staged, empty diff exits cleanly, concurrency correct.
"""
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_PATH = os.path.join(ROOT, ".github", "workflows", "build-wager-research.yml")


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

    def test_triggered_by_clv_update_workflow_run(self):
        doc = _doc()
        triggers = doc.get(True, doc.get("on"))
        assert "workflow_run" in triggers
        assert "Update CLV (Post-Slate Review)" in triggers["workflow_run"]["workflows"]

    def test_has_nightly_schedule_fallback(self):
        doc = _doc()
        triggers = doc.get(True, doc.get("on"))
        assert "schedule" in triggers
        assert len(triggers["schedule"]) >= 1

    def test_supports_workflow_dispatch(self):
        doc = _doc()
        triggers = doc.get(True, doc.get("on"))
        assert "workflow_dispatch" in triggers

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
        steps = doc["jobs"]["build"]["steps"]
        checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout"))
        assert checkout["with"]["ref"] == "main"

    def test_runs_build_and_report_scripts(self):
        src = _read()
        assert "scripts/build_wager_research_db.py" in src
        assert "scripts/generate_wager_research_report.py" in src

    def test_commit_occurs_before_push(self):
        body = _commit_step_body()
        commit_idx = body.index("git commit -m")
        push_idx = body.index("git push origin HEAD:main")
        assert commit_idx < push_idx

    def test_only_research_artifacts_staged(self):
        body = _commit_step_body()
        assert "data/research/wagers.jsonl" in body
        assert "data/research/wagers.csv" in body
        assert "data/research/reports/" in body
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

    def test_never_writes_bets_json(self):
        src = _read()
        assert "> bets.json" not in src
        assert "git add bets.json" not in src
