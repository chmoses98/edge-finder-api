#!/usr/bin/env python3
"""
scripts/replay_eligibility_report.py
========================================
Level 2 Historical Replay Engine milestone (item 13): runs the replay
eligibility assessment across EVERY existing PRE_GAME_DECISION snapshot in
data/edgelab/snapshots/, processed chronologically (lib.edgelab.replay.
sorted_snapshot_dates -- item 9's walk-forward-integrity ordering
requirement), and executes actual CANDIDATE_MODEL Level 2 replay only for
snapshots whose eligibility is honestly ELIGIBLE_LEVEL_2. Never expands
the historical sample with fabricated inputs, and never silently
downgrades an ELIGIBLE_LEVEL_1_ONLY/ineligible snapshot to a Level 2 run
-- those are classified only, exactly like scripts/backfill_snapshots.py
classifies (without fabricating) pre-pipeline dates it cannot honestly
snapshot.

Usage:
  python3 scripts/replay_eligibility_report.py [--report PATH] [--dry-run]

  --dry-run: classify every snapshot's eligibility but never call
             execute_replay()/write_replay_outputs() for real (no writes).
"""
import argparse
import json
import os
import re
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from lib.edgelab import replay  # noqa: E402
from lib.edgelab import snapshot as snap  # noqa: E402

_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _all_snapshot_dates():
    """Every date this repo has ANY PRE_GAME_DECISION snapshot for -- real
    evidence only (a directory that actually exists on disk), never an
    assumed/fabricated date range."""
    if not os.path.isdir(snap.SNAPSHOTS_ROOT):
        return []
    return [d for d in os.listdir(snap.SNAPSHOTS_ROOT) if _DATE_DIR_RE.match(d)]


def main():
    parser = argparse.ArgumentParser(description="Historical Level 2 replay eligibility + execution report.")
    parser.add_argument("--dry-run", action="store_true", help="Classify only, never execute/write real replay runs.")
    parser.add_argument(
        "--report", default=os.path.join("data", "edgelab", "reports", "replay_eligibility_report.json"),
    )
    args = parser.parse_args()

    dates = replay.sorted_snapshot_dates(_all_snapshot_dates())

    per_snapshot = []
    eligibility_counts = {}
    ineligible_reason_counts = {}
    replayed = []

    markets_evaluated_total = 0
    markets_comparable_total = 0
    decisions_changed_total = 0
    settled_resolved_total = 0
    settled_unresolved_total = 0
    clv_resolved_total = 0

    # Chronological across dates (sorted_snapshot_dates), and chronological
    # within a date (list_pregame_run_dirs is already oldest-first) -- a
    # hypothetical future fitted-model replay run over this same loop
    # structure could never see a later run before an earlier one.
    for date in dates:
        for run_dir in snap.list_pregame_run_dirs(date):
            manifest = snap.load_manifest(snap.STAGE_PRE_GAME_DECISION, date, run_key=run_dir)
            if manifest is None:
                continue

            eligibility = replay.assess_replay_eligibility(manifest)
            status = eligibility["eligibilityStatus"]
            eligibility_counts[status] = eligibility_counts.get(status, 0) + 1

            entry = {
                "snapshotId": manifest.get("snapshotId"),
                "snapshotDate": date,
                "runDir": run_dir,
                "eligibilityStatus": status,
                "limitationReasons": eligibility["limitationReasons"],
                "replayed": False,
            }

            if status not in replay.ELIGIBLE_STATUSES:
                for reason in eligibility["limitationReasons"]:
                    ineligible_reason_counts[reason] = ineligible_reason_counts.get(reason, 0) + 1

            # Item 13: run actual Level 2 replay only where eligibility is
            # honest -- never at ELIGIBLE_LEVEL_1_ONLY here (that would
            # require the CLI's explicit --allow-approximate request this
            # unattended batch job never makes on a caller's behalf).
            if status == replay.ELIGIBLE_LEVEL_2 and not args.dry_run:
                run, results = replay.execute_replay(manifest, replay_mode=replay.MODE_CANDIDATE)
                write_result = replay.write_replay_outputs(run, results)
                entry["replayed"] = True
                entry["replayRunId"] = run["replayRunId"]
                entry["runStatus"] = run["runStatus"]
                entry["writeOutcome"] = write_result["outcome"]
                entry["outputPath"] = write_result["path"]
                if run["runStatus"] == replay.RUN_STATUS_COMPLETED:
                    replayed.append(run["replayRunId"])
                    s = run["summary"]
                    markets_evaluated_total += s["marketsEvaluated"]
                    markets_comparable_total += s["marketsComparable"]
                    decisions_changed_total += s["decisionsChanged"]
                    settled_resolved_total += s["settledResolved"]
                    settled_unresolved_total += s["settledUnresolved"]
                    clv_resolved_total += s["clvResolved"]

            per_snapshot.append(entry)

    total_snapshots = len(per_snapshot)
    settlement_total = settled_resolved_total + settled_unresolved_total
    report = {
        "schemaVersion": "1",
        "note": (
            "Eligibility is a mechanical assessment (lib.edgelab.replay.assess_replay_eligibility) -- "
            "no historical input is ever fabricated. Actual Level 2 CANDIDATE_MODEL replay is executed "
            "only for snapshots that assess as ELIGIBLE_LEVEL_2; ELIGIBLE_LEVEL_1_ONLY and every "
            "INELIGIBLE_* snapshot are classified only, never silently promoted or auto-approximated."
        ),
        "datesProcessed": dates,
        "totalSnapshots": total_snapshots,
        "eligibilityCounts": eligibility_counts,
        "ineligibleReasonCounts": ineligible_reason_counts,
        "successfullyReplayedCount": len(replayed),
        "replayRunIds": replayed,
        "comparableOriginalDecisions": markets_comparable_total,
        "mismatchesFound": decisions_changed_total,
        "marketsEvaluated": markets_evaluated_total,
        "settlementCoverage": {
            "resolved": settled_resolved_total,
            "unresolved": settled_unresolved_total,
            "coverageRate": round(settled_resolved_total / settlement_total, 4) if settlement_total else None,
        },
        "clvCoverage": {
            "resolved": clv_resolved_total,
            "ofMarketsEvaluated": markets_evaluated_total,
            "coverageRate": round(clv_resolved_total / markets_evaluated_total, 4) if markets_evaluated_total else None,
        },
        "perSnapshot": per_snapshot,
        "dryRun": args.dry_run,
    }

    print(json.dumps({k: v for k, v in report.items() if k != "perSnapshot"}, indent=2))

    os.makedirs(os.path.dirname(args.report), exist_ok=True)
    with open(args.report, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)
    print(f"\nFull report written to {args.report}", file=sys.stderr)


if __name__ == "__main__":
    main()
