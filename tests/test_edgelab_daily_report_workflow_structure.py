#!/usr/bin/env python3
"""
tests/test_edgelab_daily_report_workflow_structure.py
============================================================
Structural regression test for .github/workflows/edgelab-daily-report.yml
-- Corpus Storage Growth mission: the corpus-compaction steps added to
this nightly (08:00 UTC) workflow must never be blocked by an unrelated
failure in the report-generation steps that precede them (`if: always()`),
and must run in dependency order (compact before commit).
"""
import os

import pytest

yaml = pytest.importorskip("yaml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_PATH = os.path.join(ROOT, ".github", "workflows", "edgelab-daily-report.yml")


@pytest.fixture(scope="module")
def steps():
    with open(WORKFLOW_PATH) as f:
        data = yaml.safe_load(f)
    return data["jobs"]["report"]["steps"]


def _index_by_name_substring(steps, substring):
    for i, s in enumerate(steps):
        if substring in (s.get("name") or ""):
            return i
    raise AssertionError(f"No step with name containing {substring!r} found in {WORKFLOW_PATH}")


def test_compact_step_exists_and_runs_always(steps):
    idx = _index_by_name_substring(steps, "Compact finalized EdgeLab partitions")
    assert steps[idx].get("if") == "always()", (
        "compaction must not be skipped just because the unrelated report-generation "
        "step earlier in this job failed"
    )
    assert "compact_edgelab_partitions.py" in steps[idx]["run"]


def test_compact_commit_step_exists_and_runs_always(steps):
    idx = _index_by_name_substring(steps, "Commit compacted partitions")
    assert steps[idx].get("if") == "always()"


def test_compact_step_precedes_its_own_commit_step(steps):
    compact_idx = _index_by_name_substring(steps, "Compact finalized EdgeLab partitions")
    commit_idx = _index_by_name_substring(steps, "Commit compacted partitions")
    assert compact_idx < commit_idx


def test_compact_commit_step_targets_only_the_five_growing_entities(steps):
    idx = _index_by_name_substring(steps, "Commit compacted partitions")
    run = steps[idx]["run"]
    for entity in ("settlements", "clv_quotes", "model_evaluations", "markets", "recommendations"):
        assert f"data/edgelab/{entity}/" in run
    # Never the corpus this mission deliberately did not touch.
    assert "data/edgelab/snapshots/" not in run
    assert "data/edgelab/observations/" not in run


# --------------------------------------------------------------------------
# PHASE 6: near-close capture health
# --------------------------------------------------------------------------

def test_capture_health_step_exists_and_runs_always(steps):
    """A coverage outage must be reported precisely when things are going
    wrong. Gating it on the report step's success would silence it exactly
    when the pipeline is unhealthy."""
    idx = _index_by_name_substring(steps, "Build near-close capture health report")
    assert steps[idx].get("if") == "always()"
    assert "build_capture_health_report.py" in steps[idx]["run"]


def test_capture_health_step_precedes_its_own_commit_step(steps):
    build = _index_by_name_substring(steps, "Build near-close capture health report")
    commit = _index_by_name_substring(steps, "Commit capture health report")
    assert build < commit
    assert steps[commit].get("if") == "always()"


def test_capture_health_commit_targets_only_its_own_two_artifacts(steps):
    """It must never be able to commit a ledger, a settlement or a slate."""
    run = steps[_index_by_name_substring(steps, "Commit capture health report")]["run"]
    paths = [tok.strip('"') for tok in run.split() if "data/edgelab" in tok]
    assert sorted(paths) == [
        "data/edgelab/reports/capture_health.json",
        "data/edgelab/reports/capture_health.md",
    ]


def test_capture_health_report_is_read_only_over_the_archive(steps):
    """It reads observations and slates; it writes nothing but its own report."""
    run = steps[_index_by_name_substring(steps, "Build near-close capture health report")]["run"]
    assert "--date" in run and "--days" in run
    assert ">" not in run and "rm " not in run
