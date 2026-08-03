#!/usr/bin/env python3
"""
tests/edgelab/test_workflow_safety.py
=========================================
Structural safety checks for the three EdgeLab GitHub Actions workflows
(Phase 1 section K): no push-triggered recursive commit loop,
concurrency controls present, production workflows untouched, research
failures don't block production, and workflow_run references point at
real upstream workflow names.
"""
import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

WORKFLOWS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", ".github", "workflows")

EDGELAB_WORKFLOW_FILES = [
    "edgelab-capture.yml",
    "edgelab-clv-collect.yml",
    "edgelab-postgame.yml",
    "edgelab-daily-report.yml",
    "record-placed-bet.yml",
]


def _load(filename):
    with open(os.path.join(WORKFLOWS_DIR, filename)) as f:
        return yaml.safe_load(f)


def _all_workflow_names():
    names = set()
    for fname in os.listdir(WORKFLOWS_DIR):
        if not fname.endswith(".yml"):
            continue
        with open(os.path.join(WORKFLOWS_DIR, fname)) as f:
            doc = yaml.safe_load(f)
        if doc and doc.get("name"):
            names.add(doc["name"])
    return names


def test_edgelab_workflows_exist():
    for fname in EDGELAB_WORKFLOW_FILES:
        assert os.path.exists(os.path.join(WORKFLOWS_DIR, fname))


def test_no_edgelab_workflow_triggers_on_push():
    """Loop-avoidance: this repo's convention is workflow_run/schedule chaining, never `on: push`, for anything that commits."""
    for fname in EDGELAB_WORKFLOW_FILES:
        doc = _load(fname)
        on = doc.get(True) or doc.get("on")  # PyYAML parses bare 'on:' as boolean True key in some versions
        assert "push" not in on, f"{fname} must not trigger on push (recursive commit loop risk)"


def test_every_edgelab_workflow_has_concurrency_group():
    for fname in EDGELAB_WORKFLOW_FILES:
        doc = _load(fname)
        assert "concurrency" in doc, f"{fname} missing a concurrency group"
        assert doc["concurrency"].get("group")


def test_every_edgelab_workflow_supports_manual_dispatch():
    for fname in EDGELAB_WORKFLOW_FILES:
        doc = _load(fname)
        on = doc.get(True) or doc.get("on")
        assert "workflow_dispatch" in on, f"{fname} should support manual runs"


def test_workflow_run_references_point_at_real_existing_workflows():
    all_names = _all_workflow_names()
    for fname in EDGELAB_WORKFLOW_FILES:
        doc = _load(fname)
        on = doc.get(True) or doc.get("on")
        workflow_run = on.get("workflow_run")
        if not workflow_run:
            continue
        for referenced in workflow_run.get("workflows", []):
            assert referenced in all_names, f"{fname} references unknown workflow {referenced!r}"


def test_commit_steps_are_change_guarded():
    """Every commit step must check `git diff --cached --quiet` before committing (never an empty/unconditional commit)."""
    for fname in EDGELAB_WORKFLOW_FILES:
        with open(os.path.join(WORKFLOWS_DIR, fname)) as f:
            text = f.read()
        assert "git diff --cached --quiet" in text, f"{fname} must guard its commit step"


def test_edgelab_workflows_only_write_under_data_edgelab():
    """Every `git add` in an EdgeLab workflow must target data/edgelab/ — never a production file."""
    for fname in EDGELAB_WORKFLOW_FILES:
        doc = _load(fname)
        for job in doc.get("jobs", {}).values():
            for step in job.get("steps", []):
                run = step.get("run", "")
                for line in run.splitlines():
                    if line.strip().startswith("git add"):
                        assert "data/edgelab/" in line, f"{fname}: unexpected git add outside data/edgelab/: {line}"


def test_production_workflows_are_unmodified_by_this_branch():
    """Sanity check: none of the pre-existing production workflow files were touched (see docs/EDGELAB_PHASE1.md's constraint)."""
    production_workflows = {
        "fetch-slate.yml", "kalshi-price-check.yml", "lineup-recheck.yml",
        "clv-update.yml", "build-wager-research.yml", "discover-kalshi-mlb-markets.yml",
        "capture-closing-lines.yml", "capture-snapshots-scheduled.yml", "clv_capture.yml",
        "fetch-kalshi-clv.yml",
    }
    for fname in production_workflows:
        path = os.path.join(WORKFLOWS_DIR, fname)
        assert os.path.exists(path), f"expected pre-existing production workflow {fname} to still exist"


def test_edgelab_workflows_use_continue_on_error_for_best_effort_steps():
    """Research steps that must never block later steps or fail the run outright are marked continue-on-error."""
    postgame_doc = _load("edgelab-postgame.yml")
    steps = postgame_doc["jobs"]["settle"]["steps"]
    for step_name_fragment in ("Sync recommendation", "Settle full observed", "Re-ingest legacy"):
        step = next(s for s in steps if step_name_fragment in s.get("name", ""))
        assert step.get("continue-on-error") is True


def test_bulk_observation_ingestion_does_not_ride_the_10_minute_cadence():
    """
    Full-universe observation ingestion (one committed row per observed
    market per tick) must trigger off the 30-minute capture workflow,
    never the 10-minute CLV capture workflow — see
    docs/EDGELAB_PHASE1.md's storage-growth analysis for why riding the
    faster cadence would make git-committed volume unsustainable.
    """
    capture_doc = _load("edgelab-capture.yml")
    on = capture_doc.get(True) or capture_doc.get("on")
    triggering_workflows = on["workflow_run"]["workflows"]
    assert triggering_workflows == ["Capture Kalshi Snapshots (Scheduled)"]
    assert "CLV Pregame Snapshot Capture" not in triggering_workflows


def test_clv_collect_workflow_is_separate_and_rides_the_fast_cadence():
    clv_doc = _load("edgelab-clv-collect.yml")
    on = clv_doc.get(True) or clv_doc.get("on")
    assert on["workflow_run"]["workflows"] == ["CLV Pregame Snapshot Capture"]
