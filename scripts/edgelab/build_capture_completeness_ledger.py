#!/usr/bin/env python3
"""
scripts/edgelab/build_capture_completeness_ledger.py
====================================================
PHASE J: a deterministic, per-snapshot record of what each historical
capture can PROVE about its own completeness.

It invents nothing. Every classification comes from evidence the snapshot
already carries (lib/edgelab/capture_completeness.py), and a snapshot
that carries no such evidence is labelled UNKNOWN_LEGACY rather than
being given the benefit of the doubt.

That last point is the whole design. Under the pre-v4 capture path a live
cursor at `maxPages = 10` was discarded with NO failure recorded, so
`fetchFailureCount: 0` means "nothing reported an error", not "everything
was retrieved". Marking those COMPLETE would launder an absence of
evidence into evidence of absence, and would do it on precisely the
busiest captures -- the ones a page cap bites first.

The consequence is uncomfortable and is stated plainly in the output:
almost the entire historical archive is UNKNOWN_LEGACY, so research that
demands proven completeness has very little history to stand on until the
v4 contract has been running for a while.

Usage:
    python3 scripts/edgelab/build_capture_completeness_ledger.py [--out PATH]
"""
import argparse
import collections
import datetime
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.capture_completeness import (
    COMPLETE, FAILED, PARTIAL, UNKNOWN_LEGACY, classify_snapshot, reconcile_snapshot,
)

SCHEMA_VERSION = "capture_completeness_ledger_v1"
SNAPSHOT_DIR = os.path.join("data", "kalshi_registry_snapshots")
DEFAULT_OUT = os.path.join("data", "edgelab", "reports", "capture_completeness_ledger.json")


def snapshot_paths(snapshot_dir=SNAPSHOT_DIR):
    """Timestamped captures only. The undated `kalshi_search_<date>.json` is a
    rolling copy of whichever capture ran last, so counting it would double
    count that capture under a name that hides which one it was."""
    return sorted(glob.glob(os.path.join(snapshot_dir, "kalshi_search_*_*.json")))


def ledger_row(path):
    name = os.path.basename(path)
    try:
        with open(path) as fh:
            snapshot = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return {"snapshot": name, "class": UNKNOWN_LEGACY,
                "reason": "snapshot unreadable: %s" % exc,
                "contractVersion": None, "marketsArchived": 0,
                "incompleteSeries": [], "truncationReasons": [],
                "reconciliation": {"reconcilable": False, "unaccounted": None},
                "unreadable": True}
    row = classify_snapshot(snapshot)
    row["snapshot"] = name
    row["fetchedAt"] = snapshot.get("fetched_at")
    row["date"] = snapshot.get("date")
    row["reconciliation"] = reconcile_snapshot(snapshot)
    return row


def build_ledger(paths):
    rows = [ledger_row(p) for p in paths]
    classes = collections.Counter(r["class"] for r in rows)
    series_hits = collections.Counter()
    truncations = collections.Counter()
    for row in rows:
        series_hits.update(row.get("incompleteSeries") or [])
        truncations.update(row.get("truncationReasons") or [])

    reconcilable = [r for r in rows if r["reconciliation"].get("reconcilable")]
    unaccounted = sum(r["reconciliation"].get("unaccounted") or 0 for r in reconcilable)

    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": datetime.datetime.now(datetime.timezone.utc)
                        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "totals": {
            "snapshots": len(rows),
            COMPLETE: classes.get(COMPLETE, 0),
            PARTIAL: classes.get(PARTIAL, 0),
            FAILED: classes.get(FAILED, 0),
            UNKNOWN_LEGACY: classes.get(UNKNOWN_LEGACY, 0),
            "unreadable": sum(1 for r in rows if r.get("unreadable")),
            "marketsArchived": sum(r.get("marketsArchived") or 0 for r in rows),
            "researchQualifiedSnapshots": classes.get(COMPLETE, 0),
            "reconcilableSnapshots": len(reconcilable),
            "unaccountedRows": unaccounted,
            "seriesIncompleteFrequency": dict(series_hits.most_common()),
            "truncationReasonFrequency": dict(truncations.most_common()),
        },
        "interpretation": {
            "whyCleanLegacySnapshotsAreNotComplete": (
                "Under the pre-v4 capture path a live cursor at maxPages=10 was "
                "discarded with no failure recorded anywhere, so fetchFailureCount=0 "
                "means 'nothing reported an error', which is strictly weaker than "
                "'everything was retrieved'. Calling those COMPLETE would launder an "
                "absence of evidence into evidence of absence, on exactly the busiest "
                "captures a page cap bites first."),
            "whyPartialIsRefusedForResearch": (
                "The missingness is systematic, not random: the old sequential fetch "
                "meant a rate limit truncated whatever series came last, so the same "
                "hitter prop families absorbed the loss every time. Mixing that into a "
                "family-level calibration adds bias in a known direction, not noise."),
            "consequence": (
                "Until the v4 contract has been capturing for a while there is almost "
                "no COMPLETE history. Research requiring proven completeness must say "
                "so and report its reduced sample rather than quietly falling back."),
        },
        "rows": rows,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot-dir", default=SNAPSHOT_DIR)
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    ledger = build_ledger(snapshot_paths(args.snapshot_dir))
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(ledger, fh, indent=2, sort_keys=True)

    print(json.dumps(ledger["totals"], indent=2, sort_keys=True))
    print("\n-> %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
