#!/usr/bin/env python3
"""
tests/edgelab/test_settlement_reconcile_workflow_structure.py
=================================================================
Structural guards for .github/workflows/edgelab-settlement-reconcile.yml.

The reconcile workflow is triggered by a push to the very file its own
settlement step writes back to. That is safe only because of a specific
set of structural properties, every one of which is easy to lose in a
later edit and impossible to notice until the day it loops. This file
pins them.
"""
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKFLOW_PATH = os.path.join(ROOT, ".github", "workflows", "edgelab-settlement-reconcile.yml")

import sys  # noqa: E402

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.edgelab import settlement_reconciliation as recon  # noqa: E402


def _doc():
    with open(WORKFLOW_PATH) as f:
        return yaml.safe_load(f)


def _source():
    with open(WORKFLOW_PATH) as f:
        return f.read()


def _on(doc):
    # PyYAML parses a bare `on:` key as the boolean True (YAML 1.1).
    return doc.get("on", doc.get(True))


def test_workflow_exists_and_is_valid_yaml():
    assert os.path.isfile(WORKFLOW_PATH)
    assert _doc()["name"] == "EdgeLab Settlement Reconcile"


def test_push_trigger_is_scoped_to_the_canonical_bet_ledger_on_main():
    push = _on(_doc())["push"]
    assert push["branches"] == ["main"]
    assert push["paths"] == [recon.BETS_LEDGER_PATH]


def test_a_scheduled_sweep_exists():
    """Without this, a bet imported BEFORE its game ends could never
    settle automatically -- the push already happened, and no later push
    is coming."""
    schedule = _on(_doc())["schedule"]
    assert len(schedule) >= 1
    assert all("cron" in entry for entry in schedule)


def test_manual_dispatch_supports_an_explicit_date_and_range_and_dry_run():
    inputs = _on(_doc())["workflow_dispatch"]["inputs"]
    for name in ("date", "start_date", "end_date", "lookback_days", "dry_run"):
        assert name in inputs, f"recovery/debug input {name!r} is missing"


def test_job_guards_against_reacting_to_its_own_commit():
    condition = _doc()["jobs"]["reconcile"]["if"]
    assert recon.RECONCILE_COMMIT_MARKER in condition
    assert "github.event_name != 'push'" in condition


def test_commit_message_carries_the_marker_the_guard_matches():
    """The recursion guard greps the head commit message for this exact
    prefix; the commit step must actually produce it."""
    source = _source()
    assert f'--message "{recon.RECONCILE_COMMIT_MARKER}' in source


def test_shares_the_postgame_concurrency_group_and_never_cancels():
    concurrency = _doc()["concurrency"]
    assert concurrency["group"] == "edgelab-postgame", (
        "must serialize against EdgeLab Postgame Settlement -- both call settle_date() "
        "and upsert the same settlements/bets partitions"
    )
    assert concurrency.get("cancel-in-progress", False) is False


def test_commits_through_the_shared_safe_git_path_only():
    source = _source()
    assert "scripts/ci/git_data_commit.py" in source
    assert "git push" not in source
    assert "rebase --autostash" not in source


def test_commit_target_branch_is_resolved_fail_closed():
    source = _source()
    assert "scripts/ci/resolve_commit_branch.py" in source
    assert "--branch " in source


def test_never_touches_production_ledgers():
    """Only ever writes under data/edgelab/ -- the same boundary every
    other EdgeLab workflow holds."""
    # Comment lines are excluded on purpose: the workflow's own header
    # NAMES these files to state that it never writes them.
    executable = "\n".join(
        line for line in _source().splitlines() if not line.strip().startswith("#")
    )
    for forbidden in ('"bets.json"', '"data/bets.json"', '"data/slate.json"', "marketLedger"):
        assert forbidden not in executable, f"{forbidden} must never be committed by this workflow"


def test_dispatch_inputs_are_never_interpolated_into_a_run_body():
    """
    A raw ${{ inputs.x }} inside a run: block is expanded by the runner
    BEFORE the shell parses the line. Every input here goes through env:
    (see import-manual-bets.yml's SECURITY note).
    """
    offenders = []
    for lineno, line in enumerate(_source().splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "${{" not in stripped:
            continue
        if "inputs." not in stripped and "github.event.before" not in stripped:
            continue
        # Allowed: a `KEY: ${{ ... }}` env/if assignment, and `if:` conditions.
        if stripped.startswith("if:") or ": ${{" in stripped:
            continue
        offenders.append(f"line {lineno}: {stripped}")
    assert offenders == [], f"workflow_dispatch inputs interpolated into a run body: {offenders}"


def test_reconciler_script_is_the_only_settlement_entry_point_used():
    """No second settlement implementation may sneak in: the workflow
    calls the reconciler, which calls settle_date(). It must never call
    a grading script of its own."""
    source = _source()
    assert "scripts/edgelab/reconcile_settlement_catchup.py" in source


def test_commit_scope_excludes_partitions_owned_by_the_capture_workflow():
    """
    data/edgelab/markets/ and data/edgelab/observations/ belong to
    "EdgeLab Market Capture", which is NOT in this job's concurrency
    group and gzips finalized partitions. git_data_commit.py cannot
    prove a .jsonl.gz conflict is a pure append, so including them would
    let a benign concurrent capture fail this commit closed and discard
    the settlement work with it.
    """
    source = _source()
    commit_block = source.split("FILES=(", 1)[1].split(")", 1)[0]
    assert '"data/edgelab/bets/bets.jsonl"' in commit_block
    assert '"data/edgelab/settlements/"' in commit_block
    assert '"data/edgelab/markets/"' not in commit_block
    assert '"data/edgelab/observations/"' not in commit_block
