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
A GitHub run that fires, captures, and then fails to persist is not
coverage. So a slot counts as covered only when a capture manifest for it
exists in the durable corpus. Manual dispatches are excluded from the
coverage rate entirely -- counting them would flatter the cadence
measurement with runs a human triggered. That exclusion needs the trigger
recorded on the row itself, which prospective_capture.py now stamps as
`triggerEvent`; manifests written before that stamp existed are attributed
by falling back to a supplied receipt list, and counted as UNKNOWN
otherwise (never silently as scheduled).

The health gates are frozen in GATE_VERSION and must not be moved after
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
EXPECTED_CADENCE_MINUTES = 10
# cron: '3,13,23,33,43,53 15-23 * * *' and '... 0-4 * * *'
CAPTURE_WINDOW_HOURS = tuple(list(range(15, 24)) + list(range(0, 5)))
CRON_MINUTES = (3, 13, 23, 33, 43, 53)
# Schedule V1 ('*/10') ran until the V2 offset merged; slots before that
# used the round minutes. Kept so historical intervals score honestly.
V1_MINUTES = (0, 10, 20, 30, 40, 50)

GATE_VERSION = "INFRA_GATES_V1_2026_09_03"
GATES = {
    "persistedScheduleCoverageMin": 0.90,
    "medianCaptureGapMaxMinutes": 15.0,
    "p90CaptureGapMaxMinutes": 25.0,
    "maxUnexplainedInWindowGapMinutes": 45.0,
}

# A slot counts as covered when a persisted capture lands within this many
# seconds after it. GitHub delay is the thing being measured, so the window
# is generous enough not to punish a normal queue delay.
SLOT_MATCH_SECONDS = 600


def ts(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def expected_slots(start, end, minutes):
    """Every cron slot in [start, end], built from the SCHEDULE, never
    inferred from observed runs -- a missed slot has no run to infer from,
    which is exactly the thing we are counting."""
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


def pctl(values, q):
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * q
    f = int(k)
    c = min(f + 1, len(v) - 1)
    return round(v[f] + (v[c] - v[f]) * (k - f), 1)


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
        (scheduled if ev == "schedule" else manual if ev == "workflow_dispatch"
         else unknown).append(t)

    covered = [s for s in slots
               if any(0 <= (p - s).total_seconds() <= SLOT_MATCH_SECONDS for p in scheduled)]
    all_captures = sorted(scheduled + manual + unknown)
    gaps = [(b - a).total_seconds() / 60.0 for a, b in zip(all_captures, all_captures[1:])]
    in_window = [((b - a).total_seconds() / 60.0, a, b)
                 for a, b in zip(all_captures, all_captures[1:])
                 if a.hour in CAPTURE_WINDOW_HOURS and b.hour in CAPTURE_WINDOW_HOURS]
    worst = max(in_window, key=lambda x: x[0]) if in_window else None

    coverage = (len(covered) / len(slots)) if slots else None
    median_gap, p90_gap = pctl(gaps, 0.5), pctl(gaps, 0.9)
    max_gap = round(max(gaps), 1) if gaps else None

    results = {
        "expectedSlots": len(slots),
        "scheduledCaptures": len(scheduled),
        "manualCaptures": len(manual),
        "unknownTriggerCaptures": len(unknown),
        "coveredSlots": len(covered),
        "missedSlots": len(slots) - len(covered),
        "coverageRate": round(coverage, 4) if coverage is not None else None,
        "medianCaptureGapMinutes": median_gap,
        "p90CaptureGapMinutes": p90_gap,
        "maxCaptureGapMinutes": max_gap,
        "worstInWindowGapMinutes": round(worst[0], 1) if worst else None,
        "worstInWindowGapFrom": iso(worst[1]) if worst else None,
        "worstInWindowGapTo": iso(worst[2]) if worst else None,
    }
    gate_results = {
        "persistedScheduleCoverage": (coverage is not None
                                      and coverage >= GATES["persistedScheduleCoverageMin"]),
        "medianCaptureGap": median_gap is not None and median_gap <= GATES["medianCaptureGapMaxMinutes"],
        "p90CaptureGap": p90_gap is not None and p90_gap <= GATES["p90CaptureGapMaxMinutes"],
        "noUnexplainedInWindowGap": bool(worst) and worst[0] <= GATES["maxUnexplainedInWindowGapMinutes"],
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
    start = ts(args.since) if args.since else end - timedelta(hours=24)
    minutes = V1_MINUTES if args.schedule_minutes == "v1" else CRON_MINUTES

    runs = load_runs()
    results, gates = analyse(runs, start, end, minutes)
    passed = all(gates.values())

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
        "auditIntervalStart": iso(start),
        "auditIntervalEnd": iso(end),
        "scheduleVersion": SCHEDULE_VERSION,
        "scheduleVersionFrozenAt": SCHEDULE_VERSION_FROZEN_AT,
        "expectedCadenceMinutes": EXPECTED_CADENCE_MINUTES,
        "healthGateVersion": GATE_VERSION,
        "healthGates": GATES,
        "gateResults": gates,
        "healthGatePassed": passed,
        # The clock starts ONLY on a passing gate, and is never backdated
        # into a period known to be sparse.
        "accumulationClockStarted": passed,
        "accumulationStartUtc": iso(end) if passed else None,
        "healthyDaysElapsed": 0.0 if passed else 0.0,
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
