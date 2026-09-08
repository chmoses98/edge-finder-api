#!/usr/bin/env python3
"""
scripts/ci/settlement_restore_manifest.py
=========================================
REMEDIATION WAVE 0, section D/H. The DRY-RUN MANIFEST that must be produced
and reviewed before any historical settlement data is written.

It answers, for a date window, exactly one question:

    "Which canonical partitions are missing, what would create them, and what
     already exists that a restore must NOT touch?"

It is strictly read-only. It writes nothing except its own manifest file, it
never invokes the settlement engine, and it never proposes a result for any
market. Its purpose is to make a restore reviewable BEFORE it happens, and
re-runnable AFTER it happens to prove the restore converged (§H: "rerun the
dry-run and require zero further changes").

WHY A MANIFEST RATHER THAN JUST RUNNING THE RESTORE
---------------------------------------------------
Settlement writes to the canonical evidence base that every calibration and
research conclusion in this repository is drawn from. The 2026-09 outage was
survivable precisely because nothing wrong was written -- work was discarded,
not corrupted. A careless repair is the one way to convert a recoverable
outage into an unrecoverable one, so the repair is gated behind a diff that a
human can read first.

IDEMPOTENCY CONTRACT
--------------------
`pendingDates` is computed purely from which partitions exist on disk. After a
successful restore every restored date has its partitions, so a second run
reports `pendingDates: []` and `converged: true`. That is the machine-checkable
form of "rerun the dry-run and require zero further changes".

EXECUTION IS DELIBERATELY NOT IMPLEMENTED HERE
----------------------------------------------
Restoring settlement requires final-score ground truth from the MLB Stats API
(lib/edgelab/mlb_schedule.py, clv_update.py's linescore path). The canonical
executors already exist and are already tested:

    clv-update.yml        workflow_dispatch, input: date
    edgelab-postgame.yml  workflow_dispatch, input: date

Both are idempotent -- already-terminal bets are skipped, settlement records
merge rather than duplicate. Adding a third, parallel restore path would mean
a second settlement implementation that could drift from the canonical one,
which is exactly the class of defect the institutional audit flagged
repeatedly. So this tool tells the operator which dates to dispatch and in
what order, and the canonical workflows do the work.

USAGE
-----
    python3 scripts/ci/settlement_restore_manifest.py --from 2026-09-01 --to 2026-09-06
    python3 scripts/ci/settlement_restore_manifest.py --from ... --to ... --write-artifact
"""

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(_HERE))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from lib.atomic_json import write_json_atomic  # noqa: E402

# Canonical partitions edgelab-postgame.yml produces for a settled date. A date
# is "restored" when the settlement partition exists; the others are recorded
# for completeness so a partial restore is visible rather than rounded off.
PARTITION_SPECS = [
    ("settlements", "data/edgelab/settlements/{date}.jsonl", True),
    ("recommendations", "data/edgelab/recommendations/{date}.jsonl", False),
    ("modelEvaluations", "data/edgelab/model_evaluations/{date}.jsonl", False),
    ("games", "data/edgelab/games/{date}.jsonl", False),
]


def _daterange(start, end):
    d0 = datetime.strptime(start, "%Y-%m-%d").date()
    d1 = datetime.strptime(end, "%Y-%m-%d").date()
    if d1 < d0:
        raise ValueError("--to (%s) is before --from (%s)" % (end, start))
    out = []
    cur = d0
    while cur <= d1:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def _partition_state(root, date_str):
    """Existing/missing state of every canonical partition for one date."""
    state = {}
    for name, template, _required in PARTITION_SPECS:
        rel = template.format(date=date_str)
        path = os.path.join(root, rel)
        gz = path + ".gz"
        if os.path.exists(path):
            state[name] = {"path": rel, "exists": True,
                           "rows": sum(1 for _ in open(path, errors="ignore")),
                           "form": "jsonl"}
        elif os.path.exists(gz):
            state[name] = {"path": rel + ".gz", "exists": True,
                           "rows": None, "form": "jsonl.gz (compacted)"}
        else:
            state[name] = {"path": rel, "exists": False, "rows": 0, "form": None}
    return state


def build_manifest(root, date_from, date_to, now=None):
    """
    Pure-ish (reads the filesystem, writes nothing). Returns the manifest dict.
    """
    now = now or datetime.now(timezone.utc)
    dates = _daterange(date_from, date_to)

    entries = []
    for date_str in dates:
        state = _partition_state(root, date_str)
        settlement_present = state["settlements"]["exists"]
        entries.append({
            "date": date_str,
            "settlementPresent": settlement_present,
            "action": "NONE_ALREADY_PRESENT" if settlement_present else "RESTORE_REQUIRED",
            "partitions": state,
            # Named explicitly so a reviewer can see that a restore CREATES
            # missing partitions and never rewrites present ones.
            "wouldCreate": [
                v["path"] for v in state.values() if not v["exists"]
            ],
            "wouldLeaveUntouched": [
                v["path"] for v in state.values() if v["exists"]
            ],
        })

    pending = [e["date"] for e in entries if not e["settlementPresent"]]

    return {
        "generatedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generatedBy": "scripts/ci/settlement_restore_manifest.py",
        "wave": "REMEDIATION_WAVE_0",
        "mode": "DRY_RUN",
        "window": {"from": date_from, "to": date_to},
        "pendingDates": pending,
        "restoredDates": [e["date"] for e in entries if e["settlementPresent"]],
        "converged": pending == [],
        "entries": entries,
        "executor": {
            "note": (
                "This tool never writes settlement data. Restore is performed by the "
                "canonical, already-tested workflows, dispatched one date at a time in "
                "chronological order."
            ),
            "commands": [
                "gh workflow run clv-update.yml -f date=%s" % d for d in pending
            ] + [
                "gh workflow run edgelab-postgame.yml -f date=%s" % d for d in pending
            ],
            "requiresNetwork": [
                "statsapi.mlb.com (final scores / linescores -- settlement ground truth)",
                "api.elections.kalshi.com (closing quotes -- CLV)",
            ],
            "idempotent": True,
            "idempotencyBasis": (
                "clv_update.py skips already-terminal bets (get_result() checks); "
                "lib/edgelab/settlement.merge_settlement_record merges rather than "
                "duplicating; bets.json is written via lib.atomic_json.write_json_atomic."
            ),
        },
        "safetyInvariants": [
            "No settlement result is proposed or inferred by this tool.",
            "No existing partition is rewritten -- only absent partitions are created.",
            "User-confirmed wager economics (stake, entryPrice, confirmedReceipt*) are "
            "never touched by settlement; only lifecycle/result fields change.",
            "A date whose game identity is ambiguous (audit CR-3 doubleheaders) is left "
            "unresolved by the canonical settler rather than guessed.",
            "Re-running this manifest after a restore must report converged: true.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Settlement restore dry-run manifest")
    parser.add_argument("--from", dest="date_from", required=True)
    parser.add_argument("--to", dest="date_to", required=True)
    parser.add_argument("--root", default=ROOT_DIR)
    parser.add_argument("--write-artifact", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require-converged", action="store_true",
                        help="exit 1 unless every date in the window is restored "
                             "(use for the post-restore verification run)")
    args = parser.parse_args(argv)

    manifest = build_manifest(args.root, args.date_from, args.date_to)

    if args.json:
        print(json.dumps(manifest, indent=2, sort_keys=True))
    else:
        print("SETTLEMENT RESTORE MANIFEST -- DRY RUN (nothing is written)")
        print("=" * 78)
        print("window: %s .. %s" % (args.date_from, args.date_to))
        print()
        for e in manifest["entries"]:
            mark = "OK " if e["settlementPresent"] else "MISS"
            print("  [%s] %s  %s" % (mark, e["date"], e["action"]))
            for path in e["wouldCreate"]:
                print("         would create: %s" % path)
        print()
        print("pending dates : %s" % (", ".join(manifest["pendingDates"]) or "(none)"))
        print("converged     : %s" % manifest["converged"])
        if manifest["pendingDates"]:
            print()
            print("Restore is NOT performed by this tool. Dispatch the canonical")
            print("workflows in chronological order:")
            for cmd in manifest["executor"]["commands"]:
                print("    %s" % cmd)

    if args.write_artifact:
        out_dir = os.path.join(args.root, "data", "edgelab", "operational_health")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "settlement_restore_manifest.json")
        write_json_atomic(manifest, out_path, indent=2)
        print("\nartifact: %s" % os.path.relpath(out_path, args.root))

    if args.require_converged and not manifest["converged"]:
        print("\nNOT CONVERGED: %d date(s) still missing settlement partitions."
              % len(manifest["pendingDates"]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
