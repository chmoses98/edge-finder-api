#!/usr/bin/env python3
"""
tests/test_kalshi_price_check_workflow.py
==============================================
Structural content tests for
.github/workflows/kalshi-price-check.yml, following this
repository's established convention (see tests/test_api_date.py) of
testing workflow/JS files via source-content assertions rather than
executing them.
"""
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_PATH = os.path.join(ROOT, ".github", "workflows", "kalshi-price-check.yml")


def _read():
    with open(WORKFLOW_PATH) as f:
        return f.read()


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

    def test_expected_inputs_present(self):
        with open(WORKFLOW_PATH) as f:
            doc = yaml.safe_load(f)
        triggers = doc.get(True, doc.get("on"))
        inputs = triggers["workflow_dispatch"]["inputs"]
        for key in ("date", "game", "team", "family", "scope", "outcome", "participant",
                    "ticker", "event_ticker", "series_ticker", "include_closed",
                    "include_unknown", "source", "max_results", "archive_snapshot"):
            assert key in inputs, f"missing input: {key}"

    def test_safe_defaults(self):
        with open(WORKFLOW_PATH) as f:
            doc = yaml.safe_load(f)
        triggers = doc.get(True, doc.get("on"))
        inputs = triggers["workflow_dispatch"]["inputs"]
        assert inputs["source"]["default"] == "auto"
        assert inputs["include_closed"]["default"] == "false"
        assert inputs["include_unknown"]["default"] == "true"
        assert inputs["archive_snapshot"]["default"] == "false"
        assert inputs["max_results"]["default"] == "250"

    def test_calls_only_the_standalone_checker(self):
        """The workflow's actual `run:` step bodies (not the header
        comment block documenting what NOT to do) must invoke
        check_kalshi_prices.py and must NEVER invoke any production
        pipeline script."""
        with open(WORKFLOW_PATH) as f:
            doc = yaml.safe_load(f)
        run_bodies = []
        for job in doc.get("jobs", {}).values():
            for step in job.get("steps", []):
                if "run" in step:
                    run_bodies.append(step["run"])
        combined_runs = "\n".join(run_bodies)

        assert "scripts/check_kalshi_prices.py" in combined_runs
        for forbidden in ("build_market_ledger.py", "risk_gate.py", "write_pending_bets.py",
                          "protect_slate.py", "validate_slate_final.py", "fetch-slate.yml",
                          "fetch_kalshi_markets.py", "build_kalshi_registry.py"):
            assert forbidden not in combined_runs, f"workflow run step invokes forbidden script/workflow: {forbidden}"

    def test_no_write_permissions_requested(self):
        with open(WORKFLOW_PATH) as f:
            doc = yaml.safe_load(f)
        assert doc.get("permissions", {}).get("contents") == "read"

    def test_no_git_commit_or_push_steps(self):
        src = _read()
        assert "git commit" not in src
        assert "git push" not in src

    def test_uploads_json_and_csv_artifacts(self):
        src = _read()
        assert "kalshi-price-check-json" in src
        assert "kalshi-price-check-csv" in src

    def test_archive_bundle_never_committed(self):
        src = _read()
        assert "NOT committed to the repository" in src
