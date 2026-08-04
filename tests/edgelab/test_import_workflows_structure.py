#!/usr/bin/env python3
"""
tests/edgelab/test_import_workflows_structure.py
====================================================
Structural content tests for the two new GitHub Actions workflows in the
MLB Market Research Corpus & Frictionless Manual Logging milestone:
.github/workflows/import-manual-bets.yml and import-postmortem.yml.
Follows this repository's established convention (see
tests/test_kalshi_price_check_workflow.py) of testing workflow files via
source-content assertions rather than executing them.
"""
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKFLOWS_DIR = os.path.join(ROOT, ".github", "workflows")
IMPORT_BETS_PATH = os.path.join(WORKFLOWS_DIR, "import-manual-bets.yml")
IMPORT_POSTMORTEM_PATH = os.path.join(WORKFLOWS_DIR, "import-postmortem.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _read(path):
    with open(path) as f:
        return f.read()


def test_both_workflows_valid_yaml_and_manual_dispatch_only():
    for path in (IMPORT_BETS_PATH, IMPORT_POSTMORTEM_PATH):
        doc = _load(path)
        assert doc is not None
        on = doc.get(True) or doc.get("on")
        assert "workflow_dispatch" in on
        assert "push" not in on
        assert "pull_request" not in on


def test_import_bets_workflow_calls_only_the_canonical_import_script():
    src = _read(IMPORT_BETS_PATH)
    assert "scripts/edgelab/import_bet_batch.py" in src
    for forbidden in ("build_market_ledger.py", "risk_gate.py", "protect_slate.py", "validate_slate_final.py"):
        assert forbidden not in src


def test_import_postmortem_workflow_calls_only_the_canonical_import_script():
    src = _read(IMPORT_POSTMORTEM_PATH)
    assert "scripts/edgelab/import_postmortem.py" in src
    for forbidden in ("build_market_ledger.py", "risk_gate.py", "protect_slate.py", "validate_slate_final.py"):
        assert forbidden not in src


def test_no_order_placement_language_in_either_workflow():
    for path in (IMPORT_BETS_PATH, IMPORT_POSTMORTEM_PATH):
        text = _read(path).lower()
        for forbidden in ("/portfolio/orders", "place_order", "createorder", "kelly", "auto_stake", "auto_bet"):
            assert forbidden not in text


def test_import_bets_requires_exactly_one_payload_source():
    src = _read(IMPORT_BETS_PATH)
    assert "payload_json" in src and "payload_file" in src
    assert "Provide exactly one of payload_json or payload_file" in src
    assert "Provide only one of payload_json or payload_file" in src


def test_import_postmortem_requires_markdown_and_findings():
    src = _read(IMPORT_POSTMORTEM_PATH)
    assert "Provide markdown_text or markdown_file" in src
    assert "Provide findings_json or findings_file" in src


def test_import_postmortem_concurrency_group_is_scoped_per_date():
    """Two different dates' postmortem imports must not serialize behind each other unnecessarily."""
    doc = _load(IMPORT_POSTMORTEM_PATH)
    assert "${{ inputs.date }}" in doc["concurrency"]["group"]


def test_both_workflows_upload_a_receipt_or_artifact():
    for path in (IMPORT_BETS_PATH, IMPORT_POSTMORTEM_PATH):
        assert "upload-artifact" in _read(path)


def _all_run_bodies(doc):
    bodies = []
    for job in doc.get("jobs", {}).values():
        for step in job.get("steps", []):
            if "run" in step:
                bodies.append((step.get("name"), step["run"]))
    return bodies


def test_no_raw_input_interpolation_inside_run_blocks():
    """
    SECURITY: a `${{ inputs.x }}` expression must never appear inside a
    `run:` shell/python source block -- it is expanded by the Actions
    runner BEFORE the shell parses the line, so a payload containing a
    double quote, `$(...)`, backticks, or a newline could otherwise break
    out of its quoting and execute arbitrary shell code. Every input that
    a `run:` block needs must instead be routed through `env:` and read
    from the environment. (`with:`/`concurrency:`/`name:` fields are NOT
    shell source -- direct `${{ inputs.x }}` there is fine and is used
    deliberately in both workflows, e.g. the checkout step's `ref:` and
    the postmortem artifact's `name:`/`path:`.)
    """
    for path in (IMPORT_BETS_PATH, IMPORT_POSTMORTEM_PATH):
        doc = _load(path)
        for step_name, body in _all_run_bodies(doc):
            assert "${{ inputs." not in body, f"{path}: step {step_name!r} interpolates a raw input inside run: {body!r}"


def test_import_step_uses_continue_on_error_so_commit_step_still_runs():
    for path in (IMPORT_BETS_PATH, IMPORT_POSTMORTEM_PATH):
        doc = _load(path)
        steps = doc["jobs"]["import"]["steps"]
        import_step = next(s for s in steps if s.get("id") == "import")
        assert import_step.get("continue-on-error") is True, f"{path}: import step must set continue-on-error: true"


def test_commit_step_is_not_gated_on_import_success():
    """
    Partial-import persistence: the commit step must run regardless of
    whether the importer reported success, so a row that DID write
    through the canonical writer is never left uncommitted just because
    another row in the same batch (or, for the postmortem workflow, a
    later best-effort step) failed.
    """
    for path in (IMPORT_BETS_PATH, IMPORT_POSTMORTEM_PATH):
        doc = _load(path)
        steps = doc["jobs"]["import"]["steps"]
        commit_step = next(s for s in steps if "Commit" in s.get("name", ""))
        condition = commit_step.get("if", "")
        assert "steps.import.outcome" not in condition, f"{path}: commit step must not be gated on import outcome: {condition!r}"
        assert condition == "always()", f"{path}: commit step should run unconditionally: {condition!r}"


def test_failure_step_runs_after_commit_step_and_checks_real_outcome():
    """
    The job-failing step must (a) come AFTER the commit step in the step
    list, so any valid write is committed before the job is ever marked
    failed, and (b) gate on steps.import.outcome (the importer's real,
    pre-continue-on-error result), so it still fires when rows were
    genuinely unresolved/invalid.
    """
    for path in (IMPORT_BETS_PATH, IMPORT_POSTMORTEM_PATH):
        doc = _load(path)
        steps = doc["jobs"]["import"]["steps"]
        names = [s.get("name", "") for s in steps]
        commit_index = next(i for i, n in enumerate(names) if "Commit" in n)
        fail_index = next(i for i, n in enumerate(names) if n.startswith("Fail the job"))
        assert fail_index > commit_index, f"{path}: the fail-the-job step must come after the commit step"
        assert steps[fail_index].get("if") == "steps.import.outcome == 'failure'"


def test_ref_input_present_and_checkout_uses_it():
    for path in (IMPORT_BETS_PATH, IMPORT_POSTMORTEM_PATH):
        doc = _load(path)
        on = doc.get(True) or doc.get("on")
        inputs = on["workflow_dispatch"]["inputs"]
        assert "ref" in inputs
        steps = doc["jobs"]["import"]["steps"]
        checkout_step = next(s for s in steps if s.get("uses", "").startswith("actions/checkout"))
        assert checkout_step["with"]["ref"] == "${{ inputs.ref || 'main' }}"


def test_file_input_descriptions_no_longer_say_this_branch_ambiguously():
    """Regression: 'already committed to this branch' was ambiguous given the workflow checks out `main` (or now, `ref`)."""
    for path in (IMPORT_BETS_PATH, IMPORT_POSTMORTEM_PATH):
        src = _read(path)
        assert "committed to this branch" not in src


def test_postmortem_date_input_is_shape_validated():
    """PM_DATE is used to build git-add paths and a commit message -- validate its shape before trusting it."""
    src = _read(IMPORT_POSTMORTEM_PATH)
    assert "PM_DATE=~" in src.replace(" ", "") or "[0-9]{4}-[0-9]{2}-[0-9]{2}" in src


def test_postmortem_commit_step_routes_date_through_env():
    doc = _load(IMPORT_POSTMORTEM_PATH)
    steps = doc["jobs"]["import"]["steps"]
    commit_step = next(s for s in steps if "Commit" in s.get("name", ""))
    assert commit_step.get("env", {}).get("PM_DATE") == "${{ inputs.date }}"
    assert "$PM_DATE" in commit_step["run"] or "${PM_DATE}" in commit_step["run"]
