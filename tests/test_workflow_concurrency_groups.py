#!/usr/bin/env python3
"""
tests/test_workflow_concurrency_groups.py
==============================================
Production Reliability and Settlement Recovery milestone: proves the
three workflows that read/write the shared system-of-record files
(data/slate.json, data/slates/<date>/authoritative.json, bets.json,
BET_LOG.md) all share ONE concurrency group, so GitHub Actions can never
run two of them at once -- and that the high-frequency, disjoint-path
snapshot workflows are deliberately NOT part of that group (they would
gain nothing from being serialized against it, and it would only slow
down their whole purpose: frequent, low-latency price snapshots).
See docs/POSTMORTEM_PRODUCTION_RELIABILITY_2026.md "Workflow
concurrency" for the full investigation this design is based on.
"""
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOWS_DIR = os.path.join(ROOT, ".github", "workflows")

LEDGER_WRITERS = ("fetch-slate.yml", "clv-update.yml", "lineup-recheck.yml")
DISJOINT_SNAPSHOT_WORKFLOWS = (
    "capture-snapshots-scheduled.yml",
    "clv_capture.yml",
    "capture-closing-lines.yml",
)
SHARED_GROUP = "edge-finder-ledger-writer"


def _load(name):
    with open(os.path.join(WORKFLOWS_DIR, name)) as f:
        return yaml.safe_load(f)


def test_all_ledger_writing_workflows_share_the_same_concurrency_group():
    for name in LEDGER_WRITERS:
        doc = _load(name)
        concurrency = doc.get("concurrency")
        assert concurrency is not None, f"{name} has no concurrency block"
        assert concurrency.get("group") == SHARED_GROUP, (
            f"{name} concurrency.group={concurrency.get('group')!r}, expected {SHARED_GROUP!r}"
        )


def test_shared_group_never_cancels_in_progress_runs():
    """
    cancel-in-progress must be false (or unset) for all three -- these
    are real-money/settlement writers; a queued run should WAIT for the
    current one to finish, never cancel it mid-write.
    """
    for name in LEDGER_WRITERS:
        doc = _load(name)
        concurrency = doc["concurrency"]
        assert concurrency.get("cancel-in-progress", False) is False, (
            f"{name} must not cancel-in-progress"
        )


def test_disjoint_snapshot_workflows_not_in_the_shared_group():
    """
    These write only their own disjoint data/ subtree (never
    data/slate.json, data/slates/, bets.json, or BET_LOG.md) -- forcing
    them into the shared group would serialize frequent, low-latency
    price snapshots against infrequent slate/settlement runs for no
    correctness benefit.
    """
    for name in DISJOINT_SNAPSHOT_WORKFLOWS:
        doc = _load(name)
        concurrency = doc.get("concurrency") or {}
        assert concurrency.get("group") != SHARED_GROUP, (
            f"{name} should not share the ledger-writer concurrency group"
        )


def test_every_workflow_file_is_valid_yaml():
    for name in os.listdir(WORKFLOWS_DIR):
        if name.endswith((".yml", ".yaml")):
            _load(name)  # raises on invalid YAML
