#!/usr/bin/env python3
"""
scripts/edgelab/build_capture_health_report.py
==============================================
PHASE 6: did near-close capture actually happen, or did we only ask for it?

The distinction this report exists to enforce is REQUESTED vs DELIVERED.
Cron configuration is a request. The archive is the delivery. For 13
consecutive days those two numbers differed by an order of magnitude --
28-78 slots requested, 5-7 snapshots delivered -- and nothing surfaced
it, so CLV was quietly scored against quotes a median 14.4 hours before
first pitch.

Per date it reports:
  slate       games, games with a resolvable start
  windows     targeted, delivered, missed-late, uncovered at end of day
  delivery    captures archived, scheduler delay median/p90
  evidence    markets archived, TRUE_CLOSE-eligible, PRE_CLOSE-only,
              markets with no pre-start evidence

Plus a trailing-7-day roll-up of the same, so degradation shows as a
trend rather than as a single bad morning.

It never infers a capture happened. Every delivery number comes from an
archived snapshot filename or an archived observation -- never from a
workflow's own claim to have run, because a workflow that fails after
its capture step still reports success.

Usage:
    python3 scripts/edgelab/build_capture_health_report.py [--date YYYY-MM-DD] [--days 7]
"""
import argparse
import collections
import datetime
import glob
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import checkpoints
from lib.edgelab.near_close_plan import (
    ACTION_ALREADY_COVERED, ACTION_MISSED_LATE, ACTION_SKIPPED_NO_START,
    TARGET_WINDOWS, build_capture_plan,
)
from scripts.edgelab.plan_near_close_capture import existing_capture_times, load_slate_games

SCHEMA_VERSION = "capture_health_v1"
OUT_DIR = os.path.join("data", "edgelab", "reports")


def _read(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def _percentiles(values):
    if not values:
        return {"median": None, "p90": None}
    vals = sorted(values)
    def at(q):
        return round(vals[min(round(q * (len(vals) - 1)), len(vals) - 1)], 1)
    return {"median": at(0.5), "p90": at(0.9)}


def evidence_from_observations(rows):
    """Coverage of the markets present in `rows`. Pure; no disk."""
    best = {}
    starts = {}
    # Why each market without usable evidence lacks it. "Seen only after first
    # pitch", "priced but we never saw a quote" and "start time unresolvable"
    # are three different problems with three different fixes, and collapsing
    # them into one bucket hides which one is actually happening.
    priced_prestart = set()
    seen_prestart = set()
    for obs in rows:
        ticker = obs.get("marketTicker")
        if not ticker:
            continue
        best.setdefault(ticker, None)
        start = obs.get("scheduledStart") or starts.get(ticker)
        if start:
            starts[ticker] = start
        if not start:
            continue
        seconds = checkpoints.seconds_before_start(obs.get("capturedAt"), start)
        # A post-start observation is not pre-start evidence, however close
        # to first pitch it landed. It is excluded, never clamped to zero.
        if seconds is None or seconds < 0:
            continue
        seen_prestart.add(ticker)
        status = (obs.get("marketStatus") or "active").lower()
        if status not in ("active", "unknown"):
            continue
        if not any(obs.get(k) is not None for k in ("yesAsk", "yesBid", "noAsk", "noBid")):
            continue
        priced_prestart.add(ticker)
        if best[ticker] is None or seconds < best[ticker]:
            best[ticker] = seconds

    ages = [s for s in best.values() if s is not None]
    true_close = sum(1 for s in ages if s <= checkpoints.TRUE_CLOSE_THRESHOLD_SECONDS)
    return {
        "marketsArchived": len(best),
        "trueCloseMarkets": true_close,
        "preCloseOnlyMarkets": len(ages) - true_close,
        "marketsWithNoPrestartEvidence": sum(
            1 for t, s in best.items() if s is None and t in starts),
        # Of those, the ones we DID observe pre-start but could not price --
        # a quote problem, not a coverage problem.
        "marketsSeenPrestartWithNoUsablePrice": sum(
            1 for t, s in best.items()
            if s is None and t in starts and t in seen_prestart and t not in priced_prestart),
        # And the ones we only ever saw after first pitch -- a coverage
        # problem, which is what the capture windows above exist to fix.
        "marketsSeenOnlyAfterFirstPitch": sum(
            1 for t, s in best.items()
            if s is None and t in starts and t not in seen_prestart),
        "startUnresolvedMarkets": sum(1 for t in best if t not in starts),
        "secondsBeforeStart": _percentiles(ages),
    }


EMPTY_EVIDENCE = {
    "marketsArchived": 0, "trueCloseMarkets": 0, "preCloseOnlyMarkets": 0,
    "marketsWithNoPrestartEvidence": 0,
    "marketsSeenPrestartWithNoUsablePrice": 0,
    "marketsSeenOnlyAfterFirstPitch": 0,
    "startUnresolvedMarkets": 0,
    "secondsBeforeStart": {"median": None, "p90": None},
}


def evidence_for_date(date):
    path = os.path.join("data", "edgelab", "observations", "%s.jsonl.gz" % date)
    if not os.path.exists(path):
        path = os.path.join("data", "edgelab", "observations", "%s.jsonl" % date)
    if not os.path.exists(path):
        return dict(EMPTY_EVIDENCE)
    return evidence_from_observations(_read(path))


def replay_instant(date, games):
    """The moment to judge the day from: after the LAST first pitch.

    Not 23:59:59Z. A west-coast game on `date` starts at 01:10Z the next
    day, so at end-of-day-UTC its windows are still NOT_YET_DUE -- neither
    delivered nor missed. They would drop out of both columns and quietly
    shrink the miss count on exactly the late games that were hardest to
    cover.
    """
    starts = [_parse_start(g.get("scheduledStart")) for g in games]
    starts = [s for s in starts if s is not None]
    end_of_day = datetime.datetime.fromisoformat("%sT23:59:59+00:00" % date)
    if not starts:
        return end_of_day
    return max(max(starts) + datetime.timedelta(minutes=1), end_of_day)


def _parse_start(value):
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def summarize_day(date, games, captures, evidence):
    """Pure: what was asked for on `date`, and what the archive proves arrived."""
    # Replay the coordinator past the last first pitch: every window that was
    # targeted, and whether an archived capture ever covered it.
    plan = build_capture_plan(games, replay_instant(date, games), captures)
    counts = collections.Counter(w["action"] for w in plan["windows"])

    delays = []
    for w in plan["windows"]:
        if w["action"] == ACTION_ALREADY_COVERED and w.get("coveredSecondsBeforeStart") is not None:
            target = next(off for label, off in TARGET_WINDOWS if label == w["targetWindow"])
            delays.append(target * 60 - w["coveredSecondsBeforeStart"])

    targeted = len(plan["windows"]) - counts.get(ACTION_SKIPPED_NO_START, 0)
    delivered = counts.get(ACTION_ALREADY_COVERED, 0)
    return {
        "date": date,
        "replayedAt": plan["evaluatedAt"],
        "slate": {
            "games": len(games),
            "gamesWithStartTime": plan["gamesWithStartTime"],
            "gamesWithoutStartTime": len(games) - plan["gamesWithStartTime"],
        },
        "windows": {
            "targeted": targeted,
            "delivered": delivered,
            "deliveredPct": round(100.0 * delivered / targeted, 1) if targeted else None,
            "missedLate": counts.get(ACTION_MISSED_LATE, 0),
            "skippedNoStartTime": counts.get(ACTION_SKIPPED_NO_START, 0),
            # Must be zero: the replay runs past the last first pitch, so every
            # targeted window has resolved. A non-zero value means windows are
            # falling out of both columns and the rate above is overstated.
            "unresolvedAtReplay": targeted - delivered - counts.get(ACTION_MISSED_LATE, 0),
        },
        "delivery": {
            "capturesArchived": len(captures),
            # How far past its own target each delivered capture landed.
            # Negative means EARLY, which is fine -- earlier is still pregame.
            "schedulerDelaySeconds": _percentiles(delays),
        },
        "evidence": evidence,
    }


def day_report(date):
    return summarize_day(date, load_slate_games(date), existing_capture_times(date),
                         evidence_for_date(date))


def roll_up(days, window_days):
    """Trailing totals. Days with no slate are excluded from every denominator:
    an off-day is not a coverage failure, and counting it as one would let a
    quiet week hide a bad one."""
    have_slate = [d for d in days if d["slate"]["games"]]

    def total(group, key):
        return sum(d[group][key] for d in have_slate)

    targeted, delivered = total("windows", "targeted"), total("windows", "delivered")
    archived, true_close = total("evidence", "marketsArchived"), total("evidence", "trueCloseMarkets")
    return {
        "days": window_days,
        "datesWithASlate": len(have_slate),
        "windowsTargeted": targeted,
        "windowsDelivered": delivered,
        "windowDeliveryPct": round(100.0 * delivered / targeted, 1) if targeted else None,
        "windowsMissedLate": total("windows", "missedLate"),
        "windowsUnresolvedAtReplay": total("windows", "unresolvedAtReplay"),
        "marketsArchived": archived,
        "trueCloseMarkets": true_close,
        "trueClosePct": round(100.0 * true_close / archived, 1) if archived else None,
        "preCloseOnlyMarkets": total("evidence", "preCloseOnlyMarkets"),
    }


def render_markdown(report):
    """The same numbers, for a human skimming a workflow summary."""
    t, today = report["trailing"], report["today"]
    lines = [
        "# Near-close capture health",
        "",
        "_generated %s (schema `%s`)_" % (report["generatedAt"], report["schemaVersion"]),
        "",
        "**Requested** is what the schedule asked for. **Delivered** is what the",
        "archive proves arrived. They are separate columns on purpose.",
        "",
        "## Trailing %d days" % t["days"],
        "",
        "| | |",
        "|---|---|",
        "| dates with a slate | %s |" % t["datesWithASlate"],
        "| windows requested | %s |" % t["windowsTargeted"],
        "| windows delivered | %s (**%s%%**) |" % (t["windowsDelivered"], t["windowDeliveryPct"]),
        "| windows missed (post-start) | %s |" % t["windowsMissedLate"],
        "| markets archived | %s |" % t["marketsArchived"],
        "| TRUE_CLOSE-eligible | %s (**%s%%**) |" % (t["trueCloseMarkets"], t["trueClosePct"]),
        "| PRE_CLOSE only | %s |" % t["preCloseOnlyMarkets"],
        "",
        "## By date",
        "",
        "| date | games | requested | delivered | missed | markets | TRUE_CLOSE | median s to start |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for d in report["byDate"]:
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            d["date"], d["slate"]["games"], d["windows"]["targeted"],
            "%s (%s%%)" % (d["windows"]["delivered"], d["windows"]["deliveredPct"]),
            d["windows"]["missedLate"], d["evidence"]["marketsArchived"],
            d["evidence"]["trueCloseMarkets"], d["evidence"]["secondsBeforeStart"]["median"]))
    lines += ["", "> " + report["note"], ""]
    if today["windows"]["targeted"] and not today["windows"]["delivered"]:
        lines.insert(3, "> **No near-close window was covered today.** Every CLV figure "
                        "for this date rests on quotes taken well before first pitch.\n")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=None)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--out-dir", default=OUT_DIR)
    args = ap.parse_args()

    today = args.date or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    base = datetime.date.fromisoformat(today)
    dates = [(base - datetime.timedelta(days=i)).isoformat() for i in range(args.days)]

    days = [day_report(d) for d in dates]

    report = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "today": days[0],
        "trailing": roll_up(days, args.days),
        "byDate": days,
        "note": (
            "windowsTargeted is what the schedule ASKED for; windowsDelivered is what "
            "the ARCHIVE proves arrived. They are reported separately on purpose: cron "
            "configuration is not coverage, and for 13 consecutive days the two differed "
            "by an order of magnitude without anything surfacing it."
        ),
    }

    os.makedirs(args.out_dir, exist_ok=True)
    out = os.path.join(args.out_dir, "capture_health.json")
    with open(out, "w") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
    md = os.path.join(args.out_dir, "capture_health.md")
    with open(md, "w") as fh:
        fh.write(render_markdown(report) + "\n")

    print(json.dumps({"today": report["today"], "trailing": report["trailing"]},
                     indent=2, sort_keys=True))
    print("\n-> %s\n-> %s" % (out, md))
    return 0


if __name__ == "__main__":
    sys.exit(main())
