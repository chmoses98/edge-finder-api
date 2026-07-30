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
        for key in ("source", "game", "team", "family", "scope", "include_closed",
                    "include_unknown", "max_results", "advanced_filters_json",
                    "archive_snapshot"):
            assert key in inputs, f"missing input: {key}"

    def test_workflow_dispatch_input_count_within_github_limit(self):
        """Regression: GitHub Actions hard-caps workflow_dispatch at 10
        top-level inputs ("you may only define up to 10 inputs for a
        workflow_dispatch event"). This workflow previously defined 15,
        which is why the "Run workflow" form behaved erratically
        (editing one field reverted others to their defaults) -- that
        was the predictable symptom of exceeding this platform limit,
        not a random UI bug. This test locks the input count at or
        below the limit forever."""
        with open(WORKFLOW_PATH) as f:
            doc = yaml.safe_load(f)
        triggers = doc.get(True, doc.get("on"))
        inputs = triggers["workflow_dispatch"]["inputs"]
        assert len(inputs) <= 10, (
            f"workflow_dispatch defines {len(inputs)} inputs, exceeding GitHub's "
            "hard limit of 10 -- this is the exact root cause of the "
            "field-reset bug; consolidate rare filters into advanced_filters_json"
        )

    def test_rare_filters_consolidated_not_individual_top_level_inputs(self):
        """The 6 rare filters must NOT reappear as individual
        workflow_dispatch inputs -- that would immediately push the
        input count back over the 10-input limit that caused this bug
        in the first place."""
        with open(WORKFLOW_PATH) as f:
            doc = yaml.safe_load(f)
        triggers = doc.get(True, doc.get("on"))
        inputs = triggers["workflow_dispatch"]["inputs"]
        for key in ("date", "outcome", "participant", "ticker", "event_ticker", "series_ticker"):
            assert key not in inputs, (
                f"{key!r} must not be a standalone workflow_dispatch input -- "
                "it belongs in advanced_filters_json"
            )

    def test_safe_defaults(self):
        with open(WORKFLOW_PATH) as f:
            doc = yaml.safe_load(f)
        triggers = doc.get(True, doc.get("on"))
        inputs = triggers["workflow_dispatch"]["inputs"]
        assert inputs["source"]["default"] == "auto"
        assert inputs["include_closed"]["type"] == "boolean"
        assert inputs["include_closed"]["default"] is False
        assert inputs["include_unknown"]["type"] == "boolean"
        assert inputs["include_unknown"]["default"] is True
        assert inputs["archive_snapshot"]["type"] == "boolean"
        assert inputs["archive_snapshot"]["default"] is False
        assert inputs["max_results"]["default"] == "250"
        assert inputs["advanced_filters_json"]["default"] == ""

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

    def test_metadata_output_requested_and_uploaded(self):
        """Regression: the workflow must actually request
        --metadata-output and upload the resulting file -- computing
        diagnostics that are never persisted was the root cause of the
        zero-results-with-no-explanation bug."""
        with open(WORKFLOW_PATH) as f:
            doc = yaml.safe_load(f)
        run_bodies = "\n".join(
            step["run"] for job in doc.get("jobs", {}).values()
            for step in job.get("steps", []) if "run" in step
        )
        assert "--metadata-output" in run_bodies
        assert "print_price_check_summary.py" in run_bodies

        artifact_names = [
            step.get("with", {}).get("name")
            for job in doc.get("jobs", {}).values()
            for step in job.get("steps", [])
            if step.get("uses", "").startswith("actions/upload-artifact")
        ]
        assert "kalshi-price-check-metadata" in artifact_names

    def test_advanced_filters_json_is_parsed_and_can_abort_the_run(self):
        """The consolidated JSON input must actually be consumed by
        parse_advanced_filters.py, and a parse failure must fail the
        step (not be silently swallowed) -- otherwise a typo'd filter
        name would be silently dropped instead of erroring loudly."""
        src = _read()
        assert "scripts/parse_advanced_filters.py" in src
        assert "ADV_ARGS" in src
        assert "exit 1" in src

    def test_run_step_does_not_interpolate_raw_expressions_into_shell(self):
        """Every workflow_dispatch input value must reach the run:
        script via `env:` (a safe assignment) rather than a raw
        ${{ inputs.x }} / ${{ github.event.inputs.x }} substitution
        directly inside the bash script body, which is a known GitHub
        Actions script-injection pattern for freeform user-supplied
        text (relevant here since advanced_filters_json is arbitrary
        JSON that could contain quotes/backticks/$())."""
        with open(WORKFLOW_PATH) as f:
            doc = yaml.safe_load(f)
        for job in doc.get("jobs", {}).values():
            for step in job.get("steps", []):
                if "run" not in step:
                    continue
                assert "github.event.inputs" not in step["run"]
                assert "${{ inputs." not in step["run"]

    def test_boolean_inputs_referenced_via_inputs_context(self):
        """archive_snapshot is a real `type: boolean` input -- its
        `if:` conditions should use the typed `inputs.` context
        directly (no redundant == 'true' string comparison)."""
        src = _read()
        assert "inputs.archive_snapshot" in src
        assert "github.event.inputs.archive_snapshot" not in src
