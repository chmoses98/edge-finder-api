#!/usr/bin/env python3
"""
tests/test_near_close_capture_workflow_safety.py
================================================
PHASE 15 safety pins for .github/workflows/near-close-capture.yml.

A coordinator that wakes every 15 minutes and can dispatch another
workflow is exactly the shape that becomes a workflow storm or a
credential leak if it drifts. These assert the properties that keep it
from doing either.
"""
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, ".github", "workflows", "near-close-capture.yml")


def _load():
    with open(PATH) as f:
        return yaml.safe_load(f)


def _raw():
    with open(PATH) as f:
        return f.read()


def test_triggers_are_schedule_and_manual_only():
    doc = _load()
    trigger = doc.get("on", doc.get(True))
    names = {trigger} if isinstance(trigger, str) else set(trigger)
    assert names == {"schedule", "workflow_dispatch"}
    # pull_request_target would run PR-authored code with repo credentials
    # AND this workflow holds actions:write. Never.
    assert "pull_request_target" not in names
    assert "pull_request" not in names


def test_permissions_are_minimal_and_cannot_write_repo_contents():
    doc = _load()
    assert doc["permissions"] == {"contents": "read", "actions": "write"}


def test_it_never_commits_to_the_repository():
    raw = _raw()
    for forbidden in ("git commit", "git push", "git add"):
        assert forbidden not in raw, "coordinator must not write to the repo: %s" % forbidden


def test_concurrency_collapses_a_backlog_of_delayed_wakes():
    """A delayed wake's plan is stale; cancelling it is correct."""
    doc = _load()
    assert doc["concurrency"]["cancel-in-progress"] is True
    assert doc["concurrency"]["group"] == "near-close-capture-coordinator"


def test_it_dispatches_at_most_one_workflow_and_that_one_is_terminal():
    """Two hops deep, so the dispatch graph cannot cycle."""
    raw = _raw()
    dispatches = [l for l in raw.splitlines() if "gh workflow run" in l]
    assert len(dispatches) == 1, "exactly one dispatch target expected"
    assert "capture-snapshots-scheduled.yml" in dispatches[0]

    target = os.path.join(ROOT, ".github", "workflows", "capture-snapshots-scheduled.yml")
    with open(target) as f:
        target_raw = f.read()
    assert "gh workflow run" not in target_raw, (
        "the capture workflow must not dispatch anything, or the graph could cycle"
    )


def test_the_dispatch_is_gated_on_the_plan_not_on_the_schedule():
    """The whole point: waking often must not mean capturing often."""
    doc = _load()
    step = next(s for s in doc["jobs"]["plan"]["steps"]
                if s.get("name", "").startswith("Dispatch"))
    assert "steps.plan.outputs.should_capture == 'true'" in step["if"]


def test_only_the_dispatch_step_receives_a_token():
    doc = _load()
    for step in doc["jobs"]["plan"]["steps"]:
        env = step.get("env") or {}
        has_token = any("secrets." in str(v) for v in env.values())
        if has_token:
            assert step.get("name", "").startswith("Dispatch"), (
                "only the dispatch step may hold a credential: %s" % step.get("name")
            )


def test_wake_cadence_stays_within_the_mlb_start_window():
    """A 24/7 */15 cron would be 96 pointless wakes a day."""
    doc = _load()
    trigger = doc.get("on", doc.get(True))
    crons = [s["cron"] for s in trigger["schedule"]]
    assert crons, "coordinator needs a wake schedule"
    for cron in crons:
        hours = cron.split()[1]
        assert hours != "*", "wake cadence must be scoped to the start window, got %r" % cron
