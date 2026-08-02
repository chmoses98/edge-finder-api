#!/usr/bin/env python3
"""
scripts/backfill_snapshots.py
================================
Historical Capture Completeness and Immutable Snapshot Foundation
milestone (item 12): classifies every historical date this repository
has ANY evidence for, and actually backfills (writes real, committed)
Snapshots only for dates where doing so is safe and honest -- i.e. dates
that already have a real data/pipeline/<date>/ artifact set. Every other
historical date is classified (fully snapshot-capable / partial /
approximate only / not reconstructable) via a DRY RUN
(lib.edgelab.snapshot.classify_date) that writes nothing, so pre-pipeline
dates are never fabricated a Snapshot they cannot honestly support.

Usage:
  python3 scripts/backfill_snapshots.py [--dry-run] [--report PATH]

  --dry-run: classify every candidate date but never call build_snapshot()
             for real (no writes at all, including for pipeline dates).
  --report:  where to write the full JSON classification report
             (default: data/edgelab/reports/snapshot_backfill_classification.json)
"""
import argparse
import json
import os
import re
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from lib.edgelab import snapshot as snap  # noqa: E402

_DATED_KALSHI_RE = re.compile(r"^kalshi_search_(\d{4}-\d{2}-\d{2})\.json$")
_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _candidate_dates():
    """Union of every date this repo has ANY archived evidence for --
    real evidence only, never a fabricated/assumed date range. Deliberately
    CWD-relative (not ROOT_DIR-based), consistent with lib.edgelab.snapshot
    and testable via monkeypatch.chdir()."""
    dates = set()

    pipeline_root = os.path.join("data", "pipeline")
    if os.path.isdir(pipeline_root):
        dates.update(d for d in os.listdir(pipeline_root) if _DATE_DIR_RE.match(d))

    slates_root = os.path.join("data", "slates")
    if os.path.isdir(slates_root):
        dates.update(d for d in os.listdir(slates_root) if _DATE_DIR_RE.match(d))

    kalshi_root = os.path.join("data", "kalshi_registry_snapshots")
    if os.path.isdir(kalshi_root):
        for fn in os.listdir(kalshi_root):
            m = _DATED_KALSHI_RE.match(fn)
            if m:
                dates.add(m.group(1))

    return sorted(dates)


def _pipeline_dates():
    pipeline_root = os.path.join("data", "pipeline")
    if not os.path.isdir(pipeline_root):
        return []
    return sorted(
        d for d in os.listdir(pipeline_root)
        if _DATE_DIR_RE.match(d) and os.path.exists(os.path.join(pipeline_root, d, "recommendations.json"))
    )


def main():
    parser = argparse.ArgumentParser(description="Classify + backfill historical EdgeLab Snapshots.")
    parser.add_argument("--dry-run", action="store_true", help="Classify only, never write real Snapshots.")
    parser.add_argument(
        "--report", default=os.path.join("data", "edgelab", "reports", "snapshot_backfill_classification.json"),
    )
    args = parser.parse_args()

    candidate_dates = _candidate_dates()
    pipeline_dates = set(_pipeline_dates())

    classifications = []
    for date in candidate_dates:
        c = snap.classify_date(snap.STAGE_PRE_GAME_DECISION, date)
        c["classificationLabel"] = snap.CLASSIFICATION_LABELS[c["completenessStatus"]]
        c["hasPipelineArtifacts"] = date in pipeline_dates
        classifications.append(c)

    backfilled = []
    if not args.dry_run:
        for date in sorted(pipeline_dates):
            for stage in (snap.STAGE_PRE_GAME_DECISION, snap.STAGE_POST_GAME_SETTLEMENT, snap.STAGE_CLOSING_LINE):
                # build_snapshot_as_backfill (not build_snapshot): stamps
                # captureMode=HISTORICAL_BACKFILL so this can never be
                # confused with a contemporaneous production capture.
                result = snap.build_snapshot_as_backfill(stage, date)
                backfilled.append({
                    "snapshotStage": stage, "snapshotDate": date, "outcome": result["outcome"],
                    "completenessStatus": result["manifest"]["completenessStatus"],
                    "captureMode": result["manifest"].get("captureMode"),
                })

    label_counts = {}
    for c in classifications:
        label_counts[c["classificationLabel"]] = label_counts.get(c["classificationLabel"], 0) + 1

    report = {
        "schemaVersion": "1",
        "note": (
            "Classification is a mechanical DRY RUN (lib.edgelab.snapshot.classify_date) -- "
            "no historical input is ever fabricated. Only dates with a real "
            "data/pipeline/<date>/recommendations.json artifact are actually backfilled "
            "(written, committed) with real Snapshots; every other date is classified only."
        ),
        "totalCandidateDates": len(candidate_dates),
        "pipelineArtifactDates": sorted(pipeline_dates),
        "classificationCounts": label_counts,
        "classifications": classifications,
        "backfilled": backfilled,
        "dryRun": args.dry_run,
    }

    print(json.dumps({k: v for k, v in report.items() if k != "classifications"}, indent=2))

    os.makedirs(os.path.dirname(args.report), exist_ok=True)
    with open(args.report, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)
    print(f"\nFull classification report written to {args.report}", file=sys.stderr)


if __name__ == "__main__":
    main()
