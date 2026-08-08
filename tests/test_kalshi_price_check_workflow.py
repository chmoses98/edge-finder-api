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

    def test_write_permission_scoped_to_the_research_corpus_archive(self):
        """
        Market Research Corpus milestone: this workflow now legitimately
        writes to the repository (it must, to archive an unfiltered
        capture into the shared research corpus on every successful run
        -- see the "Archive"/"Ingest"/"Commit" steps). `contents: write`
        is therefore now required, a deliberate, milestone-directed
        change from the prior read-only posture -- but every `git add` in
        this workflow (see test_commit_steps_only_touch_the_research_corpus
        below) is scoped to data/kalshi_registry_snapshots/ and
        data/edgelab/ only, so this permission is never used to touch
        bets.json, data/bets.json, data/slate.json, or any other
        production file.
        """
        with open(WORKFLOW_PATH) as f:
            doc = yaml.safe_load(f)
        assert doc.get("permissions", {}).get("contents") == "write"

    def test_commit_steps_only_touch_the_research_corpus(self):
        """
        Market Research Corpus milestone: git commit/push steps now exist
        (see above), but every `git add` line in this workflow must only
        ever target data/kalshi_registry_snapshots/ or data/edgelab/ --
        never bets.json, data/bets.json, data/slate.json, data/pipeline/,
        or any other production file. The tool's own price-check display
        output (kalshi_price_check.json/.csv) must also never be added.
        """
        with open(WORKFLOW_PATH) as f:
            doc = yaml.safe_load(f)
        assert "git commit" in _read()  # the corpus-archive step below now legitimately commits
        for job in doc.get("jobs", {}).values():
            for step in job.get("steps", []):
                for line in step.get("run", "").splitlines():
                    stripped = line.strip()
                    if not stripped.startswith("git add"):
                        continue
                    assert "data/kalshi_registry_snapshots/" in stripped or "data/edgelab/" in stripped, (
                        f"unexpected git add outside the research corpus: {stripped!r}"
                    )
                    for forbidden in ("bets.json", "data/slate.json", "data/pipeline", "kalshi_price_check.json", "kalshi_price_check.csv"):
                        assert forbidden not in stripped, f"git add touches forbidden path: {stripped!r}"

    def test_uploads_json_and_csv_artifacts(self):
        src = _read()
        assert "kalshi-price-check-json" in src
        assert "kalshi-price-check-csv" in src

    def test_archive_bundle_never_committed(self):
        src = _read()
        assert "NOT committed to the repository" in src

    def test_corpus_archive_ingestion_tagged_as_standalone(self):
        """
        Market Research Corpus milestone: the corpus-archive ingestion
        step must tag its observations with source_system=
        standalone_price_check (lib.edgelab.observation_linkage prefers
        this source when linking a manual bet -- see its docstring), and
        must only run after a successful price-check.
        """
        with open(WORKFLOW_PATH) as f:
            doc = yaml.safe_load(f)
        steps = doc["jobs"]["check-prices"]["steps"]
        ingest_step = next(s for s in steps if "Ingest into EdgeLab market corpus" in s.get("name", ""))
        assert "--source-system standalone_price_check" in ingest_step["run"]
        assert "steps.price_check.outcome" in ingest_step["if"] or "corpus_archive.outputs.snapshot_path" in ingest_step["if"]

    def test_corpus_archive_step_is_unaffected_by_display_filters(self):
        """
        Market-integrity requirement: display filtering (team/game/
        games/scope/family/exclude_started/etc.) must affect ONLY this
        run's own display output -- never the archival capture. Proof:
        the corpus-archive step's own curl fetch is a raw,
        unconditional call to the endpoint with no filter flags at
        all -- it makes its own independent fetch rather than reusing
        anything scripts/check_kalshi_prices.py computed, so the new
        `--games`/`--exclude-started` filters (and every pre-existing
        filter) cannot possibly reach it.
        """
        with open(WORKFLOW_PATH) as f:
            doc = yaml.safe_load(f)
        steps = doc["jobs"]["check-prices"]["steps"]
        archive_step = next(s for s in steps if s.get("name") == "Archive an unfiltered complete-market capture for the research corpus")
        run_body = archive_step["run"]
        assert "curl" in run_body
        for forbidden in ("--games", "--exclude-started", "--team", "--game ", "--scope", "--family", "check_kalshi_prices.py"):
            assert forbidden not in run_body, f"corpus-archive step must never reference a display filter or the price-check CLI: {forbidden!r}"

    def test_raw_json_summary_step_embeds_the_same_json_the_artifact_uploads(self):
        """
        Artifact-usability improvement: the primary JSON output must
        also be reachable without downloading the ZIP-wrapped artifact
        -- embedded directly in the job summary via the same
        kalshi_price_check.json the JSON artifact step uploads
        unchanged.
        """
        with open(WORKFLOW_PATH) as f:
            doc = yaml.safe_load(f)
        steps = doc["jobs"]["check-prices"]["steps"]
        json_summary_step = next(s for s in steps if "Embed raw JSON" in s.get("name", ""))
        assert "print_price_check_json_summary.py" in json_summary_step["run"]
        assert "kalshi_price_check.json" in json_summary_step["run"]
        assert "GITHUB_STEP_SUMMARY" in json_summary_step["run"]
        assert json_summary_step["if"] == "always() && steps.price_check.outcome == 'success'"

    def test_shares_concurrency_group_with_scheduled_edgelab_capture(self):
        with open(WORKFLOW_PATH) as f:
            doc = yaml.safe_load(f)
        assert doc.get("concurrency", {}).get("group") == "edgelab-capture"

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

    def test_games_and_exclude_started_documented_in_advanced_filters(self):
        """
        Standalone usability mission: selected-game (`games`) and
        not-started-only (`exclude_started`) filtering must be reachable
        via advanced_filters_json (like the other rare filters), NOT as
        new standalone top-level inputs -- that would push the input
        count back over GitHub's 10-input hard limit.
        """
        with open(WORKFLOW_PATH) as f:
            doc = yaml.safe_load(f)
        triggers = doc.get(True, doc.get("on"))
        inputs = triggers["workflow_dispatch"]["inputs"]
        assert "games" not in inputs
        assert "exclude_started" not in inputs
        assert "games" in inputs["advanced_filters_json"]["description"]
        assert "exclude_started" in inputs["advanced_filters_json"]["description"]

    def test_boolean_inputs_referenced_via_inputs_context(self):
        """archive_snapshot is a real `type: boolean` input -- its
        `if:` conditions should use the typed `inputs.` context
        directly (no redundant == 'true' string comparison)."""
        src = _read()
        assert "inputs.archive_snapshot" in src
        assert "github.event.inputs.archive_snapshot" not in src


class TestMobileMarketTableStep:
    """
    Mobile-reading mission: every returned market must be displayed
    directly in the job summary AND the workflow log, without requiring
    an artifact download, while every existing output (JSON/CSV/
    metadata/archive bundle) is preserved unchanged.
    """

    def test_market_table_script_invoked(self):
        src = _read()
        assert "scripts/print_price_check_table.py" in src

    def test_table_written_to_job_summary(self):
        with open(WORKFLOW_PATH) as f:
            doc = yaml.safe_load(f)
        run_bodies = "\n".join(
            step["run"] for job in doc.get("jobs", {}).values()
            for step in job.get("steps", []) if "run" in step
        )
        assert "GITHUB_STEP_SUMMARY" in run_bodies
        assert "print_price_check_table.py" in run_bodies

    def test_table_also_printed_to_stdout_via_tee(self):
        """The mobile app surfaces workflow logs more readily than
        artifacts -- the same rendered table must also reach stdout
        (the job log), not only the summary file."""
        src = _read()
        assert "tee -a" in src
        assert '"$GITHUB_STEP_SUMMARY"' in src

    def test_market_table_step_gated_on_successful_price_check(self):
        with open(WORKFLOW_PATH) as f:
            doc = yaml.safe_load(f)
        steps = doc["jobs"]["check-prices"]["steps"]
        table_step = next(s for s in steps if s.get("name", "").startswith("Display every returned market"))
        assert table_step["if"] == "always() && steps.price_check.outcome == 'success'"

    def test_market_table_step_reads_the_same_json_the_artifacts_upload(self):
        """The summary table must render the EXACT same
        kalshi_price_check.json the JSON/CSV artifact steps upload --
        never a separately re-fetched or re-filtered result."""
        with open(WORKFLOW_PATH) as f:
            doc = yaml.safe_load(f)
        steps = doc["jobs"]["check-prices"]["steps"]
        table_step = next(s for s in steps if s.get("name", "").startswith("Display every returned market"))
        assert "kalshi_price_check.json" in table_step["run"]

    def test_all_existing_outputs_still_preserved(self):
        """Adding the mobile table must not remove or replace any
        pre-existing output."""
        src = _read()
        for expected in (
            "kalshi_price_check.json", "kalshi_price_check.csv",
            "kalshi_price_check_metadata.json", "kalshi-price-check-metadata",
            "kalshi-price-check-json", "kalshi-price-check-csv",
            "kalshi-price-check-archive-bundle", "print_price_check_summary.py",
        ):
            assert expected in src, f"missing pre-existing output/step: {expected}"

    def test_disclaimer_footer_still_present(self):
        src = _read()
        assert "does not determine whether a wager has positive expected value" in src
