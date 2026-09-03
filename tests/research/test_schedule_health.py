#!/usr/bin/env python3
"""
tests/research/test_schedule_health.py
==========================================
Guards for the MLB-ALPHA-0002 schedule-health gate and the accumulation
clock. The gates are an infrastructure control, so the things worth
locking are: the thresholds do not drift, manual runs cannot be counted as
scheduled coverage, expected slots come from the cron spec rather than
from observed runs, and the clock cannot start on a failing gate.
"""
import os
import sys
from datetime import datetime

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from scripts.research.mlb_alpha_0002 import schedule_health as sh  # noqa: E402

CAPTURE_WF = os.path.join(REPO, ".github", "workflows",
                          "research-mlb-alpha-0002-capture.yml")


def _run(at, event):
    return {"capturedAt": at, "triggerEvent": event, "runId": "R" + at}


# ---------------------------------------------------------------------------
# Frozen gates
# ---------------------------------------------------------------------------

def test_gate_thresholds_are_the_agreed_values():
    """Frozen. Moving a threshold after seeing results turns the check into
    a rubber stamp."""
    assert sh.GATE_VERSION == "INFRA_GATES_V1_2026_09_03"
    assert sh.GATES["persistedScheduleCoverageMin"] == 0.90
    assert sh.GATES["medianCaptureGapMaxMinutes"] == 15.0
    assert sh.GATES["p90CaptureGapMaxMinutes"] == 25.0
    assert sh.GATES["maxUnexplainedInWindowGapMinutes"] == 45.0


def test_expected_cadence_and_window_match_the_workflow_cron():
    """The analyser's idea of the schedule must match what actually runs."""
    wf = yaml.safe_load(open(CAPTURE_WF))
    triggers = wf.get("on") or wf.get(True)
    crons = [c["cron"] for c in triggers["schedule"]]
    assert crons == ["3,13,23,33,43,53 15-23 * * *", "3,13,23,33,43,53 0-4 * * *"]
    assert sh.CRON_MINUTES == (3, 13, 23, 33, 43, 53)
    assert set(sh.CAPTURE_WINDOW_HOURS) == set(range(15, 24)) | set(range(0, 5))
    assert sh.EXPECTED_CADENCE_MINUTES == 10


# ---------------------------------------------------------------------------
# Expected slots come from the cron spec, not from observed runs
# ---------------------------------------------------------------------------

def test_expected_slots_are_built_from_the_schedule_not_from_runs():
    start = sh.ts("2026-09-03T15:00:00Z")
    end = sh.ts("2026-09-03T16:00:00Z")
    slots = sh.expected_slots(start, end, sh.CRON_MINUTES)
    assert [s.strftime("%H:%M") for s in slots] == [
        "15:03", "15:13", "15:23", "15:33", "15:43", "15:53"]


def test_slots_outside_the_capture_window_are_not_expected():
    start = sh.ts("2026-09-03T08:00:00Z")
    end = sh.ts("2026-09-03T09:00:00Z")
    assert sh.expected_slots(start, end, sh.CRON_MINUTES) == []


# ---------------------------------------------------------------------------
# Manual runs must never count toward scheduled coverage
# ---------------------------------------------------------------------------

def test_manual_dispatches_do_not_count_as_coverage():
    start, end = sh.ts("2026-09-03T15:00:00Z"), sh.ts("2026-09-03T16:00:00Z")
    runs = [_run("2026-09-03T15:0%d:00Z" % m, "workflow_dispatch") for m in (3, 4, 5)]
    res, _gates = sh.analyse(runs, start, end, sh.CRON_MINUTES)
    assert res["manualCaptures"] == 3
    assert res["scheduledCaptures"] == 0
    assert res["coveredSlots"] == 0
    assert res["coverageRate"] == 0.0


def test_unstamped_manifests_are_unknown_never_assumed_scheduled():
    """Manifests written before triggerEvent existed must not be silently
    credited to the schedule."""
    start, end = sh.ts("2026-09-03T15:00:00Z"), sh.ts("2026-09-03T16:00:00Z")
    runs = [{"capturedAt": "2026-09-03T15:03:30Z", "runId": "legacy"}]
    res, _gates = sh.analyse(runs, start, end, sh.CRON_MINUTES)
    assert res["unknownTriggerCaptures"] == 1
    assert res["scheduledCaptures"] == 0
    assert res["coveredSlots"] == 0


def test_a_scheduled_capture_covers_its_slot():
    start, end = sh.ts("2026-09-03T15:00:00Z"), sh.ts("2026-09-03T16:00:00Z")
    runs = [_run("2026-09-03T15:%02d:30Z" % m, "schedule")
            for m in (3, 13, 23, 33, 43, 53)]
    res, gates = sh.analyse(runs, start, end, sh.CRON_MINUTES)
    assert res["coveredSlots"] == 6 and res["missedSlots"] == 0
    assert res["coverageRate"] == 1.0
    assert gates["persistedScheduleCoverage"] is True


def test_a_slot_that_fires_hours_late_outside_the_window_is_a_miss():
    """The real failure: the 04:50Z slot fired at 07:54Z, 184 minutes late
    and outside the capture window entirely. The slot must score as missed,
    and the stray capture must not manufacture coverage of its own -- there
    are no expected slots at 07:54."""
    start, end = sh.ts("2026-09-03T04:00:00Z"), sh.ts("2026-09-03T08:00:00Z")
    runs = [_run("2026-09-03T07:55:05Z", "schedule")]
    res, _gates = sh.analyse(runs, start, end, sh.V1_MINUTES)
    assert res["expectedSlots"] == 6              # 04:00-04:50 only; 05-07 out of window
    assert res["coveredSlots"] == 0
    assert res["scheduledCaptures"] == 1          # the run happened
    assert res["coverageRate"] == 0.0             # but covered nothing


def test_the_slot_match_window_equals_one_cadence_interval():
    """Documented tolerance: a capture credits the most recent slot within
    one cadence interval. Generous by design -- GitHub queue delay is the
    thing being measured, not punished."""
    assert sh.SLOT_MATCH_SECONDS == sh.EXPECTED_CADENCE_MINUTES * 60


# ---------------------------------------------------------------------------
# The clock cannot start on a failing gate
# ---------------------------------------------------------------------------

def test_every_gate_fails_on_the_measured_v1_schedule():
    """The real V1 numbers: 3 scheduled firings against 54 slots."""
    start, end = sh.ts("2026-09-02T23:08:44Z"), sh.ts("2026-09-03T18:04:09Z")
    runs = [_run(t, "schedule") for t in ("2026-09-03T01:07:51Z",
                                          "2026-09-03T02:58:07Z",
                                          "2026-09-03T07:55:05Z")]
    res, gates = sh.analyse(runs, start, end, sh.V1_MINUTES)
    assert res["expectedSlots"] == 54
    assert res["coverageRate"] < 0.90
    assert not all(gates.values()), "V1 must not pass the gate"


def test_a_single_failing_gate_blocks_the_clock():
    """All four gates must pass; coverage alone is not enough."""
    start, end = sh.ts("2026-09-03T15:00:00Z"), sh.ts("2026-09-03T16:00:00Z")
    # perfect coverage, but a 50-minute in-window hole before it
    runs = [_run("2026-09-03T15:%02d:30Z" % m, "schedule") for m in (3, 53)]
    _res, gates = sh.analyse(runs, start, end, sh.CRON_MINUTES)
    assert gates["persistedScheduleCoverage"] is False
    assert not all(gates.values())


# ---------------------------------------------------------------------------
# Provenance the coverage calculation depends on
# ---------------------------------------------------------------------------

def test_capture_records_the_trigger_event():
    src = open(os.path.join(REPO, "scripts", "research", "mlb_alpha_0002",
                            "prospective_capture.py")).read()
    assert '"triggerEvent"' in src and "GITHUB_EVENT_NAME" in src
    wf = open(CAPTURE_WF).read()
    assert "GITHUB_EVENT_NAME: ${{ github.event_name }}" in wf


def test_status_artifact_is_research_only():
    src = open(os.path.join(REPO, "scripts", "research", "mlb_alpha_0002",
                            "schedule_health.py")).read()
    assert '"researchOnly": True' in src
    assert '"productionConsumer": None' in src
    assert '"historicalPinnacleAuthorized": False' in src
    assert '"historicalPinnacleCreditsSpent": 0' in src


def test_percentiles_handle_small_and_empty_samples():
    assert sh.pctl([], 0.5) is None
    assert sh.pctl([10.0], 0.9) == 10.0
    assert sh.pctl([10.0, 20.0], 0.5) == 15.0
