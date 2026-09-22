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


# Workflows this coordinator can reach by dispatching. Chaining a
# workflow_run trigger off anything in here would close a cycle.
DISPATCH_CLOSURE = {
    "Capture Kalshi Snapshots (Scheduled)",   # dispatched directly
    "EdgeLab Market Capture",                 # chained off the above
}


def test_triggers_are_schedule_manual_or_a_chained_completion():
    """A closed set, widened once and deliberately.

    workflow_run was added in PHASE O because the coordinator is itself
    starved: its cron asks for 4 wakes an hour and it got two in its first
    hours on main, one of them delivered at 03:13 UTC -- past the last slot
    its schedule contains. workflow_run events are not subject to
    scheduled-run throttling, so chaining off workflows that DID get
    delivered buys independent wake opportunities.

    The set stays closed. This workflow holds actions:write, so a trigger
    that can be influenced by PR-authored content is never acceptable.
    """
    doc = _load()
    trigger = doc.get("on", doc.get(True))
    names = {trigger} if isinstance(trigger, str) else set(trigger)
    assert names == {"schedule", "workflow_dispatch", "workflow_run"}
    # pull_request_target would run PR-authored code with repo credentials
    # AND this workflow holds actions:write. Never.
    assert "pull_request_target" not in names
    assert "pull_request" not in names
    assert "issue_comment" not in names
    assert "repository_dispatch" not in names


def test_the_chained_hosts_are_outside_this_workflows_own_dispatch_closure():
    """THE cycle guard for the workflow_run trigger.

    coordinator -> capture-snapshots-scheduled.yml -> edgelab-capture.yml.
    Waking on the completion of anything in that chain would mean the
    coordinator could trigger the capture that triggers the coordinator.
    """
    doc = _load()
    trigger = doc.get("on", doc.get(True))
    hosts = set(trigger["workflow_run"]["workflows"])
    assert hosts, "a workflow_run trigger with no hosts is dead config"
    assert not (hosts & DISPATCH_CLOSURE), (
        "chaining off %s would close a dispatch cycle" % (hosts & DISPATCH_CLOSURE))


def test_the_chained_hosts_exist_and_dispatch_nothing_themselves():
    """A host that dispatches could reach back into the closure indirectly."""
    doc = _load()
    trigger = doc.get("on", doc.get(True))
    hosts = set(trigger["workflow_run"]["workflows"])

    found = {}
    workflow_dir = os.path.join(ROOT, ".github", "workflows")
    for filename in os.listdir(workflow_dir):
        if not filename.endswith((".yml", ".yaml")):
            continue
        path = os.path.join(workflow_dir, filename)
        with open(path) as fh:
            body = fh.read()
        parsed = yaml.safe_load(body)
        if isinstance(parsed, dict) and parsed.get("name") in hosts:
            found[parsed["name"]] = body

    assert set(found) == hosts, (
        "workflow_run names must match a real workflow: missing %s" % (hosts - set(found)))
    for name, body in found.items():
        assert "gh workflow run" not in body, (
            "%s dispatches another workflow, so chaining off it risks a cycle" % name)


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
