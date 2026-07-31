#!/usr/bin/env python3
"""
tests/test_capture_closing_lines_workflow.py
================================================
Structural + safety-isolation tests for
.github/workflows/capture-closing-lines.yml — the scheduled, every-5-minute
pregame closing-line collector.
"""
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_PATH = os.path.join(ROOT, ".github", "workflows", "capture-closing-lines.yml")

FORBIDDEN_SCRIPTS = (
    "build_market_ledger.py",
    "validate_slate_final.py",
    "risk_gate.py",
    "write_pending_bets.py",
    "validate_bet_logging.py",
    "generate_f5_audit.py",
    "regression_test.py",
)


def _read():
    with open(WORKFLOW_PATH) as f:
        return f.read()


def _doc():
    with open(WORKFLOW_PATH) as f:
        return yaml.safe_load(f)


def _run_bodies():
    doc = _doc()
    bodies = []
    for job in doc.get("jobs", {}).values():
        for step in job.get("steps", []):
            if "run" in step:
                bodies.append(step["run"])
    return "\n".join(bodies)


class TestWorkflowStructure:

    def test_valid_yaml(self):
        assert _doc() is not None

    def test_scheduled_every_5_minutes(self):
        doc = _doc()
        triggers = doc.get(True, doc.get("on"))
        schedules = triggers.get("schedule", [])
        assert any(s.get("cron") == "*/5 * * * *" for s in schedules)

    def test_workflow_dispatch_with_optional_date(self):
        doc = _doc()
        triggers = doc.get(True, doc.get("on"))
        assert "workflow_dispatch" in triggers
        inputs = triggers["workflow_dispatch"]["inputs"]
        assert "date" in inputs
        assert inputs["date"]["required"] is False
        assert inputs["date"]["default"] == ""

    def test_permissions_contents_write(self):
        doc = _doc()
        assert doc.get("permissions", {}).get("contents") == "write"

    def test_concurrency_prevents_overlap(self):
        doc = _doc()
        concurrency = doc.get("concurrency")
        assert concurrency is not None
        assert concurrency.get("group")
        # Must not cancel an in-progress run mid-write (that could corrupt
        # a partially-written commit) — it should queue, not cancel.
        assert concurrency.get("cancel-in-progress") is False

    def test_checks_out_main(self):
        doc = _doc()
        steps = doc["jobs"]["capture"]["steps"]
        checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout"))
        assert checkout["with"]["ref"] == "main"

    def test_resolves_et_date_when_not_provided(self):
        src = _read()
        assert "America/New_York" in src
        assert "github.event.inputs.date" in src

    def test_runs_capture_script_with_date_arg(self):
        run_bodies = _run_bodies()
        assert "scripts/capture_pregame_closing_lines.py" in run_bodies
        assert 'capture_pregame_closing_lines.py "${{ env.DATE }}"' in run_bodies

    def test_commits_only_registry_and_clv_log(self):
        src = _read()
        assert "data/kalshi_market_registry.json" in src
        assert "data/clv/" in src
        assert "closing_capture_log.json" in src
        # Must not stage the whole data/ directory or bets.json.
        assert "git add data/\n" not in src
        assert "git add data\n" not in src
        assert "git add bets.json" not in src
        assert "git add ." not in src
        assert "git add -A" not in src

    def test_commit_only_when_changed(self):
        src = _read()
        assert "git diff --cached --quiet" in src

    def test_safe_commit_pattern_fetch_rebase_push(self):
        run_bodies = _run_bodies()
        assert "git fetch origin main" in run_bodies
        assert "git rebase --autostash origin/main" in run_bodies
        assert "git push origin HEAD:main" in run_bodies

    def test_exits_cleanly_with_nothing_to_commit(self):
        src = _read()
        assert "nothing to commit" in src.lower() or "no game inside the capture window" in src.lower()

    def test_never_invokes_forbidden_stages(self):
        run_bodies = _run_bodies()
        for forbidden in FORBIDDEN_SCRIPTS:
            assert forbidden not in run_bodies, f"capture-closing-lines.yml invokes forbidden script: {forbidden}"

    def test_never_touches_bets_json(self):
        run_bodies = _run_bodies()
        assert "bets.json" not in run_bodies
