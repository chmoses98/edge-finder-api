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
