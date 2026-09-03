#!/usr/bin/env python3
"""
scripts/research/mlb_alpha_0002/schedule_health.py
=======================================================
MLB-ALPHA-0002 schedule reliability + accumulation clock.

Answers one operational question: is the prospective collector actually
producing a dense enough persisted microstructure panel to start a formal
accumulation clock? It is deliberately NOT an alpha metric -- nothing here
says anything about whether a strategy works.

WHY COVERAGE IS MEASURED FROM THE PERSISTED CORPUS
--------------------------------------------------
A run that fires, captures, and then fails to persist is not coverage --
that failure has actually happened here. So a slot counts as covered only
when a capture manifest for it exists in the durable corpus, and only when
that manifest was written by a `schedule` trigger. Manifests predating the
`triggerEvent` stamp are reported as UNKNOWN and never silently credited
to the schedule.

BOOKKEEPING RULES THIS MODULE ENFORCES
--------------------------------------
1. STICKY DAY 0. accumulationStartUtc is written ONCE, on the first
   eligible pass, and preserved verbatim thereafter. Recomputing it each
   cycle would march Day 0 forward forever and defeat the clock entirely.
2. REAL ELAPSED TIME. healthyDaysElapsed is measured from the frozen
   start to the newest evidence, never hardcoded.
3. SCHEDULE-ONLY GATES. The four health gates read scheduled captures
   ONLY. A human dispatching runs by hand must not be able to make GitHub
   scheduling look healthy. All-capture metrics are reported alongside for
   data-density purposes and feed no gate.
4. ONE-TO-ONE SLOT MATCHING. One persisted run can never cover two cron
   opportunities, so coveredSlots <= scheduledCaptures always holds.
5. NO BACKDATED V2. Schedule V2 slots are never constructed before the
   commit that made V2 live.
6. MINIMUM SAMPLE. The clock cannot start on a lucky partial day: one
   complete contiguous operating window (84 slots) is required. That is a
   statement about how much evidence is needed to judge the gates, not a
   change to the gates themselves.

The gate thresholds are frozen in GATE_VERSION and must not be moved after
seeing results -- moving a threshold to fit an outcome is how an
infrastructure check quietly becomes a rubber stamp.

RESEARCH ONLY. Reads the prospective corpus; writes one status artifact.
No network, no orders, no production consumer.
"""
import argparse
import glob
import gzip
import json
import os
import sys
from datetime import datetime, timedelta

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)

ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0002")
CAP = os.path.join(ART, "prospective")
OUT_STATUS = os.path.join(ART, "accumulation_status.json")

# ---------------------------------------------------------------- frozen
SCHEDULE_VERSION = "V2_OFFSET_3_13_23_33_43_53"
SCHEDULE_VERSION_FROZEN_AT = "2026-09-03T18:04:09Z"
# The commit that put the V2 cron on the default branch (#186). Schedules
# only fire from the default branch, so V2 could not have produced a slot
# before this instant and must never be scored against one.
SCHEDULE_V2_EFFECTIVE_FROM_UTC = "2026-09-03T18:22:12Z"
EXPECTED_CADENCE_MINUTES = 10
# cron: '3,13,23,33,43,53 15-23 * * *' and '... 0-4 * * *'
CAPTURE_WINDOW_HOURS = tuple(list(range(15, 24)) + list(range(0, 5)))
CRON_MINUTES = (3, 13, 23, 33, 43, 53)
# Schedule V1 ('*/10') ran until the V2 offset merged. Kept so the
# historical V1 report stays reproducible and immutable.
V1_MINUTES = (0, 10, 20, 30, 40, 50)

# One operating window runs 15:03Z -> next-day 04:53Z under V2.
WINDOW_START_HOUR, WINDOW_START_MINUTE = 15, CRON_MINUTES[0]
WINDOW_END_HOUR, WINDOW_END_MINUTE = 4, CRON_MINUTES[-1]
SLOTS_PER_WINDOW = len(CAPTURE_WINDOW_HOURS) * len(CRON_MINUTES)      # 84
# The clock may not start on a tiny lucky sample.
MIN_EXPECTED_SLOTS_FOR_ACCUMULATION_START = 84

GATE_VERSION = "INFRA_GATES_V1_2026_09_03"
GATES = {
    "persistedScheduleCoverageMin": 0.90,
    "medianCaptureGapMaxMinutes": 15.0,
    "p90CaptureGapMaxMinutes": 25.0,
    "maxUnexplainedInWindowGapMinutes": 45.0,
}

# A capture may claim a slot at most this far after it. Equal to one
# cadence interval: GitHub queue delay is the thing being measured, not
# punished, but a capture can never reach back beyond the previous slot.
SLOT_MATCH_SECONDS = 600


def ts(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def expected_slots(start, end, minutes):
    """Every cron slot in [start, end], built from the SCHEDULE, never
    inferred from observed runs -- a missed slot has no run to infer from,
    which is exactly the thing being counted."""
    slots = []
    t = start.replace(minute=0, second=0, microsecond=0)
    while t <= end:
        if t.hour in CAPTURE_WINDOW_HOURS:
            for m in minutes:
                s = t.replace(minute=m)
                if start <= s <= end:
                    slots.append(s)
        t += timedelta(hours=1)
    return sorted(slots)


def complete_windows(start, end):
    """Complete contiguous operating windows fully inside [start, end].

    A window is 15:03Z on day D through 04:53Z on D+1. A partial window
    cannot start the clock -- 84 slots is the minimum honest sample for
    judging the gates."""
    out = []
    day = (start - timedelta(days=1)).date()
    last = end.date()
    while day <= last:
        w0 = datetime.combine(day, datetime.min.time()).replace(
            hour=WINDOW_START_HOUR, minute=WINDOW_START_MINUTE)
        w1 = datetime.combine(day + timedelta(days=1), datetime.min.time()).replace(
            hour=WINDOW_END_HOUR, minute=WINDOW_END_MINUTE)
        if w0 >= start and w1 <= end:
            out.append((w0, w1))
        day += timedelta(days=1)
    return out


def pctl(values, q):
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * q
    f = int(k)
    c = min(f + 1, len(v) - 1)
    return round(v[f] + (v[c] - v[f]) * (k - f), 1)


def match_slots_one_to_one(slots, captures):
    """Assign each scheduled capture to AT MOST ONE unmatched expected slot.

    Necessary because SLOT_MATCH_SECONDS equals the cadence: a capture
    landing exactly on slot N is also exactly 600 s after slot N-1, so a
    naive "does any capture fall within 600 s of this slot" test lets one
    run satisfy two cron opportunities and silently inflates coverage.

    Rule, applied to captures in chronological order: take the MOST RECENT
    unmatched slot at or before the capture whose delay is within
    SLOT_MATCH_SECONDS. Deterministic, and guarantees
    coveredSlots <= scheduledCaptures.

    `captures` is a list of (datetime, runMeta). Returns the assignment
    list, newest last.
    """
    unmatched = sorted(slots)
    used = set()
    assignments = []
    for at, meta in sorted(captures, key=lambda c: c[0]):
        best = None
        for s in unmatched:
            if s > at:
                break
            if s in used:
                continue
            if (at - s).total_seconds() <= SLOT_MATCH_SECONDS:
                best = s              # keep walking; we want the LATEST eligible
        if best is not None:
            used.add(best)
            assignments.append({
                "expectedSlotUtc": iso(best),
                "captureUtc": iso(at),
                "delaySeconds": int((at - best).total_seconds()),
                "githubRunId": meta.get("githubRunId"),
                "runId": meta.get("runId"),
            })
    return assignments


def gap_metrics(times):
    """Gap statistics over a set of capture instants."""
    times = sorted(times)
    gaps = [(b - a).total_seconds() / 60.0 for a, b in zip(times, times[1:])]
    in_window = [((b - a).total_seconds() / 60.0, a, b)
                 for a, b in zip(times, times[1:])
                 if a.hour in CAPTURE_WINDOW_HOURS and b.hour in CAPTURE_WINDOW_HOURS]
    worst = max(in_window, key=lambda x: x[0]) if in_window else None
    return {
        "captures": len(times),
        "medianGapMinutes": pctl(gaps, 0.5),
        "p90GapMinutes": pctl(gaps, 0.9),
        "maxGapMinutes": round(max(gaps), 1) if gaps else None,
        "worstInWindowGapMinutes": round(worst[0], 1) if worst else None,
        "worstInWindowGapFrom": iso(worst[1]) if worst else None,
        "worstInWindowGapTo": iso(worst[2]) if worst else None,
    }


def load_runs():
    runs = []
    for f in sorted(glob.glob(os.path.join(CAP, "runs", "*.jsonl"))):
        for line in open(f):
            if line.strip():
                runs.append(json.loads(line))
    runs.sort(key=lambda r: r.get("capturedAt") or "")
    return runs


def count_rows(pattern, gz=True):
    total = 0
    for f in glob.glob(pattern):
        op = gzip.open if f.endswith(".gz") else open
        with op(f, "rt") as fh:
            total += sum(1 for line in fh if line.strip())
    return total


def load_prior_status():
    """The artifact is regenerated every cycle, so the prior one is the only
    memory Day 0 has. Never let a rolling-window miss erase it."""
    if not os.path.exists(OUT_STATUS):
        return {}
    try:
        with open(OUT_STATUS) as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return {}


def analyse(runs, start, end, minutes):
    slots = expected_slots(start, end, minutes)
    scheduled, manual, unknown = [], [], []
    for r in runs:
        at = r.get("capturedAt")
        if not at:
            continue
        t = ts(at)
        if not (start <= t <= end):
            continue
        ev = r.get("triggerEvent")
        bucket = scheduled if ev == "schedule" else manual if ev == "workflow_dispatch" else unknown
        bucket.append((t, r))

    assignments = match_slots_one_to_one(slots, scheduled)
    covered = len(assignments)

    sched_times = [t for t, _ in scheduled]
    all_times = [t for t, _ in scheduled + manual + unknown]
    sched_gaps = gap_metrics(sched_times)
    all_gaps = gap_metrics(all_times)

    delays = [a["delaySeconds"] / 60.0 for a in assignments]
    coverage = (covered / len(slots)) if slots else None

    windows = complete_windows(start, end)
    results = {
        "expectedSlots": len(slots),
        "completeOperatingWindows": len(windows),
        "completeWindowBounds": [[iso(a), iso(b)] for a, b in windows],
        "scheduledCaptures": len(scheduled),
        "manualCaptures": len(manual),
        "unknownTriggerCaptures": len(unknown),
        "coveredSlots": covered,
        "missedSlots": len(slots) - covered,
        "coverageRate": round(coverage, 4) if coverage is not None else None,
        # GATE INPUT -- scheduled captures only.
        "scheduledGapMetrics": sched_gaps,
        # Reported for data density / development-data availability only.
        # Feeds no gate: a manual dispatch must not make GitHub look healthy.
        "allCaptureGapMetrics": all_gaps,
        "scheduledDelayMedianMinutes": pctl(delays, 0.5),
        "scheduledDelayP90Minutes": pctl(delays, 0.9),
        "scheduledDelayMaxMinutes": round(max(delays), 1) if delays else None,
        "slotAssignments": assignments,
    }

    worst_sched = sched_gaps["worstInWindowGapMinutes"]
    gate_results = {
        "persistedScheduleCoverage": (coverage is not None
                                      and coverage >= GATES["persistedScheduleCoverageMin"]),
        "medianCaptureGap": (sched_gaps["medianGapMinutes"] is not None
                             and sched_gaps["medianGapMinutes"] <= GATES["medianCaptureGapMaxMinutes"]),
        "p90CaptureGap": (sched_gaps["p90GapMinutes"] is not None
                          and sched_gaps["p90GapMinutes"] <= GATES["p90CaptureGapMaxMinutes"]),
        "noUnexplainedInWindowGap": (worst_sched is not None
                                     and worst_sched <= GATES["maxUnexplainedInWindowGapMinutes"]),
    }
    return results, gate_results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None,
                    help="audit interval start, ISO Z (default: 24h before --now)")
    ap.add_argument("--now", default=None, help="audit interval end, ISO Z (default: utcnow)")
    ap.add_argument("--schedule-minutes", default="v2", choices=("v1", "v2"),
                    help="which frozen cron minute set the interval ran under")
    ap.add_argument("--write", action="store_true", help="write the status artifact")
    args = ap.parse_args()

    end = ts(args.now) if args.now else datetime.utcnow().replace(microsecond=0)
    requested_start = ts(args.since) if args.since else end - timedelta(hours=24)
    is_v2 = args.schedule_minutes == "v2"
    minutes = CRON_MINUTES if is_v2 else V1_MINUTES
    # V2 never scores slots from before V2 existed.
    start = max(requested_start, ts(SCHEDULE_V2_EFFECTIVE_FROM_UTC)) if is_v2 else requested_start

    runs = load_runs()
    results, gates = analyse(runs, start, end, minutes)
    gates_passed = all(gates.values())

    enough_sample = (results["expectedSlots"] >= MIN_EXPECTED_SLOTS_FOR_ACCUMULATION_START
                     and results["completeOperatingWindows"] >= 1)
    eligible_to_start = bool(is_v2 and gates_passed and enough_sample)

    prior = load_prior_status()
    already_started = bool(prior.get("accumulationClockStarted"))

    if already_started:
        # STICKY. Day 0 is written once and never recomputed. A later
        # health failure is recorded as a warning, not a rewrite of
        # history -- any invalidation policy is a separate decision.
        clock_started = True
        start_utc = prior.get("accumulationStartUtc")
        version_at_start = prior.get("scheduleVersionAtStart")
        gate_version_at_start = prior.get("healthGateVersionAtStart")
    elif eligible_to_start:
        clock_started = True
        # Day 0 is the first valid healthy-regime boundary: the start of
        # the first complete operating window in the evaluated interval.
        # Never "now", which would drift, and never backdated into a
        # period that was not evaluated.
        start_utc = results["completeWindowBounds"][0][0]
        version_at_start = SCHEDULE_VERSION
        gate_version_at_start = GATE_VERSION
    else:
        clock_started = False
        start_utc = None
        version_at_start = None
        gate_version_at_start = None

    evidence_through = max((ts(r["capturedAt"]) for r in runs if r.get("capturedAt")), default=end)
    if clock_started and start_utc:
        elapsed = (evidence_through - ts(start_utc)).total_seconds()
        healthy_hours = round(max(0.0, elapsed) / 3600.0, 4)
        healthy_days = round(max(0.0, elapsed) / 86400.0, 6)
        dates_touched = len({ts(r["capturedAt"]).date() for r in runs
                             if r.get("capturedAt") and ts(r["capturedAt"]) >= ts(start_utc)})
    else:
        healthy_hours, healthy_days, dates_touched = 0.0, 0.0, 0

    latest = runs[-1] if runs else {}
    credits = latest.get("oddsCredits") or {}
    integrity = latest.get("referenceIntegrity") or {}
    health = latest.get("orderbookHealth") or {}

    shadows = {}
    for d in sorted(glob.glob(os.path.join(CAP, "shadows", "*"))):
        shadows[os.path.basename(d)] = count_rows(os.path.join(d, "*.jsonl"), gz=False)

    status = {
        "programId": "MLB-ALPHA-0002",
        "generatedAt": iso(end),
        "researchOnly": True,
        "productionConsumer": None,
        "requestedAuditIntervalStart": iso(requested_start),
        "auditIntervalStart": iso(start),
        "auditIntervalEnd": iso(end),
        "evidenceThroughUtc": iso(evidence_through),
        "scheduleVersion": SCHEDULE_VERSION,
        "scheduleVersionFrozenAt": SCHEDULE_VERSION_FROZEN_AT,
        "scheduleV2EffectiveFromUtc": SCHEDULE_V2_EFFECTIVE_FROM_UTC,
        "scheduleMinutesEvaluated": args.schedule_minutes,
        "expectedCadenceMinutes": EXPECTED_CADENCE_MINUTES,
        "healthGateVersion": GATE_VERSION,
        "healthGates": GATES,
        "minExpectedSlotsForAccumulationStart": MIN_EXPECTED_SLOTS_FOR_ACCUMULATION_START,
        "slotsPerCompleteWindow": SLOTS_PER_WINDOW,
        "gateResults": gates,
        # Health of THIS evaluation, always current.
        "currentHealthGatePassed": gates_passed,
        "sufficientSampleForAccumulationStart": enough_sample,
        "eligibleToStartAccumulation": eligible_to_start,
        # Day 0 -- sticky once set.
        "accumulationClockStarted": clock_started,
        "accumulationStartUtc": start_utc,
        "scheduleVersionAtStart": version_at_start,
        "healthGateVersionAtStart": gate_version_at_start,
        # A later failure warns; it does NOT rewrite history. Any
        # invalidation policy is a separate, future decision.
        "accumulationHealthWarning": bool(clock_started and not gates_passed),
        "healthyDaysElapsed": healthy_days,
        "healthyHoursElapsed": healthy_hours,
        "calendarDatesTouchedSinceStart": dates_touched,
        "today": results,
        "data": {
            "quotes": count_rows(os.path.join(CAP, "quotes", "*.jsonl.gz")),
            "books": count_rows(os.path.join(CAP, "books", "*.jsonl.gz")),
            "trades": count_rows(os.path.join(CAP, "trades", "*.jsonl.gz")),
            "externalOdds": count_rows(os.path.join(CAP, "odds", "*.jsonl.gz")),
            "mlbState": count_rows(os.path.join(CAP, "mlb_state", "*.jsonl.gz")),
            "quotesUnchangedRefs": count_rows(os.path.join(CAP, "quotes_unchanged", "*.jsonl.gz")),
            "booksUnchangedRefs": count_rows(os.path.join(CAP, "books_unchanged", "*.jsonl.gz")),
        },
        "candidates": shadows,
        "queue": {
            "queueObservationsPresent": os.path.isdir(os.path.join(CAP, "queue_observations")),
            "signals": count_rows(os.path.join(CAP, "queue_observations", "opened", "*.jsonl.gz")),
            "note": "no C01-F5REV signal has fired yet; its frozen rule and checkpoint are untouched",
        },
        "integrity": {
            "latestRunId": latest.get("runId"),
            "newBookRefResolutionRate": integrity.get("bookRefResolutionRate"),
            "newQuoteRefResolutionRate": integrity.get("quoteRefResolutionRate"),
            "trulyDanglingRefs": (integrity.get("bookRefsDangling", 0) or 0)
                                 + (integrity.get("quoteRefsDangling", 0) or 0),
            "booksNonEmpty": health.get("nonEmpty"),
            "booksNullOrEmpty": health.get("nullOrEmpty"),
            "ordersPlaced": latest.get("ordersPlaced"),
            "readOnly": latest.get("readOnly"),
        },
        "oddsApi": {
            "lastCallCost": credits.get("last"),
            "used": credits.get("used"),
            "remaining": credits.get("remaining"),
            "historicalPinnacleCreditsSpent": 0,
            "historicalPinnacleProposalCredits": 2220,
            "historicalPinnacleAuthorized": False,
        },
    }

    if args.write:
        os.makedirs(os.path.dirname(OUT_STATUS), exist_ok=True)
        with open(OUT_STATUS, "w") as fh:
            json.dump(status, fh, indent=2, sort_keys=True)
            fh.write("\n")
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
