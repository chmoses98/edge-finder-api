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
from datetime import datetime, timedelta

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


# ===========================================================================
# BOOKKEEPING DEFECT REGRESSIONS
# The capture data was never in question; these guard the MEASUREMENT and
# the CLOCK STATE, which are what actually decide when Day 0 begins.
# ===========================================================================

import json
import subprocess
import tempfile

HEALTH = os.path.join(REPO, "scripts", "research", "mlb_alpha_0002", "schedule_health.py")


def _full_window_runs(day="2026-09-04"):
    """A complete 15:03Z -> next-day 04:53Z window, every slot on time."""
    runs = []
    d0 = datetime.strptime(day, "%Y-%m-%d")
    t = d0.replace(hour=15, minute=3)
    end = (d0 + timedelta(days=1)).replace(hour=4, minute=53)
    i = 0
    while t <= end:
        if t.hour in sh.CAPTURE_WINDOW_HOURS and t.minute in sh.CRON_MINUTES:
            runs.append({"capturedAt": sh.iso(t + timedelta(seconds=30)),
                         "triggerEvent": "schedule", "runId": "R%d" % i,
                         "githubRunId": str(1000 + i)})
            i += 1
        t += timedelta(minutes=1)
    return runs


def _run_cli(tmpdir, runs, since, now, prior=None, extra=()):
    """Invoke the real CLI against a scratch corpus so clock persistence is
    exercised end to end, not just in-process."""
    cap = os.path.join(tmpdir, "prospective", "runs")
    os.makedirs(cap, exist_ok=True)
    with open(os.path.join(cap, "2026-09-04.jsonl"), "w") as fh:
        for r in runs:
            fh.write(json.dumps(r) + "\n")
    status_path = os.path.join(tmpdir, "accumulation_status.json")
    if prior is not None:
        with open(status_path, "w") as fh:
            json.dump(prior, fh)
    env = dict(os.environ)
    code = (
        "import runpy,sys,os;"
        "sys.argv=['h','--since','%s','--now','%s','--write']+%r;"
        "import scripts.research.mlb_alpha_0002.schedule_health as sh;"
        "sh.CAP=%r; sh.OUT_STATUS=%r;"
        "sh.main()" % (since, now, list(extra), os.path.join(tmpdir, "prospective"), status_path)
    )
    subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                   check=True, capture_output=True)
    with open(status_path) as fh:
        return json.load(fh)


# --- 1/2/3: sticky Day 0 and real elapsed time ------------------------------

def test_first_passing_full_window_starts_the_clock_once():
    with tempfile.TemporaryDirectory() as tmp:
        st = _run_cli(tmp, _full_window_runs(),
                      "2026-09-04T15:00:00Z", "2026-09-05T05:00:00Z")
        assert st["today"]["expectedSlots"] == 84
        assert st["today"]["completeOperatingWindows"] == 1
        assert st["currentHealthGatePassed"] is True
        assert st["accumulationClockStarted"] is True
        assert st["accumulationStartUtc"] == "2026-09-04T15:03:00Z"
        assert st["scheduleVersionAtStart"] == sh.SCHEDULE_VERSION
        assert st["healthGateVersionAtStart"] == sh.GATE_VERSION


def test_a_later_run_preserves_the_identical_accumulation_start():
    """Day 0 must not march forward on every cycle -- the defect this
    replaces recomputed it as `now` each time."""
    with tempfile.TemporaryDirectory() as tmp:
        first = _run_cli(tmp, _full_window_runs(),
                         "2026-09-04T15:00:00Z", "2026-09-05T05:00:00Z")
        second = _run_cli(tmp, _full_window_runs(),
                          "2026-09-05T06:00:00Z", "2026-09-05T07:00:00Z",
                          prior=first)
        assert second["accumulationStartUtc"] == first["accumulationStartUtc"]
        assert second["accumulationClockStarted"] is True


def test_healthy_days_elapsed_grows_and_is_not_hardcoded_zero():
    with tempfile.TemporaryDirectory() as tmp:
        first = _run_cli(tmp, _full_window_runs(),
                         "2026-09-04T15:00:00Z", "2026-09-05T05:00:00Z")
        assert first["healthyDaysElapsed"] > 0.0
        assert first["healthyHoursElapsed"] > 13.0      # 15:03 -> 04:53 next day
        assert first["calendarDatesTouchedSinceStart"] >= 2
        later = _full_window_runs() + [
            {"capturedAt": "2026-09-06T15:03:30Z", "triggerEvent": "schedule",
             "runId": "later", "githubRunId": "9999"}]
        second = _run_cli(tmp, later, "2026-09-04T15:00:00Z",
                          "2026-09-06T16:00:00Z", prior=first)
        assert second["healthyDaysElapsed"] > first["healthyDaysElapsed"]


# --- 10: a later failure warns but never rewrites Day 0 ---------------------

def test_a_later_gate_failure_preserves_day_zero_and_raises_a_warning():
    with tempfile.TemporaryDirectory() as tmp:
        first = _run_cli(tmp, _full_window_runs(),
                         "2026-09-04T15:00:00Z", "2026-09-05T05:00:00Z")
        sparse = _full_window_runs() + [
            {"capturedAt": "2026-09-06T15:03:30Z", "triggerEvent": "schedule",
             "runId": "lonely", "githubRunId": "1"}]
        second = _run_cli(tmp, sparse, "2026-09-06T15:00:00Z",
                          "2026-09-07T05:00:00Z", prior=first)
        assert second["currentHealthGatePassed"] is False
        assert second["accumulationHealthWarning"] is True
        assert second["accumulationClockStarted"] is True
        assert second["accumulationStartUtc"] == first["accumulationStartUtc"]


# --- 4: manual dispatches cannot improve the gate metrics -------------------

def test_manual_captures_cannot_improve_scheduled_gap_metrics():
    """A human dispatching runs must not make GitHub scheduling look healthy."""
    start, end = sh.ts("2026-09-04T15:00:00Z"), sh.ts("2026-09-04T17:00:00Z")
    sparse = [{"capturedAt": "2026-09-04T15:03:30Z", "triggerEvent": "schedule",
               "runId": "s1", "githubRunId": "1"},
              {"capturedAt": "2026-09-04T16:53:30Z", "triggerEvent": "schedule",
               "runId": "s2", "githubRunId": "2"}]
    manual_filler = [{"capturedAt": "2026-09-04T15:%02d:30Z" % m,
                      "triggerEvent": "workflow_dispatch", "runId": "m%d" % m}
                     for m in (13, 23, 33, 43, 53)]
    bare, gates_bare = sh.analyse(sparse, start, end, sh.CRON_MINUTES)
    padded, gates_padded = sh.analyse(sparse + manual_filler, start, end, sh.CRON_MINUTES)
    assert bare["scheduledGapMetrics"] == padded["scheduledGapMetrics"]
    assert gates_bare == gates_padded
    # the manual runs DO show up in the all-capture density view
    assert padded["allCaptureGapMetrics"]["captures"] > bare["allCaptureGapMetrics"]["captures"]
    assert padded["manualCaptures"] == 5


# --- 5/6: one-to-one matching --------------------------------------------

def test_one_capture_cannot_cover_two_slots():
    """SLOT_MATCH_SECONDS equals the cadence, so a capture landing exactly on
    slot N is also exactly 600 s after slot N-1. Naive matching credited
    both."""
    start, end = sh.ts("2026-09-04T15:00:00Z"), sh.ts("2026-09-04T15:30:00Z")
    runs = [{"capturedAt": "2026-09-04T15:13:00Z", "triggerEvent": "schedule",
             "runId": "one", "githubRunId": "7"}]
    res, _g = sh.analyse(runs, start, end, sh.CRON_MINUTES)
    assert res["scheduledCaptures"] == 1
    assert res["coveredSlots"] == 1
    assert len(res["slotAssignments"]) == 1
    assert res["slotAssignments"][0]["expectedSlotUtc"] == "2026-09-04T15:13:00Z"


def test_covered_slots_never_exceeds_scheduled_captures_invariant():
    """Property check across a spread of arrival offsets."""
    start, end = sh.ts("2026-09-04T15:00:00Z"), sh.ts("2026-09-04T18:00:00Z")
    for offset in (0, 1, 59, 300, 599, 600):
        runs = [{"capturedAt": sh.iso(sh.ts("2026-09-04T15:%02d:00Z" % m)
                                     + timedelta(seconds=offset)),
                 "triggerEvent": "schedule", "runId": "r%d" % m}
                for m in (3, 13, 23, 33, 43, 53)]
        res, _g = sh.analyse(runs, start, end, sh.CRON_MINUTES)
        assert res["coveredSlots"] <= res["scheduledCaptures"], offset


# --- 11: delay metrics derive from the one-to-one mapping -------------------

def test_delay_metrics_come_from_the_slot_assignment():
    start, end = sh.ts("2026-09-04T15:00:00Z"), sh.ts("2026-09-04T16:00:00Z")
    runs = [{"capturedAt": "2026-09-04T15:04:00Z", "triggerEvent": "schedule",
             "runId": "a", "githubRunId": "11"},
            {"capturedAt": "2026-09-04T15:19:00Z", "triggerEvent": "schedule",
             "runId": "b", "githubRunId": "12"}]
    res, _g = sh.analyse(runs, start, end, sh.CRON_MINUTES)
    delays = [a["delaySeconds"] for a in res["slotAssignments"]]
    assert delays == [60, 360]
    assert res["scheduledDelayMaxMinutes"] == 6.0
    assert res["slotAssignments"][0]["githubRunId"] == "11"


# --- 7/8/9: V2 clamp and minimum sample ------------------------------------

def test_v2_expected_slots_never_precede_the_v2_effective_timestamp():
    with tempfile.TemporaryDirectory() as tmp:
        st = _run_cli(tmp, [], "2026-09-01T15:00:00Z", "2026-09-03T19:00:00Z")
        assert st["auditIntervalStart"] == sh.SCHEDULE_V2_EFFECTIVE_FROM_UTC
        assert st["requestedAuditIntervalStart"] == "2026-09-01T15:00:00Z"
        first_slot = sh.expected_slots(sh.ts(st["auditIntervalStart"]),
                                       sh.ts(st["auditIntervalEnd"]), sh.CRON_MINUTES)
        assert all(s >= sh.ts(sh.SCHEDULE_V2_EFFECTIVE_FROM_UTC) for s in first_slot)


def test_a_partial_v2_window_cannot_start_the_clock():
    """Even a perfect partial day is not enough evidence for Day 0."""
    with tempfile.TemporaryDirectory() as tmp:
        partial = [r for r in _full_window_runs() if r["capturedAt"] < "2026-09-04T20:00:00Z"]
        st = _run_cli(tmp, partial, "2026-09-04T15:00:00Z", "2026-09-04T20:00:00Z")
        assert st["today"]["expectedSlots"] < sh.MIN_EXPECTED_SLOTS_FOR_ACCUMULATION_START
        assert st["today"]["completeOperatingWindows"] == 0
        assert st["sufficientSampleForAccumulationStart"] is False
        assert st["accumulationClockStarted"] is False
        assert st["accumulationStartUtc"] is None


def test_minimum_sample_constant_is_one_full_window():
    assert sh.MIN_EXPECTED_SLOTS_FOR_ACCUMULATION_START == 84
    assert sh.SLOTS_PER_WINDOW == 84


# --- 12: the historical V1 report stays immutable ---------------------------

def test_historical_v1_report_is_unchanged_by_the_v2_clamp():
    """V1's measured result must remain reproducible: 54 expected slots,
    3 scheduled firings, every gate failing."""
    start, end = sh.ts("2026-09-02T23:08:44Z"), sh.ts("2026-09-03T18:04:09Z")
    runs = [{"capturedAt": t, "triggerEvent": "schedule", "runId": t}
            for t in ("2026-09-03T01:07:51Z", "2026-09-03T02:58:07Z", "2026-09-03T07:55:05Z")]
    res, gates = sh.analyse(runs, start, end, sh.V1_MINUTES)
    assert res["expectedSlots"] == 54
    assert res["scheduledCaptures"] == 3
    assert res["coverageRate"] < 0.90
    assert not any(gates.values())
