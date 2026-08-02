#!/usr/bin/env python3
"""
tests/test_clv_update_workflow_summary.py
=============================================
Production Reliability and Settlement Recovery milestone: coverage for
the observability additions to .github/workflows/clv-update.yml (the
"Record starting state" and "Workflow summary" steps) and to
clv_update.py's data/clv_update_run_summary.json output.
"""
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_PATH = os.path.join(ROOT, ".github", "workflows", "clv-update.yml")


def _load():
    with open(WORKFLOW_PATH) as f:
        return yaml.safe_load(f)


def _steps():
    return _load()["jobs"]["update-clv"]["steps"]


def _step(steps, name):
    matches = [s for s in steps if s.get("name") == name]
    assert len(matches) == 1, f"expected exactly one step named {name!r}"
    return matches[0]


class TestWorkflowStructure:

    def test_workflow_is_valid_yaml(self):
        _load()  # raises on invalid YAML

    def test_every_stage_step_has_a_stable_id(self):
        steps = _steps()
        expected_ids = {
            "Record starting state (backlog count, commit SHA)": "before",
            "Run CLV update (settlement + Pinnacle CLV)": "clv_update",
            "Snapshot coverage check (warns if CLV source missing)": "snapshot_check",
            "Run Kalshi CLV (snapshot-first, API fallback)": "kalshi_clv",
            "Run identity audit": "identity_audit",
            "Run Rule 71 tracking report": "rule71_report",
            "Commit all updates": "commit",
        }
        for name, expected_id in expected_ids.items():
            assert _step(steps, name)["id"] == expected_id

    def test_workflow_summary_step_runs_on_always(self):
        step = _step(_steps(), "Workflow summary")
        assert step["if"] == "always()"

    def test_workflow_summary_is_the_last_step(self):
        steps = _steps()
        assert steps[-1]["name"] == "Workflow summary"

    def test_intermediate_steps_have_no_explicit_if_condition(self):
        """
        Regression guard: these steps must rely on GitHub Actions' default
        cascading skip-on-failure behavior (no explicit `if:`), so a
        failure anywhere in the chain still skips everything downstream of
        it exactly as before this milestone -- only the final summary step
        should override that with always().
        """
        steps = _steps()
        for name in (
            "Run CLV update (settlement + Pinnacle CLV)",
            "Run Kalshi CLV (snapshot-first, API fallback)",
            "Run identity audit",
            "Run Rule 71 tracking report",
            "Commit all updates",
        ):
            assert "if" not in _step(steps, name), f"{name} should not have an explicit if: condition"

    def test_workflow_summary_script_is_valid_bash(self, tmp_path):
        script = _step(_steps(), "Workflow summary")["run"]
        script_path = tmp_path / "summary.sh"
        script_path.write_text(script)
        result = subprocess.run(["bash", "-n", str(script_path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    def test_workflow_summary_script_produces_expected_sections_on_success(self, tmp_path):
        """
        Substitutes GitHub Actions step-outcome expressions with fake
        'success' values and runs the real script against a throwaway
        bets.json, proving the summary renders every requested section:
        stage table, failure category, backlog before/after, mutation
        status, and rerun-safety statement.
        """
        script = _step(_steps(), "Workflow summary")["run"]
        subs = {
            "${{ steps.clv_update.outcome }}": "success",
            "${{ steps.snapshot_check.outcome }}": "success",
            "${{ steps.kalshi_clv.outcome }}": "success",
            "${{ steps.identity_audit.outcome }}": "success",
            "${{ steps.rule71_report.outcome }}": "success",
            "${{ steps.commit.outcome }}": "success",
            "${{ steps.before.outputs.backlog_before }}": "3",
            "${{ steps.before.outputs.base_sha }}": "0" * 40,
        }
        for k, v in subs.items():
            script = script.replace(k, v)
        script_path = tmp_path / "summary.sh"
        script_path.write_text(script)

        import json
        import shutil
        (tmp_path / "bets.json").write_text(json.dumps([
            {"id": "1", "date": "2026-08-01", "status": "settled", "result": "WIN"},
            {"id": "2", "date": "2026-08-01", "status": "pending"},
        ]))
        # The script's inline python does `sys.path.insert(0, '.')` then
        # `from lib.bet_backlog_classifier import is_non_terminal`, matching
        # exactly how it runs in the real workflow (cwd = repo root). Copy
        # just that one dependency-free module in so the same import
        # resolves under this test's throwaway cwd too.
        (tmp_path / "lib").mkdir()
        shutil.copy(os.path.join(ROOT, "lib", "bet_backlog_classifier.py"), tmp_path / "lib" / "bet_backlog_classifier.py")

        summary_out = tmp_path / "step_summary.md"
        env = dict(os.environ, GITHUB_STEP_SUMMARY=str(summary_out))
        result = subprocess.run(
            ["bash", str(script_path)], cwd=str(tmp_path), capture_output=True, text=True, env=env,
        )
        assert result.returncode == 0, result.stderr
        output = summary_out.read_text()
        assert "## clv-update.yml run summary" in output
        assert "| Stage | Outcome |" in output
        assert "**Failure category:** none -- full run succeeded." in output
        assert "before=3, after=1" in output
        assert "**Safe to simply re-trigger this workflow?** Yes." in output

    def test_workflow_summary_explains_zero_records_when_no_summary_json(self, tmp_path):
        script = _step(_steps(), "Workflow summary")["run"]
        subs = {
            "${{ steps.clv_update.outcome }}": "success",
            "${{ steps.snapshot_check.outcome }}": "success",
            "${{ steps.kalshi_clv.outcome }}": "success",
            "${{ steps.identity_audit.outcome }}": "success",
            "${{ steps.rule71_report.outcome }}": "success",
            "${{ steps.commit.outcome }}": "success",
            "${{ steps.before.outputs.backlog_before }}": "0",
            "${{ steps.before.outputs.base_sha }}": "0" * 40,
        }
        for k, v in subs.items():
            script = script.replace(k, v)
        script_path = tmp_path / "summary.sh"
        script_path.write_text(script)

        import json
        (tmp_path / "bets.json").write_text(json.dumps([]))

        summary_out = tmp_path / "step_summary.md"
        env = dict(os.environ, GITHUB_STEP_SUMMARY=str(summary_out))
        result = subprocess.run(
            ["bash", str(script_path)], cwd=str(tmp_path), capture_output=True, text=True, env=env,
        )
        assert result.returncode == 0, result.stderr
        output = summary_out.read_text()
        assert "No data/clv_update_run_summary.json found" in output

    def test_before_step_computes_backlog_using_the_real_classifier(self, tmp_path):
        """
        Confirms the "Record starting state" step's inline python actually
        imports lib.bet_backlog_classifier.is_non_terminal (the same
        classifier used by scripts/remediate_bet_backlog.py), not a
        reimplemented ad hoc check.
        """
        step = _step(_steps(), "Record starting state (backlog count, commit SHA)")
        assert "from lib.bet_backlog_classifier import is_non_terminal" in step["run"]


class TestClvUpdateRunSummaryFile:

    def test_clv_update_writes_summary_via_atomic_json(self):
        with open(os.path.join(ROOT, "clv_update.py")) as f:
            content = f.read()
        assert "write_json_atomic(run_summary, 'data/clv_update_run_summary.json'" in content

    def test_commit_step_does_not_add_the_run_summary_file(self):
        """
        data/clv_update_run_summary.json is a per-run observability
        artifact, not a durable ledger record -- deliberately NOT added to
        the commit step's git add list, so it stays a local, ephemeral,
        per-job file (consumed by the Workflow summary step in the same
        job) rather than an ever-growing committed history.
        """
        commit_step = _step(_steps(), "Commit all updates")
        assert "clv_update_run_summary.json" not in commit_step["run"]
